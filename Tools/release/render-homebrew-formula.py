#!/usr/bin/env python3
"""Render the Homebrew formula for a release archive."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

from versioning import require_semantic_version


CURRENT_VERSION_PATTERN = re.compile(
    r"^current\.(?P<run>[1-9][0-9]*)\.(?P<commit>[0-9a-f]{12})$"
)
RELEASE_ROOT = "https://github.com/stephenlclarke/devcontainer/releases/download"


def validate_identity(
    product_version: str,
    formula_version: str,
    formula_class: str,
    url: str,
    conflict: str,
) -> None:
    """Reject formula metadata that could escape the two release channels."""

    require_semantic_version(product_version)
    if formula_class == "Devcontainer":
        if formula_version != product_version:
            raise ValueError("stable formula version must equal the product version")
        if conflict:
            raise ValueError("stable formula must not conflict with an optional formula")
        expected_url = (
            f"{RELEASE_ROOT}/{product_version}/devcontainer-release-arm64.tar.gz"
        )
        if url != expected_url:
            raise ValueError(f"stable formula URL must be {expected_url}")
        return

    if formula_class == "DevcontainerCurrent":
        match = CURRENT_VERSION_PATTERN.fullmatch(formula_version)
        if match is None:
            raise ValueError(
                "Current formula version must be current.RUN.SHA12"
            )
        if conflict != "devcontainer":
            raise ValueError("Current formula must conflict with devcontainer")
        expected_url = (
            f"{RELEASE_ROOT}/current/"
            f"devcontainer-current-{match.group('commit')}-arm64.tar.gz"
        )
        if url != expected_url:
            raise ValueError(f"Current formula URL must be {expected_url}")
        return

    raise ValueError(f"unsupported formula class: {formula_class}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-version", required=True)
    parser.add_argument("--formula-version")
    parser.add_argument("--formula-class", default="Devcontainer")
    parser.add_argument("--url", required=True)
    parser.add_argument("--conflicts-with", default="")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    formula_version = args.formula_version or args.product_version
    try:
        validate_identity(
            args.product_version,
            formula_version,
            args.formula_class,
            args.url,
            args.conflicts_with,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error
    digest = hashlib.sha256(args.archive.read_bytes()).hexdigest()
    conflict_declaration = "\n"
    version_declaration = ""
    if args.formula_class == "DevcontainerCurrent":
        conflict_declaration = (
            f'  conflicts_with "{args.conflicts_with}", '
            'because: "both install devcontainer commands"\n\n'
        )
        version_declaration = f'  version "{formula_version}"\n'
    values = {
        "CONFLICT_DECLARATION": conflict_declaration,
        "FORMULA_CLASS": args.formula_class,
        "PRODUCT_VERSION": args.product_version,
        "SHA256": digest,
        "URL": args.url,
        "VERSION_DECLARATION": version_declaration,
    }
    rendered = args.template.read_text(encoding="utf-8")
    for name, value in values.items():
        rendered = rendered.replace(f"@{name}@", value)
    unresolved = sorted(set(re.findall(r"@[A-Z_]+@", rendered)))
    if unresolved:
        raise SystemExit(
            "formula template has unresolved placeholders: " + ", ".join(unresolved)
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
