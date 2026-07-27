#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Compare normalized observations from all required parity lanes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from parity_lib import LANES, ParityError, atomic_json


def compare(root: Path) -> tuple[dict[str, Any], str]:
    """Load all lanes, enforce exact semantics, and return JSON and Markdown."""

    lane_results: dict[str, dict[str, Any]] = {}
    for lane in LANES:
        path = root / lane / "results.json"
        if not path.is_file():
            raise ParityError(f"missing lane evidence: {path}")
        lane_results[lane] = json.loads(path.read_text(encoding="utf-8"))

    fixture_ids = {
        result["id"]
        for payload in lane_results.values()
        for result in payload.get("fixtures", [])
    }
    comparisons: list[dict[str, Any]] = []
    lines = [
        "# Dev Containers runtime parity",
        "",
        "| Fixture | Docker oracle | Stock Apple | Apple Compose | Equivalent |",
        "| --- | --- | --- | --- | --- |",
    ]
    all_equivalent = True
    for fixture_id in sorted(fixture_ids):
        by_lane: dict[str, dict[str, Any] | None] = {}
        for lane, payload in lane_results.items():
            by_lane[lane] = next(
                (
                    result
                    for result in payload.get("fixtures", [])
                    if result["id"] == fixture_id
                ),
                None,
            )
        missing = [lane for lane, result in by_lane.items() if result is None]
        statuses = {
            lane: result["status"] if result is not None else "missing"
            for lane, result in by_lane.items()
        }
        oracle = by_lane["docker"]
        differences: list[str] = []
        if missing:
            differences.append(f"missing lanes: {', '.join(missing)}")
        if oracle is not None:
            for lane in ("apple-stock", "apple-compose"):
                candidate = by_lane[lane]
                if candidate is not None and (
                    candidate.get("observations") != oracle.get("observations")
                ):
                    differences.append(f"{lane} observations differ from docker")
        if any(status != "passed" for status in statuses.values()):
            differences.append("one or more lanes failed")
        equivalent = not differences
        all_equivalent = all_equivalent and equivalent
        comparisons.append(
            {
                "id": fixture_id,
                "statuses": statuses,
                "equivalent": equivalent,
                "differences": differences,
            }
        )
        cells = [
            fixture_id,
            statuses["docker"],
            statuses["apple-stock"],
            statuses["apple-compose"],
            "yes" if equivalent else "no",
        ]
        lines.append("| " + " | ".join(cells) + " |")

    payload = {
        "schemaVersion": 1,
        "status": "passed" if all_equivalent else "failed",
        "requireZeroFunctionalDifferences": True,
        "fixtures": comparisons,
    }
    return payload, "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    try:
        payload, markdown = compare(args.evidence)
    except (OSError, ValueError, ParityError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    atomic_json(args.evidence / "comparison.json", payload)
    (args.evidence / "matrix.md").write_text(markdown, encoding="utf-8")
    print(markdown, end="")
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
