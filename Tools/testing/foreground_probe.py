"""E08 terminal detach/reconnect and acknowledged exit across auto-removal."""

from contextlib import contextmanager
import json
import re
import socket
import time

from case_evidence import canonical, digest
from engine_probe import UnixHTTPConnection
from exec_probe import remaining, upgrade
from guest_fixture import GuestFixture


FIXTURE = "E08-foreground-terminal"
SETTINGS = {"Tty": True, "OpenStdin": True, "StdinOnce": False,
            "AttachStdin": True, "AttachStdout": True, "AttachStderr": True}
MARKERS = b"tty-stdout\ntty-stderr\n"
COMMAND = ("sh", "-c", "read -r incarnation </proc/sys/kernel/random/uuid; "
           "stty -echo -onlcr; printf 'tty-stdout\\n'; printf 'tty-stderr\\n' >&2; "
           "while IFS= read -r line; do case \"$line\" in size) stty size;; "
           "identity) printf '%s\\n' \"$incarnation\";; "
           "quit) exit 17;; *) printf 'seen:%s\\n' \"$line\";; esac; done; exit 19")


def receive_exact(connection, initial: bytes, expected: bytes, end: float):
    """The controlled guest emits no prompt, echo, CRLF conversion or framing."""
    output = bytearray(initial)
    while bytes(output) != expected:
        if not expected.startswith(output):
            raise ValueError("Terminal output differs from exact guest bytes")
        connection.settimeout(remaining(end))
        chunk = connection.recv(4096)
        if not chunk:
            raise ValueError("Terminal closed before expected output")
        output.extend(chunk)


def require_eof(connection, end: float):
    connection.settimeout(remaining(end))
    if connection.recv(4096):
        raise ValueError("Terminal emitted unexpected trailing bytes")


def receive_incarnation(connection, end: float) -> bytes:
    """The shell captures this UUID once; restarting the same container changes it."""
    output = bytearray()
    while b"\n" not in output:
        connection.settimeout(remaining(end))
        chunk = connection.recv(64)
        if not chunk or len(output) + len(chunk) > 37:
            raise ValueError("Invalid foreground process incarnation")
        output.extend(chunk)
    if re.fullmatch(rb"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\n", output) is None:
        raise ValueError("Invalid foreground process incarnation")
    return bytes(output)


class ForegroundFixture(GuestFixture):
    """One owned terminal process; no shell proxy, exec substitution or daemon restart."""

    def __init__(self, *args, command=COMMAND, **kwargs):
        super().__init__(*args, command=command, **kwargs)
        self.intent["foreground"] = {"config": dict(SETTINGS), "autoRemove": True}

    def creation_body(self, body):
        return {**body, **SETTINGS, "HostConfig": {**body["HostConfig"], "AutoRemove": True}}

    def owned(self, value):
        identifier = super().owned(value)
        if (any(value["Config"].get(key) is not expected for key, expected in SETTINGS.items()) or
                value.get("HostConfig", {}).get("AutoRemove") is not True):
            raise ValueError("Foreground terminal configuration changed")
        return identifier

    @contextmanager
    def route_event(self, route: str):
        event = {"method": "POST", "route": f"/v{self.version}/containers/{self.identifier}{route}"}
        started = time.monotonic_ns()
        try:
            yield event
        except (Exception, KeyboardInterrupt) as error:
            event["error"] = type(error).__name__
            raise
        finally:
            event["durationNS"] = time.monotonic_ns() - started
            if self.observe is not None:
                self.observe(event)

    @contextmanager
    def attachment(self, end: float):
        route = "/attach?logs=0&stream=1&stdin=1&stdout=1&stderr=1"
        with self.route_event(route) as event, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(remaining(end))
            connection.connect(str(self.socket))
            status, initial = upgrade(connection, event["route"], b"", end)
            event["status"] = status
            yield connection, initial

    @contextmanager
    def exit_subscription(self, end: float):
        # Docker flushes these headers only after registering the next-exit wait.
        # Never substitute a thread-start signal for server acknowledgement.
        with self.route_event("/wait?condition=next-exit") as event:
            connection = UnixHTTPConnection(self.socket, remaining(end))
            response = None
            try:
                connection.request("POST", event["route"], body=b"")
                response = connection.getresponse()
                event["status"] = response.status
                if response.status != 200:
                    raise ValueError("Foreground exit subscription was not acknowledged")
                yield connection, response
            finally:
                if response is not None:
                    response.close()
                connection.close()

    def require_exit(self, connection, response, end: float):
        payload = bytearray()
        while True:
            connection.deadline_socket.settimeout(remaining(end))
            part = response.read1(4097 - len(payload))
            payload.extend(part)
            if len(payload) > 4096:
                raise ValueError("Foreground exit receipt exceeds limit")
            if not part:
                break
        if response.length not in (None, 0):
            raise ValueError("Foreground exit receipt is truncated")
        value = json.loads(payload)
        error = value.get("Error") if isinstance(value, dict) else None
        if (not isinstance(value, dict) or type(value.get("StatusCode")) is not int or value["StatusCode"] != 17 or
                (error is not None and (not isinstance(error, dict) or error.get("Message") != ""))):
            raise ValueError("Foreground wait did not preserve the real exit status")
        self.journal.put("foreground-exit.json", canonical(value))

    def require_auto_removed(self):
        end = time.monotonic() + 5
        while True:
            current = self.inspect(self.identifier)
            named = self.inspect(self.name)
            if current is None and named is None:
                return
            for actual in (current, named):
                if actual is not None and (self.owned(actual) != self.identifier or
                                           actual.get("State", {}).get("Status") not in {"exited", "removing"}):
                    raise ValueError("Auto-removal changed foreground identity or exit state")
            time.sleep(min(remaining(end), 0.025))

    def operation(self):
        created = self.create()
        if created.get("State", {}).get("Status") != "created":
            raise ValueError("Foreground init ran before subscriptions")
        end = time.monotonic() + 30
        with self.exit_subscription(end) as (wait_connection, response):
            with self.attachment(end) as (connection, initial):
                self.start()
                receive_exact(connection, initial, MARKERS, end)
                status, _ = self.call("POST", f"/containers/{self.identifier}/resize?h=53&w=121",
                                      timeout=remaining(end), total_timeout=remaining(end))
                if status != 200:
                    raise ValueError("Running terminal resize failed")
                connection.sendall(b"size\n")
                receive_exact(connection, b"", b"53 121\n", end)
                connection.sendall(b"identity\n")
                incarnation = receive_incarnation(connection, end)
                connection.sendall(b"\x10")
                connection.sendall(b"\x11")
                require_eof(connection, end)
            current = self.inspect(self.identifier)
            if current is None or self.owned(current) != self.identifier or current.get("State", {}).get("Status") != "running":
                raise ValueError("Detachment stopped or replaced foreground init")
            token = ("after-detach-" + self.owner[:16]).encode()
            with self.attachment(end) as (connection, initial):
                connection.sendall(b"identity\n")
                receive_exact(connection, initial, incarnation, end)
                connection.sendall(token + b"\n")
                receive_exact(connection, b"", b"seen:" + token + b"\n", end)
                connection.sendall(b"quit\n")
                require_eof(connection, end)
            self.require_exit(wait_connection, response, end)
        self.require_auto_removed()
        self.journal.put("foreground-terminal.json", canonical({
            "width": 121, "height": 53, "reconnectedTokenSHA256": digest(token),
            "processIncarnationSHA256": digest(incarnation)}))
        return {key: "true" for key in ("tty_raw_output", "running_resize", "detach_preserves_init",
                                        "reconnect_stdin", "acknowledged_wait", "exact_exit", "auto_remove")}
