"""Prove the selected private Apple API answers before accepting case traffic.

This read-only startup probe runs once, never retries a fixture operation, and
retains its process lifetime and bounded diagnostics privately for recovery.
"""

import json
from pathlib import Path
import subprocess
import time

from case_evidence import canonical, digest
from guest_runtime import diagnostic_snapshot, require_diagnostic
from host_runtime import OwnedProcess


NAME = "api-readiness"


def require_probe_stopped(records: dict[str, bytes]) -> None:
    """A missing process or exit code cannot prove an interrupted spawn safe."""
    intent = records.get(NAME + "-intent.json")
    if intent is None:
        return  # Historical transactions and failures before this probe.
    stopped = json.loads(records.get(NAME + "-stopped.json", b"null"))
    if (not isinstance(stopped, dict) or stopped.get("verifiedStopped") is not True or
            stopped.get("intentSHA256") != digest(intent)):
        raise ValueError("API readiness helper needs explicit process reconciliation")


def probe_diagnostics(root: Path, journal) -> None:
    """Retain before scratch deletion, resuming interrupted diagnostic retention."""
    records = journal.records()
    if NAME + "-intent.json" not in records:
        return
    require_probe_stopped(records)
    if NAME + ".log" not in records or NAME + "-log.json" not in records:
        payload, metadata = diagnostic_snapshot(root / (NAME + ".log"))
        journal.put(NAME + ".log", payload)
        journal.put(NAME + "-log.json", metadata)
    require_diagnostic(journal.records(), NAME)


def probe_api(root: Path, executable: Path, journal, verify) -> None:
    """List the isolated empty inventory once through the selected released CLI."""
    if (not executable.is_absolute() or executable.resolve() != executable or
            not executable.is_file() or executable.name != "container"):
        raise ValueError("API readiness requires the canonical prepared container CLI")
    if NAME + "-intent.json" in journal.records():
        raise ValueError("API readiness already attempted; reconcile instead of retrying")
    arguments = [str(executable), "list", "--all", "--format", "json"]
    intent = canonical({"arguments": arguments, "root": str(root)})
    child = OwnedProcess()
    started = time.monotonic_ns()
    verify()
    with (root / (NAME + ".log")).open("xb") as output:
        journal.put(NAME + "-intent.json", intent)
        try:
            child.start(arguments, root, output, provider_install=executable.parent.parent)
            journal.put(NAME + "-process.json", canonical({"pid": child.process.pid}))
            code = child.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            # The case store distinguishes timeouts from other failed setup.
            # Never export subprocess command/output fields in public evidence.
            raise TimeoutError("Selected API readiness deadline expired") from None
        finally:
            child.stop()
            journal.put(NAME + "-stopped.json", canonical({
                "verifiedStopped": True, "intentSHA256": digest(intent),
                "durationNS": time.monotonic_ns() - started}))
            output.flush()
            probe_diagnostics(root, journal)
    verify()
    records = journal.records()
    metadata = json.loads(records[NAME + "-log.json"])
    if code != 0 or metadata["truncated"] or json.loads(records[NAME + ".log"]) != []:
        raise ValueError("Selected API did not confirm an empty isolated inventory")
    journal.put(NAME + "-ready.json", canonical({"emptyInventory": True, "intentSHA256": digest(intent)}))
