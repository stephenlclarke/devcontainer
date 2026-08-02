#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Tests for the specification coverage validator."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from validate_coverage import CoverageError, validate_coverage


class ValidateCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        repository = Path(__file__).parents[2]
        cls.coverage = json.loads(
            (repository / "Tests/Parity/spec-coverage.json").read_text(
                encoding="utf-8"
            )
        )
        cls.manifest = json.loads(
            (repository / "Tests/Parity/manifest.json").read_text(
                encoding="utf-8"
            )
        )

    def test_checked_coverage_is_complete(self) -> None:
        validate_coverage(
            copy.deepcopy(self.coverage),
            copy.deepcopy(self.manifest),
        )

    def test_injected_schema_property_fails_closed(self) -> None:
        coverage = copy.deepcopy(self.coverage)
        coverage["expectedProperties"].append("futureRuntimeControl")
        manifest = copy.deepcopy(self.manifest)

        with self.assertRaisesRegex(CoverageError, "lack coverage"):
            validate_coverage(coverage, manifest)

    def test_blocker_requires_owner_and_reason(self) -> None:
        coverage = copy.deepcopy(self.coverage)
        group = next(
            value
            for value in coverage["propertyCoverageGroups"]
            if value["status"] == "blocked"
        )
        group["blocker"] = ""
        manifest = copy.deepcopy(self.manifest)

        with self.assertRaisesRegex(CoverageError, "explicit blocker"):
            validate_coverage(coverage, manifest)

    def test_certification_requires_implemented_fixture(self) -> None:
        coverage = copy.deepcopy(self.coverage)
        group = next(
            value
            for value in coverage["propertyCoverageGroups"]
            if value["status"] == "certified"
        )
        group["fixtures"] = ["future-fixture"]
        manifest = copy.deepcopy(self.manifest)

        with self.assertRaisesRegex(CoverageError, "unknown fixtures"):
            validate_coverage(coverage, manifest)


if __name__ == "__main__":
    unittest.main()
