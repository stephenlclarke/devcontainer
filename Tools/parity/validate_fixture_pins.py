#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Fail when parity fixtures depend on mutable images or Feature payloads."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
DIGEST_REFERENCE = re.compile(r"^\S+@sha256:[0-9a-f]{64}$")
COMPOSE_IMAGE = re.compile(r"^\s*image:\s*([^#\s]+)")
DOCKERFILE_FROM = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*([^\s]+)", re.IGNORECASE)


class FixturePinError(ValueError):
    """Raised when a parity input can change without a repository change."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FixturePinError(message)


def validate_image(reference: Any, location: str) -> None:
    _require(
        isinstance(reference, str) and DIGEST_REFERENCE.fullmatch(reference) is not None,
        f"{location} must include an immutable sha256 digest",
    )


def validate_feature_lock(
    configuration: dict[str, Any],
    lock: dict[str, Any],
    location: str,
) -> None:
    requested = configuration.get("features", {})
    _require(isinstance(requested, dict), f"{location}.features must be an object")
    locked = lock.get("features", {})
    _require(isinstance(locked, dict), f"{location} Feature lock must define features")
    _require(
        set(locked) == set(requested),
        f"{location} Feature lock keys must exactly match requested Features",
    )
    for reference, entry in locked.items():
        _require(isinstance(entry, dict), f"{location} lock entry {reference} is invalid")
        resolved = entry.get("resolved")
        integrity = entry.get("integrity")
        _require(
            isinstance(resolved, str)
            and DIGEST_REFERENCE.fullmatch(resolved) is not None,
            f"{location} Feature {reference} lacks a resolved digest",
        )
        _require(
            isinstance(integrity, str) and DIGEST.fullmatch(integrity) is not None,
            f"{location} Feature {reference} lacks an integrity digest",
        )
        _require(
            resolved.endswith(f"@{integrity}"),
            f"{location} Feature {reference} resolved and integrity digests differ",
        )
        _require(
            isinstance(entry.get("version"), str) and entry["version"],
            f"{location} Feature {reference} lacks a resolved version",
        )


def validate_fixture_pins(repository: Path, manifest: dict[str, Any]) -> None:
    """Validate immutable identities for every checked parity fixture input."""

    for fixture in manifest.get("fixtures", []):
        if fixture.get("status") != "implemented":
            continue
        identifier = str(fixture["id"])
        directory = repository / "Tests/Parity/fixtures" / identifier
        configuration_path = directory / ".devcontainer/devcontainer.json"
        if configuration_path.is_file():
            configuration = json.loads(configuration_path.read_text(encoding="utf-8"))
            if "image" in configuration:
                validate_image(
                    configuration["image"],
                    f"{identifier} devcontainer image",
                )
            if configuration.get("features"):
                lock_path = configuration_path.with_name("devcontainer-lock.json")
                _require(
                    lock_path.is_file(),
                    f"{identifier} must check in devcontainer-lock.json",
                )
                validate_feature_lock(
                    configuration,
                    json.loads(lock_path.read_text(encoding="utf-8")),
                    identifier,
                )

        for dockerfile in directory.rglob("Dockerfile*"):
            if not dockerfile.is_file():
                continue
            for line_number, line in enumerate(
                dockerfile.read_text(encoding="utf-8").splitlines(),
                start=1,
            ):
                match = DOCKERFILE_FROM.match(line)
                if match and not match.group(1).startswith("$"):
                    validate_image(
                        match.group(1),
                        f"{dockerfile.relative_to(repository)}:{line_number}",
                    )

        for compose_name in ("compose.yaml", "compose.yml", "docker-compose.yml"):
            compose = directory / compose_name
            if not compose.is_file():
                continue
            for line_number, line in enumerate(
                compose.read_text(encoding="utf-8").splitlines(),
                start=1,
            ):
                match = COMPOSE_IMAGE.match(line)
                if match:
                    validate_image(
                        match.group(1).strip("'\""),
                        f"{compose.relative_to(repository)}:{line_number}",
                    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("Tests/Parity/manifest.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repository = Path(__file__).resolve().parents[2]
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    try:
        validate_fixture_pins(repository, manifest)
    except (FixturePinError, json.JSONDecodeError) as error:
        raise SystemExit(f"error: {error}") from error
    print("validated immutable parity fixture inputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
