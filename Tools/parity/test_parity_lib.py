#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Tests for parity normalization and comparison."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from compare_results import compare
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
            result, markdown = compare(root)
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

            result, markdown = compare(root)

            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["schemaVersion"], 2)
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

            result, _ = compare(root)

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

            result, markdown = compare(root)

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

            result, markdown = compare(root)

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

            result, _ = compare(root)

            self.assertEqual(result["status"], "failed")
            self.assertIn(
                "missing or invalid duration: apple-stock",
                result["fixtures"][0]["timingDifferences"],
            )

    @staticmethod
    def write_lane(root: Path, lane: str, duration: float | None) -> None:
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
                    "status": "passed",
                    "fixtures": [fixture],
                }
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
