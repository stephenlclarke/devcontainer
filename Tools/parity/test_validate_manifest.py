#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Tests for the differential parity manifest validator."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from validate_manifest import ManifestError, validate_manifest


class ValidateManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        manifest_path = Path(__file__).parents[2] / "Tests/Parity/manifest.json"
        cls.payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    def test_design_manifest_is_valid(self) -> None:
        validate_manifest(copy.deepcopy(self.payload))

    def test_release_rejects_planned_fixtures(self) -> None:
        with self.assertRaisesRegex(ManifestError, "cannot enter a stable release"):
            validate_manifest(copy.deepcopy(self.payload), release=True)

    def test_backend_omission_is_rejected(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["fixtures"][0]["backends"].remove("apple-compose")

        with self.assertRaisesRegex(ManifestError, "all three required backends"):
            validate_manifest(payload)

    def test_functional_difference_policy_cannot_be_disabled(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["releasePolicy"]["requireZeroFunctionalDifferences"] = False

        with self.assertRaisesRegex(ManifestError, "zero functional differences"):
            validate_manifest(payload)


if __name__ == "__main__":
    unittest.main()
