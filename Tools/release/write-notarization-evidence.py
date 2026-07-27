#!/usr/bin/env python3
"""Validate notarytool output and retain only non-secret release evidence."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Sequence


IDENTIFIER_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def sanitized_evidence(document: object, archive_digest: str) -> dict[str, str]:
    """Return the accepted submission identity without retaining credentials."""

    if not isinstance(document, dict):
        raise ValueError("notarytool output must be a JSON object")
    identifier = document.get("id")
    status = document.get("status")
    if not isinstance(identifier, str) or not IDENTIFIER_PATTERN.fullmatch(
        identifier
    ):
        raise ValueError("notarytool output is missing a valid submission id")
    if status != "Accepted":
        raise ValueError(f"notary submission was not accepted: {status!r}")
    if not DIGEST_PATTERN.fullmatch(archive_digest):
        raise ValueError("notarization archive digest must be lowercase SHA-256")
    return {
        "archiveSHA256": archive_digest,
        "id": identifier.lower(),
        "status": status,
    }


def parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    args = parse_args(arguments)
    try:
        document = json.loads(args.input.read_text(encoding="utf-8"))
        evidence = sanitized_evidence(document, args.archive_sha256)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
