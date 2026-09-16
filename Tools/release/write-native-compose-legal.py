#!/usr/bin/env python3
"""Create legal notices and an SPDX inventory for the bundled Compose provider."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from dependency_metadata import Dependency, load_dependencies


MODULE_PATTERN = re.compile(r"^# (?P<module>\S+) (?P<version>\S+)$")
LEGAL_PREFIXES = ("copying", "license", "notice")


def spdx_id(name: str) -> str:
    """Return a collision-resistant SPDX identifier."""

    readable = "".join(character if character.isalnum() else "-" for character in name)
    digest = hashlib.sha256(name.encode()).hexdigest()[:12]
    return f"SPDXRef-{readable}-{digest}"


def checkout_name(dependency: Dependency) -> str:
    """Return the repository basename used by SwiftPM."""

    return Path(urlparse(dependency.location).path).name.removesuffix(".git")


def swift_checkout(checkouts: Path, dependency: Dependency) -> Path:
    """Resolve exactly one SwiftPM checkout for a dependency."""

    expected = {dependency.identity.casefold(), checkout_name(dependency).casefold()}
    matches = [
        path
        for path in checkouts.iterdir()
        if path.is_dir() and path.name.casefold() in expected
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one checkout for {dependency.identity}, found {len(matches)}"
        )
    return matches[0]


def root_legal_files(root: Path) -> list[Path]:
    """Return legal files from one dependency root."""

    files = sorted(
        (
            path
            for path in root.iterdir()
            if path.is_file() and path.name.casefold().startswith(LEGAL_PREFIXES)
        ),
        key=lambda path: path.name.casefold(),
    )
    if not files:
        raise ValueError(f"dependency has no root legal file: {root}")
    return files


def go_modules(modules_file: Path) -> list[tuple[str, str]]:
    """Parse the exact module build list emitted by `go mod vendor`."""

    modules: list[tuple[str, str]] = []
    for line in modules_file.read_text(encoding="utf-8").splitlines():
        if " => " in line:
            raise ValueError("replaced Go modules are not supported in release packages")
        match = MODULE_PATTERN.fullmatch(line)
        if match is not None:
            modules.append((match.group("module"), match.group("version")))
    if not modules or len({module for module, _ in modules}) != len(modules):
        raise ValueError("vendored Go module inventory is empty or duplicated")
    return sorted(modules)


def go_legal_files(
    vendor: Path,
    module: str,
    all_modules: set[str],
) -> list[Path]:
    """Return legal files owned by one vendored Go module."""

    root = vendor.joinpath(*module.split("/"))
    if not root.is_dir() or vendor.resolve() not in root.resolve().parents:
        raise ValueError(f"vendored Go module path is missing or unsafe: {module}")
    child_modules = {
        candidate for candidate in all_modules if candidate.startswith(module + "/")
    }
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or not path.name.casefold().startswith(LEGAL_PREFIXES):
            continue
        relative = path.relative_to(vendor).as_posix()
        if any(
            relative == child or relative.startswith(child + "/")
            for child in child_modules
        ):
            continue
        files.append(path)
    if not files:
        raise ValueError(f"vendored Go module has no legal files: {module}")
    return sorted(files, key=lambda path: path.relative_to(vendor).as_posix())


def detected_license(files: list[Path]) -> str:
    """Classify the reviewed license texts using fail-closed signatures."""

    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    identifiers: list[str] = []
    signatures = (
        ("Apache-2.0", "Apache License", "Version 2.0"),
        ("MIT", "Permission is hereby granted, free of charge"),
        ("MPL-2.0", "Mozilla Public License, version 2.0"),
        ("ISC", "Permission to use, copy, modify, and/or distribute"),
        ("CC-BY-SA-4.0", "Attribution-ShareAlike 4.0 International"),
    )
    for identifier, *needles in signatures:
        if all(needle in text for needle in needles):
            identifiers.append(identifier)
    if "Redistribution and use in source and binary forms" in text:
        clause = "Neither the name" in text or (
            "may not be used to endorse" in text and "The name of" in text
        )
        identifiers.append("BSD-3-Clause" if clause else "BSD-2-Clause")
    identifiers = list(dict.fromkeys(identifiers))
    if not identifiers:
        raise ValueError(f"legal text has an unrecognized license: {files[0]}")
    return " AND ".join(sorted(identifiers))


def append_legal_section(
    sections: list[str],
    dependency_type: str,
    name: str,
    version: str,
    source: str,
    license_expression: str,
    files: list[Path],
    display_root: Path,
) -> None:
    """Append one dependency and its complete legal texts."""

    sections.extend(
        [
            "=" * 78,
            f"Dependency type: {dependency_type}",
            f"Dependency: {name}",
            f"Version: {version}",
            f"Source: {source}",
            f"Declared license: {license_expression}",
            "",
        ]
    )
    for path in files:
        contents = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        sections.extend(
            [
                f"----- {path.relative_to(display_root).as_posix()} -----",
                contents.rstrip(),
                "",
            ]
        )


def package(
    name: str,
    version: str,
    source: str,
    license_expression: str,
    source_info: str,
) -> dict[str, object]:
    """Create one SPDX package record."""

    return {
        "SPDXID": spdx_id(name),
        "name": name,
        "versionInfo": version,
        "downloadLocation": source,
        "licenseConcluded": license_expression,
        "licenseDeclared": license_expression,
        "filesAnalyzed": False,
        "sourceInfo": source_info,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    parser.add_argument("--resolved", type=Path, required=True)
    parser.add_argument("--license-manifest", type=Path, required=True)
    parser.add_argument("--checkouts", type=Path, required=True)
    parser.add_argument("--go-vendor", type=Path, required=True)
    parser.add_argument("--go-version", required=True)
    parser.add_argument("--go-license", type=Path, required=True)
    parser.add_argument("--notices-output", type=Path, required=True)
    parser.add_argument("--sbom-output", type=Path, required=True)
    args = parser.parse_args()
    if args.source_date_epoch < 0:
        parser.error("--source-date-epoch must be non-negative")

    swift_dependencies = load_dependencies(args.resolved, args.license_manifest)
    modules_file = args.go_vendor / "modules.txt"
    modules = go_modules(modules_file)
    module_names = {module for module, _ in modules}
    root_name = "container-compose"
    root_identifier = spdx_id(root_name)
    packages = [
        package(
            root_name,
            args.version,
            "https://github.com/stephenlclarke/container-compose",
            "Apache-2.0",
            f"Exact Git revision {args.commit}",
        )
    ]
    relationships: list[dict[str, str]] = []
    sections = [
        "container-compose bundled provider third-party notices",
        "======================================================",
        "",
        "This file contains the legal texts for the exact Swift and Go",
        "dependency graphs compiled into the bundled stock Compose provider.",
        "",
    ]

    for dependency in swift_dependencies:
        checkout = swift_checkout(args.checkouts, dependency)
        files = root_legal_files(checkout)
        name = f"container-compose-swift:{dependency.identity}"
        append_legal_section(
            sections,
            "SwiftPM",
            name,
            dependency.version,
            dependency.location,
            dependency.license,
            files,
            checkout,
        )
        packages.append(
            package(
                name,
                dependency.version,
                dependency.location,
                dependency.license,
                f"Exact Git revision {dependency.revision}",
            )
        )
        relationships.append(
            {
                "spdxElementId": root_identifier,
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": spdx_id(name),
            }
        )

    for module, version in modules:
        files = go_legal_files(args.go_vendor, module, module_names)
        license_expression = detected_license(files)
        name = f"container-compose-go:{module}"
        source = f"https://pkg.go.dev/{module}@{version}"
        append_legal_section(
            sections,
            "Go module",
            name,
            version,
            source,
            license_expression,
            files,
            args.go_vendor,
        )
        packages.append(
            package(
                name,
                version,
                source,
                license_expression,
                f"Exact Go module {module}@{version}",
            )
        )
        relationships.append(
            {
                "spdxElementId": root_identifier,
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": spdx_id(name),
            }
        )

    go_files = [args.go_license]
    go_name = "container-compose-go:standard-library"
    go_license = detected_license(go_files)
    append_legal_section(
        sections,
        "Go standard library",
        go_name,
        args.go_version,
        "https://go.dev",
        go_license,
        go_files,
        args.go_license.parent,
    )
    packages.append(
        package(
            go_name,
            args.go_version,
            "https://go.dev",
            go_license,
            f"Go toolchain standard library {args.go_version}",
        )
    )
    relationships.append(
        {
            "spdxElementId": root_identifier,
            "relationshipType": "DEPENDS_ON",
            "relatedSpdxElement": spdx_id(go_name),
        }
    )

    created = datetime.fromtimestamp(args.source_date_epoch, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    namespace_hash = hashlib.sha256(
        f"container-compose:{args.version}:{args.commit}".encode()
    ).hexdigest()
    document = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"container-compose-{args.version}-bundled-stock",
        "documentNamespace": (
            "https://github.com/stephenlclarke/container-compose/sbom/"
            + namespace_hash
        ),
        "creationInfo": {
            "created": created,
            "creators": ["Tool: devcontainer-write-native-compose-legal"],
        },
        "packages": packages,
        "relationships": relationships,
    }
    args.notices_output.parent.mkdir(parents=True, exist_ok=True)
    args.sbom_output.parent.mkdir(parents=True, exist_ok=True)
    args.notices_output.write_text(
        "\n".join(sections).rstrip() + "\n",
        encoding="utf-8",
    )
    args.sbom_output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
