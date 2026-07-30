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

    def test_comparison_records_informational_slowdowns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for lane, duration in (
                ("docker", 2.0),
                ("apple-stock", 19.98),
                ("container-compose", 4.0),
            ):
                self.write_lane(root, lane, duration)

            result, markdown = compare(root)

            self.assertEqual(result["status"], "passed")
            fixture = result["fixtures"][0]
            self.assertEqual(fixture["relativeDurations"]["apple-stock"], 9.99)
            self.assertTrue(fixture["performanceEquivalent"])
            self.assertIn("9.990x", markdown)

    def test_comparison_fails_at_an_order_of_magnitude(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for lane, duration in (
                ("docker", 2.0),
                ("apple-stock", 4.0),
                ("container-compose", 20.0),
            ):
                self.write_lane(root, lane, duration)

            result, _ = compare(root)

            self.assertEqual(result["status"], "failed")
            fixture = result["fixtures"][0]
            self.assertFalse(fixture["performanceEquivalent"])
            self.assertIn(
                "container-compose duration is 10.000x Docker",
                fixture["performanceDifferences"][0],
            )

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
                result["fixtures"][0]["performanceDifferences"],
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
