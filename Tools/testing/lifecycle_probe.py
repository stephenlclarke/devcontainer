"""E02 lifecycle through direct Engine HTTP and journalled resource ownership."""

import json
import time

from guest_fixture import GuestFixture


# Each start emits a new generation only after installing its TERM handler.
# Waiting for generation 2 avoids signalling a restarted shell before its trap
# exists or mistaking generation 1's retained log for new-process readiness.
COMMAND = ("sh", "-c", "trap 'exit 7' TERM; "
           "n=0; test ! -f /tmp/parity-generation || read -r n </tmp/parity-generation; "
           "n=$((n+1)); printf '%s\\n' \"$n\" >/tmp/parity-generation; "
           "printf 'ready-%s\\n' \"$n\"; while :; do sleep 1; done")


def stdout_frames(payload: bytes) -> bytes:
    """Decode bounded non-TTY Docker log frames without accepting raw fallback."""
    output = bytearray()
    offset = 0
    while offset < len(payload):
        header = payload[offset:offset + 8]
        if len(header) != 8 or header[0] not in (1, 2) or header[1:4] != b"\0\0\0":
            raise ValueError("Malformed Docker log frame header")
        count = int.from_bytes(header[4:], "big")
        offset += 8
        if count > len(payload) - offset:
            raise ValueError("Truncated Docker log frame")
        if header[0] == 1:
            output.extend(payload[offset:offset + count])
        offset += count
    return bytes(output)


def ready(guest: GuestFixture, generation: int, *, seconds: float = 5):
    end = time.monotonic() + seconds
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Guest signal handler did not become ready")
        status, payload = guest.call("GET", f"/containers/{guest.identifier}/logs?stdout=true&stderr=true&tail=all",
                                     timeout=min(remaining, 1))
        if status != 200:
            raise ValueError("Cannot observe guest signal-handler readiness")
        if f"ready-{generation}".encode() in stdout_frames(payload).splitlines():
            return
        time.sleep(min(remaining, 0.025))


def lifecycle(guest: GuestFixture) -> dict[str, str]:
    """Keep all five E02 observations; caller always runs guest.cleanup afterward."""
    if guest.intent["command"] != list(COMMAND):
        raise ValueError("Lifecycle fixture requires its signal-aware command")
    created = guest.create()
    guest.start()
    ready(guest, 1)
    identifier = guest.identifier
    # Cover the server's ten-second stop grace plus startup. The caller's
    # whole-operation deadline remains authoritative for the complete fixture.
    status, _ = guest.call("POST", f"/containers/{identifier}/restart?t=10", timeout=20)
    if status != 204:
        raise ValueError("Guest restart failed")
    restarted = guest.inspect(identifier)
    if restarted is None or guest.owned(restarted) != identifier:
        raise ValueError("Restart changed the guest identity")
    ready(guest, 2)
    status, _ = guest.call("POST", f"/containers/{identifier}/kill?signal=TERM")
    if status != 204:
        raise ValueError("Guest TERM delivery failed")
    status, payload = guest.call("POST", f"/containers/{identifier}/wait?condition=not-running")
    waited = json.loads(payload)
    error = waited.get("Error") if isinstance(waited, dict) else None
    if (status != 200 or not isinstance(waited, dict) or type(waited.get("StatusCode")) is not int or
            (error is not None and (not isinstance(error, dict) or error.get("Message") != ""))):
        raise ValueError("Guest wait failed")
    exited = guest.inspect(identifier)
    if exited is None or guest.owned(exited) != identifier:
        raise ValueError("Exited guest identity changed")
    exit_code = exited.get("State", {}).get("ExitCode")
    if type(exit_code) is not int:
        raise ValueError("Guest inspect omitted its exit code")
    guest.remove()
    status, payload = guest.call("DELETE", f"/containers/{identifier}")
    repeated = json.loads(payload)
    return {"create_state": str(created.get("State", {}).get("Status")),
            "restart_state": str(restarted.get("State", {}).get("Status")),
            "wait_status": str(waited["StatusCode"]), "exit_status": str(exit_code),
            "idempotent_cleanup": str(status == 404 and isinstance(repeated, dict) and
                                      isinstance(repeated.get("message"), str)).lower()}
