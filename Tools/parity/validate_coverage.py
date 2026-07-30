#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Validate the pinned Development Containers specification coverage ledger."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
STATUSES = {"blocked", "certified"}


class CoverageError(ValueError):
    """Raised when specification coverage is incomplete or contradictory."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CoverageError(message)


def schema_properties(schema: dict[str, Any]) -> set[str]:
    """Return explicit property paths from the pinned base schema."""

    discovered: set[str] = set()

    def resolve(reference: str) -> Any:
        value: Any = schema
        for component in reference.removeprefix("#/").split("/"):
            value = value[component.replace("~1", "/").replace("~0", "~")]
        return value

    def visit(
        value: Any,
        path: tuple[str, ...] = (),
        seen: frozenset[tuple[str, tuple[str, ...]]] = frozenset(),
    ) -> None:
        if not isinstance(value, dict):
            return
        reference = value.get("$ref")
        marker = (str(reference), path)
        if (
            isinstance(reference, str)
            and reference.startswith("#/")
            and marker not in seen
        ):
            visit(resolve(reference), path, seen | {marker})

        properties = value.get("properties", {})
        if isinstance(properties, dict):
            for name, child in properties.items():
                child_path = path + (name,)
                discovered.add(".".join(child_path))
                visit(child, child_path, seen)

        patterns = value.get("patternProperties", {})
        if isinstance(patterns, dict) and patterns:
            wildcard_path = path + ("*",)
            discovered.add(".".join(wildcard_path))
            for pattern, child in patterns.items():
                if pattern != "additionalProperties":
                    visit(child, wildcard_path, seen)

        for collection in ("allOf", "anyOf", "oneOf"):
            children = value.get(collection, [])
            if isinstance(children, list):
                for child in children:
                    visit(child, path, seen)

        items = value.get("items")
        if isinstance(items, dict):
            array_path = (
                path[:-1] + (f"{path[-1]}[]",)
                if path
                else ("[]",)
            )
            visit(items, array_path, seen)

    visit(schema)
    return discovered


def validate_coverage(
    payload: dict[str, Any],
    manifest: dict[str, Any],
    schema: dict[str, Any] | None = None,
) -> None:
    """Validate complete ownership and evidence for every pinned property."""

    _require(payload.get("schemaVersion") == 1, "coverage schemaVersion must be 1")
    source = payload.get("source")
    _require(isinstance(source, dict), "coverage source must be an object")
    _require(
        source.get("repository") == "https://github.com/devcontainers/spec",
        "coverage source must use the upstream specification repository",
    )
    _require(
        GIT_COMMIT.fullmatch(str(source.get("commit", ""))) is not None,
        "coverage source commit must be a full Git commit",
    )
    _require(
        SHA256.fullmatch(str(source.get("schemaSHA256", ""))) is not None,
        "coverage source schemaSHA256 must be a SHA-256 digest",
    )

    expected_values = payload.get("expectedProperties")
    _require(
        isinstance(expected_values, list) and expected_values,
        "expectedProperties must be a non-empty array",
    )
    expected = {str(value) for value in expected_values}
    _require(
        len(expected) == len(expected_values),
        "expectedProperties contains duplicates",
    )

    fixture_ids = {
        str(fixture["id"])
        for fixture in manifest.get("fixtures", [])
        if isinstance(fixture, dict) and fixture.get("status") == "implemented"
    }
    covered: set[str] = set()
    groups = payload.get("propertyCoverageGroups")
    _require(
        isinstance(groups, list) and groups,
        "propertyCoverageGroups must be a non-empty array",
    )
    group_ids: set[str] = set()
    for index, group in enumerate(groups):
        _require(isinstance(group, dict), f"coverage group {index} must be an object")
        identifier = group.get("id")
        _require(
            isinstance(identifier, str) and identifier,
            f"coverage group {index} must have an id",
        )
        _require(identifier not in group_ids, f"duplicate coverage group: {identifier}")
        group_ids.add(identifier)
        status = group.get("status")
        _require(status in STATUSES, f"{identifier} has invalid status")
        _require(
            isinstance(group.get("owner"), str) and group["owner"].strip(),
            f"{identifier} must have an owner",
        )
        properties = group.get("properties")
        _require(
            isinstance(properties, list) and properties,
            f"{identifier} must cover at least one property",
        )
        for property_name in properties:
            _require(
                isinstance(property_name, str) and property_name,
                f"{identifier} contains an invalid property",
            )
            _require(
                property_name not in covered,
                f"property has duplicate coverage: {property_name}",
            )
            covered.add(property_name)
        fixtures = group.get("fixtures", [])
        _require(isinstance(fixtures, list), f"{identifier}.fixtures must be an array")
        unknown_fixtures = set(fixtures) - fixture_ids
        _require(
            not unknown_fixtures,
            f"{identifier} references unknown fixtures: "
            + ", ".join(sorted(unknown_fixtures)),
        )
        if status == "certified":
            _require(fixtures, f"{identifier} is certified without fixture evidence")
            _require(
                "blocker" not in group,
                f"{identifier} cannot be certified and blocked",
            )
        else:
            _require(
                isinstance(group.get("blocker"), str) and group["blocker"].strip(),
                f"{identifier} is blocked without an explicit blocker",
            )

    missing = expected - covered
    unexpected = covered - expected
    _require(
        not missing,
        "schema properties lack coverage: " + ", ".join(sorted(missing)),
    )
    _require(
        not unexpected,
        "coverage contains unpinned properties: " + ", ".join(sorted(unexpected)),
    )

    lifecycle = payload.get("lifecycleCoverage")
    _require(
        isinstance(lifecycle, list) and lifecycle,
        "lifecycleCoverage must be a non-empty array",
    )
    lifecycle_rules: set[str] = set()
    for index, group in enumerate(lifecycle):
        _require(isinstance(group, dict), f"lifecycle group {index} must be an object")
        identifier = str(group.get("id", ""))
        _require(identifier, f"lifecycle group {index} must have an id")
        _require(group.get("status") in STATUSES, f"{identifier} has invalid status")
        _require(
            isinstance(group.get("owner"), str) and group["owner"].strip(),
            f"{identifier} must have an owner",
        )
        rules = group.get("rules")
        _require(
            isinstance(rules, list) and rules,
            f"{identifier} must define lifecycle rules",
        )
        for rule in rules:
            _require(
                isinstance(rule, str) and rule and rule not in lifecycle_rules,
                f"duplicate or invalid lifecycle rule: {rule}",
            )
            lifecycle_rules.add(rule)
        fixtures = group.get("fixtures", [])
        _require(
            isinstance(fixtures, list) and not (set(fixtures) - fixture_ids),
            f"{identifier} references unknown lifecycle fixtures",
        )
        if group["status"] == "certified":
            _require(fixtures, f"{identifier} is certified without fixture evidence")
        else:
            _require(
                isinstance(group.get("blocker"), str) and group["blocker"].strip(),
                f"{identifier} is blocked without an explicit blocker",
            )

    if schema is not None:
        actual = schema_properties(schema)
        added = actual - expected
        removed = expected - actual
        _require(
            not added,
            "upstream schema properties lack coverage: " + ", ".join(sorted(added)),
        )
        _require(
            not removed,
            "coverage properties are absent from the schema: "
            + ", ".join(sorted(removed)),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--coverage",
        type=Path,
        default=Path("Tests/Parity/spec-coverage.json"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("Tests/Parity/manifest.json"),
    )
    parser.add_argument(
        "--schema",
        type=Path,
        help="optionally compare an upstream base schema with the checked inventory",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = json.loads(args.coverage.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    schema = (
        json.loads(args.schema.read_text(encoding="utf-8"))
        if args.schema is not None
        else None
    )
    try:
        validate_coverage(payload, manifest, schema=schema)
    except CoverageError as error:
        raise SystemExit(f"error: {error}") from error
    print(f"validated specification coverage: {args.coverage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
