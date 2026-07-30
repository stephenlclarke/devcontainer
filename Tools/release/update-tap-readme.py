#!/usr/bin/env python3
"""Install the source-owned devcontainer section into the Homebrew tap README."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence


START_MARKER = "<!-- devcontainer-docs:start -->"
END_MARKER = "<!-- devcontainer-docs:end -->"
SECTION_HEADING = "## Dev Containers For Apple container"


def documentation_section(source: str) -> str:
    """Extract the tap-ready section from the maintained design document."""

    start = source.find(SECTION_HEADING)
    if start < 0:
        raise ValueError(f"documentation source is missing {SECTION_HEADING}")
    return source[start:].strip()


def update_readme(readme: str, section: str) -> str:
    """Append or replace exactly one managed tap documentation block."""

    start_count = readme.count(START_MARKER)
    end_count = readme.count(END_MARKER)
    if start_count != end_count or start_count > 1:
        raise ValueError("tap README has malformed devcontainer documentation markers")
    block = f"{START_MARKER}\n{section.strip()}\n{END_MARKER}"
    if start_count == 0:
        return readme.rstrip() + "\n\n" + block + "\n"
    start = readme.index(START_MARKER)
    end = readme.index(END_MARKER, start) + len(END_MARKER)
    suffix = readme[end:].lstrip("\n")
    if suffix:
        return readme[:start] + block + "\n\n" + suffix
    return readme[:start] + block + "\n"


def parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--readme", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    args = parse_args(arguments)
    try:
        readme = args.readme.read_text(encoding="utf-8")
        source = args.source.read_text(encoding="utf-8")
        updated = update_readme(readme, documentation_section(source))
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
    args.readme.write_text(updated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
