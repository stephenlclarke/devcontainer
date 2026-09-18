"""Direct Unix HTTP parity probes: no Docker CLI, builds, downloads or daemons."""

from __future__ import annotations

import http.client
import json
from pathlib import Path
import re
import socket
import threading
import time


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: Path, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self.path = path
        self.deadline_socket = None
        self.expired = threading.Event()

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.deadline_socket = self.sock
        if self.expired.is_set():
            self.sock.close()
            raise TimeoutError("Engine total request deadline exceeded")
        self.sock.settimeout(self.timeout)
        try:
            self.sock.connect(str(self.path))
        except BaseException:
            self.sock.close()
            raise


def request(path: Path, method: str, route: str, body: bytes | None = None, timeout: float = 5,
            *, content_type: str = "application/json", max_bytes: int = 65536,
            total_timeout: float | None = None) -> tuple[int, bytes]:
    """Require complete bounded HTTP framing; the Bazel case owns the total deadline."""
    if type(max_bytes) is not int or not 1 <= max_bytes <= 16 * 1024**2:
        raise ValueError("Invalid Engine response limit")
    if total_timeout is not None and (type(total_timeout) not in (int, float) or not 0 < total_timeout <= 300):
        raise ValueError("Invalid Engine total request deadline")
    connection = UnixHTTPConnection(path, timeout)
    expired = connection.expired

    def expire():
        expired.set()
        # HTTPConnection may relinquish sock for a close-delimited response;
        # retain this exact request's socket, never locate another descriptor.
        active = connection.deadline_socket
        if active is not None:
            try:
                active.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # Already closed by the request thread.
            finally:
                # If expiry raced the pre-connect check, shutdown can report
                # ENOTCONN. Closing prevents that socket from connecting later.
                active.close()

    timer = threading.Timer(total_timeout, expire) if total_timeout is not None else None
    try:
        if timer is not None:
            timer.start()
        headers = {"Content-Type": content_type} if body is not None else {}
        connection.request(method, route, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read(max_bytes + 1)
        if expired.is_set():
            raise TimeoutError("Engine total request deadline exceeded")
        if len(data) > max_bytes:
            raise ValueError(f"Engine response exceeds {max_bytes / 1024:g} KiB")
        # read(amt) permits early EOF for Content-Length responses. Valid JSON
        # before that EOF must not become proof that a mutating request finished.
        if response.length not in (None, 0):
            raise http.client.IncompleteRead(data, response.length)
        return response.status, data
    except socket.timeout as error:
        raise TimeoutError("Engine negotiation request timed out") from error
    except (OSError, http.client.HTTPException) as error:
        if expired.is_set():
            raise TimeoutError("Engine total request deadline exceeded") from error
        raise
    finally:
        if timer is not None:
            timer.cancel()
            timer.join()
        connection.close()


def engine_negotiation(path: Path, observe=None) -> dict[str, str]:
    """Preserve every existing E01 assertion using the actual Engine protocol."""
    def probe(method, route, body=None):
        started = time.monotonic_ns()
        event = {"method": method, "route": route}
        try:
            status, response = request(path, method, route, body)
            event["status"] = status
            return status, response
        except (Exception, KeyboardInterrupt) as error:
            event["error"] = type(error).__name__
            raise
        finally:
            event["durationNS"] = time.monotonic_ns() - started
            if observe is not None:
                observe(event)

    ping = probe("GET", "/_ping")
    version_status, version_body = probe("GET", "/version")
    if version_status != 200:
        raise ValueError("Engine version negotiation failed")
    minimum = json.loads(version_body).get("MinAPIVersion")
    if not isinstance(minimum, str) or re.fullmatch(r"[0-9]+\.[0-9]+", minimum) is None:
        raise ValueError("Engine did not declare a valid minimum API version")
    versioned = probe("GET", f"/v{minimum}/_ping")
    head = probe("HEAD", "/_ping")
    missing_status, missing_body = probe("GET", f"/v{minimum}/devcontainer-missing")
    malformed_status, malformed_body = probe("POST", f"/v{minimum}/containers/create?name=bad", b"{")
    values = {
        "api_prefix": versioned == (200, b"OK"),
        "error_envelope": missing_status == 404 and isinstance(json.loads(missing_body).get("message"), str),
        "head_ping": head == (200, b""),
        "malformed_request": malformed_status == 400 and isinstance(json.loads(malformed_body).get("message"), str),
        "ping": ping == (200, b"OK"),
    }
    return {key: "true" if value else "false" for key, value in values.items()}
