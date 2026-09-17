"""Direct Unix HTTP parity probes: no Docker CLI, builds, downloads or daemons."""

from __future__ import annotations

import http.client
import json
from pathlib import Path
import re
import socket


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: Path, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        try:
            self.sock.connect(str(self.path))
        except BaseException:
            self.sock.close()
            raise


def request(path: Path, method: str, route: str, body: bytes | None = None, timeout: float = 5) -> tuple[int, bytes]:
    """Bound response size; the enclosing Bazel case supplies its total deadline."""
    connection = UnixHTTPConnection(path, timeout)
    try:
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, route, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read(65537)
        if len(data) > 65536:
            raise ValueError("Engine negotiation response exceeds 64 KiB")
        return response.status, data
    except socket.timeout as error:
        raise TimeoutError("Engine negotiation request timed out") from error
    finally:
        connection.close()


def engine_negotiation(path: Path) -> dict[str, str]:
    """Preserve every existing E01 assertion using the actual Engine protocol."""
    ping = request(path, "GET", "/_ping")
    version_status, version_body = request(path, "GET", "/version")
    if version_status != 200:
        raise ValueError("Engine version negotiation failed")
    minimum = json.loads(version_body).get("MinAPIVersion")
    if not isinstance(minimum, str) or re.fullmatch(r"[0-9]+\.[0-9]+", minimum) is None:
        raise ValueError("Engine did not declare a valid minimum API version")
    versioned = request(path, "GET", f"/v{minimum}/_ping")
    head = request(path, "HEAD", "/_ping")
    missing_status, missing_body = request(path, "GET", f"/v{minimum}/devcontainer-missing")
    malformed_status, malformed_body = request(path, "POST", f"/v{minimum}/containers/create?name=bad", b"{")
    values = {
        "api_prefix": versioned == (200, b"OK"),
        "error_envelope": missing_status == 404 and isinstance(json.loads(missing_body).get("message"), str),
        "head_ping": head == (200, b""),
        "malformed_request": malformed_status == 400 and isinstance(json.loads(malformed_body).get("message"), str),
        "ping": ping == (200, b"OK"),
    }
    return {key: "true" if value else "false" for key, value in values.items()}
