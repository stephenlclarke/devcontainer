#!/usr/bin/env python3
"""Remove only known repository-owned generated paths."""

from __future__ import annotations

import shutil
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    for relative in (
        ".build",
        "_site",
        "dist",
        "coverage.xml",
        "coverage.lcov",
    ):
        target = (root / relative).resolve()
        if target.parent != root and root not in target.parents:
            raise RuntimeError(f"refusing unsafe clean target: {target}")
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
