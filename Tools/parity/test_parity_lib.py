#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Tests for parity normalization and comparison."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from compare_results import compare, expected_fixtures
from parity_lib import ParityError, parse_observations


class ParityLibraryTests(unittest.TestCase):
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
            self.assertEqual(result["schemaVersion"], 4)
            self.assertTrue(result["performanceInvestigationRequired"])
            fixture = result["fixtures"][0]
            self.assertEqual(fixture["relativeDurations"]["apple-stock"], 2.51)
            self.assertEqual(fixture["providerToStockRatio"], 0.797)
            self.assertTrue(fixture["functionalEquivalent"])
            self.assertFalse(fixture["performanceTargetMet"])
            self.assertTrue(fixture["performanceInvestigationRequired"])
            self.assertIn(
                "apple-stock duration is 2.510x Docker",
                fixture["performanceInvestigations"][0],
            )
            self.assertIn("investigate", markdown)
            self.assertIn("Provider/Stock", markdown)

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

    def test_provider_to_stock_slowdown_uses_common_timing_policy(self) -> None:
        cases = ((9.999, "passed"), (10.0, "failed"))
        for ratio, expected_status in cases:
            with (
                self.subTest(ratio=ratio),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                self.write_lane(root, "docker", 10.0)
                self.write_lane(root, "apple-stock", 1.0)
                self.write_lane(root, "container-compose", ratio)

                result, _ = compare(root, {"D01"})

                fixture = result["fixtures"][0]
                self.assertEqual(result["status"], expected_status)
                self.assertEqual(fixture["providerToStockRatio"], ratio)
                self.assertEqual(
                    fixture["performanceAcceptancePassed"],
                    ratio < 10.0,
                )
                self.assertIn(
                    "stock Apple",
                    result["performancePolicy"]["failureRule"],
                )

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
