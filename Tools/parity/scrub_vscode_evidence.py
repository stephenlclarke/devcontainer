#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Fail closed after scrubbing credential-shaped VS Code evidence files."""

from __future__ import annotations

import argparse
from pathlib import Path

from parity_lib import atomic_json
from run_vscode import scrub_sensitive_evidence


def parse_args() -> argparse.Namespace:
    """Parse the lane evidence directory to validate."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    return parser.parse_args()


def main() -> int:
    """Scrub unsafe files and retain a value-free security result."""

    root = parse_args().evidence.resolve()
    root.mkdir(parents=True, exist_ok=True)
    detected, removed = scrub_sensitive_evidence(root)
    atomic_json(
        root / "security-scan.json",
        {
            "detectedNames": detected,
            "removedFiles": removed,
            "status": "failed" if detected else "passed",
        },
    )
    if detected:
        print(
            "VS Code evidence security scan failed; "
            f"removed {len(removed)} file(s)"
        )
        return 1
    print("VS Code evidence security scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
