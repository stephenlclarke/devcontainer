"""Shared release-version validation and formatting."""

from __future__ import annotations

import re


SEMVER_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")


def require_semantic_version(value: str) -> str:
    """Return a strict semantic version or reject it."""

    if not SEMVER_PATTERN.fullmatch(value):
        raise ValueError(f"version must be MAJOR.MINOR.PATCH: {value}")
    return value


def require_commit(value: str) -> str:
    """Return a normalized full Git commit SHA or reject it."""

    if not SHA_PATTERN.fullmatch(value):
        raise ValueError(f"git commit must be a 40-character hexadecimal SHA: {value}")
    return value.lower()


def current_formula_version(run_number: str, commit: str) -> str:
    """Create a Current version from an Actions run number and source SHA."""

    if not run_number.isdecimal() or int(run_number) <= 0:
        raise ValueError(
            f"workflow run number must be a positive integer: {run_number}"
        )
    normalized_commit = require_commit(commit)
    return f"current.{int(run_number)}.{normalized_commit[:12]}"
