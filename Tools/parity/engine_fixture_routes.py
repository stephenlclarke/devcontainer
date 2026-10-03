#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# SPDX-License-Identifier: Apache-2.0

"""Closed dispatch contract for manifest-backed Engine fixtures."""

from __future__ import annotations

from typing import Any

LEGACY_ENGINE_FIXTURES = frozenset({
    "E01-engine-negotiation",
    "E02-container-lifecycle",
    "E03-exec-streams",
    "E04-image-build",
    "E05-archive-copy",
    "E06-network-volume",
    "F01-fault-recovery",
})

OWNED_GUEST_FIXTURES = frozenset({
    "E07-init-attachment",
    "E08-foreground-terminal",
    "E09-compose-foreground",
    "E10-compose-quiet",
    "E11-compose-redirected",
    "E12-compose-tty-input",
    "E13-compose-signals",
    "E14-compose-terminal-size",
    "E15-initial-terminal-size",
})

ENGINE_FIXTURE_ROUTES = {
    **{identifier: "legacy_engine" for identifier in LEGACY_ENGINE_FIXTURES},
    **{identifier: "owned_guest" for identifier in OWNED_GUEST_FIXTURES},
}


def validate_engine_fixture_routes(manifest: dict[str, Any]) -> dict[str, str]:
    """Require every implemented Engine fixture to have one maintained route."""

    entries = manifest.get("fixtures")
    if not isinstance(entries, list):
        raise ValueError("parity manifest fixtures must be a list")
    seen: set[str] = set()
    active_engine: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("parity manifest fixture entries must be objects")
        identifier = entry.get("id")
        if not isinstance(identifier, str):
            raise ValueError("parity fixture identifier must be a string")
        if identifier in seen:
            raise ValueError(f"duplicate parity fixture identifier {identifier!r}")
        seen.add(identifier)
        if entry.get("status") != "implemented":
            continue
        if identifier in ENGINE_FIXTURE_ROUTES and entry.get("runner", "devcontainer") != "engine":
            raise ValueError(f"{identifier} must use the engine runner")
        if entry.get("runner") != "engine":
            continue
        if identifier not in ENGINE_FIXTURE_ROUTES:
            raise ValueError(f"implemented engine fixture {identifier!r} has no maintained route")
        active_engine.add(identifier)
    expected = set(ENGINE_FIXTURE_ROUTES)
    if active_engine != expected:
        missing = sorted(expected - active_engine)
        extra = sorted(active_engine - expected)
        raise ValueError(f"engine fixture route closure differs (missing={missing}, extra={extra})")
    return dict(ENGINE_FIXTURE_ROUTES)

