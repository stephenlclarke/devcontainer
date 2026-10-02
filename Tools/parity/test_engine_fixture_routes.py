#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# SPDX-License-Identifier: Apache-2.0

"""Closed parity Engine-fixture dispatch regressions."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from engine_fixture_routes import (
    ENGINE_FIXTURE_ROUTES,
    LEGACY_ENGINE_FIXTURES,
    OWNED_GUEST_FIXTURES,
    validate_engine_fixture_routes,
)


REPOSITORY = Path(__file__).resolve().parents[2]


class EngineFixtureRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = json.loads((REPOSITORY / "Tests/Parity/manifest.json").read_text())

    def test_manifest_has_complete_nonoverlapping_maintained_routes(self) -> None:
        routes = validate_engine_fixture_routes(self.manifest)
        self.assertEqual(set(routes), LEGACY_ENGINE_FIXTURES | OWNED_GUEST_FIXTURES)
        self.assertFalse(LEGACY_ENGINE_FIXTURES & OWNED_GUEST_FIXTURES)
        self.assertEqual({key for key, value in routes.items() if value == "legacy_engine"},
                         LEGACY_ENGINE_FIXTURES)
        self.assertEqual({key for key, value in routes.items() if value == "owned_guest"},
                         OWNED_GUEST_FIXTURES)
        self.assertEqual(len(ENGINE_FIXTURE_ROUTES), 16)

    def test_unknown_engine_row_fails_route_closure(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["fixtures"].append({
            "id": "E99-unimplemented",
            "status": "implemented",
            "runner": "engine",
            "backends": ["docker", "apple-stock", "container-compose"],
        })
        with self.assertRaisesRegex(ValueError, "has no maintained route"):
            validate_engine_fixture_routes(manifest)

    def test_known_probe_cannot_be_moved_to_an_unimplemented_runner(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        row = next(item for item in manifest["fixtures"] if item["id"] == "E07-init-attachment")
        row["runner"] = "devcontainer"
        with self.assertRaisesRegex(ValueError, "must use the engine runner"):
            validate_engine_fixture_routes(manifest)

    def test_missing_implemented_engine_route_fails_closed(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        row = next(item for item in manifest["fixtures"] if item["id"] == "E14-compose-terminal-size")
        row["status"] = "planned"
        with self.assertRaisesRegex(ValueError, "route closure differs"):
            validate_engine_fixture_routes(manifest)

