#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Validate the design-time differential parity manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REQUIRED_BACKENDS = {"docker", "apple-stock", "apple-compose"}
IMPLEMENTED_STATUS = "implemented"


class ManifestError(ValueError):
    """Raised when the parity manifest violates a release invariant."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ManifestError(message)


def validate_manifest(payload: dict[str, Any], release: bool = False) -> None:
    """Validate one parsed parity manifest."""

    _require(payload.get("schemaVersion") == 1, "schemaVersion must be 1")

    policy = payload.get("releasePolicy")
    _require(isinstance(policy, dict), "releasePolicy must be an object")
    _require(
        policy.get("requireZeroFunctionalDifferences") is True,
        "stable releases must require zero functional differences",
    )
    _require(
        set(policy.get("requiredBackends", [])) == REQUIRED_BACKENDS,
        "releasePolicy.requiredBackends must contain docker, apple-stock, and apple-compose",
    )

    backends = payload.get("backends")
    _require(isinstance(backends, dict), "backends must be an object")
    _require(
        set(backends) == REQUIRED_BACKENDS,
        "backends must define exactly docker, apple-stock, and apple-compose",
    )
    _require(
        backends["docker"].get("role") == "oracle",
        "the docker backend must be the behavioral oracle",
    )

    fixtures = payload.get("fixtures")
    _require(isinstance(fixtures, list) and fixtures, "fixtures must be a non-empty array")

    fixture_ids: set[str] = set()
    for index, fixture in enumerate(fixtures):
        _require(isinstance(fixture, dict), f"fixture {index} must be an object")
        fixture_id = fixture.get("id")
        _require(
            isinstance(fixture_id, str) and fixture_id,
            f"fixture {index} must have an id",
        )
        _require(fixture_id not in fixture_ids, f"duplicate fixture id: {fixture_id}")
        fixture_ids.add(fixture_id)

        _require(
            set(fixture.get("backends", [])) == REQUIRED_BACKENDS,
            f"{fixture_id} must run against all three required backends",
        )
        _require(
            isinstance(fixture.get("assertions"), list) and fixture["assertions"],
            f"{fixture_id} must define semantic assertions",
        )
        _require(
            fixture.get("status") in {"planned", IMPLEMENTED_STATUS},
            f"{fixture_id} has an invalid implementation status",
        )
        if release:
            _require(
                fixture.get("status") == IMPLEMENTED_STATUS,
                f"{fixture_id} is not implemented and cannot enter a stable release",
            )

    normalization = payload.get("normalization")
    _require(isinstance(normalization, dict), "normalization must be an object")
    allowed = normalization.get("allowed")
    forbidden = normalization.get("forbidden")
    _require(isinstance(allowed, list) and allowed, "normalization.allowed is required")
    _require(
        isinstance(forbidden, list) and forbidden,
        "normalization.forbidden is required",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "manifest",
        nargs="?",
        type=Path,
        default=Path("Tests/Parity/manifest.json"),
    )
    parser.add_argument(
        "--release",
        action="store_true",
        help="require every fixture to be implemented",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = json.loads(args.manifest.read_text(encoding="utf-8"))
    try:
        validate_manifest(payload, release=args.release)
    except ManifestError as error:
        raise SystemExit(f"error: {error}") from error
    print(f"validated parity manifest: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
