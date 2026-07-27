#!/usr/bin/env python3
"""Write deterministic release build metadata."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from versioning import require_commit, require_semantic_version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument(
        "--lane",
        choices=("development", "current", "stable"),
        required=True,
    )
    parser.add_argument("--architecture", choices=("arm64", "x86_64"), required=True)
    parser.add_argument("--container-distribution", default="apple")
    parser.add_argument("--provider", default="none")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        version = require_semantic_version(args.version)
        commit = (
            require_commit(args.commit)
            if args.commit != "unspecified"
            else args.commit
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error
    value = {
        "architecture": args.architecture,
        "buildType": (
            "development" if args.lane == "development" else "release"
        ),
        "commit": commit,
        "containerDistribution": args.container_distribution,
        "lane": args.lane,
        "provider": args.provider,
        "source": "stephenlclarke/devcontainer",
        "version": version,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
