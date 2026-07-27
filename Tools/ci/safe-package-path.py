#!/usr/bin/env python3
"""Validate packaging paths before a bounded stage cleanup."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: safe-package-path.py STAGE DIST")
    root = Path.cwd().resolve()
    stage = Path(sys.argv[1]).resolve()
    dist = Path(sys.argv[2]).resolve()
    if dist != root / "dist" or stage.parent.parent != dist or stage.parent.name != "stage":
        raise SystemExit(f"refusing unsafe package path: {stage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
