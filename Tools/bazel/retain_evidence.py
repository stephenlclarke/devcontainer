"""Retain immutable Bazel evidence in SQLite; never schedule or retry work."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from urllib.parse import unquote, urlparse


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_client_environment(value: object) -> None:
    """Reject unsanitized legacy events rather than persist shell credentials."""
    allowed = {"HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "TMPDIR", "TMP", "TEMP",
               "DEVELOPER_DIR", "PYTHONDONTWRITEBYTECODE", "PWD", "SHLVL", "_",
               "DARWIN_USER_TEMP_DIR", "DARWIN_USER_CACHE_DIR", "__CF_USER_TEXT_ENCODING",
               "DEVCONTAINER_HOST_INTEGRATION"}
    if isinstance(value, dict):
        if value.get("optionName") == "client_env":
            assignment = value.get("optionValue", "")
            key = assignment.split("=", 1)[0]
            if key not in allowed:
                raise ValueError("Unsafe inherited environment in Bazel evidence; do not share raw logs")
            if key == "DEVCONTAINER_HOST_INTEGRATION" and assignment not in {key + "=0", key + "=1"}:
                raise ValueError("Invalid host integration environment")
        for child in value.values():
            validate_client_environment(child)
    elif isinstance(value, list):
        for child in value:
            validate_client_environment(child)
    elif isinstance(value, str) and value.startswith("--client_env="):
        validate_client_environment({"optionName": "client_env", "optionValue": value.removeprefix("--client_env=")})


def evidence_paths(events: list[dict], root: Path) -> dict[str, Path]:
    """Only retain declared test evidence and the combined coverage report."""
    result = {}
    for event in events:
        if "testResult" in event:
            label = event["id"]["testResult"]["label"]
            identity = json.dumps(event["id"]["testResult"], sort_keys=True)
            outputs = event["testResult"].get("testActionOutput", [])
        else:
            label, identity = "build", "build"
            outputs = event.get("buildToolLogs", {}).get("log", [])
        for output in outputs:
            if output.get("name") not in {"test.xml", "test.log", "test.lcov", "coverage.dat", "coverage_report.lcov"}:
                continue
            uri = urlparse(output.get("uri", ""))
            path = Path(unquote(uri.path)).resolve()
            if uri.scheme != "file" or uri.netloc or not path.is_relative_to(root.resolve()):
                raise ValueError("Evidence must refer to a local file on the enrolled SSD")
            if not path.is_file() or path.stat().st_size > 128 * 1024 * 1024:
                raise ValueError("Missing or oversized evidence file")
            name = f"{label}:{identity}:{output['name']}"
            if name in result and result[name] != path:
                raise ValueError("Conflicting evidence paths")
            result[name] = path
    return result


def artifact_paths(events: list[dict], root: Path) -> dict[str, Path]:
    """Retain only the explicit candidate target, not arbitrary dependency outputs."""
    if any(event.get("started", {}).get("command") in {"query", "cquery", "aquery", "info"} for event in events):
        return {}
    sets = {event["id"]["namedSet"]["id"]: event["namedSetOfFiles"] for event in events if "namedSetOfFiles" in event}
    pending = []
    requested = False
    for event in events:
        if event.get("id", {}).get("targetCompleted", {}).get("label") == "//:candidate_archive" and event.get("completed", {}).get("success"):
            requested = True
            for group in event["completed"].get("outputGroup", []):
                if group["name"] == "default":
                    pending.extend(item["id"] for item in group.get("fileSets", []))
    files, visited = {}, set()
    while pending:
        identifier = pending.pop()
        if identifier in visited:
            continue
        visited.add(identifier)
        if identifier not in sets:
            raise ValueError("Missing artifact output set")
        group = sets[identifier]
        pending.extend(item["id"] for item in group.get("fileSets", []))
        for item in group.get("files", []):
            uri = urlparse(item.get("uri", ""))
            path = Path(unquote(uri.path)).resolve()
            if uri.scheme != "file" or uri.netloc or not path.is_relative_to(root.resolve()):
                raise ValueError("Artifact must be local to the enrolled SSD")
            if path.name not in {"candidate_archive.tar.gz", "candidate_archive.json"} or not path.is_file() or path.stat().st_size > 512 * 1024 * 1024:
                raise ValueError("Unexpected, missing or oversized candidate artifact")
            key = "artifact:" + path.name
            if key in files and files[key] != path:
                raise ValueError("Conflicting candidate artifacts")
            files[key] = path
    if requested and set(files) != {"artifact:candidate_archive.tar.gz", "artifact:candidate_archive.json"}:
        raise ValueError("Incomplete candidate artifact outputs")
    return files


def retain(events_path: Path, database: Path, scratch_root: Path) -> str:
    """Commit an invocation and deduplicated bytes together, or neither."""
    event_bytes = events_path.read_bytes()
    events = [json.loads(line) for line in event_bytes.splitlines()]
    validate_client_environment(events)
    starts = [event["started"] for event in events if "started" in event]
    ends = [event["finished"] for event in events if "finished" in event]
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError("Cannot seal an incomplete invocation")
    invocation = starts[0].get("uuid")
    if not invocation:
        if starts[0].get("command") not in {"query", "info"}:
            raise ValueError("Build invocation is missing its identity")
        # These non-build commands omit UUID in Bazel 8's BEP. Their exact event
        # stream identifies a diagnostic receipt, never a passing build receipt.
        invocation = "diagnostic-" + digest(event_bytes)
    contents = {"events.json": event_bytes}
    contents.update({name: path.read_bytes() for name, path in evidence_paths(events, scratch_root).items()})
    contents.update({name: path.read_bytes() for name, path in artifact_paths(events, scratch_root).items()})
    for name in ("owner.json", "qualification.json", "source-tests.json", "inputs-before.json", "inputs-after.json", "outcome.json"):
        path = events_path.with_name(name)
        if path.is_file():
            contents[name] = path.read_bytes()
    manifest = json.dumps({name: digest(data) for name, data in sorted(contents.items())}, sort_keys=True)
    if database.is_symlink():
        raise ValueError("Refusing symlinked evidence database")
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA temp_store=MEMORY")
        db.execute("CREATE TABLE IF NOT EXISTS blobs (sha256 TEXT PRIMARY KEY, bytes BLOB NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS invocations (id TEXT PRIMARY KEY, manifest TEXT NOT NULL, exit_code INTEGER NOT NULL)")
        prior = db.execute("SELECT manifest FROM invocations WHERE id=?", (invocation,)).fetchone()
        if prior is not None and prior[0] != manifest:
            raise ValueError("Invocation already retained with different evidence")
        for data in contents.values():
            sha = digest(data)
            row = db.execute("SELECT bytes FROM blobs WHERE sha256=?", (sha,)).fetchone()
            if row is not None and digest(row[0]) != sha:
                raise ValueError("Retained evidence failed integrity verification")
            db.execute("INSERT OR IGNORE INTO blobs VALUES (?, ?)", (sha, data))
        outcome = json.loads(contents.get("outcome.json", b"{}"))
        status = ends[0]["exitCode"].get("code", 0) or outcome.get("validation_exit_code", 0)
        db.execute("INSERT OR IGNORE INTO invocations VALUES (?, ?, ?)", (invocation, manifest, status))
    return invocation


def restore_candidate(database: Path, invocation: str, scratch: Path) -> Path:
    """Restore authenticated final bytes only; never rebuild missing intermediates."""
    if database.is_symlink() or not database.is_file():
        raise ValueError("Missing or symlinked retained evidence database")
    names = {"candidate_archive.tar.gz", "candidate_archive.json"}
    contents = {}
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as db:
        row = db.execute("SELECT manifest, exit_code FROM invocations WHERE id=?", (invocation,)).fetchone()
        if row is None or row[1] != 0:
            raise ValueError("Candidate needs a successful retained invocation")
        manifest = json.loads(row[0])
        for name in sorted(names):
            expected = manifest.get("artifact:" + name)
            blob = db.execute("SELECT bytes FROM blobs WHERE sha256=?", (expected,)).fetchone()
            if blob is None or digest(blob[0]) != expected:
                raise ValueError("Missing or corrupt retained candidate bytes")
            contents[name] = blob[0]
    receipt = json.loads(contents["candidate_archive.json"])
    if receipt.get("archiveSHA256") != digest(contents["candidate_archive.tar.gz"]):
        raise ValueError("Candidate receipt does not match archive")
    parent = scratch / "restored"
    parent.mkdir(exist_ok=True)
    if parent.resolve() != parent:
        raise ValueError("Refusing symlinked restore destination")
    destination = parent / digest(invocation.encode())
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_dir() or {p.name for p in destination.iterdir()} != names:
            raise ValueError("Conflicting restored candidate")
        for name, data in contents.items():
            path = destination / name
            if path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != digest(data):
                raise ValueError("Conflicting restored candidate bytes")
        return destination
    # This staging directory is disposable SSD data; retained bytes are already
    # committed internally. An interrupted restore never changes the receipt.
    with tempfile.TemporaryDirectory(dir=scratch / "tmp", prefix="restore-") as temporary:
        stage = Path(temporary) / "candidate"
        stage.mkdir(mode=0o700)
        for name, data in contents.items():
            with (stage / name).open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        stage.rename(destination)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events", type=Path, nargs="?")
    parser.add_argument("--restore-candidate", metavar="INVOCATION")
    args = parser.parse_args()
    if bool(args.events) == bool(args.restore_candidate):
        parser.error("Supply events or --restore-candidate, not both")
    root = Path.home() / "Library/Application Support/ContainerFamily/retained/workflow"
    if root.resolve() != root or root.stat().st_dev != Path.home().stat().st_dev:
        raise ValueError("Retained evidence requires non-symlinked internal storage")
    scratch = Path("/Volumes/SSD/cf/bazel")
    os.umask(0o077)
    if args.restore_candidate:
        print(restore_candidate(root / "bazel-evidence.sqlite", args.restore_candidate, scratch))
        return
    if not args.events.resolve().is_relative_to(scratch):
        raise ValueError("Bazel events must reside on the enrolled SSD")
    identifier = retain(args.events, root / "bazel-evidence.sqlite", scratch)
    print(f"Retained Bazel invocation: {identifier}")


if __name__ == "__main__":
    main()
