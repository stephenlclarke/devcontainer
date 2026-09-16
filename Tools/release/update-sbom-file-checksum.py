#!/usr/bin/env python3
"""Bind one SPDX package checksum to the exact staged file bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def update(sbom_path: Path, package_name: str, file_path: Path) -> str:
    document = json.loads(sbom_path.read_text(encoding="utf-8"))
    packages = document.get("packages") if isinstance(document, dict) else None
    if not isinstance(packages, list):
        raise ValueError("SPDX document is missing packages")
    matches = [
        package
        for package in packages
        if isinstance(package, dict) and package.get("name") == package_name
    ]
    if len(matches) != 1:
        raise ValueError(f"SPDX package {package_name!r} must appear exactly once")
    checksum = sha256(file_path)
    matches[0]["checksums"] = [{"algorithm": "SHA256", "checksumValue": checksum}]
    temporary = sbom_path.with_name(sbom_path.name + ".tmp")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(sbom_path)
    return checksum


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--file", type=Path, required=True)
    args = parser.parse_args()
    try:
        update(args.sbom, args.package, args.file)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
