#!/usr/bin/env python3
"""Print the monotonically ordered Homebrew Current-channel version."""

from __future__ import annotations

import argparse
import re
from typing import Sequence


SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")


def current_formula_version(run_number: str, commit: str) -> str:
    """Create a Current version from an Actions run number and source SHA."""

    if not run_number.isdecimal() or int(run_number) <= 0:
        raise ValueError(
            f"workflow run number must be a positive integer: {run_number}"
        )
    if not SHA_PATTERN.fullmatch(commit):
        raise ValueError(f"git commit must be a 40-character hexadecimal SHA: {commit}")
    return f"current.{int(run_number)}.{commit[:12].lower()}"


def parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-number", required=True)
    parser.add_argument("--commit", required=True)
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    args = parse_args(arguments)
    try:
        print(current_formula_version(args.run_number, args.commit))
    except ValueError as error:
        raise SystemExit(str(error)) from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
