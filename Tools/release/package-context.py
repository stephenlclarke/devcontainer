#!/usr/bin/env python3
"""Resolve immutable package, release, and Homebrew identities."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Sequence

from versioning import (
    current_formula_version,
    require_commit,
    require_semantic_version,
)


@dataclass(frozen=True)
class PackageContext:
    """The externally visible identity of one package lane."""

    asset: str
    commit: str
    formulaVersion: str
    lane: str
    productVersion: str
    releaseTag: str


def package_context(
    product_version: str,
    lane: str,
    commit: str,
    run_number: str | None = None,
) -> PackageContext:
    """Return validated package identity for development, Current, or stable."""

    version = require_semantic_version(product_version)
    if lane == "development":
        normalized_commit = (
            require_commit(commit) if commit != "unspecified" else commit
        )
        return PackageContext(
            asset=f"devcontainer-{version}-macos-arm64.tar.gz",
            commit=normalized_commit,
            formulaVersion=version,
            lane=lane,
            productVersion=version,
            releaseTag="",
        )

    normalized_commit = require_commit(commit)
    if lane == "stable":
        return PackageContext(
            asset="devcontainer-release-arm64.tar.gz",
            commit=normalized_commit,
            formulaVersion=version,
            lane=lane,
            productVersion=version,
            releaseTag=version,
        )
    if lane == "current":
        if run_number is None:
            raise ValueError("Current packages require a workflow run number")
        return PackageContext(
            asset=f"devcontainer-current-{normalized_commit[:12]}-arm64.tar.gz",
            commit=normalized_commit,
            formulaVersion=current_formula_version(run_number, normalized_commit),
            lane=lane,
            productVersion=version,
            releaseTag="current",
        )
    raise ValueError(f"package lane must be development, current, or stable: {lane}")


def parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-version", required=True)
    parser.add_argument(
        "--lane",
        choices=("development", "current", "stable"),
        required=True,
    )
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-number")
    parser.add_argument(
        "--field",
        choices=tuple(PackageContext.__annotations__),
        help="print one field instead of the JSON context",
    )
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    args = parse_args(arguments)
    try:
        context = package_context(
            args.product_version,
            args.lane,
            args.commit,
            args.run_number,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error
    if args.field is not None:
        print(getattr(context, args.field))
    else:
        print(json.dumps(asdict(context), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
