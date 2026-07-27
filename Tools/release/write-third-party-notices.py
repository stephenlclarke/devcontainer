#!/usr/bin/env python3
"""Aggregate reviewed third-party license and notice files deterministically."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlparse

from dependency_metadata import Dependency, load_dependencies


def checkout_name(dependency: Dependency) -> str:
    """Return the repository basename used by SwiftPM's checkout directory."""

    name = Path(urlparse(dependency.location).path).name
    return name.removesuffix(".git")


def find_checkout(checkouts: Path, dependency: Dependency) -> Path:
    """Resolve exactly one checkout by identity or repository basename."""

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


def legal_files(checkout: Path) -> list[Path]:
    """Return root license and notice files, preferring LICENSE over COPYING."""

    files = [path for path in checkout.iterdir() if path.is_file()]
    licenses = sorted(
        (path for path in files if path.name.casefold().startswith("license")),
        key=lambda path: path.name.casefold(),
    )
    if not licenses:
        licenses = sorted(
            (path for path in files if path.name.casefold().startswith("copying")),
            key=lambda path: path.name.casefold(),
        )
    notices = sorted(
        (path for path in files if path.name.casefold().startswith("notice")),
        key=lambda path: path.name.casefold(),
    )
    if not licenses:
        raise ValueError(f"checkout has no root license file: {checkout}")
    return [*licenses, *notices]


def require_declared_license(dependency: Dependency, text: str) -> None:
    """Require the reviewed SPDX declaration to match recognizable license text."""

    signatures = {
        "Apache-2.0": ("Apache License", "Version 2.0"),
        "BSD-2-Clause": ("Redistribution and use in source and binary forms",),
        "BSD-3-Clause": (
            "Redistribution and use in source and binary forms",
            "Neither the name",
        ),
        "ISC": ("Permission to use, copy, modify, and/or distribute",),
        "MIT": ("Permission is hereby granted, free of charge",),
        "Unicode-3.0": ("UNICODE LICENSE V3",),
        "Zlib": ("This software is provided 'as-is'",),
    }
    if not all(signature in text for signature in signatures[dependency.license]):
        raise ValueError(
            f"license text does not match {dependency.license}: {dependency.identity}"
        )


def render(
    dependencies: list[Dependency],
    checkouts: Path,
) -> str:
    """Render full legal texts for every exact resolved dependency."""

    sections = [
        "devcontainer third-party notices",
        "================================",
        "",
        "This file contains the license and notice texts for every dependency",
        "recorded in the exact Package.resolved used to build this archive.",
        "",
    ]
    for dependency in dependencies:
        checkout = find_checkout(checkouts, dependency)
        files = legal_files(checkout)
        require_declared_license(
            dependency,
            files[0].read_text(encoding="utf-8"),
        )
        sections.extend(
            [
                "=" * 78,
                f"Dependency: {dependency.identity}",
                f"Version: {dependency.version}",
                f"Revision: {dependency.revision}",
                f"Source: {dependency.location}",
                f"Declared license: {dependency.license}",
                "",
            ]
        )
        for path in files:
            contents = path.read_text(encoding="utf-8").replace("\r\n", "\n")
            sections.extend(
                [
                    f"----- {path.name} -----",
                    contents.rstrip(),
                    "",
                ]
            )
    return "\n".join(sections).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resolved", type=Path, default=Path("Package.resolved"))
    parser.add_argument(
        "--license-manifest",
        type=Path,
        default=Path("Tools/release/dependency-licenses.json"),
    )
    parser.add_argument("--checkouts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    dependencies = load_dependencies(
        arguments.resolved,
        arguments.license_manifest,
    )
    rendered = render(dependencies, arguments.checkouts)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
