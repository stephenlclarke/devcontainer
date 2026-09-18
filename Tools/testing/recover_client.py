"""Stop a captured E04 Engine incarnation after all owned build resources close."""

import os
from pathlib import Path
import signal
import stat
import time

from case_evidence import canonical, digest, validate_identity
from guest_runtime import diagnostic_snapshot, require_diagnostic, require_guest_cleanup
from host_runtime import deadline
from private_keychain import require_keychain_stopped
from recover_apple_build import admit_original, require_previous_commands_closed
from recover_runtime import private_json, verify_case_evidence
from runtime_probe import require_probe_stopped
from runtime_services import process_inventory
from service_journal import ServiceJournal


def captured_client(artifacts: dict, owner: dict) -> dict:
    """Only spawn-time evidence, never a newly observed legacy PID, grants authority."""
    captured = artifacts.get("process-incarnation.json")
    process = artifacts.get("process.json", {})
    intent = artifacts.get("process-intent.json", {})
    root = Path(owner["root"])
    if (not isinstance(captured, dict) or set(captured) != {"pid", "parent", "group", "started", "program", "arguments"} or
            type(captured["pid"]) is not int or captured["pid"] <= 0 or captured["pid"] != process.get("pid") or
            type(captured["parent"]) is not int or captured["parent"] <= 0 or captured["group"] != captured["pid"] or
            not isinstance(captured["started"], str) or not captured["started"] or
            captured["program"] != intent.get("program") or process.get("root") != str(root)):
        raise ValueError("No valid spawn-time Engine incarnation; operator reconciliation required")
    arguments = captured["arguments"]
    if (not isinstance(arguments, list) or len(arguments) != 7 or arguments[0] != captured["program"] or
            arguments[1] != "--container" or arguments[3:] != ["--socket", str(root / "engine.sock"),
                                                             "--state", str(root / "state.sqlite")]):
        raise ValueError("Captured Engine arguments differ from private case")
    return captured


def client_running(captured, current):
    """A reused PID or unknown descendant is never signalled, even on timeout."""
    pid = captured["pid"]
    descendants = {pid}
    while True:
        expanded = descendants | {item["pid"] for item in current.values() if item.get("parent") in descendants}
        if expanded == descendants:
            break
        descendants = expanded
    if len(descendants) != 1 or any(item.get("group") == pid and item["pid"] != pid for item in current.values()):
        raise ValueError("Captured Engine has unreconciled descendants")
    actual = current.get(pid)
    if actual is None:
        return False
    if (any(actual.get(key) != captured[key] for key in ("pid", "group", "started", "program")) or
            actual.get("parent") not in {captured["parent"], 1}):
        raise ValueError("Engine incarnation changed; refusing signal")
    return True


def stop_captured_client(captured, journal, *, apply):
    """One bounded TERM attempt; uncertain outcomes stay quarantined, never retried."""
    running = client_running(captured, process_inventory())
    records = journal.records()
    intent = canonical(captured)
    if records.get("engine-stop-intent.json", intent) != intent:
        raise ValueError("Engine stop intent changed")
    if "engine-stop-verified.json" in records and running:
        raise ValueError("Stopped Engine reappeared")
    if not apply:
        return running
    if running:
        if "engine-stop-intent.json" in records:
            raise ValueError("Prior Engine stop is unresolved; refusing a second signal")
        journal.put("engine-stop-intent.json", intent)
        # Recheck the captured incarnation at the signal boundary.
        if client_running(captured, process_inventory()):
            try:
                os.kill(captured["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
        with deadline(10):
            while client_running(captured, process_inventory()):
                time.sleep(0.05)
    journal.put("engine-stop-verified.json", canonical({"incarnationSHA256": digest(intent), "absent": True}))
    return False


def retain_engine_diagnostics(root: Path, journal, *, apply: bool) -> None:
    """A stopped Engine's bounded private log must survive later root disposal."""
    records = journal.records()
    if not any(name in records for name in ("engine-stop-intent.json", "engine-stop-verified.json",
                                            "operator-engine-stop-verified.json")):
        return
    names = ("engine-recovery.log", "engine-recovery-log.json")
    if all(name in records for name in names):
        require_diagnostic(records, "engine-recovery")
        return
    payload, metadata = diagnostic_snapshot(root / "engine.log")
    for name, data in zip(names, (payload, metadata)):
        if name in records and records[name] != data:
            raise ValueError("Partial Engine diagnostic changed; preserve root")
    if apply:
        for name, data in zip(names, (payload, metadata)):
            journal.put(name, data)
        require_diagnostic(journal.records(), "engine-recovery")


def recover_engine(retained: Path, owner: dict, guard, *, apply: bool) -> dict:
    """No provider restoration/root removal; existing stopped-client recovery follows."""
    key = validate_identity(owner["identity"])
    if owner["identity"]["fixture"] != "E04-image-build" or owner["identity"]["lane"] == "docker":
        raise ValueError("Captured Engine recovery currently requires an Apple E04 case")
    root = Path(owner["root"])
    info = root.lstat()
    if (root.resolve() != root or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or
            private_json(root / "owner.json") != owner or private_json(guard.path) != owner):
        raise ValueError("Engine recovery ownership changed")
    artifacts = verify_case_evidence(retained, owner)
    captured = captured_client(artifacts, owner)
    releases, _inputs = admit_original(retained, owner, artifacts)
    if (captured["program"] != releases[0]["executables"]["devcontainer-engine"] or
            captured["arguments"][2] != releases[1]["executables"]["container"]):
        raise ValueError("Captured Engine differs from admitted binaries")
    journal = ServiceJournal(retained / "private-runtime" / (digest(str(root).encode()) + ".sqlite"), owner)
    records = journal.records()
    require_guest_cleanup(records)
    require_previous_commands_closed(records)
    require_keychain_stopped(records)
    require_probe_stopped(records)
    running = stop_captured_client(captured, journal, apply=apply)
    if not running:
        retain_engine_diagnostics(root, journal, apply=apply)
    return {"status": "ready-to-stop-captured-engine" if running else "captured-engine-stopped",
            "caseID": key, "changed": apply, "providerRestored": False}
