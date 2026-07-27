#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Shared helpers for the three-lane differential parity harness."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LANES = ("docker", "apple-stock", "apple-compose")
OBSERVATION_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


class ParityError(RuntimeError):
    """Raised when evidence cannot satisfy the parity contract."""


@dataclass(frozen=True)
class Fixture:
    """One implemented parity fixture and its semantic contract."""

    identifier: str
    directory: Path
    expected: dict[str, str]
    backends: tuple[str, ...]
    runner: str


def load_manifest(path: Path) -> dict[str, Any]:
    """Load the checked-in manifest."""

    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schemaVersion") != 1:
        raise ParityError(f"unsupported manifest schema in {path}")
    return value


def implemented_fixtures(repository: Path, manifest: dict[str, Any]) -> list[Fixture]:
    """Resolve implemented fixture directories and validate their contracts."""

    fixtures: list[Fixture] = []
    for entry in manifest["fixtures"]:
        if entry["status"] != "implemented":
            continue
        identifier = str(entry["id"])
        directory = repository / "Tests" / "Parity" / "fixtures" / identifier
        contract_path = directory / "contract.json"
        runner = str(entry.get("runner", "devcontainer"))
        required = [contract_path]
        if runner == "devcontainer":
            required += [
                directory / "probe.sh",
                directory / ".devcontainer" / "devcontainer.json",
            ]
        elif runner not in {"engine", "fault", "vscode"}:
            raise ParityError(f"{identifier} has unknown runner {runner!r}")
        missing = [
            str(path.relative_to(repository))
            for path in required
            if not path.is_file()
        ]
        if missing:
            raise ParityError(
                f"{identifier} is implemented but is missing: {', '.join(missing)}"
            )
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        expected = contract.get("expected")
        if not isinstance(expected, dict) or not expected:
            raise ParityError(f"{identifier} contract must define non-empty expected")
        normalized: dict[str, str] = {}
        for key, value in expected.items():
            if not isinstance(key, str) or not OBSERVATION_KEY.fullmatch(key):
                raise ParityError(f"{identifier} has invalid observation key {key!r}")
            if not isinstance(value, (str, int, float, bool)):
                raise ParityError(f"{identifier}.{key} must be a scalar")
            normalized[key] = scalar_text(value)
        fixtures.append(
            Fixture(
                identifier=identifier,
                directory=directory,
                expected=normalized,
                backends=tuple(entry["backends"]),
                runner=runner,
            )
        )
    if not fixtures:
        raise ParityError("the manifest has no implemented fixtures")
    return fixtures


def scalar_text(value: object) -> str:
    """Convert contract JSON scalars to the probe's canonical text form."""

    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def parse_observations(output: str) -> dict[str, str]:
    """Parse a strict newline-delimited ``key=value`` probe result."""

    observations: dict[str, str] = {}
    for line_number, line in enumerate(output.splitlines(), start=1):
        if not line:
            continue
        if "=" not in line:
            raise ParityError(f"probe line {line_number} is not key=value: {line!r}")
        key, value = line.split("=", 1)
        if not OBSERVATION_KEY.fullmatch(key):
            raise ParityError(f"probe line {line_number} has invalid key: {key!r}")
        if key in observations:
            raise ParityError(f"probe emitted duplicate key: {key}")
        observations[key] = value
    if not observations:
        raise ParityError("probe produced no semantic observations")
    return observations


def assert_contract(
    fixture: Fixture,
    observations: dict[str, str],
) -> list[str]:
    """Return human-readable contract differences without normalizing semantics."""

    differences: list[str] = []
    for key in sorted(set(fixture.expected) | set(observations)):
        if key not in fixture.expected:
            differences.append(f"unexpected observation {key}={observations[key]!r}")
        elif key not in observations:
            differences.append(f"missing observation {key}")
        elif observations[key] != fixture.expected[key]:
            differences.append(
                f"{key}: expected {fixture.expected[key]!r}, got {observations[key]!r}"
            )
    return differences


def atomic_json(path: Path, value: object) -> None:
    """Write deterministic evidence without exposing a partially written result."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
