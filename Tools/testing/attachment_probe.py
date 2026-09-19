"""E07 init attachment/history over the same downloaded Engine oracle boundary."""

from __future__ import annotations

import json
import socket
import time

from exec_probe import BINARY_INPUT, duplex, remaining, streams, upgrade
from guest_fixture import GuestFixture


FIXTURE = "E07-init-attachment"
OUTPUT_PREFIX = b"init-stdout\n"
OUTPUT_SUFFIX = b"\nstdin-closed\n"
ERROR_OUTPUT = b"init-stderr\n"
COMMAND = ("sh", "-c", "printf 'init-stdout\\n'; printf 'init-stderr\\n' >&2; "
           "cat; printf '\\nstdin-closed\\n'; exit 17")
SETTINGS = {"OpenStdin": True, "StdinOnce": True, "Tty": False,
            "AttachStdin": True, "AttachStdout": True, "AttachStderr": True}


class AttachmentFixture(GuestFixture):
    """One owned container, two init generations, no exec or generated output."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, command=COMMAND, **kwargs)
        self.intent["attachment"] = dict(SETTINGS)

    def creation_body(self, body):
        return {**body, **self.intent["attachment"]}

    def owned(self, value):
        identifier = super().owned(value)
        if any(value["Config"].get(key) is not expected for key, expected in SETTINGS.items()):
            raise ValueError("Init attachment configuration changed")
        return identifier

    def transfer(self, *, history: bool, live: bool, incoming: bytes = b""):
        """Acknowledge attach before start; retain exact bytes and route timing."""
        actual = self.inspect(self.identifier)
        if actual is None or self.owned(actual) != self.identifier:
            raise ValueError("Init attachment target changed")
        if live and actual.get("State", {}).get("Status") not in {"created", "exited"}:
            raise ValueError("Init attachment requires an unstarted generation")
        route = (f"/v{self.version}/containers/{self.identifier}/attach?logs={int(history)}"
                 f"&stream={int(live)}&stdin={int(live)}&stdout=1&stderr=1")
        event = {"method": "POST", "route": route}
        started = time.monotonic_ns()
        end = time.monotonic() + 30
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(remaining(end))
                connection.connect(str(self.socket))
                status, initial = upgrade(connection, route, b"", end)
                event["status"] = status
                if live:
                    # Small startup markers cannot fill the output pipe; all
                    # large output follows stdin, which is drained concurrently.
                    status, _ = self.call("POST", f"/containers/{self.identifier}/start",
                                          timeout=remaining(end), total_timeout=remaining(end))
                    if status != 204:
                        raise ValueError("Attached init start failed")
                return streams(duplex(connection, initial, incoming, end))
        except (Exception, KeyboardInterrupt) as error:
            event["error"] = type(error).__name__
            raise
        finally:
            event["durationNS"] = time.monotonic_ns() - started
            if self.observe is not None:
                self.observe(event)

    def require_exit(self):
        status, payload = self.call("POST", f"/containers/{self.identifier}/wait?condition=not-running")
        waited = json.loads(payload)
        error = waited.get("Error") if isinstance(waited, dict) else None
        if (status != 200 or not isinstance(waited, dict) or type(waited.get("StatusCode")) is not int or
                waited["StatusCode"] != 17 or
                (error is not None and (not isinstance(error, dict) or error.get("Message") != ""))):
            raise ValueError("Init wait did not preserve the real exit status")
        actual = self.inspect(self.identifier)
        if (actual is None or self.owned(actual) != self.identifier or
                actual.get("State", {}).get("Status") != "exited" or
                type(actual["State"].get("ExitCode")) is not int or actual["State"]["ExitCode"] != 17):
            raise ValueError("Init inspection disagrees with its exit receipt")

    def operation(self):
        created = self.create()
        if created.get("State", {}).get("Status") != "created":
            raise ValueError("Init ran before attachment was registered")
        history = (b"", b"")
        for incoming in (BINARY_INPUT, BINARY_INPUT[::-1]):
            expected = (OUTPUT_PREFIX + incoming + OUTPUT_SUFFIX, ERROR_OUTPUT)
            if self.transfer(history=False, live=True, incoming=incoming) != expected:
                raise ValueError("Init binary output or stdout/stderr separation differs")
            self.require_exit()
            history = tuple(before + after for before, after in zip(history, expected))
            if self.transfer(history=True, live=False) != history:
                raise ValueError("Init history lost, duplicated or relabelled output")
        return {key: "true" for key in ("prestart_attach", "binary_duplex", "source_separation",
                                        "stdin_eof", "exact_exit", "history", "restart_history")}
