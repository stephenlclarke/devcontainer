"""Prepare pinned official CLI/Node reference tools, without npm or global installs."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile

from prepare_guest_images import private_file
from prepare_releases import (durable_file, layout, prepare, require_retained, retain_prepared,
                              retain_receipt, sync_directory)
from release_inputs import canonical, sha256


SOURCES = {
    "node": ("24.21.0", "https://nodejs.org/dist/v24.21.0/node-v24.21.0-darwin-arm64.tar.gz",
             "sha256", 64 * 1024**2, "nodejs.org/dist"),
    "cli": ("0.88.0", "https://registry.npmjs.org/@devcontainers/cli/-/cli-0.88.0.tgz",
            "sha512", 4 * 1024**2, "registry.npmjs.org/@devcontainers/cli"),
}


def validate_lock(lock: dict, oracle: dict) -> None:
    if (not isinstance(lock, dict) or set(lock) != {"schemaVersion", "node", "cli"} or
            type(lock["schemaVersion"]) is not int or lock["schemaVersion"] != 1):
        raise ValueError("Expected the reviewed CLI reference lock")
    for name, (version, url, algorithm, maximum, _) in SOURCES.items():
        item = lock[name]
        if (not isinstance(item, dict) or set(item) != {"version", "url", "integrity", "maxBytes"} or
                item["version"] != version or item["url"] != url or type(item["maxBytes"]) is not int or
                not 0 < item["maxBytes"] <= maximum or not isinstance(item["integrity"], str)):
            raise ValueError("CLI reference source or download bound changed")
        prefix, separator, encoded = item["integrity"].partition("-")
        value = base64.b64decode(encoded, validate=True)
        if (separator != "-" or prefix != algorithm or len(value) != hashlib.new(algorithm).digest_size or
                base64.b64encode(value).decode() != encoded):
            raise ValueError("CLI reference integrity must be canonical and complete")
    if (lock["cli"]["version"] != oracle.get("version") or lock["cli"]["integrity"] != oracle.get("npmIntegrity")):
        raise ValueError("CLI reference differs from the parity oracle")


def verify_archive(path: Path, item: dict) -> None:
    private_file(path)
    if not 0 < path.stat().st_size <= item["maxBytes"]:
        raise ValueError("CLI reference archive exceeds its bound")
    algorithm, encoded = item["integrity"].split("-", 1)
    value = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    if base64.b64encode(value.digest()).decode() != encoded:
        raise ValueError("CLI reference archive integrity mismatch")


def download(item: dict, directory: Path) -> Path:
    target = directory / "archive"
    # -q disables user curl configuration. There are no redirects, retries,
    # credentials, proxy settings, npm install hooks or caller startup hooks.
    subprocess.run(["/usr/bin/curl", "-q", "--fail", "--silent", "--show-error", "--proto", "=https",
                    "--connect-timeout", "30", "--max-time", "300", "--max-filesize", str(item["maxBytes"]),
                    "--retry", "0", "--output", str(target), "--url", item["url"]],
                   check=True, capture_output=True, timeout=330,
                   env={"PATH": "/usr/bin:/bin", "HOME": str(directory), "TMPDIR": str(directory)})
    target.chmod(0o600)
    return target


def acquire(item: dict, scratch: Path, retained: Path, *, offline=False, fetch=download) -> Path:
    """Caller holds reference lease; sealed bytes are immutable, pending copies recover."""
    key = hashlib.sha256(canonical(item).encode()).hexdigest()
    target, receipt, pending = (retained / (key + suffix) for suffix in (".tar.gz", ".json", ".pending.json"))
    if receipt.exists() or receipt.is_symlink():
        private_file(receipt)
        verify_archive(target, item)
        if json.loads(receipt.read_text()) != item:
            raise ValueError("Sealed CLI reference receipt changed")
        if pending.exists() or pending.is_symlink():
            if offline:
                raise ValueError("Pending reference requires preparation recovery")
            private_file(pending)
            if json.loads(pending.read_text()) != item:
                raise ValueError("Pending CLI reference ownership changed")
            retain_receipt(receipt, item)
            pending.unlink()
            sync_directory(retained)
        return target
    if offline:
        raise ValueError("Offline reference acquisition cannot repair or download")
    if target.exists() or target.is_symlink():
        private_file(target)
        private_file(pending)
    if pending.exists() or pending.is_symlink():
        private_file(pending)
        if json.loads(pending.read_text()) != item:
            raise ValueError("Pending CLI reference ownership changed")
    complete = False
    if target.exists():
        try:
            verify_archive(target, item)
            complete = True
        except ValueError:
            pass  # Only a registered unfinished copy may be replaced below.
    if not complete:
        with tempfile.TemporaryDirectory(dir=scratch, prefix="devcontainers-reference-") as temporary:
            source = fetch(item, Path(temporary))
            verify_archive(source, item)
            retain_receipt(pending, item)
            durable_file(target, source)
        verify_archive(target, item)
    with target.open("rb") as durable:
        os.fsync(durable.fileno())
    retain_receipt(receipt, item)
    pending.unlink()
    sync_directory(retained)
    return target


def private_directory(path: Path, *, private=True) -> None:
    if (path.resolve() != path or not path.is_dir() or path.stat().st_uid != os.getuid() or
            stat.S_IMODE(path.stat().st_mode) & 0o022 or
            (private and stat.S_IMODE(path.stat().st_mode) != 0o700)):
        raise ValueError("CLI reference storage must be private, canonical and owned")


def prepare_cli(lock: dict, oracle: dict, scratch: Path, retained: Path, *, offline=False, fetch=download) -> dict:
    validate_lock(lock, oracle)
    private_directory(retained)
    for root in (scratch, scratch / "tmp"):
        private_directory(root, private=False)
    directories = [retained / "devcontainers-cli", retained / "prepared-receipts", retained / "prepared-releases"]
    for directory in directories:
        if not offline:
            directory.mkdir(mode=0o700, exist_ok=True)
        private_directory(directory)
    archives, receipts, executables = directories
    results = {}
    for name, (_, _, _, _, repository) in SOURCES.items():
        item = lock[name]
        source = acquire(item, scratch / "tmp", archives, offline=offline, fetch=fetch)
        asset = {"repository": repository, "tag": item["version"], "name": item["url"].rsplit("/", 1)[1],
                 "sha256": sha256(source), "size": source.stat().st_size}
        key = hashlib.sha256(canonical({"schemaVersion": 1, "assetSHA256": asset["sha256"], "layout": layout(asset)}).encode()).hexdigest()
        if offline or ((executables / key).exists() and not (executables / (key + ".pending.json")).exists()):
            result = require_retained(asset, source, executables, receipts)
        else:
            extracted = scratch / "prepared-releases"
            extracted.mkdir(mode=0o700, exist_ok=True)
            private_directory(extracted)
            prepare(asset, source, extracted, receipts)
            result = retain_prepared(asset, source, extracted, executables, receipts)
        results[name] = result
    package = json.loads(Path(results["cli"]["files"]["package"]).read_text())
    if (package.get("name") != "@devcontainers/cli" or package.get("version") != lock["cli"]["version"] or
            package.get("bin") != {"devcontainer": "devcontainer.js"} or
            package.get("dependencies") or package.get("optionalDependencies")):
        raise ValueError("Official CLI package is not the pinned self-contained distribution")
    return {"scope": "devcontainers-reference-tools-only", "runtimeReady": False,
            "lockSHA256": hashlib.sha256(canonical(lock).encode()).hexdigest(), "prepared": results,
            "node": results["node"]["executables"]["node"], "cli": results["cli"]["executables"]["devcontainer-cli"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lock", type=Path)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    scratch = Path("/Volumes/SSD/cf/bazel")
    retained = Path.home() / "Library/Application Support/ContainerFamily/retained/workflow"
    if retained.stat().st_dev != Path.home().stat().st_dev or retained.stat().st_dev == scratch.stat().st_dev:
        raise ValueError("CLI references require internal storage and separate SSD scratch")
    oracle = json.loads((Path(__file__).resolve().parents[2] / "Tests/Parity/manifest.json").read_text())["referencePins"]["devcontainersCli"]
    print(json.dumps(prepare_cli(json.loads(args.lock.read_text()), oracle, scratch, retained, offline=args.offline), sort_keys=True))


if __name__ == "__main__":
    main()
