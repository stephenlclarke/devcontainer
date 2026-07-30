#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Validate the design-time differential parity manifest."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

REQUIRED_BACKENDS = {"docker", "apple-stock", "container-compose"}
IMPLEMENTED_STATUS = "implemented"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
SHA512_SRI = re.compile(r"^sha512-[A-Za-z0-9+/]{86}==$")


class ManifestError(ValueError):
    """Raised when the parity manifest violates a release invariant."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ManifestError(message)


def validate_manifest(payload: dict[str, Any], release: bool = False) -> None:
    """Validate one parsed parity manifest."""

    _require(payload.get("schemaVersion") == 1, "schemaVersion must be 1")

    references = payload.get("referencePins")
    _require(isinstance(references, dict), "referencePins must be an object")
    devcontainers = references.get("devcontainersCli")
    _require(
        isinstance(devcontainers, dict),
        "referencePins.devcontainersCli must be an object",
    )
    _require(
        devcontainers.get("source") == "https://github.com/devcontainers/cli",
        "referencePins.devcontainersCli.source must identify the upstream repository",
    )
    _require(
        GIT_COMMIT.fullmatch(str(devcontainers.get("commit", ""))) is not None,
        "referencePins.devcontainersCli.commit must be a full Git commit",
    )
    _require(
        SHA512_SRI.fullmatch(str(devcontainers.get("npmIntegrity", ""))) is not None,
        "referencePins.devcontainersCli.npmIntegrity must be a SHA-512 SRI digest",
    )
    docker = references.get("docker")
    _require(isinstance(docker, dict), "referencePins.docker must be an object")
    for field in (
        "cliSHA256",
        "cliBottleSHA256",
        "engineSHA256",
        "composeSHA256",
        "composeBottleSHA256",
    ):
        _require(
            SHA256.fullmatch(str(docker.get(field, ""))) is not None,
            f"referencePins.docker.{field} must be a SHA-256 digest",
        )

    release_host = references.get("releaseHost")
    _require(
        isinstance(release_host, dict),
        "referencePins.releaseHost must be an object",
    )
    _require(
        release_host.get("architecture") == "arm64",
        "the release host must use arm64",
    )
    for field in (
        "macOSProductVersion",
        "macOSBuildVersion",
        "xcodeVersion",
        "xcodeBuildVersion",
        "swiftVersion",
    ):
        _require(
            isinstance(release_host.get(field), str) and release_host[field],
            f"referencePins.releaseHost.{field} is required",
        )

    vscode = references.get("vscode")
    _require(isinstance(vscode, dict), "referencePins.vscode must be an object")
    _require(
        GIT_COMMIT.fullmatch(str(vscode.get("commit", ""))) is not None,
        "referencePins.vscode.commit must be a full Git commit",
    )
    _require(
        SHA256.fullmatch(str(vscode.get("archiveSHA256", ""))) is not None,
        "referencePins.vscode.archiveSHA256 must be a SHA-256 digest",
    )
    _require(
        vscode.get("platform") == "darwin-arm64",
        "the VS Code reference must use darwin-arm64",
    )
    extension = vscode.get("devContainersExtension")
    _require(
        isinstance(extension, dict),
        "referencePins.vscode.devContainersExtension must be an object",
    )
    _require(
        SHA256.fullmatch(str(extension.get("vsixSHA256", ""))) is not None,
        "the Dev Containers VSIX must have a SHA-256 digest",
    )
    _require(
        GIT_COMMIT.fullmatch(str(extension.get("embeddedCliCommit", "")))
        is not None,
        "the embedded Dev Containers CLI must have a full Git commit",
    )
    _require(
        SHA256.fullmatch(str(extension.get("embeddedCliSHA256", ""))) is not None,
        "the embedded Dev Containers CLI must have a SHA-256 digest",
    )
    _require(
        extension.get("embeddedCliVersion")
        == devcontainers.get("version"),
        "VS Code and direct parity must use the same Dev Containers CLI version",
    )
    _require(
        extension.get("embeddedCliCommit") == devcontainers.get("commit"),
        "VS Code and direct parity must use the same Dev Containers CLI commit",
    )

    policy = payload.get("releasePolicy")
    _require(isinstance(policy, dict), "releasePolicy must be an object")
    _require(
        policy.get("requireZeroFunctionalDifferences") is True,
        "stable releases must require zero functional differences",
    )
    _require(
        set(policy.get("requiredBackends", [])) == REQUIRED_BACKENDS,
        "releasePolicy.requiredBackends must contain docker, apple-stock, and container-compose",
    )

    backends = payload.get("backends")
    _require(isinstance(backends, dict), "backends must be an object")
    _require(
        set(backends) == REQUIRED_BACKENDS,
        "backends must define exactly docker, apple-stock, and container-compose",
    )
    _require(
        backends["docker"].get("role") == "oracle",
        "the docker backend must be the behavioral oracle",
    )

    fixtures = payload.get("fixtures")
    _require(isinstance(fixtures, list) and fixtures, "fixtures must be a non-empty array")

    fixture_ids: set[str] = set()
    for index, fixture in enumerate(fixtures):
        _require(isinstance(fixture, dict), f"fixture {index} must be an object")
        fixture_id = fixture.get("id")
        _require(
            isinstance(fixture_id, str) and fixture_id,
            f"fixture {index} must have an id",
        )
        _require(fixture_id not in fixture_ids, f"duplicate fixture id: {fixture_id}")
        fixture_ids.add(fixture_id)

        _require(
            set(fixture.get("backends", [])) == REQUIRED_BACKENDS,
            f"{fixture_id} must run against all three required backends",
        )
        _require(
            isinstance(fixture.get("assertions"), list) and fixture["assertions"],
            f"{fixture_id} must define semantic assertions",
        )
        _require(
            fixture.get("status") in {"planned", IMPLEMENTED_STATUS},
            f"{fixture_id} has an invalid implementation status",
        )
        if release:
            _require(
                fixture.get("status") == IMPLEMENTED_STATUS,
                f"{fixture_id} is not implemented and cannot enter a stable release",
            )

    normalization = payload.get("normalization")
    _require(isinstance(normalization, dict), "normalization must be an object")
    allowed = normalization.get("allowed")
    forbidden = normalization.get("forbidden")
    _require(isinstance(allowed, list) and allowed, "normalization.allowed is required")
    _require(
        isinstance(forbidden, list) and forbidden,
        "normalization.forbidden is required",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "manifest",
        nargs="?",
        type=Path,
        default=Path("Tests/Parity/manifest.json"),
    )
    parser.add_argument(
        "--release",
        action="store_true",
        help="require every fixture to be implemented",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = json.loads(args.manifest.read_text(encoding="utf-8"))
    try:
        validate_manifest(payload, release=args.release)
    except ManifestError as error:
        raise SystemExit(f"error: {error}") from error
    print(f"validated parity manifest: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
