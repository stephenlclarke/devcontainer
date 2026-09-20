"""E07 init attachment/history over the same downloaded Engine oracle boundary."""

from __future__ import annotations

import json
import selectors
import socket
import time

from exec_probe import BINARY_INPUT, MAX_OUTPUT, close_write, duplex, remaining, streams, upgrade
from guest_fixture import GuestFixture
from case_evidence import canonical, digest
from json_file_oracle import history_bytes


FIXTURE = "E07-init-attachment"
OUTPUT_PREFIX = b"init-stdout\n"
OUTPUT_SUFFIX = b"\nstdin-closed\n"
ERROR_OUTPUT = b"init-stderr\n"
COMMAND = ("sh", "-c", "printf 'init-stdout\\n'; printf 'init-stderr\\n' >&2; "
           "cat; printf '\\nstdin-closed\\n'; exit 17")
SETTINGS = {"OpenStdin": True, "StdinOnce": True, "Tty": False,
            "AttachStdin": True, "AttachStdout": True, "AttachStderr": True}


def started_output(connection, initial: bytes, end: float) -> bytes:
    """Observe both complete log records before subscribing to history plus live."""
    payload, offset = bytearray(initial), 0
    outputs = (bytearray(), bytearray())
    expected = (OUTPUT_PREFIX, ERROR_OUTPUT)
    while True:
        if len(payload) > 4096:
            raise ValueError("Init startup output exceeds its bounded markers")
        while len(payload) - offset >= 8:
            header = payload[offset:offset + 8]
            if header[0] not in (1, 2) or header[1:4] != b"\0\0\0":
                raise ValueError("Malformed init startup stream")
            count = int.from_bytes(header[4:], "big")
            if count > 4096:
                raise ValueError("Init startup frame exceeds its bounded markers")
            if len(payload) - offset - 8 < count:
                break
            outputs[header[0] - 1].extend(payload[offset + 8:offset + 8 + count])
            offset += 8 + count
            if any(not wanted.startswith(actual) for wanted, actual in zip(expected, outputs)):
                raise ValueError("Init startup markers differ")
        if tuple(map(bytes, outputs)) == expected and offset == len(payload):
            return bytes(payload)
        connection.settimeout(remaining(end))
        chunk = connection.recv(4096)
        if not chunk:
            raise ValueError("Init closed before both startup markers")
        payload.extend(chunk)


def observed_duplex(primary, initial: bytes, observer, history: bytes, incoming: bytes, end: float):
    """Drain both attachments concurrently; the output-only peer cannot own stdin."""
    output = {primary: bytearray(initial), observer: bytearray(history)}
    pending = memoryview(incoming)
    with selectors.DefaultSelector() as selector:
        for connection in output:
            connection.setblocking(False)
            selector.register(connection, selectors.EVENT_READ |
                              (selectors.EVENT_WRITE if connection is primary and pending else 0))
        close_write(observer)
        if not pending:
            close_write(primary)
        while selector.get_map():
            for key, mask in selector.select(remaining(end)):
                connection = key.fileobj
                if mask & selectors.EVENT_READ:
                    chunk = connection.recv(65536)
                    if not chunk:
                        if connection is primary and pending:
                            raise ValueError("Init closed before input was delivered")
                        selector.unregister(connection)
                        continue
                    output[connection].extend(chunk)
                    if len(output[connection]) > MAX_OUTPUT:
                        raise ValueError("Init attachment output exceeds limit")
                if mask & selectors.EVENT_WRITE:
                    sent = primary.send(pending[:65536])
                    if not sent:
                        raise ValueError("Init stopped accepting input")
                    pending = pending[sent:]
                    if not pending:
                        close_write(primary)
                        selector.modify(primary, selectors.EVENT_READ)
    return tuple(streams(bytes(output[connection])) for connection in (primary, observer))


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

    def transfer(self, *, history: bool, live: bool, incoming: bytes = b"", observer=False):
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
                if observer:
                    initial = started_output(connection, initial, end)
                    return self.observe_running(connection, initial, incoming, end)
                return streams(duplex(connection, initial, incoming, end))
        except (Exception, KeyboardInterrupt) as error:
            event["error"] = type(error).__name__
            raise
        finally:
            event["durationNS"] = time.monotonic_ns() - started
            if self.observe is not None:
                self.observe(event)

    def observe_running(self, primary, initial: bytes, incoming: bytes, end: float):
        """Saved startup records and subsequent raw output must each appear once."""
        actual = self.inspect(self.identifier)
        if actual is None or self.owned(actual) != self.identifier or actual.get("State", {}).get("Status") != "running":
            raise ValueError("Combined attachment target is not the owned running init")
        route = f"/v{self.version}/containers/{self.identifier}/attach?logs=1&stream=1&stdin=0&stdout=1&stderr=1"
        event = {"method": "POST", "route": route}
        started = time.monotonic_ns()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as observer:
                observer.settimeout(remaining(end))
                observer.connect(str(self.socket))
                status, history = upgrade(observer, route, b"", end)
                event["status"] = status
                # Upgrade precedes the server's history snapshot on some engines.
                # Do not let new binary output enter that saved-log prefix.
                history = started_output(observer, history, end)
                original, observed = observed_duplex(primary, initial, observer, history, incoming, end)
            # The independent observer sees only complete ASCII startup records
            # in its saved prefix, then the same unmodified binary live bytes.
            if observed != original:
                raise ValueError("Combined history/live output lost, duplicated or relabelled bytes")
            self.journal.put("init-combined-attachment.json", canonical({
                name: {"bytes": len(data), "sha256": digest(data)}
                for name, data in zip(("stdout", "stderr"), observed)}))
            return original
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
        driver = created.get("HostConfig", {}).get("LogConfig")
        if driver != {"Type": "json-file", "Config": {}}:
            raise ValueError("Init history requires the pinned json-file driver without options")
        self.journal.put("init-log-driver.json", canonical(driver))
        history = (b"", b"")
        for generation, incoming in enumerate((BINARY_INPUT, BINARY_INPUT[::-1]), 1):
            expected = (OUTPUT_PREFIX + incoming + OUTPUT_SUFFIX, ERROR_OUTPUT)
            if self.transfer(history=False, live=True, incoming=incoming, observer=generation == 1) != expected:
                raise ValueError("Init binary output or stdout/stderr separation differs")
            self.require_exit()
            history = tuple(before + history_bytes(after) for before, after in zip(history, expected))
            observed = self.transfer(history=True, live=False)
            self.journal.put(f"init-history-{generation}.json", canonical({
                name: {"expectedBytes": len(wanted), "actualBytes": len(actual),
                       "expectedSHA256": digest(wanted), "actualSHA256": digest(actual),
                       "firstDifference": next((i for i, (left, right) in enumerate(zip(wanted, actual))
                                                if left != right), min(len(wanted), len(actual))),
                       "sampleHex": actual[:280].hex()}
                for name, wanted, actual in zip(("stdout", "stderr"), history, observed)}))
            if observed != history:
                raise ValueError("Init history lost, duplicated or relabelled output")
        return {key: "true" for key in ("prestart_attach", "binary_duplex", "source_separation",
                                        "stdin_eof", "exact_exit", "history", "restart_history", "combined_history_live")}
