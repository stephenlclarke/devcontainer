#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

from __future__ import annotations

import os
import signal
import subprocess
import sys


TERMINATION_GRACE_SECONDS = 2
TIMEOUT_EXIT_CODE = 124


def terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    try:
        process.wait(timeout=TERMINATION_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        process.wait()
        return
    process.wait()


def main(arguments: list[str]) -> int:
    if len(arguments) < 2:
        print("usage: run-with-timeout.py SECONDS COMMAND [ARG ...]", file=sys.stderr)
        return 2
    try:
        timeout_seconds = int(arguments[0])
    except ValueError:
        print("timeout must be a positive integer", file=sys.stderr)
        return 2
    if timeout_seconds <= 0:
        print("timeout must be a positive integer", file=sys.stderr)
        return 2

    process = subprocess.Popen(arguments[1:], start_new_session=True)
    try:
        return process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        terminate_process_group(process)
        return TIMEOUT_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
