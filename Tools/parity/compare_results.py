#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Compare normalized observations from all required parity lanes."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

from parity_lib import LANES, ParityError, atomic_json, load_manifest

PERFORMANCE_TARGET_FACTOR = 1.0
PERFORMANCE_INVESTIGATION_FACTOR = 2.5
PERFORMANCE_FAILURE_FACTOR = 10.0
FINALIZED_SHA_FIELDS = (
    "candidateReceiptSHA256", "finalizationProvenanceSHA256", "trustedStateSHA256",
    "archiveSHA256", "preparationSHA256", "inventorySHA256", "signatureInventorySHA256",
)
FINALIZED_BINARIES = {
    "bin/devcontainer", "bin/devcontainer-compose", "bin/devcontainer-engine",
    "bin/devcontainer-docker", "libexec/container/plugins/devcontainer/bin/devcontainer",
    "libexec/devcontainer/reference/node",
}
FINALIZED_REFERENCE = {
    "node", "NODE-LICENSE.txt", "runtime-lock.json", "cli/devcontainer.js",
    "cli/dist/spec-node/devContainersSpecCLI.js", "cli/scripts/updateUID.Dockerfile",
    "cli/package.json", "cli/LICENSE.txt", "cli/ThirdPartyNotices.txt",
}
SIGNAL_STREAM_PREFIX = b"compose-stdout\n"
SIGNAL_STREAM_USR1 = b"signal:USR1\n"
SIGNAL_STREAM_TERM = b"signal:TERM\n"


def valid_signal_stream(value: Any) -> bool:
    """Validate the closed, lossless E13 signal-stream measurement."""
    if not isinstance(value, dict) or set(value) != {"stdoutSHA256", "signals", "counts"}:
        return False
    digest = value["stdoutSHA256"]
    signals = value["signals"]
    counts = value["counts"]
    if (not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or signals != ["SIGUSR1", "SIGTERM"]
            or not isinstance(counts, dict) or set(counts) != {"SIGUSR1", "SIGTERM"}
            or type(counts["SIGUSR1"]) is not int or counts["SIGUSR1"] != 1
            or type(counts["SIGTERM"]) is not int or counts["SIGTERM"] != 1):
        return False
    expected_stdout = SIGNAL_STREAM_PREFIX + SIGNAL_STREAM_USR1 + SIGNAL_STREAM_TERM
    return hashlib.sha256(expected_stdout).hexdigest() == digest


def valid_signal_contract(value: Any) -> bool:
    """Admit only the measured TTY fixture, never older non-TTY E13 results."""
    return (isinstance(value, dict) and set(value) == {"modeVersion", "tty", "openStdin"}
            and type(value["modeVersion"]) is int and value["modeVersion"] == 2
            and type(value["tty"]) is bool and value["tty"] is True
            and type(value["openStdin"]) is bool and value["openStdin"] is False)


def compare_finalized_inputs(root: Path, lanes: dict[str, dict[str, Any]]) -> tuple[dict[str, Any] | None, list[str]]:
    """Require one authenticated stock package identity across all three lanes."""

    selected = {lane: payload.get("finalizedPackage") if isinstance(payload, dict) else None
                for lane, payload in lanes.items()}
    if all(value is None for value in selected.values()):
        return None, []
    errors: list[str] = []
    identity = selected.get("docker")
    if not isinstance(identity, dict):
        errors.append("Docker oracle lacks the common finalized package identity")
        return None, errors
    if (identity.get("scope") != "finalized-native-package-runtime-input"
            or identity.get("kind") != "signed-notarized-native-package"
            or identity.get("runtimeProfile") != "stock"
            or re.fullmatch(r"[0-9a-f]{40}", str(identity.get("sourceCommit", ""))) is None
            or any(re.fullmatch(r"[0-9a-f]{64}", str(identity.get(key, ""))) is None
                   for key in FINALIZED_SHA_FIELDS)
            or type(identity.get("archiveSize")) is not int or identity["archiveSize"] <= 0
            or not isinstance(identity.get("productionBinarySHA256"), dict)
            or set(identity["productionBinarySHA256"]) != FINALIZED_BINARIES
            or any(re.fullmatch(r"[0-9a-f]{64}", str(value)) is None
                   for value in identity["productionBinarySHA256"].values())
            or not isinstance(identity.get("referenceRuntimeFiles"), dict)
            or set(identity["referenceRuntimeFiles"]) != FINALIZED_REFERENCE
            or any(re.fullmatch(r"[0-9a-f]{64}", str(value)) is None
                   for value in identity["referenceRuntimeFiles"].values())):
        errors.append("common finalized package identity is incomplete")
    harness = lanes["docker"].get("parityHarnessSHA256")
    if re.fullmatch(r"[0-9a-f]{64}", str(harness)) is None:
        errors.append("Docker oracle lacks the finalized parity harness identity")
    for lane in LANES:
        if selected.get(lane) != identity:
            errors.append(f"{lane} finalized package differs from Docker comparison identity")
            continue
        path = root / lane / "fingerprint.json"
        if not path.is_file():
            errors.append(f"{lane} finalized fingerprint is missing")
            continue
        try:
            fingerprint = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            errors.append(f"{lane} finalized fingerprint is unreadable")
            continue
        if not isinstance(fingerprint, dict) or fingerprint.get("backend") != lane or fingerprint.get("finalizedPackage") != identity:
            errors.append(f"{lane} finalized fingerprint differs from results")
        provider = lanes[lane].get("providerBinarySHA256")
        if not isinstance(fingerprint, dict) or fingerprint.get("providerBinarySHA256") != provider:
            errors.append(f"{lane} provider fingerprint differs from results")
        if (lanes[lane].get("parityHarnessSHA256") != harness
                or not isinstance(fingerprint, dict)
                or fingerprint.get("parityHarnessSHA256") != harness):
            errors.append(f"{lane} parity harness differs from Docker comparison identity")
        expected = set() if lane == "docker" else {"DEVCONTAINER_CONTAINER_BIN"}
        if lane == "container-compose":
            expected.add("DEVCONTAINER_COMPOSE_BIN")
        if (not isinstance(provider, dict) or set(provider) != expected
                or any(re.fullmatch(r"[0-9a-f]{64}", str(value)) is None for value in provider.values())):
            errors.append(f"{lane} qualified provider identity is incomplete")
    return identity, errors


def expected_fixtures(manifest_path: Path, suite: str) -> set[str]:
    """Return the exact implemented fixture set for one comparison suite."""

    manifest = load_manifest(manifest_path)
    expected: set[str] = set()
    for entry in manifest.get("fixtures", []):
        if entry.get("status") != "implemented":
            continue
        runner = str(entry.get("runner", "devcontainer"))
        selected = runner == "vscode" if suite == "vscode" else runner != "vscode"
        if not selected:
            continue
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ParityError("implemented fixture has no valid id")
        if identifier in expected:
            raise ParityError(f"manifest has duplicate fixture id: {identifier}")
        backends = entry.get("backends")
        if not isinstance(backends, list) or set(backends) != set(LANES):
            raise ParityError(
                f"{identifier} must declare exactly the required parity lanes"
            )
        expected.add(identifier)
    if not expected:
        raise ParityError(f"manifest has no implemented {suite} fixtures")
    return expected


def recorded_duration(result: dict[str, Any] | None) -> float | None:
    """Return a valid wall-clock duration from one fixture result."""

    if result is None:
        return None
    value = result.get("durationSeconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    duration = float(value)
    return duration if math.isfinite(duration) and duration >= 0 else None


def compare(
    root: Path,
    expected_fixture_ids: set[str],
    suite: str = "cli",
) -> tuple[dict[str, Any], str]:
    """Load all lanes, enforce exact semantics, and return JSON and Markdown."""

    lane_results: dict[str, dict[str, Any]] = {}
    for lane in LANES:
        path = root / lane / "results.json"
        if not path.is_file():
            raise ParityError(f"missing lane evidence: {path}")
        lane_results[lane] = json.loads(path.read_text(encoding="utf-8"))

    if not expected_fixture_ids:
        raise ParityError("comparison requires at least one expected fixture")

    evidence_errors: list[str] = []
    finalized_identity, finalized_errors = compare_finalized_inputs(root, lane_results)
    evidence_errors.extend(finalized_errors)
    indexed_results: dict[str, dict[str, dict[str, Any]]] = {}
    for lane, payload in lane_results.items():
        if not isinstance(payload, dict):
            raise ParityError(f"{lane} lane evidence is not a JSON object")
        if payload.get("backend") != lane:
            evidence_errors.append(
                f"{lane} backend is {payload.get('backend')!r}, expected {lane!r}"
            )
        if payload.get("status") != "passed":
            evidence_errors.append(
                f"{lane} lane status is {payload.get('status')!r}, expected 'passed'"
            )
        fixtures = payload.get("fixtures")
        if not isinstance(fixtures, list) or not fixtures:
            evidence_errors.append(f"{lane} fixture evidence is empty or invalid")
            fixtures = []
        by_id: dict[str, dict[str, Any]] = {}
        for index, result in enumerate(fixtures):
            if not isinstance(result, dict):
                evidence_errors.append(f"{lane} fixture {index} is not an object")
                continue
            identifier = result.get("id")
            if not isinstance(identifier, str) or not identifier:
                evidence_errors.append(f"{lane} fixture {index} has no valid id")
                continue
            if identifier in by_id:
                evidence_errors.append(
                    f"{lane} has duplicate fixture evidence for {identifier}"
                )
                continue
            by_id[identifier] = result
            if identifier != "E13-compose-signals" and ("signalStream" in result or "signalContract" in result):
                evidence_errors.append(f"{lane} {identifier} has unexpected signal-stream evidence")
        actual = set(by_id)
        missing = sorted(expected_fixture_ids - actual)
        unexpected = sorted(actual - expected_fixture_ids)
        if missing:
            evidence_errors.append(
                f"{lane} is missing expected fixtures: {', '.join(missing)}"
            )
        if unexpected:
            evidence_errors.append(
                f"{lane} has unexpected fixtures: {', '.join(unexpected)}"
            )
        indexed_results[lane] = by_id

    fixture_ids = expected_fixture_ids
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
            f"a candidate at least {PERFORMANCE_FAILURE_FACTOR:.2f}x Docker fails "
            "timing acceptance without changing functional parity."
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
            by_lane[lane] = indexed_results[lane].get(fixture_id)
        missing = [lane for lane, result in by_lane.items() if result is None]
        statuses = {
            lane: result["status"] if result is not None else "missing"
            for lane, result in by_lane.items()
        }
        oracle = by_lane["docker"]
        functional_differences: list[str] = []
        timing_differences: list[str] = []
        performance_investigations: list[str] = []
        performance_failures: list[str] = []
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
        signal_streams: dict[str, Any] = {}
        if fixture_id == "E13-compose-signals":
            for lane, result in by_lane.items():
                contract = result.get("signalContract") if result is not None else None
                if not valid_signal_contract(contract):
                    functional_differences.append(f"{lane} E13 TTY signal contract is missing or invalid")
                stream = result.get("signalStream") if result is not None else None
                if not valid_signal_stream(stream):
                    functional_differences.append(f"{lane} E13 signal-stream evidence is missing or invalid")
                else:
                    signal_streams[lane] = stream
            if "docker" in signal_streams:
                for lane in ("apple-stock", "container-compose"):
                    if lane in signal_streams and signal_streams[lane] != signal_streams["docker"]:
                        functional_differences.append(f"{lane} E13 signal stream differs from docker")
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
                    if (
                        statuses[lane] == "passed"
                        and ratio >= PERFORMANCE_FAILURE_FACTOR
                    ):
                        performance_failures.append(
                            f"{lane} duration is {ratio:.3f}x Docker "
                            f"(failure: >={PERFORMANCE_FAILURE_FACTOR:g}x)"
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
        differences = (
            functional_differences + timing_differences + performance_failures
        )
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
                "performanceAcceptancePassed": not performance_failures,
                "performanceFailures": performance_failures,
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
        elif performance_failures:
            performance_cell = "failed (>=10x)"
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

    evidence_valid = not evidence_errors
    overall_passed = evidence_valid and all_equivalent
    timing_passed = all(
        fixture["timingEvidenceValid"]
        and fixture["performanceAcceptancePassed"]
        for fixture in comparisons
    )
    payload = {
        "schemaVersion": 3,
        "suite": suite,
        "status": "passed" if overall_passed else "failed",
        "evidenceStatus": "passed" if evidence_valid else "failed",
        "evidenceErrors": evidence_errors,
        "expectedFixtures": sorted(expected_fixture_ids),
        "requireZeroFunctionalDifferences": True,
        "functionalParityStatus": (
            "passed"
            if evidence_valid and all_functionally_equivalent
            else "failed"
        ),
        "timingStatus": "passed" if evidence_valid and timing_passed else "failed",
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
            "failureFactor": PERFORMANCE_FAILURE_FACTOR,
            "failureRule": (
                "lane failure, incomplete evidence, missing or invalid timing, "
                "or completed candidate duration at least "
                f"{PERFORMANCE_FAILURE_FACTOR:g}x Docker; timing failure does not "
                "alter functional parity"
            ),
        },
        "fixtures": comparisons,
    }
    if finalized_identity is not None:
        payload["finalizedPackage"] = finalized_identity
        payload["parityHarnessSHA256"] = lane_results["docker"].get("parityHarnessSHA256")
    return payload, "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--suite", choices=("cli", "vscode"), default="cli")
    args = parser.parse_args()
    try:
        fixture_ids = expected_fixtures(args.manifest, args.suite)
        payload, markdown = compare(args.evidence, fixture_ids, args.suite)
    except (OSError, ValueError, ParityError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    atomic_json(args.evidence / "comparison.json", payload)
    (args.evidence / "matrix.md").write_text(markdown, encoding="utf-8")
    print(markdown, end="")
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
