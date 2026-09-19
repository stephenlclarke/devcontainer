"""Extract on SSD and retain verified release payloads as durable local assets."""

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
KERNEL_MEMBER = "./opt/kata/share/kata-containers/vmlinux-6.18.35-197-debug"
KERNEL_BYTES = 30423552


def layout(asset: dict) -> dict:
    """Only reviewed release layouts are admitted; never guess a missing binary."""
    repository, name = asset["repository"], asset["name"]
    if (repository, asset["tag"], name) == (
            "nodejs.org/dist", "24.21.0", "node-v24.21.0-darwin-arm64.tar.gz"):
        return {"format": "selected-tar", "executables": {"node": "node-v24.21.0-darwin-arm64/bin/node"},
                "files": {"license": "node-v24.21.0-darwin-arm64/LICENSE"}}
    if (repository, asset["tag"], name) == ("registry.npmjs.org/@devcontainers/cli", "0.88.0", "cli-0.88.0.tgz"):
        return {"format": "tar", "executables": {"devcontainer-cli": "package/devcontainer.js"},
                "files": {"package": "package/package.json"}}
    if (repository, asset["tag"], name) == (
            "ghcr.io/homebrew/core/docker", "29.6.2", "docker--29.6.2.arm64_tahoe.bottle.tar.gz"):
        return {"format": "tar", "executables": {"docker": "docker/29.6.2/bin/docker"}}
    if repository == "local/devcontainer-candidate" and name == "candidate_archive.tar.gz":
        return {"format": "tar", "executables": {
            key: f"devcontainer-{asset['tag']}/bin/{key}"
            for key in ("devcontainer", "devcontainer-engine", "devcontainer-compose")}}
    if repository == "local/container-compose-candidate" and name == "candidate_archive.tar.gz":
        return {"format": "tar", "executables": {"compose": "compose/bin/compose",
                "compose-normalizer": "compose/resources/compose-normalizer", **{
                    "compose-volume-initializer-linux-" + arch:
                        "compose/resources/volume-initializer/compose-volume-initializer-linux-" + arch
                    for arch in ("arm64", "amd64")}}}
    if repository == "local/devcontainer-candidate" and name == "candidate_archive_v2.tar.gz":
        prefix = f"devcontainer-{asset['tag']}/"
        reference = prefix + "libexec/devcontainer/reference/"
        return {"format": "tar", "executables": {
            **{key: prefix + "bin/" + key for key in
               ("devcontainer", "devcontainer-engine", "devcontainer-compose", "devcontainer-docker")},
            "reference-node": reference + "node"}, "files": {
                name: reference + name for name in (
                    "NODE-LICENSE.txt", "runtime-lock.json", "cli/devcontainer.js",
                    "cli/dist/spec-node/devContainersSpecCLI.js", "cli/scripts/updateUID.Dockerfile",
                    "cli/package.json", "cli/LICENSE.txt", "cli/ThirdPartyNotices.txt")}}
    if (repository, asset["tag"], name) == ("kata-containers/kata-containers", "3.32.0", "kata-static-3.32.0-arm64.tar.zst"):
        return {"format": "kernel-zstd", "executables": {}, "files": {"kernel": "kernel/vmlinux"}}
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
    if (repository, asset["tag"], name) == ("abiosoft/colima", "v0.10.3", "colima-Darwin-arm64"):
        return {"format": "raw", "executables": {"colima": "bin/colima"}}
    if (repository, asset["tag"], name) == ("lima-vm/lima", "v2.2.0", "lima-2.2.0-Darwin-arm64.tar.gz"):
        return {"format": "tar", "executables": {"limactl": "bin/limactl", "lima": "bin/lima"},
                "files": {"guest-agent": "share/lima/lima-guestagent.Linux-aarch64.gz"},
                # Documentation alias only. Never materialize archive links or
                # relax the regular-file rule for any runtime payload.
                "omittedLinks": {"share/doc/lima/templates": "../../lima/templates"}}
    if (repository, asset["tag"], name) == (
            "abiosoft/colima-core", "v0.10.4", "ubuntu-24.04-minimal-cloudimg-arm64-docker.raw.gz"):
        return {"format": "raw-data", "executables": {}, "files": {"disk-image": "images/" + name}}
    raise ValueError("No reviewed layout for this published asset")


def member_path(name: str) -> Path:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or "\x00" in name:
        raise ValueError("Unsafe release archive member")
    if path.parts and path.parts[0] == RECEIPT:
        raise ValueError("Archive collides with preparation receipt")
    return Path(*path.parts)


def unpack_tar(source: Path, destination: Path, omitted_links: dict | None = None) -> None:
    """No links, device nodes, owner restoration or implicit tar extraction."""
    seen = set()
    omitted_links = omitted_links or {}
    omitted = set()
    size = 0
    with tarfile.open(source, "r:gz") as archive:
        for entry in archive:
            path = member_path(entry.name)
            if path == Path(".") and entry.isdir():
                continue
            if path == Path(".") or path in seen or len(seen) >= MAX_FILES:
                raise ValueError("Duplicate or excessive release archive members")
            seen.add(path)
            if path.as_posix() in omitted_links:
                if not entry.issym() or entry.linkname != omitted_links[path.as_posix()] or entry.size != 0:
                    raise ValueError("Reviewed documentation link changed")
                omitted.add(path.as_posix())
                continue
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
    if omitted != set(omitted_links):
        raise ValueError("Reviewed documentation link is missing")


def copy_raw(source: Path, destination: Path, specification: dict) -> None:
    """Copy one reviewed raw asset; VM archives remain non-executable data."""
    executable = specification["format"] == "raw"
    paths = specification["executables" if executable else "files"]
    if len(paths) != 1:
        raise ValueError("Raw release layout requires exactly one destination")
    target = destination / member_path(next(iter(paths.values())))
    target.parent.mkdir(parents=True)
    shutil.copyfile(source, target)
    target.chmod(0o755 if executable else 0o600)


def unpack_selected_tar(source: Path, destination: Path, specification: dict) -> None:
    """Retain only reviewed Node binary/license; never materialize npm or links."""
    executables = set(specification["executables"].values())
    selected = executables | set(specification["files"].values())
    seen, found, size = set(), set(), 0
    with tarfile.open(source, "r:gz") as archive:
        for entry in archive:
            path = member_path(entry.name)
            if path == Path(".") and entry.isdir():
                continue
            if path == Path(".") or path in seen or len(seen) >= MAX_FILES or entry.size < 0:
                raise ValueError("Duplicate or excessive selected archive members")
            seen.add(path)
            size += entry.size
            if size > MAX_BYTES:
                raise ValueError("Expanded selected archive exceeds size limit")
            if path.as_posix() not in selected:
                continue
            if not entry.isfile():
                raise ValueError("Selected archive member is not a regular file")
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(entry) as incoming, target.open("xb") as output:
                shutil.copyfileobj(incoming, output)
            target.chmod(0o755 if path.as_posix() in executables else 0o600)
            found.add(path.as_posix())
    if found != selected:
        raise ValueError("Selected release payload is incomplete")


def expand_package(source: Path, destination: Path) -> None:
    # pkgutil expands payloads only. It does not run installer scripts, write to
    # /usr/local, register packages, start services or ask for administrator auth.
    subprocess.run(["/usr/sbin/pkgutil", "--expand-full", str(source), str(destination)],
                   check=True, capture_output=True, timeout=120,
                   env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "TMPDIR": str(destination.parent)})


def unpack_kernel(source: Path, destination: Path) -> None:
    """Read one reviewed release member; never extract the full Kata root tree."""
    result = subprocess.run(["/usr/bin/tar", "-xOf", str(source), KERNEL_MEMBER],
                            check=True, capture_output=True, timeout=60,
                            env={"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "TMPDIR": str(destination.parent)})
    # The input archive digest is checked before and after this extraction.
    # Pin size as well: duplicate matches cannot silently concatenate kernels.
    if result.stderr or len(result.stdout) != KERNEL_BYTES or result.stdout[56:60] != b"ARMd":
        raise ValueError("Recommended kernel has unexpected size, header or extraction warning")
    kernel = destination / "kernel/vmlinux"
    kernel.parent.mkdir()
    with kernel.open("xb") as output:
        output.write(result.stdout)
    kernel.chmod(0o600)


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
    for path in specification["layout"].get("files", {}).values():
        item = receipt["inventory"].get(path, {})
        if item.get("kind") != "file" or item.get("size", 0) <= 0 or item["mode"] & 0o111:
            raise ValueError("Required release data file is missing, empty or executable")
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
            with os.fdopen(os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
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
                    unpack_tar(source, staged, specification["layout"].get("omittedLinks"))
                elif kind == "selected-tar":
                    unpack_selected_tar(source, staged, specification["layout"])
                elif kind == "kernel-zstd":
                    unpack_kernel(source, staged)
                elif kind in ("raw", "raw-data"):
                    copy_raw(source, staged, specification["layout"])
                else:
                    raise ValueError("Unsupported release preparation format")
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
            "executables": {name: str(destination / path) for name, path in specification["layout"]["executables"].items()},
            **({"files": {name: str(destination / path) for name, path in specification["layout"]["files"].items()}}
               if "files" in specification["layout"] else {})}


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
            "executables": {name: str(destination / path) for name, path in specification["layout"]["executables"].items()},
            **({"files": {name: str(destination / path) for name, path in specification["layout"]["files"].items()}}
               if "files" in specification["layout"] else {})}


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_file(path: Path, source: Path | None, data: bytes | None = None, mode: int = 0o600) -> None:
    """Write only registered pending asset files, never linked or foreign files."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        info = os.fstat(output.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError("Pending release file is not privately owned")
        os.ftruncate(output.fileno(), 0)
        if source is not None:
            with source.open("rb") as incoming:
                shutil.copyfileobj(incoming, output)
        else:
            output.write(data)
        output.flush()
        os.fchmod(output.fileno(), mode)
        os.fsync(output.fileno())


def require_retained(asset: dict, source: Path, root: Path, receipts: Path) -> dict:
    specification = {"schemaVersion": 1, "assetSHA256": asset["sha256"], "layout": layout(asset)}
    key = hashlib.sha256(canonical(specification).encode()).hexdigest()
    pending = root / (key + ".pending.json")
    if pending.exists() or pending.is_symlink():
        raise ValueError("Retained executable publication is unfinished")
    return require_prepared(asset, source, root, receipts)


def retain_prepared(asset: dict, source: Path, scratch: Path, root: Path, receipts: Path) -> dict:
    """Publish into permanent asset slots; scratch/extraction stays on SSD.

    The caller holds the reference-store lease. Pending intent precedes writes;
    runtime admission rejects that intent until every byte and mode is durable.
    Completed assets are immutable and never silently repaired or overwritten.
    """
    if root.resolve() != root or not root.is_dir():
        raise ValueError("Retained executable root must be a canonical directory")
    info = root.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("Retained executable root must be private")
    prepared = require_prepared(asset, source, scratch, receipts)
    key = prepared["preparationSHA256"]
    original, destination = Path(prepared["root"]), root / key
    receipt = json.loads((receipts / (key + ".json")).read_text())
    intent = canonical({"preparationSHA256": key, "inventorySHA256": prepared["inventorySHA256"]}).encode()
    pending = root / (key + ".pending.json")
    if pending.is_symlink() or destination.is_symlink():
        raise ValueError("Retained release paths must not be aliases")
    if pending.exists():
        info = pending.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or
                stat.S_IMODE(info.st_mode) != 0o600 or pending.read_bytes() != intent):
            raise ValueError("Pending release ownership changed")
    elif destination.exists():
        return require_retained(asset, source, root, receipts)
    else:
        with os.fdopen(os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
            output.write(intent)
            output.flush()
            os.fsync(output.fileno())
        sync_directory(root)
    destination.mkdir(mode=0o700, exist_ok=True)
    if destination.stat().st_uid != os.getuid() or stat.S_IMODE(destination.stat().st_mode) != 0o700:
        raise ValueError("Pending release directory ownership changed")
    # Reject foreign residue before resuming owned, partially copied files.
    existing = inventory(destination)
    expected = receipt["inventory"]
    if not existing.keys() <= expected.keys() or any(existing[name]["kind"] != expected[name]["kind"] for name in existing):
        raise ValueError("Unregistered files in pending release")
    for name, item in sorted(expected.items(), key=lambda pair: (pair[0].count("/"), pair[0])):
        target = destination / name
        if item["kind"] == "directory":
            target.mkdir(exist_ok=True)
            if target.stat().st_uid != os.getuid():
                raise ValueError("Pending release directory is foreign")
            target.chmod(item["mode"])
        else:
            if existing.get(name) == item:
                with os.fdopen(os.open(target, os.O_RDONLY | os.O_NOFOLLOW), "rb") as durable:
                    info = os.fstat(durable.fileno())
                    if info.st_uid != os.getuid() or info.st_nlink != 1:
                        raise ValueError("Pending release file ownership changed")
                    os.fsync(durable.fileno())
            else:
                durable_file(target, original / name, mode=item["mode"])
    durable_file(destination / RECEIPT, None, canonical(receipt).encode())
    validate_prepared(destination, receipt["specification"], receipt)
    # Detect source changes during copying, before publishing any executable.
    require_prepared(asset, source, scratch, receipts)
    for directory, _, _ in os.walk(destination, topdown=False):
        sync_directory(Path(directory))
    sync_directory(root)
    pending.unlink()
    sync_directory(root)
    return require_retained(asset, source, root, receipts)


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
    executables = retained / "prepared-releases"
    executables.mkdir(mode=0o700, exist_ok=True)
    acquired = acquire(json.loads(args.lock.read_text()), retained, scratch / "tmp", offline=args.offline)
    result = {"schemaVersion": 1, "scope": "prepared-release-assets-only", "runtimeReady": False,
              "lockSHA256": acquired["lockSHA256"], "assets": []}
    for asset in acquired["assets"]:
        source, receipts = Path(asset["path"]), retained / "prepared-receipts"
        specification = {"schemaVersion": 1, "assetSHA256": asset["sha256"], "layout": layout(asset)}
        key = hashlib.sha256(canonical(specification).encode()).hexdigest()
        if (executables / key).exists() and not (executables / (key + ".pending.json")).exists():
            result["assets"].append(require_retained(asset, source, executables, receipts))
        else:
            prepare(asset, source, scratch / "prepared-releases", receipts)
            result["assets"].append(retain_prepared(asset, source, scratch / "prepared-releases", executables, receipts))
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
