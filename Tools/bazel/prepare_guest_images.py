"""Prepare digest-pinned OCI data without Docker, a VM, a build or image loading."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import tempfile

from oci_image_layout import validate_archive
from prepare_releases import durable_file, retain_receipt, sync_directory
from release_inputs import canonical, sha256


LIMIT = 256 * 1024**2


def validate_image(image: dict) -> None:
    if not isinstance(image, dict) or set(image) != {"name", "repository", "manifest", "config", "reference"}:
        raise ValueError("Invalid guest image specification")
    patterns = {"name": r"[a-z][a-z0-9-]*", "repository": r"[a-z0-9.-]+/[a-z0-9/_.-]+",
                "reference": r"[a-z0-9.-]+/[a-z0-9/_.-]+:[a-zA-Z0-9_.-]+",
                "manifest": r"sha256:[0-9a-f]{64}", "config": r"sha256:[0-9a-f]{64}"}
    if any(not isinstance(image[k], str) or re.fullmatch(pattern, image[k]) is None for k, pattern in patterns.items()):
        raise ValueError("Guest image requires explicit immutable digests and a safe reference")
    if image["reference"].rsplit(":", 1)[0] != image["repository"]:
        raise ValueError("Guest image reference belongs to another repository")


def private_file(path: Path) -> None:
    info = path.lstat()
    if (path.resolve() != path or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
            info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
        raise ValueError("Guest image storage must be private, canonical and singly owned")


def verify_archive(path: Path, image: dict) -> dict:
    """Reuse Compose's closure validator, then bind the exact manifest/config."""
    validate_image(image)
    if path.stat().st_size > LIMIT:
        raise ValueError("Guest image archive exceeds bound")
    # Bound the reused general-purpose validator before it reads JSON/blobs.
    with tarfile.open(path, "r:") as archive:
        count, size = 0, 0
        for member in archive:
            count += 1
            size += member.size
            if count > 128 or member.size < 0 or size > LIMIT:
                raise ValueError("Guest image archive inventory exceeds bound")
    validate_archive(path, {image["reference"]})
    with tarfile.open(path, "r:") as archive:
        def document(name):
            member = archive.getmember(name)
            if member.size > 1024**2:
                raise ValueError("Guest image metadata exceeds bound")
            return json.load(archive.extractfile(member))
        roots = document("index.json")["manifests"]
        if len(roots) != 1 or roots[0]["digest"] != image["manifest"]:
            raise ValueError("Guest image manifest differs from the pin")
        manifest = document("blobs/sha256/" + image["manifest"].split(":")[1])
        if manifest.get("config", {}).get("digest") != image["config"]:
            raise ValueError("Guest image config differs from the pin")
        config = document("blobs/sha256/" + image["config"].split(":")[1])
        if (config.get("os"), config.get("architecture")) != ("linux", "arm64"):
            raise ValueError("Guest image must be linux/arm64")
    return {"image": image, "sha256": sha256(path), "size": path.stat().st_size}


def download(image: dict, directory: Path) -> Path:
    """Copy only immutable public registry data; never contact a Docker daemon."""
    layout = directory / "layout"
    subprocess.run(["/opt/homebrew/bin/skopeo", "copy", "--src-no-creds", "--preserve-digests", "--retry-times=0",
                    "docker://" + image["repository"] + "@" + image["manifest"],
                    "oci:" + str(layout) + ":" + image["reference"]], check=True, timeout=600,
                   env={"PATH": "/usr/bin:/bin", "TMPDIR": str(directory)}, capture_output=True)
    target = directory / "image.tar"
    # Repack metadata deterministically so an interrupted durable copy can be
    # resumed using newly downloaded identical registry bytes.
    with tarfile.open(target, "w", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(layout.rglob("*")):
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                raise ValueError("OCI download contains nonregular entries")
            if path.is_file():
                entry = tarfile.TarInfo(path.relative_to(layout).as_posix())
                entry.size, entry.mode = path.stat().st_size, 0o600
                with path.open("rb") as incoming:
                    archive.addfile(entry, incoming)
    return target


def prepare(image: dict, scratch: Path, retained: Path, *, offline=False, fetch=download) -> dict:
    """Caller holds the shared reference-store lease; a pending copy is not usable."""
    validate_image(image)
    for root in (scratch, retained):
        if root.resolve() != root or not root.is_dir() or root.stat().st_uid != os.getuid():
            raise ValueError("Guest image roots must be canonical owned directories")
    if stat.S_IMODE(retained.stat().st_mode) != 0o700:
        raise ValueError("Retained guest image root must be private")
    key = hashlib.sha256(canonical(image).encode()).hexdigest()
    target, receipt, pending = (retained / (key + suffix) for suffix in (".tar", ".json", ".pending.json"))
    if receipt.exists() or receipt.is_symlink():
        private_file(receipt)
        private_file(target)
        result = verify_archive(target, image)
        if json.loads(receipt.read_text()) != result:
            raise ValueError("Sealed guest image changed; refusing repair")
        if pending.exists() or pending.is_symlink():
            private_file(pending)
            if json.loads(pending.read_text()) != result:
                raise ValueError("Pending guest image owner changed")
            pending.unlink()  # Crash after sealing; reverified before clearing intent.
            sync_directory(retained)
        return dict(result, path=str(target))
    if offline:
        raise ValueError("Guest image is not sealed; offline admission cannot download or repair it")
    if target.exists() or target.is_symlink():
        private_file(target)
        private_file(pending)  # Never adopt an unregistered file.
    with tempfile.TemporaryDirectory(dir=scratch, prefix="guest-image-") as temporary:
        source = fetch(image, Path(temporary))
        result = verify_archive(source, image)
        if pending.exists() or pending.is_symlink():
            private_file(pending)
            if json.loads(pending.read_text()) != result:
                raise ValueError("Pending guest image owner changed")
        else:
            retain_receipt(pending, result)
        durable_file(target, source)
        if verify_archive(target, image) != result:
            raise ValueError("Guest image changed during publication")
        retain_receipt(receipt, result)
        pending.unlink()
        sync_directory(retained)
        return dict(result, path=str(target))


def require_image(image: dict, retained: Path) -> dict:
    """Read-only runtime admission never repairs an interrupted publication."""
    validate_image(image)
    if (retained.resolve() != retained or not retained.is_dir() or
            retained.stat().st_uid != os.getuid() or stat.S_IMODE(retained.stat().st_mode) != 0o700):
        raise ValueError("Guest image admission requires private canonical storage")
    key = hashlib.sha256(canonical(image).encode()).hexdigest()
    target, receipt, pending = (retained / (key + suffix) for suffix in (".tar", ".json", ".pending.json"))
    if pending.exists() or pending.is_symlink():
        raise ValueError("Pending guest publication requires preparation recovery before admission")
    private_file(receipt)
    private_file(target)
    result = verify_archive(target, image)
    if json.loads(receipt.read_text()) != result:
        raise ValueError("Sealed guest image changed; refusing admission")
    return dict(result, path=str(target))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lock", type=Path)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    lock = json.loads(args.lock.read_text())
    images = lock.get("images")
    if lock.get("schemaVersion") != 1 or not isinstance(images, list) or not images:
        raise ValueError("Expected nonempty version-1 guest image lock")
    for image in images:
        validate_image(image)
    if len({image["name"] for image in images}) != len(images):
        raise ValueError("Duplicate guest image name")
    scratch = Path("/Volumes/SSD/cf/bazel/tmp")
    retained = Path.home() / "Library/Application Support/ContainerFamily/retained/workflow/guest-images"
    retained.mkdir(mode=0o700, exist_ok=True)
    if retained.stat().st_dev != Path.home().stat().st_dev or retained.stat().st_dev == scratch.stat().st_dev:
        raise ValueError("Guest assets require internal storage and separate SSD scratch")
    result = [prepare(image, scratch, retained, offline=args.offline) for image in images]
    print(json.dumps({"scope": "guest-images-only", "runtimeReady": False, "images": result}, sort_keys=True))


if __name__ == "__main__":
    main()
