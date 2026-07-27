#!/usr/bin/env python3
"""Print the monotonically ordered Homebrew Current-channel version."""

from __future__ import annotations

import argparse
from typing import Sequence

from versioning import current_formula_version


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
