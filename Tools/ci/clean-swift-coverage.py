#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Remove stale coverage output only from the selected SwiftPM product directory."""

from __future__ import annotations

import argparse
from pathlib import Path
import stat

ROOT = Path(__file__).resolve().parents[2]
PROFILE_OUTPUTS = frozenset({"default.profdata", "devcontainer.json"})


def physical_directory(path: Path, label: str, *, allow_missing: bool = False) -> Path:
    absolute = path if path.is_absolute() else Path.cwd() / path
    resolved = absolute.resolve(strict=not allow_missing)
    if absolute != resolved:
        raise ValueError(f"{label} must be an existing physical directory")
    existing = absolute
    while not existing.exists() and not existing.is_symlink():
        existing = existing.parent
    if (existing.is_symlink() or not existing.is_dir()
            or existing.resolve(strict=True) != existing
            or absolute.exists() and not absolute.is_dir()):
        raise ValueError(f"{label} has a nonphysical or non-directory parent")
    if not allow_missing and not resolved.is_dir():
        raise ValueError(f"{label} must be an existing physical directory")
    return resolved


def clean_profiles(scratch_root: Path, bin_directory: Path) -> tuple[str, ...]:
    """Delete generated profile files under one exact build's codecov directory."""
    repository = ROOT.resolve(strict=True)
    scratch = physical_directory(scratch_root, "SwiftPM scratch path")
    if not scratch.is_relative_to(repository):
        raise ValueError("coverage paths must remain inside the selected repository scratch")
    binary = physical_directory(bin_directory, "SwiftPM binary path", allow_missing=True)
    if not binary.is_relative_to(scratch):
        raise ValueError("SwiftPM binary path must remain inside selected scratch")

    profile_directory = binary / "codecov"
    if profile_directory.is_symlink():
        raise ValueError("coverage output directory must not be a symlink")
    if not profile_directory.exists():
        return ()
    if not profile_directory.is_dir() or profile_directory.resolve(strict=True) != profile_directory:
        raise ValueError("coverage output directory must be physical")

    removed = []
    for path in profile_directory.iterdir():
        if path.name not in PROFILE_OUTPUTS and not path.name.endswith(".profraw"):
            continue
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("coverage output must be a regular file: " + path.name)
        path.unlink()
        removed.append(path.name)
    return tuple(sorted(removed))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-root", required=True, type=Path)
    parser.add_argument("--bin-directory", required=True, type=Path)
    args = parser.parse_args()
    clean_profiles(args.scratch_root, args.bin_directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
