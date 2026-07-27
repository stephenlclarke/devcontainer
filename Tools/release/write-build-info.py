#!/usr/bin/env python3
"""Write deterministic release build metadata."""

from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = {
        "architecture": platform.machine(),
        "buildType": "release",
        "commit": args.commit,
        "lane": "stable",
        "provider": "none",
        "source": "stephenlclarke/devcontainer",
        "version": args.version,
    }
    args.output.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
