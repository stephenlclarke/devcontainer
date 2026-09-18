"""Resource-only reconciliation of completed Apple E04 builds; no client signals."""

import json
import os
from pathlib import Path
import stat

from build_images import BuildImages
from build_runtime import ReleasedBuilder
from case_evidence import canonical, digest, validate_identity
from guest_fixture import GuestFixture, OWNER_LABEL
from guest_runtime import (GUEST_API_VERSION, ReleasedGuest, admit_guest,
                           require_diagnostic, require_guest_commands_stopped)
from host_runtime import deadline
from recover_runtime import private_json, verify_case_evidence
from released_engine import admit
from runtime_services import ControlledRuntime, process_inventory
from service_journal import ServiceJournal


def admit_original(retained, owner, artifacts):
    """Reopen precisely the admitted candidate/release and guest closure offline."""
    admission = artifacts.get("admission.json", {})
    runtime = admission.get("runtime", {})
    if digest(canonical(runtime)) != owner["identity"]["runtimeSHA256"]:
        raise ValueError("Original Apple runtime fingerprint differs")
    releases = runtime.get("releases", [])
    candidate = releases[0].get("candidateInvocation") if releases else None
    selected = admit(admission["releaseLock"], owner["identity"]["lane"], retained, candidate)
    inputs = admit_guest(*admission["guestLocks"], owner["identity"]["lane"], retained,
                        builder_lock=admission["builderLock"])
    if selected != releases or inputs != runtime.get("guestInputs"):
        raise ValueError("Original Apple build inputs changed")
    return selected, inputs


def require_outputs_absent(images):
    """Only already-deleted outputs qualify; this path never deletes an image."""
    if images.recovery_plan():
        raise ValueError("Apple build output still exists; explicit image reconciliation required")
    records = images.records()
    for failing in (False, True):
        if images.key(failing, "removed") not in records:
            raise ValueError("Apple output has no durable deletion receipt")
    # Historical engines ignored the requested label filter. Read the complete
    # inventory and inspect each entry: only the exact admitted base may remain.
    # This cleanup evidence does not repair or relabel the failed test result.
    status, payload = images.client.call("GET", "/images/json?all=true")
    values = json.loads(payload)
    if status != 200 or not isinstance(values, list) or len(values) != 1:
        raise ValueError("Unexpected Apple image inventory; preserve runtime")
    if not isinstance(values[0], dict) or values[0].get("Id") != images.intent["image"]:
        raise ValueError("Unreconciled Apple image remains")
    base = images.inspect(images.intent["base"])
    if (base is None or base.get("Id") != images.intent["image"] or
            images.intent["base"] not in base.get("RepoDigests", []) or
            OWNER_LABEL in base.get("Config", {}).get("Labels", {})):
        raise ValueError("Remaining base image differs from admission")


def builder_state(builder):
    records = builder.journal.records()
    if (records.get("e04-builder-intent.json") != canonical(builder.intent) or
            records.get("e04-builder-start-completed.json") != canonical({"completed": True}) or
            "e04-builder-created.json" not in records):
        raise ValueError("Builder has no matching captured creation transaction")
    removed = records.get("e04-builder-removed.json")
    if removed is not None:
        expected = canonical({"intentSHA256": digest(canonical(builder.intent)), "absent": True})
        if removed != expected:
            raise ValueError("Builder closure receipt differs from admission")
        builder.verify_configuration()
        if builder.inventory():
            raise ValueError("Removed builder reappeared")
        return "absent"
    value, _identity = builder.verify()
    return value.get("status", {}).get("state")


def require_previous_commands_closed(records):
    """Never step over an interrupted inventory/stop/delete helper on resume."""
    current = process_inventory()
    for name in require_guest_commands_stopped(records):
        process = json.loads(records.get(name + "-process.json", b"null"))
        if (not isinstance(process, dict) or type(process.get("pid")) is not int or process["pid"] <= 0 or
                any(item["pid"] == process["pid"] or item.get("group") == process["pid"]
                    for item in current.values())):
            raise ValueError("Prior Apple guest helper requires explicit reconciliation")
        require_diagnostic(records, name)


def recover_resources(retained: Path, owner: dict, guard, *, apply: bool) -> dict:
    """Caller holds the family lease; the Engine and provider stay untouched."""
    key = validate_identity(owner["identity"])
    if owner["identity"]["fixture"] != "E04-image-build" or owner["identity"]["lane"] not in {
            "apple-stock", "container-compose"}:
        raise ValueError("Resource reconciliation requires an Apple E04 case")
    root = Path(owner["root"])
    info = root.lstat()
    if (root.resolve() != root or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or
            private_json(root / "owner.json") != owner or private_json(guard.path) != owner):
        raise ValueError("Apple build root ownership changed")
    artifacts = verify_case_evidence(retained, owner)
    releases, inputs = admit_original(retained, owner, artifacts)
    journal = ServiceJournal(retained / "private-runtime" / (digest(str(root).encode()) + ".sqlite"), owner)
    require_previous_commands_closed(journal.records())
    runtime = ControlledRuntime(root, owner, Path(releases[1]["executables"]["container-apiserver"]), journal.path.parent)
    runtime.journal, runtime.service = journal, artifacts.get("api-service.json")
    image = inputs["workload"]["image"]
    socket = root / "engine.sock"
    client = GuestFixture(socket, key, image["config"], GUEST_API_VERSION, journal)
    images = BuildImages(client, journal, key, image["repository"] + "@" + image["manifest"],
                         private_named_cleanup=True)
    guest = ReleasedGuest(inputs, "E04-image-build", root, owner, runtime,
                          releases[1]["executables"]["container"], socket)
    builder = ReleasedBuilder(inputs["builder"], root, journal, guest.command)
    with deadline(45):
        runtime.verify()
        require_outputs_absent(images)
        # Verify the captured private builder before any stop/delete. Inventory
        # commands produce journalled read-only diagnostics, not workload mutation.
        state = builder_state(builder)
    if not apply:
        guest.retain_logs()
        return {"status": "ready-to-clean-apple-build-resources", "caseID": key,
                "changed": False, "builderState": state, "diagnosticsRecorded": True,
                "engineAction": "separate-process-reconciliation-required"}
    with deadline(150):
        if private_json(guard.path) != owner or admit_original(retained, owner, artifacts) != (releases, inputs):
            raise ValueError("Apple recovery ownership or inputs changed")
        runtime.verify()
        require_outputs_absent(images)
        journal.put("e04-resource-recovery-authorized.json", canonical({"caseID": key, "resourcesOnly": True}))
        journal.put("e04-images-removed.json", canonical({"absent": True}))
        builder.cleanup()
        guest.retain_logs()
    return {"status": "apple-build-resources-removed", "caseID": key, "changed": True,
            "engineAction": "separate-process-reconciliation-required"}
