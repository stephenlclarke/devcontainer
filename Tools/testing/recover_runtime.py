"""Reconcile a journalled service transaction; never rerun or rewrite a case."""

from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import plistlib
import shutil
import sqlite3
import stat

from case_evidence import CaseStore, canonical, digest, validate_identity
from host_runtime import HostGuard, runtime_lease
from runtime_services import (ControlledRuntime, capture_owned_processes, process_inventory,
                              require_captured_processes_stopped, require_idle, require_owned_volume)
from service_journal import ServiceJournal
from service_switch import Launchd, canonical_file


def private_json(path: Path) -> dict:
    data = json.loads(canonical_file(path))
    if not isinstance(data, dict):
        raise ValueError("Recovery ownership must be a JSON object")
    return data


def verify_case(retained: Path, owner: dict) -> None:
    """Read existing evidence without creating, updating or completing a case."""
    path = retained / "runtime-cases.sqlite"
    info = path.lstat()
    if (path.resolve() != path or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
            info.st_nlink != 1 or info.st_mode & 0o077):
        raise ValueError("Case evidence must be private canonical storage")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        key = validate_identity(owner["identity"])
        row = db.execute("SELECT identity,result,sha256 FROM cases WHERE id=?", (key,)).fetchone()
        if row is None or row[0] != canonical(owner["identity"]):
            raise ValueError("Recovery owner does not match an admitted case")
        artifact = db.execute("SELECT bytes,sha256 FROM artifacts WHERE case_id=? AND name='owner.json'", (key,)).fetchone()
        if artifact != (canonical(owner), digest(canonical(owner))):
            raise ValueError("Recovery ownership artifact is missing or changed")
        if row[1] is not None:
            CaseStore.read_row(owner["identity"], row, db)
        artifacts = {}
        for name, data, checksum in db.execute(
                "SELECT name,bytes,sha256 FROM artifacts WHERE case_id=? AND name IN ('process-intent.json','process.json')", (key,)):
            if digest(data) != checksum:
                raise ValueError("Corrupt process ownership evidence")
            artifacts[name] = json.loads(data)
        intent, process = artifacts.get("process-intent.json"), artifacts.get("process.json")
        if intent is not None and process is None:
            raise ValueError("Interrupted client spawn needs explicit process reconciliation")
        if process is not None:
            if (not isinstance(process, dict) or set(process) != {"pid", "root"} or
                    type(process["pid"]) is not int or process["pid"] <= 0 or process["root"] != owner["root"]):
                raise ValueError("Invalid client process record")
            # We have no durable start token for the legacy Engine handle. A
            # reused PID is conservatively blocked, never signalled or adopted.
            if any(item["pid"] == process["pid"] or item.get("group") == process["pid"]
                   for item in process_inventory().values()):
                raise ValueError("Recorded client PID or descendants require explicit reconciliation")


def recovery_idle(launchd, prior: list[dict], root: Path) -> None:
    """Never guess which live client to kill after a worker lost its handles."""
    originals = [item for item in prior if launchd.inspect(item["label"]) ==
                 {key: item[key] for key in ("label", "path", "program")}]
    roots = {launchd.process_id(item["label"]): item for item in originals}
    registered = capture_owned_processes(launchd, originals)
    allowed = {item["pid"]: item for item in registered if item["pid"] in roots}
    others = []
    for pid, item in process_inventory().items():
        program = Path(item["program"])
        original = roots.get(pid)
        administrative = (original is not None and original["label"] == "sh.brew.container" and
                          plistlib.loads(original["payload"]).get("ProgramArguments") ==
                          [item["program"], "system", "start"])
        if not administrative:
            # A worker/guest is unsafe even when descended from a valid runner.
            require_idle([item["program"]])
        previous = allowed.get(pid)
        if previous is not None and all(item[key] == previous[key] for key in ("started", "program")):
            continue
        if program.is_relative_to(root) or program.name in {"devcontainer-engine", "Runner.Listener"}:
            raise ValueError("Live case client requires explicit process reconciliation; quarantine retained")
        others.append(item["program"])
    require_idle(others)


def restore_only(runtime: ControlledRuntime) -> None:
    recovery_idle(runtime.launchd, runtime.switch.prior, runtime.root)
    runtime.require_original_processes_stopped(allow_registered=True)
    runtime.restore()
    runtime.preserve_logs()
    # Immutable receipt stays separate from the original failed/interrupted case.
    runtime.journal.put("recovery-cleanup-authorized.json", cleanup_receipt(runtime.owner, runtime.root))


def root_identity(root: Path) -> dict:
    info = root.stat()
    # macOS birth time remains stable through partial recursive deletion, unlike
    # ctime. It distinguishes replacement even when an inode is later reused.
    return {"device": info.st_dev, "inode": info.st_ino,
            "birthtimeNS": getattr(info, "st_birthtime_ns", int(info.st_birthtime * 1e9))}


def cleanup_receipt(owner: dict, root: Path) -> bytes:
    return canonical({"ownerSHA256": digest(canonical(owner)), "rootIdentity": root_identity(root)})


def recover(retained: Path, ssd: Path, *, apply: bool, expected_case: str | None, launchd=None) -> dict:
    """Caller owns the family lease, including report-only inspection."""
    guard = HostGuard(retained / "runtime-admission.json")
    if not guard.path.exists() and not guard.path.is_symlink():
        return {"status": "clear", "changed": False}
    owner = private_json(guard.path)
    if set(owner) != {"root", "identity"} or not isinstance(owner["root"], str):
        raise ValueError("Invalid recovery owner")
    key = validate_identity(owner["identity"])
    if apply and expected_case != key:
        raise ValueError("Apply requires the exact case ID from the recovery report")
    root = Path(owner["root"])
    live = ssd / "live"
    if (not root.is_absolute() or root.parent != live or not root.name.startswith("case-") or
            live.resolve() != live or root.resolve() != root):
        raise ValueError("Recovery root is not a canonical owned case directory")
    verify_case(retained, owner)
    journal = ServiceJournal(retained / "private-runtime" / (digest(str(root).encode()) + ".sqlite"), owner)
    records = journal.records()
    context = json.loads(records.get("runtime-context.json", b"null"))
    if not isinstance(context, dict) or set(context) != {"apiExecutable"} or not isinstance(context["apiExecutable"], str):
        raise ValueError("No recorded runtime context; manual journal reconciliation required")
    executable = Path(context["apiExecutable"])
    allowed = (ssd / "prepared-releases", retained / "prepared-releases")
    if (executable.name != "container-apiserver" or not any(executable.is_relative_to(path) for path in allowed) or
            executable.resolve() != executable or not executable.is_file()):
        raise ValueError("Recorded API executable is outside prepared releases or unavailable")
    backend = launchd or Launchd()
    switch = journal.recover_switch(backend, root)
    original_processes = plistlib.loads(records["original-processes.plist"])
    if not isinstance(original_processes, list):
        raise ValueError("Invalid original process record")
    for original in switch.prior:
        switch.check_original(original)
    if not root.exists():
        receipt = json.loads(records.get("recovery-cleanup-authorized.json", b"null"))
        if not isinstance(receipt, dict) or receipt.get("ownerSHA256") != digest(canonical(owner)) or "rootIdentity" not in receipt:
            raise ValueError("Missing root has no verified recovery cleanup receipt")
        # A crash after root removal must not require recreating scratch state.
        if switch.owned_survivors({item["label"]: item for item in switch.prior}) or any(
                backend.inspect(item["label"]) != {key: item[key] for key in ("label", "path", "program")}
                for item in switch.prior):
            raise ValueError("Original services changed after recovery cleanup")
        recovery_idle(backend, switch.prior, root)
        require_captured_processes_stopped(backend, switch.prior, original_processes, allow_registered=True)
        if any(Path(item["program"]).is_relative_to(executable.parent.parent) for item in process_inventory().values()):
            raise ValueError("Selected runtime process survived recovery cleanup")
        if apply:
            guard.clear(owner)
        return {"status": "restored" if apply else "ready-to-clear", "caseID": key, "changed": apply}
    info = root.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("Recovery root must remain a user-owned mode 0700 directory")
    marker = root / "owner.json"
    if marker.exists() or marker.is_symlink():
        if private_json(marker) != owner:
            raise ValueError("Disposable ownership marker changed")
    elif records.get("recovery-cleanup-authorized.json") != cleanup_receipt(owner, root):
        raise ValueError("Missing marker has no matching partial-cleanup receipt")
    if "recovery-cleanup-authorized.json" in records and records["recovery-cleanup-authorized.json"] != cleanup_receipt(owner, root):
        raise ValueError("Recovery directory identity changed")
    runtime = ControlledRuntime(root, owner, executable, journal.path.parent, launchd=backend)
    runtime.journal, runtime.switch = journal, switch
    runtime.original_processes = original_processes
    recovery_idle(backend, switch.prior, root)
    runtime.require_original_processes_stopped(allow_registered=True)
    switch.owned_survivors({item["label"]: item for item in switch.prior})
    if not apply:
        return {"status": "ready-to-restore", "caseID": key, "changed": False}
    restore_only(runtime)
    # Revalidate both markers before the only destructive operation. The saved
    # authorization permits resuming a crash after removal but before guard clear.
    if (private_json(guard.path) != owner or root.resolve() != root or
            runtime.journal.records()["recovery-cleanup-authorized.json"] != cleanup_receipt(owner, root) or
            ((marker.exists() or marker.is_symlink()) and private_json(marker) != owner)):
        raise ValueError("Recovery ownership changed before cleanup")
    shutil.rmtree(root)
    guard.clear(owner)
    return {"status": "restored", "caseID": key, "changed": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="restore only the explicitly named quarantined case")
    parser.add_argument("--case", help="exact case ID printed by the default report")
    args = parser.parse_args()
    os.umask(0o077)
    retained = Path.home() / "Library/Application Support/ContainerFamily/retained/workflow"
    ssd = Path("/Volumes/SSD/cf/bazel")
    volume = require_owned_volume(Path("/Volumes/SSD"))
    if (retained.resolve() != retained or ssd.resolve() != ssd or
            retained.stat().st_dev == ssd.stat().st_dev or
            canonical_file(retained / "ssd-volume.uuid").decode().strip() != volume["uuid"]):
        raise ValueError("Recovery requires the enrolled SSD and separate canonical internal evidence")
    with runtime_lease(Path(f"/private/tmp/container-compose-runtime-{os.getuid()}.lock")):
        try:
            print(canonical(recover(retained, ssd, apply=args.apply, expected_case=args.case)).decode())
        except (Exception, KeyboardInterrupt) as error:
            # Do not leak file paths from original definitions or exception text.
            print(canonical({"status": "quarantined", "error": type(error).__name__, "changed": "not-confirmed"}).decode())
            raise SystemExit(1) from None


if __name__ == "__main__":
    main()
