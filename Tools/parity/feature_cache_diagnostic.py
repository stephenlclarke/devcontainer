#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Collect a separately labeled native D05 warm-cache diagnostic.

This module does not alter the timed fixture runner or qualify performance. A
caller may run an untimed official CLI build against the exact D05 workspace,
then ask this module to verify that both feature installer stages were served
from cache during a subsequent functional run. Any ambiguity is not comparable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

WARMUP_TIMEOUT_SECONDS = 1800
SUPPORTED_LANES = {"apple-stock", "container-compose"}
EXPECTED_D05_FILES = {
    "contract.json", ".devcontainer/devcontainer.json",
    ".devcontainer/devcontainer-lock.json", "probe.sh",
}
FEATURE_STAGE_NAMES = ("common-utils_0", "git_1")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_COMMIT = re.compile(r"^[0-9a-f]{40}$")


class DiagnosticError(ValueError):
    """The requested diagnostic is not bound to an admitted D05 input."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True) + "\n").encode()


def _write_exclusive(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _admission(identity: Mapping[str, Any]) -> dict[str, str]:
    scope = identity.get("scope")
    if scope not in {"finalized-native-package-runtime-input",
                     "local-candidate-integration-only"}:
        raise DiagnosticError("candidate identity is not an admitted native package")
    source = identity.get("sourceCommit")
    archive = identity.get("archiveSHA256", identity.get("assetSHA256"))
    receipt = identity.get("candidateReceiptSHA256")
    if (identity.get("runtimeProfile") != "stock"
            or not isinstance(source, str) or not _SOURCE_COMMIT.fullmatch(source)
            or not isinstance(archive, str) or not _SHA256.fullmatch(archive)
            or not isinstance(receipt, str) or not _SHA256.fullmatch(receipt)):
        raise DiagnosticError("native package identity is incomplete")
    return {"scope": scope, "sourceCommit": source,
            "packageSHA256": archive, "admissionReceiptSHA256": receipt}


def _fixture_identity(workspace: Path) -> dict[str, Any]:
    if workspace.is_symlink():
        raise DiagnosticError("D05 workspace must not be a symlink")
    root = workspace.resolve(strict=True)
    if not root.is_dir():
        raise DiagnosticError("D05 workspace must be a real directory")
    files: dict[str, dict[str, Any]] = {}
    found: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise DiagnosticError("D05 workspace contains an unexpected symlink")
        if path.is_dir():
            continue
        if not path.is_file():
            raise DiagnosticError("D05 workspace contains a non-regular file")
        found.add(relative)
        data = path.read_bytes()
        files[relative] = {"sha256": _sha(data), "mode": path.stat().st_mode & 0o777}
    if found != EXPECTED_D05_FILES:
        raise DiagnosticError("workspace inventory is not the exact D05 fixture")
    return {
        "files": files, "inventorySHA256": _sha(_json_bytes(files)),
        "workspacePathSHA256": _sha(str(root).encode()),
    }


def _validate_target(lane: str, fixture_id: str, workspace: Path,
                     identity: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    if lane not in SUPPORTED_LANES:
        raise DiagnosticError("warm-cache diagnostic is limited to native lanes")
    if fixture_id != "D05-features":
        raise DiagnosticError("warm-cache diagnostic is limited to D05-features")
    return _admission(identity), _fixture_identity(workspace)


def prepare_d05_feature_cache(
    *, lane: str, fixture_id: str, workspace: Path,
    backend_arguments: Sequence[str], candidate_identity: Mapping[str, Any],
    evidence_dir: Path, invoke: Callable[[Sequence[str], int], Any],
) -> dict[str, Any]:
    """Run one bounded, untimed official CLI build and retain its raw evidence.

    invoke receives argv and the fixed timeout (seconds), and returns a
    CompletedProcess-like object with returncode, stdout, and stderr. Timeouts
    may raise TimeoutError with partial stdout and stderr attributes.
    """
    admission, fixture = _validate_target(lane, fixture_id, workspace, candidate_identity)
    evidence_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(evidence_dir, 0o700)
    argv = ["build", "--workspace-folder", str(workspace.resolve(strict=True)),
            *map(str, backend_arguments), "--frozen-lockfile", "--log-level", "info",
            "--log-format", "json"]
    argv_hash = _sha(_json_bytes(argv))
    started = time.monotonic_ns()
    return_code: int | None = None
    stdout = b""
    stderr = b""
    timed_out = False
    invocation_error_type: str | None = None
    try:
        result = invoke(argv, WARMUP_TIMEOUT_SECONDS)
        return_code = int(result.returncode)
        stdout = _as_bytes(result.stdout)
        stderr = _as_bytes(result.stderr)
    except (TimeoutError, subprocess.TimeoutExpired) as exc:
        timed_out = True
        stdout = _as_bytes(getattr(exc, "stdout", None))
        stderr = _as_bytes(getattr(exc, "stderr", None))
    except Exception as exc:
        invocation_error_type = type(exc).__name__
        stdout = _as_bytes(getattr(exc, "stdout", None))
        stderr = _as_bytes(getattr(exc, "stderr", None))
    finished = time.monotonic_ns()
    _write_exclusive(evidence_dir / "warmup.stdout", stdout)
    _write_exclusive(evidence_dir / "warmup.stderr", stderr)
    comparable = return_code == 0 and not timed_out and invocation_error_type is None
    receipt = {
        "schema": "d05-feature-cache-warmup-v1", "lane": lane,
        "fixture": fixture_id, "candidate": admission, "fixtureInput": fixture,
        "argvSHA256": argv_hash, "timeoutSeconds": WARMUP_TIMEOUT_SECONDS,
        "startedMonotonicNS": started, "finishedMonotonicNS": finished,
        "durationNS": max(0, finished - started), "exitCode": return_code,
        "timedOut": timed_out, "stdoutSHA256": _sha(stdout),
        "stderrSHA256": _sha(stderr), "invocationErrorType": invocation_error_type,
        "comparisonEligible": comparable,
        "status": "warmup_completed_unverified" if comparable else "not_comparable",
    }
    _write_exclusive(evidence_dir / "warmup-receipt.json", _json_bytes(receipt))
    return receipt


def _as_bytes(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8", "replace")
    return repr(value).encode("utf-8", "replace")


def _progress_text(data: bytes) -> tuple[str | None, str | None]:
    """Decode recognized BuildKit plain/JSON progress events; reject ambiguity."""
    decoded = data.decode("utf-8", "replace")
    lines: list[str] = []
    for raw_line in decoded.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                return None, "malformed JSON progress event"
            if not isinstance(event, dict):
                return None, "unknown JSON progress event"
            event_type = event.get("type")
            text = event.get("text")
            if event_type == "raw" and isinstance(text, str):
                lines.extend(text.splitlines())
            elif event_type == "raw" and isinstance(event.get("data"), dict):
                nested = event["data"]
                nested_text = nested.get("text", nested.get("stream"))
                if not isinstance(nested_text, str):
                    return None, "unknown nested raw progress event"
                lines.extend(nested_text.splitlines())
            elif "stream" in event and isinstance(event["stream"], str):
                lines.extend(event["stream"].splitlines())
            else:
                return None, "unrecognized JSON progress event"
        else:
            lines.append(line)
    return "\n".join(lines), None


def _cache_proof(stdout: bytes, stderr: bytes) -> tuple[bool, str]:
    merged = []
    for stream in (stdout, stderr):
        text, error = _progress_text(stream)
        if error:
            return False, error
        merged.append(text or "")
    output = "\n".join(merged)
    package_activity = re.compile(
        r"(?i)^(?!\s*#\d+\s+\[).*"
        r"(?:\bapt(?:-get)?\s+(?:install|upgrade|update)\b|"
        r"reading package lists|building dependency tree|"
        r"following additional packages will be installed|"
        r"setting up [a-z0-9.+:-]+)",
    )
    if any(package_activity.search(line) for line in output.splitlines()):
        return False, "feature build output contains apt package installation"
    for name in FEATURE_STAGE_NAMES:
        candidates = re.findall(
            rf"(?im)^\s*#(?P<id>\d+)\s+\[[^\]\n]*\]\s+RUN\b[^\n]*"
            rf"{re.escape(name)}[^\n]*$",
            output)
        if len(candidates) != 1:
            return False, f"missing or ambiguous {name} feature installer stage"
        step_id = candidates[0]
        if not re.search(rf"(?im)^\s*#{step_id}\s+CACHED\s*$", output):
            return False, f"{name} feature installer stage was not proven cached"
    return True, "both feature installer stages were proven cached"


def verify_d05_feature_cache(
    *, lane: str, fixture_id: str, workspace: Path,
    candidate_identity: Mapping[str, Any], warmup_receipt: Mapping[str, Any],
    functional_started_monotonic_ns: int,
    up_stdout: bytes | str, up_stderr: bytes | str, evidence_dir: Path,
) -> dict[str, Any]:
    """Verify exact-input warmup plus cache hits in functional up output."""
    admission, fixture = _validate_target(lane, fixture_id, workspace, candidate_identity)
    reason: str | None = None
    if warmup_receipt.get("schema") != "d05-feature-cache-warmup-v1":
        reason = "warmup receipt schema is unknown"
    elif warmup_receipt.get("lane") != lane or warmup_receipt.get("fixture") != fixture_id:
        reason = "warmup target differs from the functional run"
    elif warmup_receipt.get("candidate") != admission or warmup_receipt.get("fixtureInput") != fixture:
        reason = "warmup input identity differs from the functional run"
    elif warmup_receipt.get("status") != "warmup_completed_unverified" or not warmup_receipt.get("comparisonEligible"):
        reason = "warmup did not complete successfully"
    elif (not isinstance(functional_started_monotonic_ns, int)
          or not isinstance(warmup_receipt.get("finishedMonotonicNS"), int)
          or warmup_receipt["finishedMonotonicNS"] > functional_started_monotonic_ns):
        reason = "warmup completion was not proven before the functional timing interval"
    stdout, stderr = _as_bytes(up_stdout), _as_bytes(up_stderr)
    _write_exclusive(evidence_dir / "functional-up.stdout", stdout)
    _write_exclusive(evidence_dir / "functional-up.stderr", stderr)
    if reason is None:
        proven, proof = _cache_proof(stdout, stderr)
        if not proven:
            reason = proof
    result = {
        "schema": "d05-feature-cache-verification-v1", "lane": lane,
        "fixture": fixture_id, "candidate": admission, "fixtureInput": fixture,
        "warmupReceiptSHA256": _sha(_json_bytes(dict(warmup_receipt))),
        "functionalStdoutSHA256": _sha(stdout), "functionalStderrSHA256": _sha(stderr),
        "performanceComparisonEligible": reason is None,
        "status": "cache_proven_diagnostic_only" if reason is None else "not_comparable",
        "reason": reason,
    }
    _write_exclusive(evidence_dir / "cache-verification.json", _json_bytes(result))
    return result
