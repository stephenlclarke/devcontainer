"""Prepare verified release binaries on SSD, without installation or compilation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tarfile
import tempfile

from release_inputs import acquire, canonical, sha256, verify_object


RECEIPT = ".prepared-release.json"
MAX_FILES = 10000
MAX_BYTES = 2 * 1024**3


def layout(asset: dict) -> dict:
    """Only reviewed release layouts are admitted; never guess a missing binary."""
    repository, name = asset["repository"], asset["name"]
    if repository == "stephenlclarke/devcontainer" and name == "devcontainer-release-arm64.tar.gz":
        return {"format": "tar", "executables": {
            key: f"devcontainer-{asset['tag']}/bin/{key}"
            for key in ("devcontainer", "devcontainer-engine", "devcontainer-compose")}}
    if repository == "stephenlclarke/container-compose":
        if name == "container-compose-plugin-release-arm64.tar.gz":
            return {"format": "tar", "executables": {"compose": "compose/bin/compose",
                    "compose-normalizer": "compose/resources/compose-normalizer"}}
        if name == "container-release-arm64.tar.gz":
            return {"format": "tar", "executables": {key: "bin/" + key for key in
                    ("container", "container-engine", "container-apiserver")}}
    if repository == "apple/container" and name == f"container-{asset['tag']}-installer-signed.pkg":
        return {"format": "pkg", "executables": {"container": "Payload/bin/container",
                "container-apiserver": "Payload/bin/container-apiserver"}}
    if repository == "docker/compose" and name == "docker-compose-darwin-aarch64":
        return {"format": "raw", "executables": {"docker-compose": "bin/docker-compose"}}
    raise ValueError("No reviewed layout for this published asset")


def member_path(name: str) -> Path:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or "\x00" in name:
        raise ValueError("Unsafe release archive member")
    if path.parts and path.parts[0] == RECEIPT:
        raise ValueError("Archive collides with preparation receipt")
    return Path(*path.parts)


def unpack_tar(source: Path, destination: Path) -> None:
    """No links, device nodes, owner restoration or implicit tar extraction."""
    seen = set()
    size = 0
    with tarfile.open(source, "r:gz") as archive:
        for entry in archive:
            path = member_path(entry.name)
            if path == Path(".") and entry.isdir():
                continue
            if path == Path(".") or path in seen or len(seen) >= MAX_FILES:
                raise ValueError("Duplicate or excessive release archive members")
            seen.add(path)
            if not (entry.isdir() or entry.isfile()) or entry.size < 0:
                raise ValueError("Release archive must contain only files and directories")
            size += entry.size
            if size > MAX_BYTES:
                raise ValueError("Expanded release exceeds size limit")
            target = destination / path
            if entry.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(entry) as incoming, target.open("xb") as output:
                    shutil.copyfileobj(incoming, output)
                # Retain executable semantics, never setuid/setgid or world write.
                target.chmod(0o755 if entry.mode & 0o111 else 0o644)


def expand_package(source: Path, destination: Path) -> None:
    # pkgutil expands payloads only. It does not run installer scripts, write to
    # /usr/local, register packages, start services or ask for administrator auth.
    subprocess.run(["/usr/sbin/pkgutil", "--expand-full", str(source), str(destination)],
                   check=True, capture_output=True, timeout=120,
                   env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "TMPDIR": str(destination.parent)})


def inventory(root: Path) -> dict:
    """Authenticate the complete payload, not just the command used first."""
    result = {}
    size = 0
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in sorted(directories + files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if relative == RECEIPT:
                continue
            info = path.lstat()
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError("Prepared release contains a link or special file")
            size += info.st_size if stat.S_ISREG(info.st_mode) else 0
            if len(result) >= MAX_FILES or size > MAX_BYTES:
                raise ValueError("Prepared release exceeds inventory limits")
            result[relative] = {"mode": stat.S_IMODE(info.st_mode), "kind": "directory" if path.is_dir() else "file"}
            if path.is_file():
                result[relative].update(size=info.st_size, sha256=sha256(path))
    return result


def validate_prepared(root: Path, specification: dict, retained_receipt: dict) -> dict:
    receipt_path = root / RECEIPT
    if root.is_symlink() or not root.is_dir() or receipt_path.is_symlink():
        raise ValueError("Invalid prepared release root or receipt")
    receipt = json.loads(receipt_path.read_text())
    if receipt != retained_receipt:
        raise ValueError("SSD receipt differs from internally retained inventory")
    if set(receipt) != {"specification", "inventory"} or receipt["specification"] != specification:
        raise ValueError("Prepared release identity differs")
    if receipt["inventory"] != inventory(root):
        raise ValueError("Prepared release bytes or modes changed")
    for path in specification["layout"]["executables"].values():
        item = receipt["inventory"].get(path, {})
        if item.get("kind") != "file" or not item["mode"] & 0o111:
            raise ValueError("Required release executable is missing or not executable")
    return receipt


def retain_receipt(path: Path, receipt: dict) -> None:
    """Publish the inventory internally before the disposable SSD tree appears."""
    if path.is_symlink():
        raise ValueError("Symlinked retained preparation receipt")
    if path.exists():
        if json.loads(path.read_text()) != receipt:
            raise ValueError("Cannot replace a different retained preparation receipt")
    else:
        with tempfile.TemporaryDirectory(dir=path.parent, prefix="prepare-receipt-") as temporary:
            staged = Path(temporary) / "receipt"
            with staged.open("x") as output:
                output.write(canonical(receipt))
                output.flush()
                os.fsync(output.fileno())
            staged.rename(path)
    # Recovery can encounter a receipt renamed just before an earlier process
    # died. Re-establish file and directory durability even when bytes match.
    with path.open("rb") as durable:
        os.fsync(durable.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def prepare(asset: dict, source: Path, root: Path, receipts: Path, *, expand=expand_package) -> dict:
    """Caller holds the reference-store lease; interruption never seals half a tree."""
    for directory in (root, receipts):
        if directory.resolve() != directory or not directory.is_dir():
            raise ValueError("Preparation roots must be existing non-symlinked directories")
    verify_object(source, asset)
    specification = {"schemaVersion": 1, "assetSHA256": asset["sha256"], "layout": layout(asset)}
    key = hashlib.sha256(canonical(specification).encode()).hexdigest()
    destination = root / key
    retained = receipts / (key + ".json")
    if retained.is_symlink():
        raise ValueError("Symlinked retained preparation receipt")
    if destination.exists() or destination.is_symlink():
        receipt = validate_prepared(destination, specification, json.loads(retained.read_text()))
    else:
        with tempfile.TemporaryDirectory(dir=root, prefix="prepare-") as temporary:
            staged = Path(temporary) / "payload"
            kind = specification["layout"]["format"]
            if kind == "pkg":
                expand(source, staged)
            else:
                staged.mkdir()
                if kind == "tar":
                    unpack_tar(source, staged)
                else:
                    command = staged / "bin/docker-compose"
                    command.parent.mkdir()
                    shutil.copyfile(source, command)
                    command.chmod(0o755)
            receipt = {"specification": specification, "inventory": inventory(staged)}
            with (staged / RECEIPT).open("x") as output:
                output.write(canonical(receipt))
            validate_prepared(staged, specification, receipt)
            # Detect changed retained bytes before publishing a preparation.
            verify_object(source, asset)
            retain_receipt(retained, receipt)
            staged.rename(destination)
    return {"assetSHA256": asset["sha256"], "preparationSHA256": key, "root": str(destination),
            "inventorySHA256": hashlib.sha256(canonical(receipt["inventory"]).encode()).hexdigest(),
            "executables": {name: str(destination / path) for name, path in specification["layout"]["executables"].items()}}


def require_prepared(asset: dict, source: Path, root: Path, receipts: Path) -> dict:
    """Runtime admission is read-only: never download, expand or repair a payload."""
    for directory in (root, receipts):
        if directory.resolve() != directory or not directory.is_dir():
            raise ValueError("Preparation roots must be existing non-symlinked directories")
    verify_object(source, asset)
    specification = {"schemaVersion": 1, "assetSHA256": asset["sha256"], "layout": layout(asset)}
    key = hashlib.sha256(canonical(specification).encode()).hexdigest()
    retained = receipts / (key + ".json")
    if retained.is_symlink():
        raise ValueError("Symlinked retained preparation receipt")
    destination = root / key
    receipt = validate_prepared(destination, specification, json.loads(retained.read_text()))
    return {"assetSHA256": asset["sha256"], "preparationSHA256": key, "root": str(destination),
            "inventorySHA256": hashlib.sha256(canonical(receipt["inventory"]).encode()).hexdigest(),
            "executables": {name: str(destination / path) for name, path in specification["layout"]["executables"].items()}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lock", type=Path)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    retained = Path.home() / "Library/Application Support/ContainerFamily/retained/workflow"
    if retained.stat().st_dev != Path.home().stat().st_dev:
        raise ValueError("Release archives require internal retained storage")
    scratch = Path("/Volumes/SSD/cf/bazel")
    acquired = acquire(json.loads(args.lock.read_text()), retained, scratch / "tmp", offline=args.offline)
    result = {"schemaVersion": 1, "scope": "prepared-binaries-only", "runtimeReady": False,
              "lockSHA256": acquired["lockSHA256"], "assets": []}
    for asset in acquired["assets"]:
        result["assets"].append(prepare(asset, Path(asset["path"]), scratch / "prepared-releases", retained / "prepared-receipts"))
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
