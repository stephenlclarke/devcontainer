# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

"""Describe the two authenticated private runtimes in a native package."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from pathlib import Path

from versioning import require_semantic_version


REFERENCE_FILES = frozenset({
    "node", "NODE-LICENSE.txt", "runtime-lock.json", "cli/devcontainer.js",
    "cli/dist/spec-node/devContainersSpecCLI.js", "cli/scripts/updateUID.Dockerfile",
    "cli/package.json", "cli/LICENSE.txt", "cli/ThirdPartyNotices.txt",
})
LEGAL_FILES = frozenset({"NODE-LICENSE.txt", "cli/LICENSE.txt", "cli/ThirdPartyNotices.txt"})
SHA256 = re.compile(r"[0-9a-f]{64}")


RUNTIME_ARCHIVES = {
    "node": ("node", "SPDXRef-Runtime-node", "sha256", 32,
             "https://nodejs.org/dist/v{v}/node-v{v}-darwin-arm64.tar.gz"),
    "cli": ("@devcontainers/cli", "SPDXRef-Runtime-devcontainers-cli", "sha512", 64,
            "https://registry.npmjs.org/@devcontainers/cli/-/cli-{v}.tgz"),
}


def candidate_reference(candidate: object, *, version: str, commit: str,
                        resolved_sha256: str) -> dict:
    """Require native source identity before trusting its runtime hash inventory."""
    if (not isinstance(candidate, dict) or candidate.get("schemaVersion") != 2
            or candidate.get("kind") != "unsigned-native-candidate"
            or candidate.get("version") != version or candidate.get("commit") != commit
            or candidate.get("runtimeProfile") not in {"stock", "enhanced"}
            or candidate.get("architecture") != "arm64" or candidate.get("compilationMode") != "opt"
            or candidate.get("distributionReady") is not False
            or candidate.get("dependencyLockSHA256") != resolved_sha256):
        raise ValueError("native candidate differs from selected package source or dependency lock")
    reference = candidate.get("referenceRuntime")
    if not isinstance(reference, dict) or set(reference) != {"nodeVersion", "cliVersion", "lockSHA256", "files"}:
        raise ValueError("native candidate runtime metadata is incomplete")
    return reference


def authenticate_runtime_files(reference: dict, files: dict[str, bytes]) -> None:
    """Authenticate the finite non-Mach-O payload against the candidate hashes."""
    hashes = reference["files"]
    if (not isinstance(hashes, dict) or set(hashes) != REFERENCE_FILES
            or set(files) != REFERENCE_FILES - {"node"}
            or not all(isinstance(value, str) and SHA256.fullmatch(value) for value in hashes.values())):
        raise ValueError("native runtime file inventory is incomplete")
    for name, content in files.items():
        if not content or hashlib.sha256(content).hexdigest() != hashes[name]:
            raise ValueError("native runtime file differs from candidate: " + name)
    if reference["lockSHA256"] != hashes["runtime-lock.json"]:
        raise ValueError("native runtime lock hash differs from candidate")


def runtime_lock(files: dict[str, bytes]) -> dict:
    """Decode only the supported two-runtime lock schema."""
    lock = json.loads(files["runtime-lock.json"])
    if not isinstance(lock, dict) or set(lock) != {"schemaVersion", "node", "cli"} or lock["schemaVersion"] != 1:
        raise ValueError("native runtime lock must contain exactly Node and CLI")
    return lock


def require_runtime_licenses(reference: dict, files: dict[str, bytes]) -> None:
    """Check the authenticated upstream declarations without inferring bundled licenses."""
    cli = json.loads(files["cli/package.json"])
    if (not isinstance(cli, dict) or cli.get("name") != "@devcontainers/cli"
            or cli.get("version") != reference["cliVersion"] or cli.get("license") != "MIT"):
        raise ValueError("private CLI package metadata differs from candidate")
    for name in ("NODE-LICENSE.txt", "cli/LICENSE.txt"):
        legal = " ".join(files[name].decode("utf-8").split())
        if ("Permission is hereby granted, free of charge" not in legal
                or "THE SOFTWARE IS PROVIDED" not in legal
                or "copyright notice" not in legal):
            raise ValueError("private runtime MIT declaration lacks authenticated license text: " + name)
    if not files["NODE-LICENSE.txt"].startswith(b"Node.js is licensed for use as follows:"):
        raise ValueError("Node license declaration is missing")
    if not files["cli/LICENSE.txt"].startswith(b"MIT License"):
        raise ValueError("CLI license declaration is missing")
    # Decode notices as legal text as well as checking their exact candidate hash.
    if not files["cli/ThirdPartyNotices.txt"].decode("utf-8").strip():
        raise ValueError("private CLI third-party notices are empty")


def archive_checksum(key: str, integrity: object, algorithm: str, length: int) -> bytes:
    """Decode one canonical integrity value using the runtime's required algorithm."""
    if not isinstance(integrity, str) or not integrity.startswith(algorithm + "-"):
        raise ValueError("private runtime archive integrity algorithm is invalid: " + key)
    encoded = integrity[len(algorithm) + 1:]
    try:
        checksum = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("private runtime archive integrity is invalid: " + key) from error
    if len(checksum) != length or base64.b64encode(checksum).decode() != encoded:
        raise ValueError("private runtime archive integrity length is invalid: " + key)
    return checksum


def runtime_package(key: str, entry: object, reference: dict) -> dict:
    """Project one of the two fixed archive policies into SPDX metadata."""
    name, identifier, algorithm, length, url = RUNTIME_ARCHIVES[key]
    if not isinstance(entry, dict) or set(entry) != {"version", "url", "integrity", "maxBytes"}:
        raise ValueError("private runtime lock entry is invalid: " + key)
    if not isinstance(entry.get("version"), str):
        raise ValueError("private runtime version is invalid: " + key)
    selected_version = require_semantic_version(entry["version"])
    if (selected_version != reference[key + "Version"]
            or entry["url"] != url.format(v=selected_version)
            or type(entry["maxBytes"]) is not int or entry["maxBytes"] <= 0):
        raise ValueError("private runtime version or archive URL differs: " + key)
    checksum = archive_checksum(key, entry["integrity"], algorithm, length)
    return {
        "SPDXID": identifier, "name": name, "versionInfo": selected_version,
        "downloadLocation": entry["url"], "filesAnalyzed": False,
        "checksums": [{"algorithm": algorithm.upper(), "checksumValue": checksum.hex()}],
        "licenseDeclared": "MIT", "licenseConcluded": "NOASSERTION",
        "licenseComments": "MIT is the upstream project declaration; bundled third-party license conclusions are not inferred.",
        "sourceInfo": "Original downloaded archive integrity " + entry["integrity"],
    }


def reference_packages(candidate: object, files: dict[str, bytes], *,
                       version: str, commit: str, resolved_sha256: str) -> list[dict]:
    """Bind immutable runtime metadata; Node's signature is verified separately.

    The candidate records unsigned Node bytes. Signing changes that binary, so
    this SBOM validator checks every non-Mach-O runtime file and reports the
    original distribution archive checksums, never a signed-binary checksum.
    """
    reference = candidate_reference(candidate, version=version, commit=commit,
                                    resolved_sha256=resolved_sha256)
    authenticate_runtime_files(reference, files)
    lock = runtime_lock(files)
    require_runtime_licenses(reference, files)
    return [runtime_package(key, lock[key], reference) for key in RUNTIME_ARCHIVES]


def packages_from_root(root: Path, *, version: str, commit: str, resolved: Path) -> list[dict]:
    """Read the finite native stage layout without following packaged links."""
    root = root.absolute()
    if root.parts[-3:] != ("libexec", "devcontainer", "reference"):
        raise ValueError("reference runtime root is not the native package layout")
    stage = root.parents[2]
    paths = [root / name for name in REFERENCE_FILES]
    candidate_path = stage / "share/devcontainer/candidate.json"
    for path in [candidate_path, *paths]:
        if not path.is_file() or path.stat().st_size == 0 or path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(stage)):
            raise ValueError("native runtime input is missing or linked: " + str(path))
    actual = {str(path.relative_to(root)) for path in root.rglob("*") if not path.is_dir()}
    if actual != REFERENCE_FILES:
        raise ValueError("native runtime contains unexpected files")
    files = {name: (root / name).read_bytes() for name in REFERENCE_FILES - {"node"}}
    return reference_packages(json.loads(candidate_path.read_bytes()), files,
                              version=version, commit=commit,
                              resolved_sha256=hashlib.sha256(resolved.read_bytes()).hexdigest())
