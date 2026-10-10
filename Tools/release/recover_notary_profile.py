#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# SPDX-License-Identifier: Apache-2.0
"""Validate repository notarization credentials into the designated login keychain."""

from __future__ import annotations

import json
import os
from pathlib import Path
import pwd
import re
import socket
import subprocess
import sys
from typing import Callable, Mapping, Sequence


EXPECTED_REPOSITORY = "stephenlclarke/devcontainer"
EXPECTED_REF = "refs/heads/main"
EXPECTED_RUNNER = "devcontainer-release-StevesM5Pro"
EXPECTED_HOST = "StevesM5Pro"
EXPECTED_USERNAME = "sclarke"
EXPECTED_UID = 501
EXPECTED_TEAM_ID = "4MEB7MUTAV"
PROFILE = "container-only-unattended"
COMMAND_TIMEOUT_SECONDS = 60
KEYCHAIN = Path("/Users/sclarke/Library/Keychains/login.keychain-db")
NOTARY_SECRETS = (
    "DEVCONTAINER_NOTARY_APPLE_ID",
    "DEVCONTAINER_NOTARY_TEAM_ID",
    "DEVCONTAINER_NOTARY_PASSWORD",
)


class RecoveryError(Exception):
    """A safe-to-display failure without command output or credential values."""


CommandRunner = Callable[[Sequence[str], int], subprocess.CompletedProcess[bytes]]


def _run_command(
    argv: Sequence[str], timeout: int
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
    )


def _short_host(hostname: str) -> str:
    return hostname.split(".", 1)[0]


def _canonical_keychain_output(output: bytes) -> Path:
    try:
        value = output.decode("utf-8").strip().strip('"')
    except UnicodeDecodeError as error:
        raise RecoveryError("could not identify the user's default keychain") from error
    if not value:
        raise RecoveryError("could not identify the user's default keychain")
    return Path(value).expanduser().resolve(strict=False)


def validate_context(
    environment: Mapping[str, str],
    *,
    username: str,
    uid: int,
    hostname: str,
    home: Path,
    keychain_owner_uid: int,
) -> None:
    """Reject any dispatch or host other than the explicitly designated target."""
    sha = environment.get("GITHUB_SHA", "")
    expected_sha = environment.get("RECOVERY_EXPECTED_CONTROL_SHA", "")
    if (
        environment.get("GITHUB_REPOSITORY") != EXPECTED_REPOSITORY
        or environment.get("GITHUB_REF") != EXPECTED_REF
        or not re.fullmatch(r"[0-9a-f]{40}", sha)
        or expected_sha != sha
        or environment.get("RECOVERY_PROFILE") != PROFILE
    ):
        raise RecoveryError(
            "recovery must match the expected main-branch control SHA "
            "and configured profile"
        )
    if environment.get("RUNNER_NAME") != EXPECTED_RUNNER:
        raise RecoveryError(
            "recovery runner identity does not match the designated host"
        )
    if (
        username != EXPECTED_USERNAME
        or uid != EXPECTED_UID
        or _short_host(hostname) != EXPECTED_HOST
        or home.resolve(strict=False) != Path("/Users/sclarke")
        or keychain_owner_uid != EXPECTED_UID
    ):
        raise RecoveryError(
            "recovery account, host, or login keychain owner is unexpected"
        )
    values = [environment.get(name, "") for name in NOTARY_SECRETS]
    if not all(values):
        raise RecoveryError("all three repository notary secrets are required")
    if environment[NOTARY_SECRETS[1]] != EXPECTED_TEAM_ID:
        raise RecoveryError(
            "notary team does not match the configured signing authority"
        )


def _history_is_valid(output: bytes) -> bool:
    try:
        value = json.loads(output)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    return isinstance(value, dict) and isinstance(value.get("history"), list)


def recover_profile(
    environment: Mapping[str, str],
    *,
    command_runner: CommandRunner = _run_command,
    keychain: Path = KEYCHAIN,
) -> None:
    """Store and validate the configured profile, then verify it noninteractively."""
    try:
        keychain_stat = keychain.stat(follow_symlinks=False)
    except OSError as error:
        raise RecoveryError("the designated login keychain is unavailable") from error
    if keychain.is_symlink() or not keychain.is_file():
        raise RecoveryError("the designated login keychain is not a regular file")

    username = pwd.getpwuid(os.getuid()).pw_name
    validate_context(
        environment,
        username=username,
        uid=os.getuid(),
        hostname=socket.gethostname(),
        home=Path.home(),
        keychain_owner_uid=keychain_stat.st_uid,
    )

    try:
        default_keychain = command_runner(
            ("/usr/bin/security", "default-keychain", "-d", "user"), 10
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RecoveryError("could not verify the user's default keychain") from error
    if default_keychain.returncode != 0 or _canonical_keychain_output(
        default_keychain.stdout
    ) != keychain.resolve(strict=False):
        raise RecoveryError(
            "the user's default keychain is not the designated login keychain"
        )

    arguments = (
        "/usr/bin/xcrun",
        "notarytool",
        "store-credentials",
        PROFILE,
        "--apple-id",
        environment[NOTARY_SECRETS[0]],
        "--team-id",
        environment[NOTARY_SECRETS[1]],
        "--password",
        environment[NOTARY_SECRETS[2]],
        "--keychain",
        str(keychain),
        "--validate",
    )
    try:
        stored = command_runner(arguments, COMMAND_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RecoveryError(
            "notarytool credential validation did not complete"
        ) from error
    if stored.returncode != 0:
        raise RecoveryError(
            "notarytool credential validation failed "
            f"(exit {stored.returncode}); output suppressed"
        )

    try:
        history = command_runner(
            (
                "/usr/bin/xcrun",
                "notarytool",
                "history",
                "--keychain-profile",
                PROFILE,
                "--keychain",
                str(keychain),
                "--output-format",
                "json",
            ),
            COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RecoveryError(
            "notary profile history validation did not complete"
        ) from error
    if history.returncode != 0 or not _history_is_valid(history.stdout):
        raise RecoveryError(
            "notary profile history validation failed; output suppressed"
        )


def main() -> int:
    try:
        recover_profile(os.environ)
    except RecoveryError as error:
        print(f"Notary profile recovery failed: {error}", file=sys.stderr)
        return 1
    except Exception:
        print(
            "Notary profile recovery failed due to an internal error; details suppressed.",
            file=sys.stderr,
        )
        return 1
    print("Notary profile validated in the designated login keychain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
