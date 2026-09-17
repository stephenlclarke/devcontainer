"""Acquire the existing Docker oracle pin from public GitHub Packages, without Brew."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

from prepare_guest_images import private_file
from prepare_releases import (durable_file, layout, prepare, require_retained, retain_prepared,
                              retain_receipt, sync_directory)
from release_inputs import canonical, sha256, verify_object


def validate_lock(lock: dict, oracle: dict) -> None:
    fields = {"schemaVersion", "repository", "version", "manifest", "manifestSize",
              "bottleSHA256", "bottleSize", "executableSHA256"}
    if (not isinstance(lock, dict) or set(lock) != fields or type(lock["schemaVersion"]) is not int or lock["schemaVersion"] != 1 or
            lock["repository"] != "ghcr.io/homebrew/core/docker" or lock["version"] != "29.6.2"):
        raise ValueError("Expected the reviewed GitHub Packages Docker CLI lock")
    for key in ("manifest", "bottleSHA256", "executableSHA256"):
        if not isinstance(lock[key], str) or re.fullmatch(r"[0-9a-f]{64}", lock[key]) is None:
            raise ValueError("Docker CLI lock requires exact SHA256 digests")
    for key, maximum in (("manifestSize", 1024**2), ("bottleSize", 64 * 1024**2)):
        if type(lock[key]) is not int or not 0 < lock[key] <= maximum:
            raise ValueError("Docker CLI lock requires bounded object sizes")
    expected = {"cliVersion": lock["version"], "cliSHA256": lock["executableSHA256"],
                "cliBottleSHA256": lock["bottleSHA256"]}
    if any(oracle.get(key) != value for key, value in expected.items()):
        raise ValueError("Docker CLI lock differs from the parity oracle")


def asset(lock: dict) -> dict:
    # This is an OCI package, not a fabricated GitHub Release asset/ID.
    return {"repository": lock["repository"], "tag": lock["version"],
            "name": "docker--29.6.2.arm64_tahoe.bottle.tar.gz",
            "size": lock["bottleSize"], "sha256": lock["bottleSHA256"]}


def download(lock: dict, directory: Path) -> Path:
    layout = directory / "layout"
    # A private per-user registries.conf suppresses system drop-ins. Do not use
    # --registries-conf: that option still permits system drop-in configuration.
    config = directory / ".config/containers"
    config.mkdir(parents=True, exist_ok=True)
    (config / "registries.conf").write_text('unqualified-search-registries = []\n')
    empty = directory / "empty-config"
    empty.mkdir(exist_ok=True)
    policy = directory / "policy.json"
    policy.write_text(canonical({"default": [{"type": "reject"}], "transports": {
        "docker": {lock["repository"]: [{"type": "insecureAcceptAnything"}]}}}))
    # TLS remains mandatory. Pinned content hashes, not optional registry
    # signatures or operator policy, authenticate this one public bottle.
    subprocess.run(["/opt/homebrew/bin/skopeo", "--policy", str(policy), "--registries.d", str(empty),
                    "copy", "--src-no-creds", "--src-cert-dir", str(empty), "--src-tls-verify=true",
                    "--preserve-digests", "--retry-times=0",
                    "docker://" + lock["repository"] + "@sha256:" + lock["manifest"],
                    "oci:" + str(layout) + ":docker-cli"], check=True, timeout=300,
                   env={"PATH": "/usr/bin:/bin", "TMPDIR": str(directory), "HOME": str(directory)}, capture_output=True)
    blobs = layout / "blobs/sha256"
    manifest = blobs / lock["manifest"]
    verify_object(manifest, {"sha256": lock["manifest"], "size": lock["manifestSize"]})
    layers = json.loads(manifest.read_text()).get("layers", [])
    if (len(layers) != 1 or layers[0].get("digest") != "sha256:" + lock["bottleSHA256"] or
            layers[0].get("size") != lock["bottleSize"] or
            layers[0].get("mediaType") != "application/vnd.oci.image.layer.v1.tar+gzip"):
        raise ValueError("Docker CLI manifest does not name the pinned bottle")
    source = blobs / lock["bottleSHA256"]
    verify_object(source, asset(lock))
    return source


def acquire(lock: dict, scratch: Path, retained: Path, *, offline=False, fetch=download) -> Path:
    """Under the reference lease, retain one immutable bottle with crash recovery."""
    for root in (scratch, retained):
        if (root.resolve() != root or not root.is_dir() or root.stat().st_uid != os.getuid() or
                stat.S_IMODE(root.stat().st_mode) & 0o022 or
                (root == retained and stat.S_IMODE(root.stat().st_mode) != 0o700)):
            raise ValueError("Docker CLI storage must be private, canonical and owned")
    key = hashlib.sha256(canonical(lock).encode()).hexdigest()
    target, receipt, pending = (retained / (key + suffix) for suffix in (".tar.gz", ".json", ".pending.json"))
    if receipt.exists() or receipt.is_symlink():
        private_file(receipt)
        private_file(target)
        verify_object(target, asset(lock))
        if json.loads(receipt.read_text()) != lock:
            raise ValueError("Sealed Docker CLI receipt changed")
        if pending.exists() or pending.is_symlink():
            if offline:
                raise ValueError("Pending Docker CLI publication requires online preparation recovery")
            private_file(pending)
            if json.loads(pending.read_text()) != lock:
                raise ValueError("Pending Docker CLI ownership changed")
            retain_receipt(receipt, lock)
            pending.unlink()
            sync_directory(retained)
        return target
    if offline:
        raise ValueError("Docker CLI is not sealed; offline acquisition cannot repair or download")
    if target.exists() or target.is_symlink():
        private_file(target)
        private_file(pending)  # Unregistered files are never adopted or overwritten.
    if pending.exists() or pending.is_symlink():
        private_file(pending)
        if json.loads(pending.read_text()) != lock:
            raise ValueError("Pending Docker CLI ownership changed")
    with tempfile.TemporaryDirectory(dir=scratch, prefix="docker-cli-") as temporary:
        source = fetch(lock, Path(temporary))
        verify_object(source, asset(lock))
        retain_receipt(pending, lock)
        durable_file(target, source)
        verify_object(target, asset(lock))
        retain_receipt(receipt, lock)
        pending.unlink()
        sync_directory(retained)
    return target


def prepare_cli(lock: dict, oracle: dict, scratch: Path, retained: Path, *, offline=False, fetch=download) -> dict:
    validate_lock(lock, oracle)
    for root in (scratch, retained):
        if (root.resolve() != root or not root.is_dir() or root.stat().st_uid != os.getuid() or
                stat.S_IMODE(root.stat().st_mode) & 0o022 or
                (root == retained and stat.S_IMODE(root.stat().st_mode) != 0o700)):
            raise ValueError("Docker CLI preparation requires private canonical directories")
    directories = [retained / "docker-cli", retained / "prepared-receipts", retained / "prepared-releases"]
    for directory in directories:
        if not offline:
            directory.mkdir(mode=0o700, exist_ok=True)
        if (directory.resolve() != directory or directory.stat().st_uid != os.getuid() or
                stat.S_IMODE(directory.stat().st_mode) != 0o700):
            raise ValueError("Docker CLI preparation requires private canonical directories")
    archives, receipts, executables = directories
    source = acquire(lock, scratch / "tmp", archives, offline=offline, fetch=fetch)
    # Offline means verification only, including the prepared payload; no repairs.
    if offline:
        result = require_retained(asset(lock), source, executables, receipts)
    else:
        specification = {"schemaVersion": 1, "assetSHA256": lock["bottleSHA256"], "layout": layout(asset(lock))}
        key = hashlib.sha256(canonical(specification).encode()).hexdigest()
        if (executables / key).exists() and not (executables / (key + ".pending.json")).exists():
            result = require_retained(asset(lock), source, executables, receipts)
        else:
            extracted = scratch / "prepared-releases"
            extracted.mkdir(mode=0o700, exist_ok=True)
            prepare(asset(lock), source, extracted, receipts)
            result = retain_prepared(asset(lock), source, extracted, executables, receipts)
    if sha256(Path(result["executables"]["docker"])) != lock["executableSHA256"]:
        raise ValueError("Prepared Docker CLI does not match the parity executable pin")
    return dict(result, scope="docker-cli-only", runtimeReady=False,
                lockSHA256=hashlib.sha256(canonical(lock).encode()).hexdigest())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lock", type=Path)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    scratch = Path("/Volumes/SSD/cf/bazel")
    retained = Path.home() / "Library/Application Support/ContainerFamily/retained/workflow"
    if retained.stat().st_dev != Path.home().stat().st_dev or retained.stat().st_dev == scratch.stat().st_dev:
        raise ValueError("Docker CLI assets require internal storage and separate SSD scratch")
    oracle = json.loads((Path(__file__).resolve().parents[2] / "Tests/Parity/manifest.json").read_text())["referencePins"]["docker"]
    print(json.dumps(prepare_cli(json.loads(args.lock.read_text()), oracle, scratch, retained,
                                 offline=args.offline), sort_keys=True))


if __name__ == "__main__":
    main()
