#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Tests for immutable parity input validation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from validate_fixture_pins import (
    FixturePinError,
    validate_feature_lock,
    validate_fixture_pins,
    validate_image,
)


class ValidateFixturePinsTests(unittest.TestCase):
    def test_checked_fixtures_are_immutable(self) -> None:
        repository = Path(__file__).parents[2]
        manifest = json.loads(
            (repository / "Tests/Parity/manifest.json").read_text(encoding="utf-8")
        )
        validate_fixture_pins(repository, manifest)

    def test_mutable_image_is_rejected(self) -> None:
        with self.assertRaisesRegex(FixturePinError, "immutable"):
            validate_image("alpine:latest", "fixture image")

    def test_feature_lock_must_match_requested_features(self) -> None:
        with self.assertRaisesRegex(FixturePinError, "exactly match"):
            validate_feature_lock(
                {"features": {"ghcr.io/example/feature:1": {}}},
                {"features": {}},
                "fixture",
            )

    def test_mutable_dockerfile_base_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            fixture = repository / "Tests/Parity/fixtures/D01"
            fixture.mkdir(parents=True)
            (fixture / "Dockerfile").write_text("FROM alpine:latest\n", encoding="utf-8")
            manifest = {
                "fixtures": [
                    {
                        "id": "D01",
                        "status": "implemented",
                    }
                ]
            }
            with self.assertRaisesRegex(FixturePinError, "immutable"):
                validate_fixture_pins(repository, manifest)


if __name__ == "__main__":
    unittest.main()
