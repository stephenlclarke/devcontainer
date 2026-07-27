#!/usr/bin/env python3
"""Verify a devcontainer release archive and its provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence

from versioning import require_commit, require_semantic_version


DIGEST_PATTERN = re.compile(r"^(?P<digest>[0-9a-f]{64})  (?P<name>[^/\n]+)\n?$")


@dataclass(frozen=True)
class Verification:
    """A compact machine-readable package verification result."""

    archive: str
    commit: str
    lane: str
    notarized: bool
    sha256: str
    version: str


def require_safe_member(member: tarfile.TarInfo, root: str) -> None:
    """Reject paths and types that could escape a package extraction root."""

    path = PurePosixPath(member.name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"archive contains an unsafe path: {member.name}")
    if path.parts[0] != root:
        raise ValueError(f"archive member is outside {root}: {member.name}")
    if member.ischr() or member.isblk() or member.isfifo():
        raise ValueError(f"archive contains a special file: {member.name}")
    if member.mode & 0o6000:
        raise ValueError(f"archive contains setuid or setgid permissions: {member.name}")
    if member.issym() or member.islnk():
        link = PurePosixPath(member.linkname)
        if link.is_absolute() or ".." in link.parts:
            raise ValueError(f"archive contains an unsafe link: {member.name}")


def read_json_member(
    archive: tarfile.TarFile,
    name: str,
) -> object:
    """Decode one required regular JSON file from an open archive."""

    try:
        member = archive.getmember(name)
    except KeyError as error:
        raise ValueError(f"archive is missing {name}") from error
    if not member.isfile():
        raise ValueError(f"archive member is not a regular file: {name}")
    extracted = archive.extractfile(member)
    if extracted is None:
        raise ValueError(f"archive member cannot be read: {name}")
    try:
        return json.loads(extracted.read())
    except json.JSONDecodeError as error:
        raise ValueError(f"archive member is not valid JSON: {name}") from error


def verify_checksum(archive: Path, checksum: Path) -> str:
    """Require a portable checksum sidecar for the exact archive basename."""

    match = DIGEST_PATTERN.fullmatch(checksum.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError("checksum must contain lowercase SHA-256 and an archive basename")
    if match.group("name") != archive.name:
        raise ValueError(
            f"checksum names {match.group('name')}, expected {archive.name}"
        )
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if match.group("digest") != actual:
        raise ValueError("archive SHA-256 does not match its checksum")
    return actual


def require_build_info(
    value: object,
    version: str,
    lane: str,
    commit: str,
) -> None:
    """Require packaged build metadata to identify the selected release."""

    if not isinstance(value, dict):
        raise ValueError("build-info.json must be a JSON object")
    expected = {
        "architecture": "arm64",
        "buildType": "development" if lane == "development" else "release",
        "commit": commit,
        "containerDistribution": "apple",
        "lane": lane,
        "provider": "none",
        "source": "stephenlclarke/devcontainer",
        "version": version,
    }
    if value != expected:
        raise ValueError("build-info.json does not match the selected package context")


def require_sbom(value: object, version: str) -> None:
    """Require a parseable SPDX document covering the root package."""

    if not isinstance(value, dict) or value.get("spdxVersion") != "SPDX-2.3":
        raise ValueError("package SBOM must be an SPDX 2.3 JSON document")
    packages = value.get("packages")
    if not isinstance(packages, list):
        raise ValueError("package SBOM is missing packages")
    roots = [
        package
        for package in packages
        if isinstance(package, dict)
        and package.get("name") == "devcontainer"
        and package.get("versionInfo") == version
    ]
    if len(roots) != 1:
        raise ValueError("package SBOM must identify one matching devcontainer root")


def verify_archive(
    archive_path: Path,
    version: str,
    lane: str,
    commit: str,
    require_notarization: bool,
) -> bool:
    """Validate archive structure, metadata, SBOM, and notary evidence."""

    root = f"devcontainer-{version}"
    required_executables = {
        f"{root}/bin/devcontainer",
        f"{root}/bin/devcontainer-compose",
        f"{root}/bin/devcontainer-engine",
        (
            f"{root}/libexec/container/plugins/devcontainer/"
            "container-devcontainer"
        ),
    }
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        if not members:
            raise ValueError("package archive is empty")
        for member in members:
            require_safe_member(member, root)
        by_name = {member.name.rstrip("/"): member for member in members}
        for executable in required_executables:
            member = by_name.get(executable)
            if member is None or not member.isfile() or not member.mode & 0o111:
                raise ValueError(
                    f"package executable is missing or not executable: {executable}"
                )

        metadata_root = f"{root}/share/devcontainer"
        build_info = read_json_member(archive, f"{metadata_root}/build-info.json")
        require_build_info(build_info, version, lane, commit)
        sbom = read_json_member(archive, f"{metadata_root}/devcontainer.spdx.json")
        require_sbom(sbom, version)
        notarization_name = f"{metadata_root}/notarization.json"
        notarized = notarization_name in by_name
        if require_notarization and not notarized:
            raise ValueError("release package is missing notarization evidence")
        if notarized:
            evidence = read_json_member(archive, notarization_name)
            if (
                not isinstance(evidence, dict)
                or evidence.get("status") != "Accepted"
                or not isinstance(evidence.get("id"), str)
            ):
                raise ValueError("package notarization evidence is not accepted")
    return notarized


def parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--checksum", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument(
        "--expected-lane",
        choices=("development", "current", "stable"),
        required=True,
    )
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--require-notarization", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    args = parse_args(arguments)
    try:
        version = require_semantic_version(args.expected_version)
        commit = (
            args.expected_commit
            if args.expected_commit == "unspecified"
            else require_commit(args.expected_commit)
        )
        digest = verify_checksum(args.archive, args.checksum)
        notarized = verify_archive(
            args.archive,
            version,
            args.expected_lane,
            commit,
            args.require_notarization,
        )
    except (OSError, tarfile.TarError, ValueError) as error:
        raise SystemExit(str(error)) from error
    result = Verification(
        archive=args.archive.name,
        commit=commit,
        lane=args.expected_lane,
        notarized=notarized,
        sha256=digest,
        version=version,
    )
    rendered = json.dumps(asdict(result), indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
