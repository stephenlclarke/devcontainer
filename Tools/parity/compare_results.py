#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Compare normalized observations from all required parity lanes."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from parity_lib import LANES, ParityError, atomic_json

PERFORMANCE_REGRESSION_FACTOR = 10.0


def recorded_duration(result: dict[str, Any] | None) -> float | None:
    """Return a valid wall-clock duration from one fixture result."""

    if result is None:
        return None
    value = result.get("durationSeconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    duration = float(value)
    return duration if math.isfinite(duration) and duration >= 0 else None


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
        (
            "Wall-clock timings are evidence, not exact-equivalence assertions. "
            "A candidate fails performance parity only when it does not complete "
            f"or takes at least {PERFORMANCE_REGRESSION_FACTOR:g}x the Docker oracle."
        ),
        "",
        (
            "| Fixture | Docker oracle | Stock Apple | container-compose provider "
            "| Stock/Docker | Provider/Docker | Equivalent |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- |",
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
        performance_differences: list[str] = []
        durations = {
            lane: recorded_duration(result)
            for lane, result in by_lane.items()
        }
        relative_durations: dict[str, float | None] = {
            lane: None for lane in LANES
        }
        if missing:
            differences.append(f"missing lanes: {', '.join(missing)}")
        invalid_timings = [
            lane
            for lane, result in by_lane.items()
            if result is not None and durations[lane] is None
        ]
        if invalid_timings:
            performance_differences.append(
                f"missing or invalid duration: {', '.join(invalid_timings)}"
            )
        docker_duration = durations["docker"]
        if docker_duration is not None:
            relative_durations["docker"] = 1.0
            if docker_duration == 0:
                performance_differences.append(
                    "docker duration is zero; relative timing cannot be compared"
                )
            else:
                for lane in ("apple-stock", "container-compose"):
                    candidate_duration = durations[lane]
                    if candidate_duration is None:
                        continue
                    ratio = candidate_duration / docker_duration
                    relative_durations[lane] = round(ratio, 3)
                    if ratio >= PERFORMANCE_REGRESSION_FACTOR:
                        performance_differences.append(
                            f"{lane} duration is {ratio:.3f}x Docker "
                            f"(limit: <{PERFORMANCE_REGRESSION_FACTOR:g}x)"
                        )
        if oracle is not None:
            for lane in ("apple-stock", "container-compose"):
                candidate = by_lane[lane]
                if candidate is not None and (
                    candidate.get("observations") != oracle.get("observations")
                ):
                    differences.append(f"{lane} observations differ from docker")
        if any(status != "passed" for status in statuses.values()):
            differences.append("one or more lanes failed")
        differences.extend(performance_differences)
        equivalent = not differences
        all_equivalent = all_equivalent and equivalent
        comparisons.append(
            {
                "id": fixture_id,
                "statuses": statuses,
                "durationsSeconds": durations,
                "relativeDurations": relative_durations,
                "performanceEquivalent": not performance_differences,
                "performanceDifferences": performance_differences,
                "equivalent": equivalent,
                "differences": differences,
            }
        )

        def status_cell(lane: str) -> str:
            duration = durations[lane]
            suffix = "" if duration is None else f" ({duration:.3f}s)"
            return statuses[lane] + suffix

        def ratio_cell(lane: str) -> str:
            ratio = relative_durations[lane]
            return "-" if ratio is None else f"{ratio:.3f}x"

        cells = [
            fixture_id,
            status_cell("docker"),
            status_cell("apple-stock"),
            status_cell("container-compose"),
            ratio_cell("apple-stock"),
            ratio_cell("container-compose"),
            "yes" if equivalent else "no",
        ]
        lines.append("| " + " | ".join(cells) + " |")

    payload = {
        "schemaVersion": 1,
        "status": "passed" if all_equivalent else "failed",
        "requireZeroFunctionalDifferences": True,
        "performancePolicy": {
            "durationMetric": "fixture wall-clock seconds",
            "oracle": "docker",
            "regressionFactor": PERFORMANCE_REGRESSION_FACTOR,
            "failureRule": (
                "candidate does not complete or duration is at least "
                f"{PERFORMANCE_REGRESSION_FACTOR:g}x Docker"
            ),
        },
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
