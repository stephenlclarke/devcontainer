#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# SPDX-License-Identifier: Apache-2.0
"""Create and remove one isolated build before the measured E04 command."""

from __future__ import annotations

import os
from pathlib import Path
import hashlib
import json
import subprocess
import tempfile
import time
from urllib.parse import quote
import uuid


def readiness_fixture():
    """Return a unique tag, argument and temporary context for one lane run."""
    token = uuid.uuid4().hex
    tag = f"dcparity-e04-readiness-{os.getpid()}-{token[:12]}:latest"
    directory = tempfile.TemporaryDirectory(prefix="devcontainer-e04-readiness-")
    root = Path(directory.name)
    (root / "Dockerfile").write_text(
        "FROM alpine:latest\n"
        "ARG PARITY_READINESS_NONCE\n"
        'RUN test "$PARITY_READINESS_NONCE" = ' + token + "\n",
        encoding="utf-8",
    )
    return directory, root, tag, token


def command_arguments(tag: str, token: str, context: Path, *, load: bool = False) -> list[str]:
    result = ["build", "--progress", "plain", "--build-arg",
              f"PARITY_READINESS_NONCE={token}", "--tag", tag]
    if load:
        result.append("--load")
    return [*result, str(context)]


def engine_request(repository: Path):
    """Load the maintained HTTP helper through the normal parity import boundary."""
    from owned_guest_fixture import _testing_path

    _testing_path(repository)
    from engine_probe import request

    return request


def docker_readiness(docker: str, repository: Path, environment: dict[str, str],
                     socket: Path, output: Path) -> dict:
    """Build and remove one nonce-tagged image before the Docker E04 timer."""
    directory, context, tag, token = readiness_fixture()
    request = engine_request(repository)
    image_path = f"/v1.53/images/{quote(tag, safe='')}/json"
    payload = {
        "schemaVersion": 1, "status": "intent", "tag": tag,
        "nonceSHA256": hashlib.sha256(token.encode()).hexdigest(),
        "dockerfileSHA256": hashlib.sha256((context / "Dockerfile").read_bytes()).hexdigest(),
    }
    output.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    built = None
    try:
        status, _body = request(socket, "GET", image_path)
        if status != 404:
            raise RuntimeError("Docker E04 readiness image tag already exists")
        started = time.monotonic_ns()
        built = subprocess.run([docker, *command_arguments(tag, token, context, load=True)], cwd=repository,
                               env=environment, capture_output=True, timeout=300, check=False)
        if built.returncode != 0:
            raise RuntimeError("Docker E04 readiness build failed")
        status, body = request(socket, "GET", image_path)
        value = json.loads(body) if body else None
        if (status != 200 or not isinstance(value, dict) or not isinstance(value.get("Id"), str)
                or tag not in value.get("RepoTags", [])):
            raise RuntimeError("Docker E04 readiness build did not create its exact image")
        image_id = value["Id"]
        status, body = request(socket, "GET", image_path)
        current = json.loads(body) if body else None
        if status != 200 or not isinstance(current, dict) or current.get("Id") != image_id:
            raise RuntimeError("Docker E04 readiness image identity changed before cleanup")
        payload.update({"imageID": image_id, "state": "remove-intent"})
        output.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        removed = subprocess.run([docker, "image", "rm", tag], cwd=repository, env=environment,
                                 capture_output=True, timeout=120, check=False)
        if removed.returncode != 0:
            raise RuntimeError("Docker E04 readiness image cleanup failed")
        status, _body = request(socket, "GET", image_path)
        if status != 404:
            raise RuntimeError("Docker E04 readiness image remains after exact cleanup")
        payload.update({
            "status": "passed", "durationNS": time.monotonic_ns() - started,
            "buildExitCode": built.returncode, "removeExitCode": removed.returncode,
            "imageAbsentAfterCleanup": True,
            "stdoutSHA256": hashlib.sha256(built.stdout).hexdigest(),
            "stderrSHA256": hashlib.sha256(built.stderr).hexdigest(),
        })
        output.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return payload
    except Exception as error:
        payload["status"] = "failed"
        payload["failureType"] = type(error).__name__
        if built is not None:
            payload.update({"buildExitCode": built.returncode,
                            "stdoutSHA256": hashlib.sha256(built.stdout).hexdigest(),
                            "stderrSHA256": hashlib.sha256(built.stderr).hexdigest()})
        output.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        raise
    finally:
        directory.cleanup()
