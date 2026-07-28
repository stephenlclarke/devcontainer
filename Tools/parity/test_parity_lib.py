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


if __name__ == "__main__":
    unittest.main()
