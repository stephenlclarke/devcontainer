"""Stable native executable locations; no service launch or macOS consent changes.

Archives/prepared releases stay immutable. Explicit activation runs under the
family lease and preserves the previous payload until its replacement is sealed.
Ordinary runtime admission is read-only and rejects an interrupted activation.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "bazel"))
from prepare_releases import RECEIPT, durable_file, inventory, sync_directory
from case_evidence import canonical, digest
from host_runtime import HostGuard, deadline, runtime_lease
from runtime_services import process_inventory, require_idle
from service_switch import Launchd, canonical_file


LANES = {"apple-stock", "container-compose"}


def private_directory(path: Path, *, create=False):
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("Runtime slot must be canonical")
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("Runtime slot must be a private owned directory")


def slot_path(retained: Path, lane: str, *, create=False) -> Path:
    if lane not in LANES:
        raise ValueError("Unsupported runtime activation lane")
    private_directory(retained)
    parent = retained / "active-runtimes"
    private_directory(parent, create=create)
    slot = parent / lane
    private_directory(slot, create=create)
    if slot.stat().st_dev != retained.stat().st_dev:
        raise ValueError("Runtime activation belongs on retained internal storage")
    return slot


def payload_inventory(path: Path) -> dict:
    private_directory(path)
    # inventory() deliberately omits preparation receipts. A live payload has no
    # such file: do not let that omission hide unregistered activation residue.
    if (path / RECEIPT).exists() or (path / RECEIPT).is_symlink():
        raise ValueError("Unregistered runtime payload receipt")
    result = inventory(path)
    for name, item in result.items():
        info = (path / name).lstat()
        if info.st_uid != os.getuid() or (item["kind"] == "file" and info.st_nlink != 1):
            raise ValueError("Runtime payload ownership changed")
    return result


def specification(source: dict, lane: str) -> dict:
    """Caller authenticates the original release before deriving a fixed layout."""
    if lane not in LANES:
        raise ValueError("Unsupported runtime activation lane")
    paths = source.get("executables", {})
    if not {"container", "container-apiserver"} <= paths.keys():
        raise ValueError("Activation requires an admitted native runtime")
    root = Path(source["root"])
    install = Path(paths["container"]).parent.parent
    if (root.resolve() != root or install.resolve() != install or not install.is_relative_to(root) or
            Path(paths["container-apiserver"]) != install / "bin/container-apiserver"):
        raise ValueError("Native runtime layout differs from its admitted installation")
    entries = inventory(install)
    executables = {}
    for name, value in paths.items():
        path = Path(value)
        if path.resolve() != path or not path.is_relative_to(install):
            raise ValueError("Native executable escapes the installation")
        relative = path.relative_to(install).as_posix()
        item = entries.get(relative, {})
        if item.get("kind") != "file" or not item["mode"] & 0o111:
            raise ValueError("Native executable is absent or not executable")
        executables[name] = relative
    if source.get("files"):
        raise ValueError("Unreviewed native data layout")
    return {"schemaVersion": 1, "lane": lane, "source": source, "install": str(install),
            "inventory": entries, "executables": executables}


def read_record(path: Path) -> dict:
    value = json.loads(canonical_file(path))
    if not isinstance(value, dict):
        raise ValueError("Runtime activation record must be an object")
    return value


def projected(slot: Path, expected: dict) -> dict:
    payload = slot / "payload"
    return {**expected["source"], "root": str(payload),
            "inventorySHA256": digest(canonical(expected["inventory"])),
            "executables": {name: str(payload / path) for name, path in expected["executables"].items()},
            "activation": {"schemaVersion": 1, "lane": expected["lane"],
                           "source": expected["source"], "receiptSHA256": digest(canonical(expected))}}


def require_active(source: dict, retained: Path, lane: str) -> dict:
    """Never copy, repair, launch or grant permission during runtime admission."""
    slot = slot_path(retained, lane)
    if (slot / "pending.json").exists() or (slot / "pending.json").is_symlink():
        raise ValueError("Runtime activation is unfinished; resume activate-runtime before testing")
    expected = specification(source, lane)
    if read_record(slot / "active.json") != expected or payload_inventory(slot / "payload") != expected["inventory"]:
        raise ValueError("Activated runtime differs; run activate-runtime for the selected release")
    return projected(slot, expected)


def require_inactive(slot: Path):
    """No running or registered service may execute bytes being replaced."""
    programs = [item["program"] for item in process_inventory().values()]
    require_idle(programs)
    if any(Path(program).resolve().is_relative_to(slot) for program in programs):
        raise ValueError("An activated runtime process still owns this slot")
    launchd = Launchd()
    with deadline(15):
        for label in launchd.labels():
            if Path(launchd.program(label)).resolve().is_relative_to(slot):
                raise ValueError("An activated runtime registration still owns this slot")


def retire_previous(slot: Path, prior: dict | None):
    previous = slot / "previous"
    if previous.exists() or previous.is_symlink():
        current = payload_inventory(previous)
        # A crash during rmtree may leave a subset of the authenticated old tree.
        if prior is None or any(prior["inventory"].get(name) != item for name, item in current.items()):
            raise ValueError("Previous runtime changed; preserve it for reconciliation")
        require_inactive(slot)
        shutil.rmtree(previous)
        sync_directory(slot)


def activate(source: dict, retained: Path, lane: str, revalidate) -> dict:
    """Caller holds the family lease; resume only the same authenticated input."""
    if revalidate() != source:
        raise ValueError("Runtime source changed before activation")
    expected = specification(source, lane)
    slot = slot_path(retained, lane, create=True)
    require_inactive(slot)
    pending, active = slot / "pending.json", slot / "active.json"
    payload, previous = slot / "payload", slot / "previous"
    if pending.exists() or pending.is_symlink():
        intent = read_record(pending)
        if set(intent) != {"next", "prior"} or intent["next"] != expected:
            raise ValueError("Interrupted activation belongs to different inputs")
        prior = intent["prior"]
    else:
        if previous.exists() or previous.is_symlink():
            raise ValueError("Unregistered previous runtime payload")
        prior = read_record(active) if active.exists() or active.is_symlink() else None
        if prior is not None:
            if prior.get("lane") != lane or payload_inventory(payload) != prior.get("inventory"):
                raise ValueError("Active runtime changed before replacement")
            if prior == expected:
                return require_active(source, retained, lane)
        elif payload.exists() or payload.is_symlink():
            raise ValueError("Unregistered runtime payload")
        with os.fdopen(os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
            output.write(canonical({"next": expected, "prior": prior}))
            output.flush()
            os.fsync(output.fileno())
        sync_directory(slot)
    # active.json is written only after all replacement bytes are durable.
    committed = False
    if active.exists() and not active.is_symlink():
        try:
            committed = read_record(active) == expected and payload_inventory(payload) == expected["inventory"]
        except (ValueError, FileNotFoundError):
            # An interrupted receipt write is recoverable under pending intent.
            committed = False
    if not committed:
        if prior is not None and not previous.exists():
            if payload_inventory(payload) != prior["inventory"]:
                raise ValueError("Previous runtime is unavailable for recovery")
            payload.rename(previous)
            sync_directory(slot)
        elif previous.exists() and (prior is None or payload_inventory(previous) != prior["inventory"]):
            raise ValueError("Previous runtime changed before replacement")
        private_directory(payload, create=True)
        existing = payload_inventory(payload)
        if not existing.keys() <= expected["inventory"].keys() or any(
                existing[name]["kind"] != expected["inventory"][name]["kind"] for name in existing):
            raise ValueError("Unregistered files in pending runtime payload")
        for name, item in sorted(expected["inventory"].items(), key=lambda pair: (pair[0].count("/"), pair[0])):
            target = payload / name
            if item["kind"] == "directory":
                target.mkdir(exist_ok=True)
                target.chmod(item["mode"])
            else:
                durable_file(target, Path(expected["install"]) / name, mode=item["mode"])
        if payload_inventory(payload) != expected["inventory"] or revalidate() != source:
            raise ValueError("Runtime bytes changed during activation")
        for directory, _, _ in os.walk(payload, topdown=False):
            sync_directory(Path(directory))
        durable_file(active, None, canonical(expected))
        sync_directory(slot)
    retire_previous(slot, prior)
    if revalidate() != source or payload_inventory(payload) != expected["inventory"]:
        raise ValueError("Runtime source changed before activation seal")
    pending.unlink()
    sync_directory(slot)
    return require_active(source, retained, lane)


def main():
    parser = argparse.ArgumentParser(description="Activate verified native release bytes at stable paths; never launch services")
    parser.add_argument("--lane", required=True, choices=sorted(LANES))
    args = parser.parse_args()
    from released_engine import RETAINED, admit
    private_directory(RETAINED)
    if RETAINED.stat().st_dev != Path.home().stat().st_dev:
        raise ValueError("Activated executables require internal storage")
    lock = json.loads((Path(__file__).parents[1] / "bazel/releases.lock.json").read_text())
    guard = HostGuard(RETAINED / "runtime-admission.json")
    os.umask(0o077)
    with runtime_lease(Path(f"/private/tmp/container-compose-runtime-{os.getuid()}.lock"), guard):
        selected = lambda: admit(lock, args.lane, RETAINED)[1]
        result = activate(selected(), RETAINED, args.lane, selected)
    print(json.dumps({"lane": args.lane, "root": result["root"], "receiptSHA256": result["activation"]["receiptSHA256"],
                      "servicesStarted": False, "authorizationVerified": False}, sort_keys=True))


if __name__ == "__main__":
    main()
