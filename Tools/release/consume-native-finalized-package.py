#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Consume one independently authenticated, completed native package output."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile


def account_home() -> Path:
    """Keep durable authority under the real account, independent of fixture HOME."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


SHA256 = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
PROVENANCE_NAME = "native-finalization-provenance.json"
INTERNAL_ROOT = account_home() / "Library/Application Support/ContainerFamily/retained"
FINAL_ROOT = INTERNAL_ROOT / "devcontainer/finalized"
SSD_ROOT = Path("/Volumes/SSD")
SSD_IDENTITY = INTERNAL_ROOT / "workflow/ssd-volume.uuid"


def digest(path: Path) -> str:
    """Hash a regular file in bounded memory."""
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def physical(path: Path, *, exists: bool = True) -> Path:
    """Require an absolute canonical path with no symlink alias."""
    if not path.is_absolute() or path.is_symlink() or path.resolve(strict=exists) != path:
        raise ValueError("Path must be absolute and have no symbolic-link aliases")
    if exists and not path.exists():
        raise ValueError("Required path is absent")
    return path


def validate_ssd_identity(expected_uuid: str, plist_data: bytes) -> None:
    """Compare the enrolled UUID with the mounted external volume record."""
    volume = plistlib.loads(plist_data)
    if (volume.get("MountPoint") != str(SSD_ROOT) or volume.get("Internal") is not False
            or str(volume.get("VolumeUUID", "")).upper() != expected_uuid):
        raise ValueError("Mounted SSD does not match the trusted external volume identity")


def validate_storage(directory: Path, scratch: Path) -> None:
    """Require private internal retained input and the enrolled external SSD."""
    home_device = account_home().stat().st_dev
    for path in (INTERNAL_ROOT, FINAL_ROOT, directory, SSD_ROOT, scratch, SSD_IDENTITY):
        physical(path)
    if (not directory.is_relative_to(FINAL_ROOT) or directory == FINAL_ROOT
            or directory.stat().st_uid != os.getuid()
            or directory.stat().st_dev != home_device
            or directory.stat().st_mode & 0o777 != 0o700):
        raise ValueError("Completed native directory must be private owned internal retained output")
    if (INTERNAL_ROOT.stat().st_dev != home_device or FINAL_ROOT.stat().st_dev != home_device
            or SSD_ROOT.stat().st_dev == home_device or not SSD_ROOT.is_mount()
            or not scratch.is_relative_to(SSD_ROOT) or scratch.stat().st_dev != SSD_ROOT.stat().st_dev
            or scratch.stat().st_uid != os.getuid()):
        raise ValueError("Native handoff storage does not match internal and enrolled SSD policy")
    if (not SSD_IDENTITY.is_file() or SSD_IDENTITY.is_symlink()
            or SSD_IDENTITY.stat().st_uid != os.getuid() or SSD_IDENTITY.stat().st_nlink != 1):
        raise ValueError("Trusted enrolled SSD identity file is missing or unsafe")
    expected_uuid = SSD_IDENTITY.read_text(encoding="ascii").strip().upper()
    if not re.fullmatch(r"[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}", expected_uuid):
        raise ValueError("Trusted enrolled SSD identity is malformed")
    result = subprocess.run(["/usr/sbin/diskutil", "info", "-plist", str(SSD_ROOT)],
                            check=True, capture_output=True, timeout=10,
                            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    validate_ssd_identity(expected_uuid, result.stdout)
    for path in directory.iterdir():
        if (path.is_symlink() or not path.is_file() or path.stat().st_uid != os.getuid()
                or path.stat().st_nlink != 1):
            raise ValueError("Completed native input contains an unsafe retained file")


def safe_inventory(archive_path: Path, root_name: str) -> dict[str, dict[str, int | str]]:
    """Hash exactly the regular archive files and reject links or unsafe entries."""
    found: dict[str, dict[str, int | str]] = {}
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            raw_name = member.name[:-1] if member.isdir() and member.name.endswith("/") else member.name
            parts = raw_name.split("/")
            if (not raw_name or raw_name.startswith("/") or not parts
                    or parts[0] != root_name
                    or any(part in {"", ".", ".."} for part in parts)
                    or member.issym() or member.islnk() or member.isdev()
                    or member.mode & 0o6000):
                raise ValueError("Finalized archive has an unsafe entry")
            if member.isdir():
                if member.mode != 0o755:
                    raise ValueError("Finalized archive directory mode differs")
                continue
            if not member.isfile() or len(parts) < 2:
                raise ValueError("Finalized archive contains an unsupported entry")
            relative = "/".join(parts[1:])
            if relative in found:
                raise ValueError("Finalized archive contains duplicate files")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("Finalized archive file cannot be read")
            content_hash = hashlib.sha256()
            size = 0
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                content_hash.update(block)
                size += len(block)
            found[relative] = {"sha256": content_hash.hexdigest(), "size": size,
                               "mode": member.mode}
    return found


def extract_member(archive_path: Path, archive_name: str, destination: Path) -> None:
    """Copy one exact regular member to SSD scratch without general extraction."""
    with tarfile.open(archive_path, "r:gz") as archive:
        matches = [member for member in archive if member.name == archive_name]
        if len(matches) != 1 or not matches[0].isfile():
            raise ValueError("Selected package metadata member is missing or ambiguous")
        stream = archive.extractfile(matches[0])
        if stream is None:
            raise ValueError("Selected package metadata member cannot be read")
        with destination.open("xb") as output:
            shutil.copyfileobj(stream, output)


def expected_context(repository: Path, version: str, lane: str, commit: str,
                     run_number: str) -> dict:
    """Resolve package identity using the maintained context implementation."""
    command = [sys.executable, str(repository / "Tools/release/package-context.py"),
               "--product-version", version, "--lane", lane, "--commit", commit]
    if run_number:
        command.extend(("--run-number", run_number))
    return json.loads(subprocess.run(command, cwd=repository, check=True, text=True,
                                     capture_output=True, timeout=15,
                                     env={"PATH": "/usr/bin:/bin", "LC_ALL": "C",
                                          "PYTHONDONTWRITEBYTECODE": "1"}).stdout)


def require_source_identity(source: str, status: str, provenance_source: str,
                           publish_sha: str) -> None:
    """Bind the candidate and optional publish source to clean current HEAD."""
    if not COMMIT.fullmatch(source) or status:
        raise ValueError("Current source checkout must be a clean full commit")
    if provenance_source != source or (publish_sha and publish_sha != source):
        raise ValueError("Finalized package, publish source, and current HEAD differ")


def require_context(supplied: dict, expected: dict, proof: dict) -> None:
    """Reject relabeling a candidate under a different release context."""
    if supplied != expected or proof.get("archive") != expected["asset"]:
        raise ValueError("Finalized package context differs from current release inputs")


def validate(repository: Path, directory: Path, expected_sha: str, lane: str,
             run_number: str, publish_sha: str, profile: str,
             scratch: Path) -> tuple[Path, dict, dict]:
    """Authenticate retained files, source identity, final tree, and selected context."""
    physical(repository)
    physical(directory)
    physical(scratch)
    if not SHA256.fullmatch(expected_sha):
        raise ValueError("Independent finalization SHA-256 is required")
    names = {path.name for path in directory.iterdir()}
    assets = [name for name in names if name.endswith(".tar.gz")]
    if len(assets) != 1:
        raise ValueError("Completed native output must contain exactly one archive")
    asset = assets[0]
    expected_names = {asset, asset + ".sha256", "package-context.json",
                      "package-verification.json", PROVENANCE_NAME}
    if names != expected_names:
        raise ValueError("Completed native output has an unexpected file set")
    for name in sorted(expected_names):
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
            raise ValueError("Completed native output contains a linked or non-regular file")

    proof_path = directory / PROVENANCE_NAME
    if digest(proof_path) != expected_sha:
        raise ValueError("Independent finalization provenance checksum differs")
    proof = json.loads(proof_path.read_bytes())
    if (proof.get("schema") != 1 or proof.get("scope") != "signed-notarized-package-assembly"
            or proof.get("distributionReady") is not False
            or proof.get("runtimeProfile") != profile
            or not COMMIT.fullmatch(proof.get("sourceCommit", ""))
            or not UUID.fullmatch(proof.get("submissionID", ""))
            or not SHA256.fullmatch(proof.get("trustedStateSHA256", ""))
            or not SHA256.fullmatch(proof.get("acceptedEvidenceSHA256", ""))
            or not SHA256.fullmatch(proof.get("candidateReceiptSHA256", ""))
            or not SHA256.fullmatch(proof.get("stageProvenanceSHA256", ""))
            or not SHA256.fullmatch(proof.get("submittedZIPSHA256", ""))):
        raise ValueError("Finalization provenance is incomplete or grants release authority")

    source = subprocess.run(["/usr/bin/git", "rev-parse", "--verify", "HEAD"], cwd=repository,
                            check=True, text=True, capture_output=True, timeout=10,
                            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}).stdout.strip()
    status = subprocess.run(["/usr/bin/git", "status", "--porcelain", "--untracked-files=all"],
                            cwd=repository, check=True, text=True, capture_output=True, timeout=10,
                            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}).stdout
    require_source_identity(source, status, proof["sourceCommit"], publish_sha)
    version = ""
    for line in (repository / "Makefile").read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[:2] == ["DEVCONTAINER_VERSION", "?="]:
            version = fields[2]
            break
    if not version:
        raise ValueError("Product version is missing from Makefile")
    context = expected_context(repository, version, lane, source, run_number)
    context_path = directory / "package-context.json"
    supplied_context = json.loads(context_path.read_bytes())
    require_context(supplied_context, context, proof)
    if (proof.get("packageContextSHA256") != digest(context_path)
            or proof.get("archiveSHA256") != digest(directory / asset)
            or proof.get("archiveSize") != (directory / asset).stat().st_size
            or proof.get("checksumSHA256") != digest(directory / (asset + ".sha256"))
            or proof.get("packageVerificationSHA256") != digest(directory / "package-verification.json")):
        raise ValueError("Completed file hashes differ from authenticated provenance")
    checksum = (directory / (asset + ".sha256")).read_text(encoding="utf-8")
    if checksum != proof["archiveSHA256"] + "  " + asset + "\n":
        raise ValueError("Completed archive checksum record differs")
    selected_provenance_tools = proof.get("releaseToolSHA256")
    required_tools = {"native_signing.py", "create-reproducible-archive.py", "verify-package.py"}
    if not isinstance(selected_provenance_tools, dict) or set(selected_provenance_tools) != required_tools:
        raise ValueError("Finalizer tool inventory is incomplete")
    for name, expected in selected_provenance_tools.items():
        if (not SHA256.fullmatch(expected)
                or digest(repository / "Tools/release" / name) != expected):
            raise ValueError("Finalizer tool differs from the current source tree")

    root = f"devcontainer-{version}"
    tree = safe_inventory(directory / asset, root)
    if tree != proof.get("finalTree"):
        raise ValueError("Actual archive file inventory differs from authenticated final tree")
    finalization = proof.get("finalTree")
    if not isinstance(finalization, dict) or not finalization:
        raise ValueError("Authenticated final tree inventory is missing")

    with tempfile.TemporaryDirectory(prefix="native-public-verify-", dir=scratch) as temporary:
        work = Path(temporary)
        metadata_root = f"{root}/share/devcontainer/"
        lock = work / "Package.resolved"
        ledger = work / "dependency-licenses.selected.json"
        extract_member(directory / asset, metadata_root + lock.name, lock)
        extract_member(directory / asset, metadata_root + ledger.name, ledger)
        verification = work / "verification.json"
        command = [sys.executable, str(repository / "Tools/release/verify-package.py"),
                   "--archive", str(directory / asset), "--checksum", str(directory / (asset + ".sha256")),
                   "--expected-version", version, "--expected-lane", lane,
                   "--expected-commit", source, "--resolved", str(lock),
                   "--license-manifest", str(ledger), "--require-notarization",
                   "--output", str(verification)]
        subprocess.run(command, cwd=repository, check=True, timeout=120,
                       env={"PATH": "/usr/bin:/bin", "LC_ALL": "C",
                            "PYTHONDONTWRITEBYTECODE": "1"})
        verified = json.loads(verification.read_bytes())
        if (verified.get("notarized") is not True or verified.get("commit") != source
                or verified.get("lane") != lane or verified.get("version") != version
                or verified.get("sha256") != proof["archiveSHA256"]
                or verification.read_bytes() != (directory / "package-verification.json").read_bytes()):
            raise ValueError("Maintained verifier did not reproduce the retained verification")
        notarization = work / "notarization.json"
        extract_member(directory / asset,
                       metadata_root + "notarization.json", notarization)
        accepted = json.loads(notarization.read_bytes())
        if accepted != {"archiveSHA256": proof["submittedZIPSHA256"],
                        "id": proof["submissionID"], "status": "Accepted"}:
            raise ValueError("Embedded notarization identity differs from accepted provenance")
    return directory / asset, context, proof


def consume(args: argparse.Namespace) -> Path:
    """Validate before touching dist, then expose existing package contracts."""
    repository = physical(args.repository)
    directory = physical(args.finalized_directory)
    scratch = physical(args.ssd_scratch)
    validate_storage(directory, scratch)
    names = {path.name for path in directory.iterdir()}
    archives = [name for name in names if name.endswith(".tar.gz")]
    if len(archives) != 1:
        raise ValueError("Completed native output must contain exactly one archive")
    asset = archives[0]
    input_names = {asset, asset + ".sha256", "package-context.json",
                   "package-verification.json", PROVENANCE_NAME}
    if names != input_names:
        raise ValueError("Completed native output has an unexpected file set")
    proof_source = directory / PROVENANCE_NAME
    if proof_source.is_symlink() or not proof_source.is_file() or digest(proof_source) != args.finalization_sha256:
        raise ValueError("Independent finalization provenance checksum differs")
    proof_header = json.loads(proof_source.read_bytes())
    if proof_header.get("runtimeProfile") != args.profile:
        raise ValueError("Finalized runtime profile differs from the requested release profile")

    with tempfile.TemporaryDirectory(prefix="native-public-input-", dir=scratch) as temporary:
        ssd_directory = Path(temporary)
        for name in sorted(input_names):
            source = directory / name
            if source.is_symlink() or not source.is_file() or source.stat().st_nlink != 1:
                raise ValueError("Completed native output contains a linked or non-regular file")
            target = ssd_directory / name
            with source.open("rb") as reader, target.open("xb") as writer:
                shutil.copyfileobj(reader, writer)
                writer.flush()
                os.fsync(writer.fileno())
            if digest(source) != digest(target):
                raise ValueError("Retained file changed while copying to SSD scratch: " + name)

        archive, context, _proof = validate(repository, ssd_directory, args.finalization_sha256,
                                             args.lane, args.run_number, args.publish_sha,
                                             args.profile, scratch)
        dist = repository / "dist"
        dist.mkdir(exist_ok=True)
        if dist.is_symlink() or not dist.is_dir():
            raise ValueError("Distribution output path is not a regular directory")
        output_names = [context["asset"], context["asset"] + ".sha256",
                        context["asset"] + ".context.json", context["asset"] + ".verification.json",
                        context["asset"] + ".native-finalization-provenance.json"]
        with tempfile.TemporaryDirectory(prefix="native-public-package-", dir=dist) as pending_text:
            pending = Path(pending_text)
            sources = [archive, archive.with_name(archive.name + ".sha256"),
                       ssd_directory / "package-context.json",
                       ssd_directory / "package-verification.json",
                       ssd_directory / PROVENANCE_NAME]
            for source, name in zip(sources, output_names, strict=True):
                with source.open("rb") as reader, (pending / name).open("xb") as writer:
                    shutil.copyfileobj(reader, writer)
                    writer.flush()
                    os.fsync(writer.fileno())
            for name in output_names:
                os.replace(pending / name, dist / name)
        return dist / context["asset"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--finalized-directory", required=True, type=Path)
    parser.add_argument("--finalization-sha256", required=True)
    parser.add_argument("--lane", choices=("current", "stable"), required=True)
    parser.add_argument("--profile", choices=("stock", "enhanced"), default="stock")
    parser.add_argument("--run-number", default="")
    parser.add_argument("--publish-sha", default="")
    parser.add_argument("--ssd-scratch", required=True, type=Path)
    args = parser.parse_args()
    try:
        archive = consume(args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError,
            tarfile.TarError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise SystemExit("Native public package handoff failed: " + str(error)) from error
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
