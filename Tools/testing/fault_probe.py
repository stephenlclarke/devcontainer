"""F01 concurrent lifecycle and bounded failure checks over the owned socket."""

from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from urllib.parse import quote

from case_evidence import canonical, digest
from engine_probe import request
from guest_fixture import GuestFixture
from lifecycle_probe import ready


COMMAND = ("sh", "-c", "trap 'exit 42' TERM; printf 'ready-1\\n'; while :; do sleep 1; done")


def concurrent_requests(guest, method: str, route: str) -> list[tuple[int, bytes]]:
    """Start four bounded requests together and join all callers before cleanup."""
    barrier = threading.Barrier(4, timeout=5)

    def invoke(_index):
        barrier.wait()
        return guest.call(method, route, timeout=30, total_timeout=30)

    with ThreadPoolExecutor(max_workers=4) as executor:
        return list(executor.map(invoke, range(4)))


def require_identity(guest, *, state: str) -> dict:
    value = guest.inspect(guest.identifier)
    if (value is None or guest.owned(value) != guest.identifier or
            value.get("State", {}).get("Status") != state):
        raise ValueError("Fault fixture identity or lifecycle state changed")
    return value


def fault_recovery(guest, removal) -> dict[str, str]:
    """Retain original F01 assertions without CLI subprocesses or shared paths.

    Separate owned containers preserve the original running-start and
    never-started removal races. The caller always performs residue cleanup.
    No broken request is retried or counted as a successful race participant.
    """
    if guest.intent["command"] != list(COMMAND) or removal.intent["command"] != ["true"]:
        raise ValueError("Fault fixture requires its signal-aware command")
    guest.create()
    identifier = guest.identifier
    guest.journal.put("f01-start-intent.json", canonical({"id": identifier, "participants": 4}))
    starts = concurrent_requests(guest, "POST", f"/containers/{identifier}/start")
    guest.journal.put("f01-start-results.json", canonical([
        {"status": status, "body": payload.decode()} for status, payload in starts]))
    start_ok = all(status in {204, 304} and not payload for status, payload in starts)
    require_identity(guest, state="running")
    # The legacy fixture could send TERM before the shell installed its trap.
    # Readiness makes exit 42 exact rather than accepting a racy generic 143.
    ready(guest, 1)
    status, _ = guest.call("POST", f"/containers/{identifier}/kill?signal=TERM")
    if status != 204:
        raise ValueError("Fault fixture TERM delivery failed")
    status, payload = guest.call("POST", f"/containers/{identifier}/wait?condition=not-running", timeout=15)
    waited = json.loads(payload)
    if (status != 200 or not isinstance(waited, dict) or type(waited.get("StatusCode")) is not int or
            waited.get("Error") not in (None, {"Message": ""})):
        raise ValueError("Fault fixture wait failed")
    exited = require_identity(guest, state="exited")
    signal_ok = waited["StatusCode"] == 42 and type(exited["State"].get("ExitCode")) is int and exited["State"]["ExitCode"] == 42
    removal.create()
    removed_id = removal.identifier
    require_identity(removal, state="created")
    removal.journal.put("container-delete-intent.json", canonical({"id": removed_id}))
    removals = concurrent_requests(removal, "DELETE", f"/containers/{removed_id}?force=true&v=true")
    removal.journal.put("f01-remove-results.json", canonical([
        {"status": status, "body": payload.decode()} for status, payload in removals]))
    remove_ok = any(status == 204 for status, _ in removals)
    for status, payload in removals:
        if status == 204:
            remove_ok = remove_ok and not payload
        elif status in {404, 409}:
            value = json.loads(payload)
            remove_ok = remove_ok and isinstance(value, dict) and isinstance(value.get("message"), str)
        else:
            remove_ok = False
    remove_ok = remove_ok and removal.inspect(removed_id) is None and removal.inspect(removal.name) is None
    since = int(time.time())
    filters = quote(canonical({"event": ["devcontainer-never"]}).decode(), safe="")
    status, payload = guest.call("GET", f"/events?since={since}&until={since + 1}&filters={filters}", timeout=5)
    events_ok = status == 200 and not payload
    missing = guest.socket.parent / "f01-missing.sock"
    if missing.exists() or missing.is_symlink():
        raise ValueError("Missing-backend probe path already exists")
    missing_ok = False
    try:
        request(missing, "GET", "/version", timeout=1)
    except FileNotFoundError:
        missing_ok = True
    return {"bounded_events": str(events_ok).lower(), "concurrent_start": str(start_ok).lower(),
            "missing_backend_error": str(missing_ok).lower(), "remove_race": str(remove_ok).lower(),
            "signal_exit": str(signal_ok).lower()}


class ScopedJournal:
    """Separate the two owned guests without creating another transaction store."""

    def __init__(self, journal, role):
        if role not in {"signal", "remove"}:
            raise ValueError("Unknown fault guest role")
        self.journal, self.prefix = journal, "f01-" + role + "-"

    def records(self):
        return {name[len(self.prefix):]: value for name, value in self.journal.records().items()
                if name.startswith(self.prefix)}

    def put(self, name, value):
        self.journal.put(self.prefix + name, value)


class FaultFixture:
    def __init__(self, socket, owner, image, version, journal, *, observe=None):
        self.journal = journal
        self.intent = {"owner": owner, "image": image, "socket": str(socket), "apiVersion": version}
        self.guests = [GuestFixture(socket, digest((owner + ":" + role).encode()), image, version,
                                   ScopedJournal(journal, role), command=command, observe=observe)
                       for role, command in (("signal", COMMAND), ("remove", ("true",)))]

    def operation(self):
        if "fault-intent.json" in self.journal.records():
            raise ValueError("Fault fixture already attempted; reconcile instead of retrying")
        self.journal.put("fault-intent.json", canonical(self.intent))
        return fault_recovery(*self.guests)

    def cleanup(self):
        intent = self.journal.records().get("fault-intent.json")
        if intent is None:
            return {"status": "passed", "remainingOwnedResources": []}
        if intent != canonical(self.intent):
            raise ValueError("Fault journal belongs to another case")
        for guest in reversed(self.guests):
            guest.cleanup()
        self.journal.put("fault-removed.json", canonical({"intentSHA256": digest(intent), "absent": True}))
        return {"status": "passed", "remainingOwnedResources": []}
