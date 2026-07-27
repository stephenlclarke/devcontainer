#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Small dependency-free Docker Engine HTTP client for contract probes."""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import urllib.parse
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes

    def json(self) -> object:
        return json.loads(self.body)


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str) -> None:
        super().__init__("localhost", timeout=30)
        self.path = path

    def connect(self) -> None:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        connection.connect(self.path)
        self.sock = connection


class DockerAPI:
    def __init__(self, docker: str = "docker") -> None:
        endpoint = os.environ.get("DOCKER_HOST", "")
        if not endpoint:
            result = subprocess.run(
                [
                    docker,
                    "context",
                    "inspect",
                    "--format",
                    "{{.Endpoints.docker.Host}}",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            endpoint = result.stdout.strip()
        self.endpoint = endpoint

    def request(
        self,
        method: str,
        target: str,
        body: bytes = b"",
        headers: Mapping[str, str] | None = None,
    ) -> Response:
        request_headers = {"Host": "docker", **dict(headers or {})}
        if body and not any(key.lower() == "content-length" for key in request_headers):
            request_headers["Content-Length"] = str(len(body))
        if self.endpoint.startswith("unix://"):
            connection: http.client.HTTPConnection = UnixHTTPConnection(
                self.endpoint.removeprefix("unix://")
            )
        else:
            parsed = urllib.parse.urlparse(self.endpoint)
            if parsed.scheme not in {"tcp", "http"}:
                raise RuntimeError(f"unsupported Docker endpoint {self.endpoint!r}")
            connection = http.client.HTTPConnection(
                parsed.hostname,
                parsed.port or 2375,
                timeout=30,
            )
        try:
            connection.request(method, target, body=body, headers=request_headers)
            raw = connection.getresponse()
            return Response(
                status=raw.status,
                headers={key.lower(): value for key, value in raw.getheaders()},
                body=raw.read(),
            )
        finally:
            connection.close()
