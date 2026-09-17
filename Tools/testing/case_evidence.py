"""Immutable per-case evidence for Bazel runtime tests; no execution or scheduling."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import time


LANES = ("docker", "apple-stock", "container-compose")
IDENTITY_FIELDS = {"campaign", "fixture", "lane", "contractSHA256", "harnessSHA256",
                   "releaseSetSHA256", "runtimeSHA256"}


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def validate_identity(identity: dict) -> str:
    """A campaign is explicit; a new request cannot accidentally reuse an old run."""
    if set(identity) != IDENTITY_FIELDS or identity["lane"] not in LANES:
        raise ValueError("Invalid case identity fields or lane")
    for key in ("campaign", "fixture"):
        if not isinstance(identity[key], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", identity[key]):
            raise ValueError("Invalid campaign or fixture identifier")
    for key in IDENTITY_FIELDS - {"campaign", "fixture", "lane"}:
        if not isinstance(identity[key], str) or not re.fullmatch(r"[0-9a-f]{64}", identity[key]):
            raise ValueError("Missing exact case fingerprint: " + key)
    return digest(canonical(identity))


def validate_result(result: dict) -> None:
    """Do not turn absent cleanup, timing or observations into a passing case."""
    if set(result) != {"status", "observations", "durationsNS", "cleanup", "errors"}:
        raise ValueError("Incomplete or unexpected result fields")
    if result["status"] not in {"passed", "failed", "timeout", "interrupted"}:
        raise ValueError("Invalid case status")
    durations = result["durationsNS"]
    if not isinstance(durations, dict) or set(durations) != {"setup", "operation", "cleanup"}:
        raise ValueError("Missing phase timings")
    if any(type(value) is not int or value < 0 for value in durations.values()):
        raise ValueError("Invalid monotonic duration")
    observations = result["observations"]
    if not isinstance(observations, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                                               for key, value in observations.items()):
        raise ValueError("Observations must be exact string values")
    errors = result["errors"]
    if not isinstance(errors, list) or any(not isinstance(value, str) for value in errors):
        raise ValueError("Invalid failure evidence")
    cleanup = result["cleanup"]
    if not isinstance(cleanup, dict) or set(cleanup) != {"status", "remainingOwnedResources"}:
        raise ValueError("Missing explicit cleanup evidence")
    if cleanup["status"] not in {"passed", "failed", "unknown"}:
        raise ValueError("Invalid cleanup status")
    remaining = cleanup["remainingOwnedResources"]
    if not isinstance(remaining, list) or any(not isinstance(value, str) for value in remaining):
        raise ValueError("Invalid owned-resource inventory")
    if cleanup["status"] == "passed" and remaining:
        raise ValueError("Cleanup cannot pass with remaining owned resources")
    if result["status"] == "passed" and (not observations or errors or cleanup["status"] != "passed"):
        raise ValueError("A passing case requires observations, no errors and verified cleanup")


class CaseStore:
    """Seal each case transactionally so Bazel can resume independent results.

    The caller holds the host runtime lease across begin, execution, cleanup and
    finish. An unfinished case is deliberately not restarted by this store:
    resources and any surviving worker must be reconciled first.
    """

    def __init__(self, path: Path):
        if path.is_symlink() or path.parent.resolve() != path.parent:
            raise ValueError("Evidence storage must not follow symlinks")
        self.path = path
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, identity BLOB NOT NULL, result BLOB, sha256 TEXT)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA temp_store=MEMORY")
            with db:
                yield db
        finally:
            db.close()

    def begin(self, identity: dict) -> dict | None:
        key = validate_identity(identity)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT identity, result, sha256 FROM cases WHERE id=?", (key,)).fetchone()
            if row is None:
                db.execute("INSERT INTO cases (id, identity) VALUES (?, ?)", (key, canonical(identity)))
                return None
            return self.read_row(identity, row)

    @staticmethod
    def read_row(identity: dict, row: tuple) -> dict:
        if row[0] != canonical(identity):
            raise ValueError("Stored identity differs from the requested case")
        if row[1] is None:
            raise ValueError("Unfinished case: reconcile worker and owned resources before a new attempt")
        if digest(row[1]) != row[2]:
            raise ValueError("Corrupt case evidence")
        result = json.loads(row[1])
        validate_result(result)
        return result

    def finish(self, identity: dict, result: dict) -> None:
        key = validate_identity(identity)
        validate_result(result)
        data = canonical(result)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT identity, result, sha256 FROM cases WHERE id=?", (key,)).fetchone()
            if row is None or row[0] != canonical(identity):
                raise ValueError("Case must be admitted before results are sealed")
            if row[1] is not None:
                self.read_row(identity, row)
                if row[1] != data:
                    raise ValueError("Cannot overwrite a completed case, including a failure")
                return
            db.execute("UPDATE cases SET result=?, sha256=? WHERE id=?", (data, digest(data), key))


def compare_cases(records: list[dict], expected: dict[str, str]) -> dict:
    """Compare all required lanes without changing exit or observation semantics."""
    if not expected or any(not isinstance(key, str) or not isinstance(value, str) for key, value in expected.items()):
        raise ValueError("A nonempty exact contract is required")
    lanes = {}
    reference_identity = None
    for record in records:
        if set(record) != {"identity", "result"}:
            raise ValueError("Invalid case record")
        identity, result = record["identity"], record["result"]
        validate_identity(identity)
        validate_result(result)
        common = {key: value for key, value in identity.items() if key not in {"lane", "runtimeSHA256"}}
        if reference_identity is not None and common != reference_identity:
            raise ValueError("Cannot mix campaigns, fixture contracts, harnesses or release sets")
        if identity["contractSHA256"] != digest(canonical(expected)):
            raise ValueError("Expected observations differ from the admitted contract")
        reference_identity = common
        if identity["lane"] in lanes:
            raise ValueError("Duplicate lane evidence")
        lanes[identity["lane"]] = result
    if set(lanes) != set(LANES):
        raise ValueError("Every fixture requires all three lanes")
    differences = []
    for lane, result in lanes.items():
        if result["status"] != "passed":
            differences.append(lane + ": " + result["status"])
        if result["observations"] != expected:
            differences.append(lane + ": observations differ from the exact contract")
    oracle_ns = lanes["docker"]["durationsNS"]["operation"]
    ratios = {lane: result["durationsNS"]["operation"] / oracle_ns if oracle_ns else None
              for lane, result in lanes.items()}
    return {"functionalParity": not differences, "differences": differences, "operationRatios": ratios,
            "timingQualified": False,
            "timingNote": "Raw per-case durations; quiet paired campaign qualification is required separately."}


def run_case(store: CaseStore, identity: dict, expected: dict[str, str], setup, operation, cleanup) -> dict:
    """Execute exactly one admitted case; resume sealed results, never retry work.

    A host adapter must hold the runtime lease and validate its artifact/runtime
    fingerprint before entering. Cleanup is called even after partial setup.
    This boundary never launches a build or creates a new campaign implicitly.
    """
    if not expected or digest(canonical(expected)) != identity.get("contractSHA256"):
        raise ValueError("Expected observations differ from the admitted contract")
    prior = store.begin(identity)
    if prior is not None:
        return prior
    result = {"status": "passed", "observations": {}, "errors": [],
              "durationsNS": {"setup": 0, "operation": 0, "cleanup": 0},
              "cleanup": {"status": "unknown", "remainingOwnedResources": []}}
    for phase, callback in [("setup", setup), ("operation", operation), ("cleanup", cleanup)]:
        if phase == "operation" and result["status"] != "passed":
            continue
        started = time.monotonic_ns()
        try:
            value = callback()
            if phase == "operation":
                result["observations"] = value
                if value != expected:
                    result["status"] = "failed"
                    result["errors"].append("Exact contract observations differ")
            elif phase == "cleanup":
                result["cleanup"] = value
                if value != {"status": "passed", "remainingOwnedResources": []}:
                    result["status"] = "failed" if result["status"] == "passed" else result["status"]
                    result["errors"].append("Owned-resource cleanup was not verified")
        except (Exception, KeyboardInterrupt) as error:
            if result["status"] == "passed":
                result["status"] = "timeout" if isinstance(error, TimeoutError) else (
                    "interrupted" if isinstance(error, KeyboardInterrupt) else "failed")
            # Exception text can contain credentials/commands. Keep the safe
            # failure class here; an adapter owns its separately scrubbed log.
            result["errors"].append(phase + ": " + type(error).__name__)
        finally:
            result["durationsNS"][phase] = time.monotonic_ns() - started
    store.finish(identity, result)
    return result
