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

PERFORMANCE_TARGET_FACTOR = 1.0
PERFORMANCE_INVESTIGATION_FACTOR = 2.5


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
            "Functional parity requires zero semantic differences and completed, "
            "valid evidence. Comparable or better performance "
            f"(at most {PERFORMANCE_TARGET_FACTOR:.2f}x Docker) is the objective. "
            "A completed candidate above "
            f"{PERFORMANCE_INVESTIGATION_FACTOR:.2f}x Docker requires investigation; "
            "a timing ratio alone does not change functional parity."
        ),
        "",
        (
            "| Fixture | Docker oracle | Stock Apple | container-compose provider "
            "| Stock/Docker | Provider/Docker | Functional parity | Performance |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    all_equivalent = True
    all_functionally_equivalent = True
    all_performance_targets_met = True
    any_performance_investigation = False
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
        functional_differences: list[str] = []
        timing_differences: list[str] = []
        performance_investigations: list[str] = []
        durations = {
            lane: recorded_duration(result)
            for lane, result in by_lane.items()
        }
        relative_durations: dict[str, float | None] = {
            lane: None for lane in LANES
        }
        candidate_ratios: dict[str, float] = {}
        if missing:
            functional_differences.append(f"missing lanes: {', '.join(missing)}")
        invalid_timings = [
            lane
            for lane, result in by_lane.items()
            if result is None or durations[lane] is None
        ]
        if invalid_timings:
            timing_differences.append(
                f"missing or invalid duration: {', '.join(invalid_timings)}"
            )
        docker_duration = durations["docker"]
        if docker_duration is not None:
            relative_durations["docker"] = 1.0
            if docker_duration == 0:
                timing_differences.append(
                    "docker duration is zero; relative timing cannot be compared"
                )
            else:
                for lane in ("apple-stock", "container-compose"):
                    candidate_duration = durations[lane]
                    if candidate_duration is None:
                        continue
                    ratio = candidate_duration / docker_duration
                    candidate_ratios[lane] = ratio
                    relative_durations[lane] = round(ratio, 3)
                    if (
                        statuses[lane] == "passed"
                        and ratio > PERFORMANCE_INVESTIGATION_FACTOR
                    ):
                        performance_investigations.append(
                            f"{lane} duration is {ratio:.3f}x Docker "
                            f"(investigate: >{PERFORMANCE_INVESTIGATION_FACTOR:g}x)"
                        )
        if oracle is not None:
            for lane in ("apple-stock", "container-compose"):
                candidate = by_lane[lane]
                if candidate is not None and (
                    candidate.get("observations") != oracle.get("observations")
                ):
                    functional_differences.append(
                        f"{lane} observations differ from docker"
                    )
        if any(status != "passed" for status in statuses.values()):
            functional_differences.append("one or more lanes failed")
        performance_target_met = (
            not timing_differences
            and all(
                lane in candidate_ratios
                and candidate_ratios[lane] <= PERFORMANCE_TARGET_FACTOR
                for lane in ("apple-stock", "container-compose")
            )
        )
        functional_equivalent = not functional_differences
        differences = functional_differences + timing_differences
        equivalent = not differences
        all_equivalent = all_equivalent and equivalent
        all_functionally_equivalent = (
            all_functionally_equivalent and functional_equivalent
        )
        all_performance_targets_met = (
            all_performance_targets_met and performance_target_met
        )
        any_performance_investigation = (
            any_performance_investigation or bool(performance_investigations)
        )
        comparisons.append(
            {
                "id": fixture_id,
                "statuses": statuses,
                "durationsSeconds": durations,
                "relativeDurations": relative_durations,
                "functionalEquivalent": functional_equivalent,
                "functionalDifferences": functional_differences,
                "timingEvidenceValid": not timing_differences,
                "timingDifferences": timing_differences,
                "performanceTargetMet": performance_target_met,
                "performanceInvestigationRequired": bool(
                    performance_investigations
                ),
                "performanceInvestigations": performance_investigations,
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

        if timing_differences:
            performance_cell = "invalid evidence"
        elif performance_investigations:
            performance_cell = "investigate"
        elif performance_target_met:
            performance_cell = "target met"
        else:
            performance_cell = "target missed"

        cells = [
            fixture_id,
            status_cell("docker"),
            status_cell("apple-stock"),
            status_cell("container-compose"),
            ratio_cell("apple-stock"),
            ratio_cell("container-compose"),
            "yes" if functional_equivalent else "no",
            performance_cell,
        ]
        lines.append("| " + " | ".join(cells) + " |")

    payload = {
        "schemaVersion": 2,
        "status": "passed" if all_equivalent else "failed",
        "requireZeroFunctionalDifferences": True,
        "functionalParityStatus": (
            "passed" if all_functionally_equivalent else "failed"
        ),
        "performanceTargetMet": all_performance_targets_met,
        "performanceInvestigationRequired": any_performance_investigation,
        "performancePolicy": {
            "durationMetric": "fixture wall-clock seconds",
            "oracle": "docker",
            "targetFactor": PERFORMANCE_TARGET_FACTOR,
            "target": (
                "completed candidate duration is at most "
                f"{PERFORMANCE_TARGET_FACTOR:g}x Docker"
            ),
            "investigationFactor": PERFORMANCE_INVESTIGATION_FACTOR,
            "investigationRule": (
                "completed candidate duration is greater than "
                f"{PERFORMANCE_INVESTIGATION_FACTOR:g}x Docker"
            ),
            "failureRule": (
                "lane failure or missing or invalid timing evidence; "
                "completed slowdown alone does not alter functional parity"
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
