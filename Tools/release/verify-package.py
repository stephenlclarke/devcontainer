#!/usr/bin/env python3
"""Verify a devcontainer release archive and its provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Sequence

from dependency_metadata import Dependency, load_dependencies
from versioning import require_commit, require_semantic_version


DIGEST_PATTERN = re.compile(r"^(?P<digest>[0-9a-f]{64})  (?P<name>[^/\n]+)\n?$")
MARKDOWN_LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\((?P<target>[^)\s]+)")
HTML_LINK_PATTERN = re.compile(
    r"\b(?:href|src)=(?P<quote>['\"])(?P<target>[^'\"]+)(?P=quote)"
)
IMMUTABLE_SOURCE_PATTERN = re.compile(
    r"^https://(?:github\.com/stephenlclarke/devcontainer/(?:blob|tree)"
    r"|raw\.githubusercontent\.com/stephenlclarke/devcontainer)"
    r"/(?P<revision>[0-9a-f]{40})/"
)
NATIVE_COMPOSE_METADATA = Path(__file__).with_name("native-compose.json")
FORBIDDEN_RUNTIME_EXECUTABLE_NAMES = {
    "colima",
    "docker",
    "docker-buildx",
    "docker-compose",
}


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
        raise ValueError(f"archive contains an unsupported link: {member.name}")


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


def require_nonempty_regular_member(
    archive: tarfile.TarFile,
    name: str,
) -> None:
    """Require one non-empty, non-executable legal or package metadata file."""

    try:
        member = archive.getmember(name)
    except KeyError as error:
        raise ValueError(f"archive is missing {name}") from error
    if not member.isfile() or member.size <= 0 or member.mode & 0o111:
        raise ValueError(f"archive legal file is invalid: {name}")


def read_text_member(
    archive: tarfile.TarFile,
    name: str,
) -> str:
    """Decode one required UTF-8 regular file from an open archive."""

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
        return extracted.read().decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"archive member is not UTF-8: {name}") from error


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


def require_sbom(
    value: object,
    version: str,
    commit: str,
    source_date_epoch: int,
    dependencies: list[Dependency],
    bundled_file_checksums: dict[str, str],
) -> None:
    """Require SPDX metadata for the root and every exact resolved dependency."""

    if not isinstance(value, dict) or value.get("spdxVersion") != "SPDX-2.3":
        raise ValueError("package SBOM must be an SPDX 2.3 JSON document")
    packages = value.get("packages")
    if not isinstance(packages, list):
        raise ValueError("package SBOM is missing packages")
    if not all(isinstance(package, dict) for package in packages):
        raise ValueError("package SBOM contains a non-object package")
    by_name = {package.get("name"): package for package in packages}
    resolved_names = {dependency.identity for dependency in dependencies}
    expected_names = {
        "devcontainer",
        *resolved_names,
        "devcontainers-cli",
        "container-compose",
    }
    if set(by_name) != expected_names or len(packages) != len(expected_names):
        raise ValueError("package SBOM dependency set is incomplete")
    root = by_name["devcontainer"]
    namespace_hash = hashlib.sha256(
        f"devcontainer:{version}:{commit}".encode()
    ).hexdigest()
    expected_created = datetime.fromtimestamp(
        source_date_epoch,
        timezone.utc,
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    if (
        root.get("versionInfo") != version
        or root.get("downloadLocation")
        != "https://github.com/stephenlclarke/devcontainer"
        or root.get("licenseDeclared") != "Apache-2.0"
        or root.get("licenseConcluded") != "Apache-2.0"
        or root.get("sourceInfo") != f"Exact Git commit {commit}"
    ):
        raise ValueError("package SBOM root metadata is invalid")
    if (
        value.get("name") != f"devcontainer-{version}"
        or value.get("documentNamespace")
        != (
            "https://github.com/stephenlclarke/devcontainer/sbom/"
            + namespace_hash
        )
        or value.get("creationInfo")
        != {
            "created": expected_created,
            "creators": ["Tool: devcontainer-write-sbom"],
        }
    ):
        raise ValueError("package SBOM document provenance is invalid")
    for dependency in dependencies:
        package = by_name[dependency.identity]
        if (
            package.get("versionInfo") != dependency.version
            or package.get("downloadLocation") != dependency.location
            or package.get("licenseDeclared") != dependency.license
            or package.get("licenseConcluded") != dependency.license
            or package.get("sourceInfo")
            != f"Exact Git revision {dependency.revision}"
        ):
            raise ValueError(
                f"package SBOM metadata is invalid for {dependency.identity}"
            )
    for name, license_name in (
        ("devcontainers-cli", "MIT"),
        ("container-compose", "Apache-2.0"),
    ):
        package = by_name[name]
        checksums = package.get("checksums")
        if (
            not isinstance(package.get("versionInfo"), str)
            or not package["versionInfo"]
            or not str(package.get("downloadLocation", "")).startswith("https://")
            or package.get("licenseDeclared") != license_name
            or package.get("licenseConcluded") != license_name
            or not str(package.get("sourceInfo", "")).startswith("Exact Git revision ")
            or not isinstance(checksums, list)
            or len(checksums) != 1
            or checksums[0].get("algorithm") != "SHA256"
            or len(str(checksums[0].get("checksumValue", ""))) != 64
        ):
            raise ValueError(f"package SBOM metadata is invalid for {name}")
        expected_checksum = bundled_file_checksums.get(name)
        if (
            expected_checksum is not None
            and checksums[0]["checksumValue"] != expected_checksum
        ):
            raise ValueError(f"package SBOM checksum does not match bundled {name}")
    relationships = value.get("relationships")
    if not isinstance(relationships, list):
        raise ValueError("package SBOM is missing dependency relationships")
    related = {
        relationship.get("relatedSpdxElement")
        for relationship in relationships
        if isinstance(relationship, dict)
        and relationship.get("spdxElementId") == "SPDXRef-Package-devcontainer"
        and relationship.get("relationshipType") == "DEPENDS_ON"
    }
    expected_related = {
        "SPDXRef-"
        + "".join(character if character.isalnum() else "-" for character in name)
        for name in expected_names
        if name != "devcontainer"
    }
    if related != expected_related or len(relationships) != len(expected_related):
        raise ValueError("package SBOM dependency relationships are incomplete")


def require_native_compose_build_info(value: object) -> None:
    """Require the bundled Compose executable to prove its stock Apple graph."""

    if not isinstance(value, dict):
        raise ValueError("native Compose build-info.json must be a JSON object")
    metadata = json.loads(NATIVE_COMPOSE_METADATA.read_text(encoding="utf-8"))
    expected = {
        "version": metadata["version"],
        "source": "stephenlclarke/container-compose",
        "lane": "bundled-stock",
        "commit": metadata["commit"],
        "buildType": "release",
        "containerSource": "apple/container",
        "containerRef": metadata["appleContainerRevision"],
        "containerizationSource": "apple/containerization",
        "containerizationRef": metadata["appleContainerizationRevision"],
        "runtimeCapabilitySchemaVersion": 1,
        "runtimeCapabilities": [],
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise ValueError("bundled native Compose does not match its stock Apple pin")


def require_third_party_notices(
    text: str,
    dependencies: list[Dependency],
) -> None:
    """Require exact dependency provenance headers and substantive legal text."""

    if len(text.encode("utf-8")) < 1_024:
        raise ValueError("third-party notices are unexpectedly small")
    headers = [
        line.removeprefix("Dependency: ")
        for line in text.splitlines()
        if line.startswith("Dependency: ")
    ]
    if headers != [dependency.identity for dependency in dependencies]:
        raise ValueError("third-party notice dependency set is incomplete")
    for dependency in dependencies:
        metadata = (
            f"Dependency: {dependency.identity}\n"
            f"Version: {dependency.version}\n"
            f"Revision: {dependency.revision}\n"
            f"Source: {dependency.location}\n"
            f"Declared license: {dependency.license}\n"
        )
        if text.count(metadata) != 1:
            raise ValueError(
                f"third-party notice metadata is invalid for {dependency.identity}"
            )


def require_packaged_readme(text: str, commit: str) -> None:
    """Require package documentation links to be usable and immutable."""

    targets = [
        *(match.group("target") for match in MARKDOWN_LINK_PATTERN.finditer(text)),
        *(match.group("target") for match in HTML_LINK_PATTERN.finditer(text)),
    ]
    for target in targets:
        if target.startswith("#") or target.startswith("mailto:"):
            continue
        if not target.startswith(("http://", "https://")):
            raise ValueError(
                f"package README contains a repository-relative target: {target}"
            )
        source_match = IMMUTABLE_SOURCE_PATTERN.match(target)
        if source_match is not None and source_match.group("revision") != commit:
            raise ValueError(
                f"package README source target is not bound to {commit}: {target}"
            )


def verify_archive(
    archive_path: Path,
    version: str,
    lane: str,
    commit: str,
    require_notarization: bool,
    dependencies: list[Dependency],
) -> bool:
    """Validate archive structure, metadata, SBOM, and notary evidence."""

    root = f"devcontainer-{version}"
    required_executables = {
        f"{root}/bin/devcontainer",
        f"{root}/bin/devcontainer-docker",
        f"{root}/bin/devcontainer-compose",
        f"{root}/bin/devcontainer-engine",
        f"{root}/libexec/container/plugins/devcontainer/bin/devcontainer",
        f"{root}/libexec/devcontainer-compose/bin/compose",
        f"{root}/libexec/devcontainer-compose/resources/compose-normalizer",
        f"{root}/libexec/devcontainer-compose/resources/volume-initializer/compose-volume-initializer-linux-arm64",
        f"{root}/libexec/devcontainer-compose/resources/volume-initializer/compose-volume-initializer-linux-amd64",
        f"{root}/share/devcontainer/reference-cli/devcontainer.js",
    }
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        if not members:
            raise ValueError("package archive is empty")
        for member in members:
            require_safe_member(member, root)
            if (
                member.isfile()
                and member.mode & 0o111
                and PurePosixPath(member.name).name.lower()
                in FORBIDDEN_RUNTIME_EXECUTABLE_NAMES
            ):
                raise ValueError(
                    "package contains a forbidden Docker/Colima runtime executable: "
                    f"{member.name}"
                )
        executable_members = {
            member.name
            for member in members
            if member.isfile() and member.mode & 0o111
        }
        if executable_members != required_executables:
            unexpected = sorted(executable_members - required_executables)
            missing = sorted(required_executables - executable_members)
            raise ValueError(
                "package executable inventory is not exact: "
                f"unexpected={unexpected}, missing={missing}"
            )
        source_date_epochs = {member.mtime for member in members}
        if len(source_date_epochs) != 1:
            raise ValueError("archive timestamps are not normalized")
        if any(
            member.uid != 0
            or member.gid != 0
            or member.uname != "root"
            or member.gname != "wheel"
            for member in members
        ):
            raise ValueError("archive ownership metadata is not normalized")
        source_date_epoch = source_date_epochs.pop()
        by_name = {member.name.rstrip("/"): member for member in members}
        if len(by_name) != len(members):
            raise ValueError("archive contains duplicate member names")
        for executable in required_executables:
            member = by_name.get(executable)
            if member is None or not member.isfile() or not member.mode & 0o111:
                raise ValueError(
                    f"package executable is missing or not executable: {executable}"
                )
        require_nonempty_regular_member(
            archive,
            f"{root}/libexec/container/plugins/devcontainer/config.toml",
        )
        require_nonempty_regular_member(
            archive,
            f"{root}/libexec/devcontainer-compose/resources/build-info.json",
        )
        require_native_compose_build_info(
            read_json_member(
                archive,
                f"{root}/libexec/devcontainer-compose/resources/build-info.json",
            )
        )
        require_nonempty_regular_member(
            archive,
            f"{root}/libexec/devcontainer-compose/LICENSE",
        )
        for legal_file in ("LICENSE.txt", "ThirdPartyNotices.txt"):
            require_nonempty_regular_member(
                archive,
                f"{root}/share/devcontainer/reference-cli/{legal_file}",
            )

        metadata_root = f"{root}/share/devcontainer"
        for legal_file in (
            "LICENSE",
            "NOTICE.md",
            "README.md",
            "THIRD-PARTY-NOTICES.txt",
            "com.github.stephenlclarke.devcontainer.plist.in",
        ):
            require_nonempty_regular_member(
                archive,
                f"{metadata_root}/{legal_file}",
            )
        build_info = read_json_member(archive, f"{metadata_root}/build-info.json")
        require_build_info(build_info, version, lane, commit)
        sbom = read_json_member(archive, f"{metadata_root}/devcontainer.spdx.json")
        compose_member = archive.extractfile(
            f"{root}/libexec/devcontainer-compose/bin/compose"
        )
        if compose_member is None:
            raise ValueError("bundled container-compose executable cannot be read")
        require_sbom(
            sbom,
            version,
            commit,
            source_date_epoch,
            dependencies,
            {"container-compose": hashlib.sha256(compose_member.read()).hexdigest()},
        )
        notices = read_text_member(
            archive,
            f"{metadata_root}/THIRD-PARTY-NOTICES.txt",
        )
        require_third_party_notices(notices, dependencies)
        readme = read_text_member(archive, f"{metadata_root}/README.md")
        require_packaged_readme(readme, commit)
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
    parser.add_argument(
        "--resolved",
        type=Path,
        default=Path("Package.resolved"),
    )
    parser.add_argument(
        "--license-manifest",
        type=Path,
        default=Path("Tools/release/dependency-licenses.json"),
    )
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
        dependencies = load_dependencies(args.resolved, args.license_manifest)
        notarized = verify_archive(
            args.archive,
            version,
            args.expected_lane,
            commit,
            args.require_notarization,
            dependencies,
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
