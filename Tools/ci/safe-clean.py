#!/usr/bin/env python3
"""Remove only known repository-owned generated paths."""

from __future__ import annotations

import shutil
from pathlib import Path


GENERATED_PATHS = (
    ".build",
    ".scannerwork",
    "_serve",
    "_site",
    "dist",
    "tmp",
    "coverage.json",
    "coverage.lcov",
    "coverage.xml",
    "default.profdata",
    "default.profraw",
    ".DS_Store",
    "docs/.DS_Store",
)


def remove_generated_path(root: Path, target: Path) -> None:
    """Remove one generated path only when it resolves inside the repository."""

    resolved_root = root.resolve()
    resolved_target = target.resolve()
    if (
        resolved_target == resolved_root
        or resolved_root not in resolved_target.parents
    ):
        raise RuntimeError(f"refusing unsafe clean target: {resolved_target}")
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()


def main(root: Path | None = None) -> int:
    repository_root = root or Path(__file__).resolve().parents[2]
    for relative in GENERATED_PATHS:
        target = repository_root / relative
        remove_generated_path(repository_root, target)

    tools_root = repository_root / "Tools"
    if tools_root.is_dir():
        for target in sorted(tools_root.rglob("__pycache__"), reverse=True):
            remove_generated_path(repository_root, target)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
