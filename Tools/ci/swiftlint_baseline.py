#!/usr/bin/env python3
"""Make SwiftLint baselines portable across repository checkout paths."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT_TOKEN = "__DEVCONTAINER_ROOT__"
SOURCE_MARKERS = ("Sources/", "Tests/")


def repository_relative_path(value: str) -> Path:
    path = unquote(urlparse(value).path)
    for marker in SOURCE_MARKERS:
        index = path.find(f"/{marker}")
        if index >= 0:
            return Path(path[index + 1 :])
    raise ValueError(f"baseline path is not first-party Swift source: {value}")


def rewrite_baseline(
    document: list[dict[str, object]],
    root: Path | None,
) -> list[dict[str, object]]:
    for entry in document:
        violation = entry.get("violation")
        if not isinstance(violation, dict):
            raise ValueError("baseline entry has no violation object")
        location = violation.get("location")
        if not isinstance(location, dict) or not isinstance(location.get("file"), str):
            raise ValueError("baseline violation has no file location")
        relative_path = repository_relative_path(location["file"])
        if root is None:
            location["file"] = f"file:///{ROOT_TOKEN}/{relative_path.as_posix()}"
        else:
            location["file"] = (root.resolve() / relative_path).as_uri()
    return document


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path)
    parser.add_argument(
        "--canonical",
        action="store_true",
        help="replace checkout roots with the repository-independent token",
    )
    args = parser.parse_args()
    if args.canonical == (args.root is not None):
        parser.error("choose exactly one of --canonical or --root")

    document = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(document, list):
        raise ValueError("SwiftLint baseline must be a JSON array")
    rewritten = rewrite_baseline(
        document,
        root=None if args.canonical else args.root,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(rewritten, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
