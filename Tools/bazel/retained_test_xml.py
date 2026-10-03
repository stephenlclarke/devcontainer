"""Read immutable test XML bound to one retained Bazel invocation."""

from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import UUID


DATABASE = Path.home() / "Library/Application Support/ContainerFamily/retained/workflow/bazel-evidence.sqlite"
REQUIRED_FILES = ("events.json", "inputs-before.json", "inputs-after.json", "outcome.json")


def digest(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def result_name(identity: dict, output: str = "test.xml") -> str:
    """Use the exact key written by retain_evidence.evidence_paths."""
    return f"{identity['label']}:{json.dumps(identity, sort_keys=True)}:{output}"


def read(invocation: Path, events: list[dict], database: Path = DATABASE) -> dict[str, bytes]:
    """Fail closed unless every XML blob belongs to this unchanged invocation."""
    if database.is_symlink() or not database.is_file():
        raise ValueError("Missing or symlinked retained test evidence database")
    starts = [event["started"] for event in events if "started" in event]
    try:
        identifier = starts[0]["uuid"]
        if len(starts) != 1 or str(UUID(identifier)) != identifier:
            raise ValueError
    except (IndexError, KeyError, TypeError, ValueError):
        raise ValueError("Test invocation has no unique Bazel UUID")
    requested = {}
    for event in events:
        if "testResult" not in event:
            continue
        identity = event["id"]["testResult"]
        outputs = [item for item in event["testResult"].get("testActionOutput", [])
                   if item.get("name") == "test.xml"]
        if len(outputs) != 1 or not isinstance(identity.get("label"), str):
            raise ValueError("Test result has no unique XML identity")
        key = result_name(identity)
        if key in requested:
            raise ValueError("Duplicate test XML result identity")
        requested[key] = identity
    if not requested:
        raise ValueError("Retained invocation has no test XML results")
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as connection:
        row = connection.execute("SELECT manifest, exit_code FROM invocations WHERE id=?",
                                 (identifier,)).fetchone()
        if row is None or row[1] != 0:
            raise ValueError("Missing or unsuccessful retained test invocation")
        manifest = json.loads(row[0])

        def blob(name: str) -> bytes:
            expected = manifest.get(name)
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError("Missing retained test evidence hash")
            selected = connection.execute("SELECT bytes FROM blobs WHERE sha256=?", (expected,)).fetchone()
            if selected is None or digest(selected[0]) != expected:
                raise ValueError("Missing or corrupt retained test evidence blob")
            return selected[0]

        for name in REQUIRED_FILES:
            if blob(name) != (invocation / name).read_bytes():
                raise ValueError("Retained test evidence differs from invocation")
        if [json.loads(line) for line in blob("events.json").splitlines()] != events:
            raise ValueError("Test events differ from retained invocation")
        outcome = json.loads(blob("outcome.json"))
        if outcome.get("bazel_exit_code") != 0 or outcome.get("validation_exit_code") != 0:
            raise ValueError("Retained test outcome did not pass")
        stored = {name for name in manifest if name.endswith(":test.xml")}
        if stored != set(requested):
            raise ValueError("Retained test XML inventory differs from BEP results")
        return {json.dumps(identity, sort_keys=True): blob(name)
                for name, identity in requested.items()}
