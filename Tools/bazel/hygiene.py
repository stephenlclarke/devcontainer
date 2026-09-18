"""Remove only owned, expired invocation scratch whose exact bytes are retained."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time


SSD = Path("/Volumes/SSD/cf/bazel")
RETAINED = Path.home() / "Library/Application Support/ContainerFamily/retained/workflow"
FILES = {"owner.json", "events.json", "inputs-before.json", "inputs-after.json", "outcome.json", "qualification.json", "source-tests.json", "timing.json"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def owner(directory: Path, scratch: Path) -> dict:
    if directory.parent != scratch / "invocations" or directory.resolve() != directory or not re.fullmatch(r"run\.[A-Za-z0-9]+", directory.name):
        raise ValueError("Not an owned invocation path")
    marker = directory / "owner.json"
    if marker.is_symlink() or not marker.is_file():
        raise ValueError("Invocation has no ownership marker")
    value = json.loads(marker.read_text())
    if value.get("schemaVersion") != 1 or value.get("kind") != "bazel-invocation" or value.get("directory") != directory.name:
        raise ValueError("Invalid invocation ownership marker")
    if re.fullmatch(r"[0-9a-f]{64}", value.get("workspaceKey", "")) is None or type(value.get("created")) is not int:
        raise ValueError("Invalid ownership identity")
    return value


def register(directory: Path, workspace: str, scratch: Path = SSD) -> None:
    if directory.parent != scratch / "invocations" or directory.resolve() != directory or not re.fullmatch(r"run\.[A-Za-z0-9]+", directory.name):
        raise ValueError("Not a managed invocation directory")
    value = {"schemaVersion": 1, "kind": "bazel-invocation", "directory": directory.name,
             "created": int(time.time()), "workspaceKey": digest(workspace.encode())}
    with (directory / "owner.json").open("x") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")


def verify_retained(directory: Path, database: Path, scratch: Path, cutoff: int) -> dict:
    """Authenticate every file against a sealed receipt before even planning deletion."""
    value = owner(directory, scratch)
    if value["created"] > cutoff:
        raise ValueError("Invocation has not expired")
    files = list(directory.iterdir())
    if any(path.name not in FILES or path.is_symlink() or not path.is_file() for path in files):
        raise ValueError("Unknown or non-regular invocation contents")
    hashes = {path.name: digest(path.read_bytes()) for path in files}
    if database.is_symlink() or not database.is_file():
        raise ValueError("No safe retained evidence database")
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as db:
        # The marker binds this exact run directory, not just a repeated command.
        rows = db.execute("SELECT id, manifest FROM invocations")
        for identifier, raw in rows:
            manifest = json.loads(raw)
            if manifest.get("owner.json") != hashes["owner.json"]:
                continue
            if not {"owner.json", "events.json", "outcome.json", "inputs-before.json", "inputs-after.json"} <= set(manifest):
                raise ValueError("Invocation has incomplete retained evidence")
            if any(manifest.get(name) != sha for name, sha in hashes.items()):
                raise ValueError("Scratch differs from retained evidence")
            # Verify all referenced bytes, including XML, coverage and archives
            # outside this directory, before removing the last scratch receipt.
            for expected in set(manifest.values()):
                blob = db.execute("SELECT bytes FROM blobs WHERE sha256=?", (expected,)).fetchone()
                if blob is None or digest(blob[0]) != expected:
                    raise ValueError("Retained evidence integrity failure")
            return {**value, "invocation": identifier, "files": sorted(hashes)}
    raise ValueError("Invocation has no matching retained ownership receipt")


def remove_verified(directory: Path, database: Path, scratch: Path, cutoff: int) -> dict:
    """Caller must hold the recorded workspace lease; never recurse or follow links."""
    value = verify_retained(directory, database, scratch, cutoff)
    # Keep the marker until last. A partial interrupted deletion can be retried:
    # absent scratch is harmless when the complete sealed bytes still validate.
    for name in value["files"]:
        if name != "owner.json":
            (directory / name).unlink()
    (directory / "owner.json").unlink()
    directory.rmdir()
    return {"removed": str(directory), "recoverableFrom": value["invocation"]}


def cleanup(scratch: Path, database: Path, days: int, apply: bool) -> list[dict]:
    if days < 0 or scratch.resolve() != scratch or any((scratch / name).resolve() != scratch / name for name in ("locks", "invocations")):
        raise ValueError("Invalid cleanup age or scratch root")
    cutoff = int(time.time()) - days * 86400
    results = []
    for directory in sorted((scratch / "invocations").iterdir()):
        try:
            value = verify_retained(directory, database, scratch, cutoff)
        except (ValueError, OSError, json.JSONDecodeError) as error:
            # Unknown/incomplete legacy directories are reported, never adopted.
            results.append({"preserved": str(directory), "reason": str(error)})
            continue
        if not apply:
            results.append({"eligible": str(directory), "invocation": value["invocation"]})
            continue
        lease = scratch / "locks" / (value["workspaceKey"] + ".lock")
        if lease.is_symlink():
            raise ValueError("Refusing symlinked workspace lease")
        with lease.open("a+") as handle:
            try:
                # macOS lockf(1) and this flock(2) operation share the same lease.
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                results.append({"preserved": str(directory), "reason": "Workspace lease is active"})
                continue
            try:
                results.append(remove_verified(directory, database, scratch, cutoff))
            except (ValueError, OSError) as error:
                results.append({"preserved": str(directory), "reason": str(error)})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    registration = sub.add_parser("register")
    registration.add_argument("directory", type=Path)
    registration.add_argument("workspace")
    clean = sub.add_parser("cleanup")
    clean.add_argument("--days", type=int, default=14)
    clean.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    database = RETAINED / "bazel-evidence.sqlite"
    if args.command == "register":
        register(args.directory, args.workspace)
    else:
        print(json.dumps(cleanup(SSD, database, args.days, args.apply), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
