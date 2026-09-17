"""E03 exec observations over a bounded full-duplex Unix Engine connection."""

from __future__ import annotations

import errno
import json
import re
import selectors
import socket
import time

from case_evidence import canonical, digest


# Allow the 4 MiB contract payload even with one multiplexing header per byte.
MAX_OUTPUT = 40 * 1024**2
BINARY_INPUT = bytes(range(256)) * 16_384
EXIT_WAIT = 5


def streams(payload: bytes) -> tuple[bytes, bytes]:
    """Decode Docker's non-TTY multiplexing without accepting a raw fallback."""
    outputs = (bytearray(), bytearray())
    offset = 0
    while offset < len(payload):
        header = payload[offset:offset + 8]
        if len(header) != 8 or header[0] not in (1, 2) or header[1:4] != b"\0\0\0":
            raise ValueError("Malformed exec stream header")
        count = int.from_bytes(header[4:], "big")
        offset += 8
        if count > len(payload) - offset:
            raise ValueError("Truncated exec stream")
        outputs[header[0] - 1].extend(payload[offset:offset + count])
        offset += count
    return bytes(outputs[0]), bytes(outputs[1])


def remaining(end: float) -> float:
    seconds = end - time.monotonic()
    if seconds <= 0:
        raise TimeoutError("Exec stream exceeded its whole-connection deadline")
    return seconds


def upgrade(connection, route: str, body: bytes, end: float) -> tuple[int, bytes]:
    request = (f"POST {route} HTTP/1.1\r\nHost: localhost\r\nConnection: Upgrade\r\n"
               f"Upgrade: tcp\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n").encode()
    connection.settimeout(remaining(end))
    connection.sendall(request + body)
    response = bytearray()
    while b"\r\n\r\n" not in response:
        connection.settimeout(remaining(end))
        chunk = connection.recv(4096)
        if not chunk:
            raise ValueError("Exec connection closed before response headers")
        response.extend(chunk)
        if len(response) > 65536:
            raise ValueError("Exec response headers exceed limit")
    head, initial = bytes(response).split(b"\r\n\r\n", 1)
    lines = head.split(b"\r\n")
    status_line = lines.pop(0).split(b" ", 2)
    if len(status_line) < 2 or status_line[0] != b"HTTP/1.1" or status_line[1] not in (b"101", b"200"):
        raise ValueError("Engine refused exec stream upgrade")
    headers = {}
    for line in lines:
        key, separator, value = line.partition(b":")
        if not separator or key.lower() in headers:
            raise ValueError("Malformed or duplicate exec response header")
        headers[key.lower()] = value.strip().lower()
    if b"transfer-encoding" in headers or b"content-length" in headers:
        raise ValueError("Exec requires an unframed hijacked response")
    status = int(status_line[1])
    if status == 101 and (b"upgrade" not in [token.strip() for token in headers.get(b"connection", b"").split(b",")] or
                          headers.get(b"upgrade") != b"tcp"):
        raise ValueError("Invalid exec upgrade headers")
    if headers.get(b"content-type", b"").split(b";", 1)[0] not in (
            b"application/vnd.docker.raw-stream", b"application/vnd.docker.multiplexed-stream"):
        raise ValueError("Invalid exec stream content type")
    return status, initial


def close_write(connection):
    try:
        connection.shutdown(socket.SHUT_WR)
    except OSError as error:
        # macOS may report a completed peer disconnect here while its final
        # output is still readable. Preserve that output and validate it normally.
        if error.errno != errno.ENOTCONN:
            raise


def duplex(connection, initial: bytes, incoming: bytes, end: float) -> bytes:
    """Drain output while sending input, then half-close only the write side."""
    output = bytearray(initial)
    pending = memoryview(incoming)
    connection.setblocking(False)
    with selectors.DefaultSelector() as selector:
        selector.register(connection, selectors.EVENT_READ | (selectors.EVENT_WRITE if pending else 0))
        if not pending:
            close_write(connection)
        while True:
            events = selector.select(remaining(end))
            for _, mask in events:
                if mask & selectors.EVENT_READ:
                    chunk = connection.recv(65536)
                    if not chunk:
                        if pending:
                            raise ValueError("Exec stream closed before input was delivered")
                        return bytes(output)
                    output.extend(chunk)
                    if len(output) > MAX_OUTPUT:
                        raise ValueError("Exec output exceeds limit")
                if mask & selectors.EVENT_WRITE:
                    sent = connection.send(pending[:65536])
                    if not sent:
                        raise ValueError("Exec stream stopped accepting input")
                    pending = pending[sent:]
                    if not pending:
                        close_write(connection)
                        selector.modify(connection, selectors.EVENT_READ)


def attached(guest, identifier: str, *, tty: bool, incoming: bytes, timeout: float) -> tuple[bytes, bytes]:
    route = f"/v{guest.version}/exec/{identifier}/start"
    event = {"method": "POST", "route": route}
    started = time.monotonic_ns()
    end = time.monotonic() + timeout
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(remaining(end))
            connection.connect(str(guest.socket))
            status, initial = upgrade(connection, route, canonical({"Detach": False, "Tty": tty}), end)
            event["status"] = status
            output = duplex(connection, initial, incoming, end)
        return (output, b"") if tty else streams(output)
    except (Exception, KeyboardInterrupt) as error:
        event["error"] = type(error).__name__
        raise
    finally:
        event["durationNS"] = time.monotonic_ns() - started
        if guest.observe is not None:
            guest.observe(event)


def inspect_exec(guest, identifier: str, *, timeout=5):
    end = time.monotonic() + timeout
    status, payload = guest.call("GET", f"/exec/{identifier}/json", timeout=remaining(end))
    value = json.loads(payload)
    if (status != 200 or not isinstance(value, dict) or value.get("ID") != identifier or
            type(value.get("Running")) is not bool):
        raise ValueError("Exec identity or parent container changed")
    parent = value.get("ContainerID")
    if not isinstance(parent, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", parent) is None:
        raise ValueError("Invalid exec parent identifier")
    if parent != guest.identifier:
        # Released devcontainer exposes the native runtime ID here. Resolve it
        # through inspect, then require the same full ID, labels, name and image.
        status, payload = guest.call("GET", f"/containers/{parent}/json", timeout=remaining(end))
        actual = json.loads(payload)
        if status != 200 or not isinstance(actual, dict) or guest.owned(actual) != guest.identifier:
            raise ValueError("Exec parent does not identify the owned guest")
    return value


def completed_exec(guest, identifier: str):
    # Stream EOF may precede publication of the process exit state. Observe the
    # same exec under a bounded deadline; never restart it or retry HTTP errors.
    end = time.monotonic() + EXIT_WAIT
    while True:
        completed = inspect_exec(guest, identifier, timeout=remaining(end))
        if not completed["Running"]:
            if type(completed.get("ExitCode")) is not int:
                raise ValueError("Exec did not report a completed exit status")
            return completed
        time.sleep(min(remaining(end), 0.025))


def execute(guest, name: str, command: list[str], *, tty=False, incoming=b"", environment=False):
    actual = guest.inspect(guest.identifier) if guest.identifier is not None else None
    if actual is None or guest.owned(actual) != guest.identifier or actual.get("State", {}).get("Status") != "running":
        raise ValueError("Exec requires the verified running guest")
    record = "exec-" + name
    if record + "-intent.json" in guest.journal.records():
        raise ValueError("Exec already attempted; do not retry uncertain execution")
    config = {"AttachStdin": bool(incoming), "AttachStdout": True, "AttachStderr": True, "Tty": tty, "Cmd": command}
    if environment:
        config.update(Env=["PARITY_VALUE=present"], WorkingDir="/tmp", User="0:0")
    guest.journal.put(record + "-intent.json", canonical({"container": guest.identifier, "config": config,
                                                       "stdinSHA256": digest(incoming)}))
    status, payload = guest.call("POST", f"/containers/{guest.identifier}/exec", config)
    value = json.loads(payload)
    identifier = value.get("Id") if isinstance(value, dict) else None
    if status != 201 or not isinstance(identifier, str) or re.fullmatch(
            r"(?:[0-9a-f]{64}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})", identifier) is None:
        raise ValueError("Exec creation did not return a valid identity")
    guest.journal.put(record + "-created.json", canonical({"id": identifier}))
    if inspect_exec(guest, identifier)["Running"]:
        raise ValueError("New exec is already running")
    stdout, stderr = attached(guest, identifier, tty=tty, incoming=incoming, timeout=300 if incoming else 10)
    completed = completed_exec(guest, identifier)
    code = completed["ExitCode"]
    guest.journal.put(record + "-completed.json", canonical({"id": identifier, "exitCode": code,
                        "stdoutSHA256": digest(stdout), "stderrSHA256": digest(stderr)}))
    return stdout, stderr, code


def exec_streams(guest) -> dict[str, str]:
    """Preserve all six E03 assertions; caller owns guest setup and cleanup."""
    environment = execute(guest, "environment", ["sh", "-c",
        'printf "%s|%s|%s" "$PARITY_VALUE" "$PWD" "$(id -u):$(id -g)"'], environment=True)
    output = execute(guest, "streams", ["sh", "-c", "printf stdout-value; printf stderr-value >&2; exit 7"])
    binary = execute(guest, "binary", ["cat"], incoming=BINARY_INPUT)
    tty = execute(guest, "tty", ["sh", "-c", "test -t 1 && printf tty-value"], tty=True)
    values = {"environment": environment[0] == b"present|/tmp|0:0" and environment[2] == 0,
              "stdout": output[0] == b"stdout-value", "stderr": output[1] == b"stderr-value",
              "exact_exit": output[2] == 7, "binary_duplex": binary[0] == BINARY_INPUT and binary[2] == 0,
              "tty": b"tty-value" in tty[0] and tty[2] == 0}
    return {key: str(value).lower() for key, value in values.items()}
