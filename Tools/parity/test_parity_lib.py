#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Tests for parity normalization and comparison."""

from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from compare_results import compare, expected_fixtures
from parity_lib import ParityError, parse_observations


class ParityLibraryTests(unittest.TestCase):
    def test_e13_compares_closed_signal_stream_measurement_across_all_lanes(self) -> None:
        raw = b"compose-stdout\nsignal:USR1\nsignal:TERM\n"
        stream = {"stdoutSHA256": hashlib.sha256(raw).hexdigest(),
                  "signals": ["SIGUSR1", "SIGTERM"],
                  "counts": {"SIGUSR1": 1, "SIGTERM": 1}}
        contract = {"modeVersion": 2, "tty": True, "openStdin": False}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for lane in ("docker", "apple-stock", "container-compose"):
                directory = root / lane
                directory.mkdir()
                (directory / "results.json").write_text(json.dumps({
                    "backend": lane, "status": "passed", "fixtures": [{
                        "id": "E13-compose-signals", "status": "passed", "durationSeconds": 1,
                        "observations": {"usr1_forwarded": "true"}, "signalStream": stream,
                        "signalContract": contract,
                    }],
                }), encoding="utf-8")
            result, _ = compare(root, {"E13-compose-signals"})
            self.assertEqual(result["status"], "passed")

            for mutation, message in (
                (lambda row: row.pop("signalStream"), "missing or invalid"),
                (lambda row: row["signalStream"].update(counts={"SIGUSR1": 2, "SIGTERM": 1}), "missing or invalid"),
                (lambda row: row["signalStream"].update(extra=True), "missing or invalid"),
                (lambda row: row["signalStream"].update(counts={"SIGUSR1": True, "SIGTERM": 1}), "missing or invalid"),
                (lambda row: row["signalStream"].update(counts={"SIGUSR1": 10**100, "SIGTERM": 1}), "missing or invalid"),
            ):
                candidate = root / "container-compose" / "results.json"
                payload = json.loads(candidate.read_text(encoding="utf-8"))
                mutation(payload["fixtures"][0])
                candidate.write_text(json.dumps(payload), encoding="utf-8")
                result, _ = compare(root, {"E13-compose-signals"})
                self.assertEqual(result["status"], "failed")
                self.assertTrue(any(message in value for value in result["fixtures"][0]["functionalDifferences"]))
                payload["fixtures"][0]["signalStream"] = stream
                candidate.write_text(json.dumps(payload), encoding="utf-8")

            candidate = root / "container-compose" / "results.json"
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            payload["fixtures"][0]["signalStream"] = {
                "stdoutSHA256": hashlib.sha256(
                    b"compose-stdout\nsignal:USR1\nsignal:USR1\nsignal:TERM\n").hexdigest(),
                "signals": ["SIGUSR1", "SIGUSR1", "SIGTERM"],
                "counts": {"SIGUSR1": 2, "SIGTERM": 1},
            }
            candidate.write_text(json.dumps(payload), encoding="utf-8")
            result, _ = compare(root, {"E13-compose-signals"})
            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("signal-stream evidence is missing or invalid" in item
                                for item in result["fixtures"][0]["functionalDifferences"]))

            for bad in (None, {"modeVersion": 1, "tty": True, "openStdin": False},
                        {"modeVersion": 2, "tty": 1, "openStdin": False},
                        {"modeVersion": 2, "tty": False, "openStdin": False},
                        {"modeVersion": 2, "tty": True, "openStdin": True}):
                payload["fixtures"][0]["signalStream"] = stream
                if bad is None:
                    payload["fixtures"][0].pop("signalContract")
                else:
                    payload["fixtures"][0]["signalContract"] = bad
                candidate.write_text(json.dumps(payload), encoding="utf-8")
                result, _ = compare(root, {"E13-compose-signals"})
                self.assertEqual(result["status"], "failed")
                self.assertTrue(any("TTY signal contract is missing or invalid" in item
                                    for item in result["fixtures"][0]["functionalDifferences"]))

    def test_finalized_comparison_requires_same_stock_package_and_fingerprints(self) -> None:
        identity = {"scope": "finalized-native-package-runtime-input",
                    "kind": "signed-notarized-native-package", "sourceCommit": "a" * 40,
                    "runtimeProfile": "stock", "archiveSize": 1,
                    "productionBinarySHA256": {name: "b" * 64 for name in (
                        "bin/devcontainer", "bin/devcontainer-compose", "bin/devcontainer-engine",
                        "bin/devcontainer-docker", "libexec/container/plugins/devcontainer/bin/devcontainer",
                        "libexec/devcontainer/reference/node")},
                    "referenceRuntimeFiles": {name: "c" * 64 for name in (
                        "node", "NODE-LICENSE.txt", "runtime-lock.json", "cli/devcontainer.js",
                        "cli/dist/spec-node/devContainersSpecCLI.js", "cli/scripts/updateUID.Dockerfile",
                        "cli/package.json", "cli/LICENSE.txt", "cli/ThirdPartyNotices.txt")}}
        for field in ("candidateReceiptSHA256", "finalizationProvenanceSHA256", "trustedStateSHA256",
                      "archiveSHA256", "preparationSHA256", "inventorySHA256", "signatureInventorySHA256"):
            identity[field] = "d" * 64
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for lane in ("docker", "apple-stock", "container-compose"):
                self.write_lane(root, lane, 1.0)
                provider = {} if lane == "docker" else {"DEVCONTAINER_CONTAINER_BIN": "e" * 64}
                if lane == "container-compose":
                    provider["DEVCONTAINER_COMPOSE_BIN"] = "f" * 64
                path = root / lane / "results.json"
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload.update(finalizedPackage=identity, providerBinarySHA256=provider,
                               parityHarnessSHA256="9" * 64)
                path.write_text(json.dumps(payload), encoding="utf-8")
                (root / lane / "fingerprint.json").write_text(
                    json.dumps({"backend": lane, "finalizedPackage": identity,
                                "providerBinarySHA256": provider,
                                "parityHarnessSHA256": "9" * 64}), encoding="utf-8")
            result, _ = compare(root, {"D01"})
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["finalizedPackage"], identity)
            path = root / "container-compose" / "results.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["finalizedPackage"] = dict(identity, archiveSHA256="0" * 64)
            path.write_text(json.dumps(payload), encoding="utf-8")
            result, _ = compare(root, {"D01"})
            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("package differs" in value for value in result["evidenceErrors"]))
            payload["finalizedPackage"] = identity
            path.write_text(json.dumps(payload), encoding="utf-8")
            fingerprint_path = root / "apple-stock" / "fingerprint.json"
            fingerprint = json.loads(fingerprint_path.read_text(encoding="utf-8"))
            fingerprint["finalizedPackage"] = dict(identity, archiveSHA256="0" * 64)
            fingerprint_path.write_text(json.dumps(fingerprint), encoding="utf-8")
            result, _ = compare(root, {"D01"})
            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("fingerprint differs" in value for value in result["evidenceErrors"]))
            fingerprint_path.write_text("[]", encoding="utf-8")
            result, _ = compare(root, {"D01"})
            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("fingerprint differs" in value for value in result["evidenceErrors"]))
            path = root / "docker" / "results.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            del payload["finalizedPackage"]
            path.write_text(json.dumps(payload), encoding="utf-8")
            result, _ = compare(root, {"D01"})
            self.assertEqual(result["status"], "failed")

    def test_observations_are_strict_and_lossless(self) -> None:
        self.assertEqual(
            parse_observations("alpha=one\nempty=\nwith_equals=a=b\n"),
            {"alpha": "one", "empty": "", "with_equals": "a=b"},
        )

    def test_duplicate_observations_are_rejected(self) -> None:
        with self.assertRaisesRegex(ParityError, "duplicate"):
            parse_observations("alpha=one\nalpha=two\n")

    def test_comparison_requires_exact_oracle_equivalence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for lane, value in (
                ("docker", "oracle"),
                ("apple-stock", "oracle"),
                ("container-compose", "different"),
            ):
                directory = root / lane
                directory.mkdir()
                (directory / "results.json").write_text(
                    json.dumps(
                        {
                            "backend": lane,
                            "status": "passed",
                            "fixtures": [
                                {
                                    "durationSeconds": 1.0,
                                    "id": "D01",
                                    "status": "passed",
                                    "observations": {"value": value},
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
            result, markdown = compare(root, {"D01"})
            self.assertEqual(result["status"], "failed")
            self.assertIn("container-compose provider", markdown)

    def test_comparison_flags_slowdowns_without_failing_functional_parity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for lane, duration in (
                ("docker", 2.0),
                ("apple-stock", 5.02),
                ("container-compose", 4.0),
            ):
                self.write_lane(root, lane, duration)

            result, markdown = compare(root, {"D01"})

            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["schemaVersion"], 3)
            self.assertTrue(result["performanceInvestigationRequired"])
            fixture = result["fixtures"][0]
            self.assertEqual(fixture["relativeDurations"]["apple-stock"], 2.51)
            self.assertTrue(fixture["functionalEquivalent"])
            self.assertFalse(fixture["performanceTargetMet"])
            self.assertTrue(fixture["performanceInvestigationRequired"])
            self.assertIn(
                "apple-stock duration is 2.510x Docker",
                fixture["performanceInvestigations"][0],
            )
            self.assertIn("investigate", markdown)

    def test_comparison_does_not_investigate_exactly_at_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for lane, duration in (
                ("docker", 2.0),
                ("apple-stock", 5.0),
                ("container-compose", 2.0),
            ):
                self.write_lane(root, lane, duration)

            result, _ = compare(root, {"D01"})

            self.assertEqual(result["status"], "passed")
            self.assertFalse(result["performanceInvestigationRequired"])
            fixture = result["fixtures"][0]
            self.assertFalse(fixture["performanceTargetMet"])
            self.assertFalse(fixture["performanceInvestigationRequired"])

    def test_comparison_records_comparable_or_better_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for lane, duration in (
                ("docker", 2.0),
                ("apple-stock", 1.9),
                ("container-compose", 2.0),
            ):
                self.write_lane(root, lane, duration)

            result, markdown = compare(root, {"D01"})

            self.assertEqual(result["status"], "passed")
            self.assertTrue(result["performanceTargetMet"])
            self.assertFalse(result["performanceInvestigationRequired"])
            self.assertIn("target met", markdown)

    def test_comparison_records_target_miss_below_investigation_threshold(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for lane, duration in (
                ("docker", 2.0),
                ("apple-stock", 3.0),
                ("container-compose", 4.0),
            ):
                self.write_lane(root, lane, duration)

            result, markdown = compare(root, {"D01"})

            self.assertEqual(result["status"], "passed")
            self.assertFalse(result["performanceTargetMet"])
            self.assertFalse(result["performanceInvestigationRequired"])
            self.assertIn("target missed", markdown)

    def test_comparison_requires_recorded_timings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for lane in ("docker", "apple-stock", "container-compose"):
                self.write_lane(
                    root,
                    lane,
                    None if lane == "apple-stock" else 1.0,
                )

            result, _ = compare(root, {"D01"})

            self.assertEqual(result["status"], "failed")
            self.assertIn(
                "missing or invalid duration: apple-stock",
                result["fixtures"][0]["timingDifferences"],
            )

    def test_comparison_rejects_empty_lane_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for lane in ("docker", "apple-stock", "container-compose"):
                directory = root / lane
                directory.mkdir()
                (directory / "results.json").write_text(
                    json.dumps({"backend": lane, "status": "passed", "fixtures": []}),
                    encoding="utf-8",
                )

            result, _ = compare(root, {"D01"})

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["evidenceStatus"], "failed")
            self.assertIn(
                "docker fixture evidence is empty or invalid",
                result["evidenceErrors"],
            )

    def test_comparison_rejects_failed_parent_lane(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for lane in ("docker", "apple-stock", "container-compose"):
                self.write_lane(
                    root,
                    lane,
                    1.0,
                    status="failed" if lane == "apple-stock" else "passed",
                )

            result, _ = compare(root, {"D01"})

            self.assertEqual(result["status"], "failed")
            self.assertIn(
                "apple-stock lane status is 'failed', expected 'passed'",
                result["evidenceErrors"],
            )

    def test_comparison_rejects_missing_fixture_in_every_lane(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for lane in ("docker", "apple-stock", "container-compose"):
                self.write_lane(root, lane, 1.0)

            result, _ = compare(root, {"D01", "D02"})

            self.assertEqual(result["status"], "failed")
            self.assertIn(
                "docker is missing expected fixtures: D02",
                result["evidenceErrors"],
            )

    def test_comparison_rejects_duplicate_and_unexpected_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for lane in ("docker", "apple-stock", "container-compose"):
                self.write_lane(root, lane, 1.0)
            docker_path = root / "docker" / "results.json"
            payload = json.loads(docker_path.read_text(encoding="utf-8"))
            payload["fixtures"] += [
                payload["fixtures"][0],
                {**payload["fixtures"][0], "id": "D99"},
            ]
            docker_path.write_text(json.dumps(payload), encoding="utf-8")

            result, _ = compare(root, {"D01"})

            self.assertEqual(result["status"], "failed")
            self.assertIn(
                "docker has duplicate fixture evidence for D01",
                result["evidenceErrors"],
            )
            self.assertIn(
                "docker has unexpected fixtures: D99",
                result["evidenceErrors"],
            )

    def test_timing_acceptance_boundary_is_ten_times(self) -> None:
        cases = ((9.999, "passed"), (10.0, "failed"), (100.0, "failed"))
        for ratio, expected_status in cases:
            with (
                self.subTest(ratio=ratio),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                self.write_lane(root, "docker", 1.0)
                self.write_lane(root, "apple-stock", ratio)
                self.write_lane(root, "container-compose", 1.0)

                result, _ = compare(root, {"D01"})

                self.assertEqual(result["status"], expected_status)
                self.assertEqual(result["functionalParityStatus"], "passed")
                self.assertEqual(
                    result["fixtures"][0]["performanceAcceptancePassed"],
                    ratio < 10.0,
                )

    def test_expected_fixtures_selects_cli_and_vscode_suites(self) -> None:
        manifest = Path(__file__).parents[2] / "Tests" / "Parity" / "manifest.json"

        cli = expected_fixtures(manifest, "cli")
        vscode = expected_fixtures(manifest, "vscode")

        self.assertIn("D01-image-config", cli)
        self.assertNotIn("V01-vscode-end-to-end", cli)
        self.assertEqual(vscode, {"V01-vscode-end-to-end"})

    @staticmethod
    def write_lane(
        root: Path,
        lane: str,
        duration: float | None,
        status: str = "passed",
    ) -> None:
        directory = root / lane
        directory.mkdir()
        fixture = {
            "id": "D01",
            "status": "passed",
            "observations": {"value": "oracle"},
        }
        if duration is not None:
            fixture["durationSeconds"] = duration
        (directory / "results.json").write_text(
            json.dumps(
                {
                    "backend": lane,
                    "status": status,
                    "fixtures": [fixture],
                }
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
