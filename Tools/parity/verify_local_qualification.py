#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Verify a retained local parity qualification and regenerate its reports."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
import os
import pwd
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from compare_results import compare, expected_fixtures
from parity_lib import LANES, ParityError, atomic_json, load_manifest
from validate_manifest import validate_manifest

SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
RECEIPT_NAME = "qualification.json"
ACCOUNT_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
QUALIFICATION_ROOT = (
    ACCOUNT_HOME / "Library/Application Support/ContainerFamily/retained/devcontainer/qualifications"
)
MAX_INVENTORY_FILES = 96
MAX_FILE_SIZE = 16 * 1024 * 1024
MAX_INVENTORY_SIZE = 64 * 1024 * 1024
PUBLIC_PACKAGE_IDENTITY_FIELDS = {
    "scope", "kind", "sourceCommit", "runtimeProfile", "candidateReceiptSHA256",
    "finalizationProvenanceSHA256", "trustedStateSHA256", "archiveSHA256",
    "archiveSize", "preparationSHA256", "inventorySHA256", "productionBinarySHA256",
    "signatureInventorySHA256", "referenceRuntimeFiles",
}


class QualificationError(ValueError):
    """Raised when a retained qualification cannot be trusted or replayed."""


def digest_bytes(value: bytes) -> str:
    """Return the lowercase SHA-256 digest of bytes."""

    return hashlib.sha256(value).hexdigest()


def digest_file(path: Path) -> str:
    """Hash one regular file without loading it all into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    """Raise a concise qualification error when a release invariant fails."""

    if not condition:
        raise QualificationError(message)


def parse_json_object(data: bytes, context: str) -> dict[str, Any]:
    """Parse one bounded JSON object from authenticated bytes."""
    try:
        value = json.loads(data)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise QualificationError(f"{context} is unreadable JSON") from error
    require(isinstance(value, dict), f"{context} must be a JSON object")
    return value


def load_json(path: Path, context: str) -> dict[str, Any]:
    """Read one bounded JSON object."""

    try:
        return parse_json_object(path.read_bytes(), context)
    except OSError as error:
        raise QualificationError(f"{context} is unreadable JSON") from error


def checked_relative_path(value: object, context: str) -> PurePosixPath:
    """Validate a portable relative evidence path without traversal."""

    require(isinstance(value, str) and value, f"{context} path is missing")
    require("\\" not in value, f"{context} path must use POSIX separators")
    path = PurePosixPath(value)
    require(not path.is_absolute(), f"{context} path must be relative")
    require(all(part not in {"", ".", ".."} for part in path.parts),
            f"{context} path has an unsafe component")
    require(path.as_posix() == value, f"{context} path is not normalized")
    require(value.startswith("inputs/"), f"{context} path must be under inputs/")
    require(path.suffix == ".json", f"{context} must reference JSON evidence")
    return path


def referenced_file_descriptors(value: object) -> list[dict[str, Any]]:
    """Find declared evidence references outside the inventory itself."""

    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if "path" in value and "sha256" in value:
            found.append(value)
        for key, item in value.items():
            if key != "inputFiles":
                found.extend(referenced_file_descriptors(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(referenced_file_descriptors(item))
    return found


def validate_file_reference(value: object, context: str) -> dict[str, Any]:
    """Require one unambiguous SHA-bound JSON evidence reference."""

    require(isinstance(value, dict) and set(value) == {"path", "sha256"},
            f"{context} file reference must contain exactly path and sha256")
    checked_relative_path(value["path"], context)
    require(isinstance(value["sha256"], str) and SHA256.fullmatch(value["sha256"]) is not None,
            f"{context} file reference digest is invalid")
    return value


def validate_retained_location(qualification_directory: Path,
                               trusted_qualification_sha256: str) -> os.stat_result:
    """Require the exact private account-local content-addressed receipt directory."""

    require(QUALIFICATION_ROOT.is_dir() and not QUALIFICATION_ROOT.is_symlink()
            and QUALIFICATION_ROOT.resolve(strict=True) == QUALIFICATION_ROOT
            and qualification_directory.is_dir()
            and not qualification_directory.is_symlink()
            and qualification_directory.resolve(strict=True) == qualification_directory
            and qualification_directory.parent == QUALIFICATION_ROOT
            and qualification_directory.name == trusted_qualification_sha256,
            "qualification is not in the trusted internal content-addressed store")
    try:
        home_info = ACCOUNT_HOME.stat()
        root_info = QUALIFICATION_ROOT.stat()
        qualification_info = qualification_directory.stat()
    except OSError as error:
        raise QualificationError("qualification retained storage is unavailable") from error
    require(root_info.st_uid == os.getuid() and root_info.st_mode & 0o777 == 0o700
            and qualification_info.st_uid == os.getuid()
            and qualification_info.st_mode & 0o777 == 0o700
            and root_info.st_dev == qualification_info.st_dev == home_info.st_dev,
            "qualification retained directory is not private user-owned internal storage")
    return root_info


def inventory_files(root: Path, receipt: dict[str, Any]) -> dict[str, bytes]:
    """Authenticate the exact bounded file closure declared by the receipt."""

    entries = receipt.get("inputFiles")
    require(isinstance(entries, list) and 0 < len(entries) <= MAX_INVENTORY_FILES,
            "qualification inputFiles inventory is empty or too large")
    references = referenced_file_descriptors(
        {key: value for key, value in receipt.items() if key != "inputFiles"}
    )
    expected: dict[str, str] = {}
    for descriptor in references:
        path = checked_relative_path(descriptor.get("path"), "referenced evidence")
        checksum = descriptor.get("sha256")
        require(isinstance(checksum, str) and SHA256.fullmatch(checksum) is not None,
                f"referenced evidence digest is invalid: {path}")
        require(path.as_posix() not in expected, f"duplicate evidence reference: {path}")
        expected[path.as_posix()] = checksum

    actual: dict[str, dict[str, Any]] = {}
    total_size = 0
    for entry in entries:
        require(isinstance(entry, dict) and set(entry) == {"path", "sha256", "size"},
                "inputFiles entries must contain exactly path, sha256, and size")
        path = checked_relative_path(entry.get("path"), "inputFiles")
        name = path.as_posix()
        checksum = entry.get("sha256")
        size = entry.get("size")
        require(name not in actual, f"duplicate inventory path: {name}")
        require(isinstance(checksum, str) and SHA256.fullmatch(checksum) is not None,
                f"inventory digest is invalid: {name}")
        require(type(size) is int and 0 <= size <= MAX_FILE_SIZE,
                f"inventory size is invalid or too large: {name}")
        total_size += size
        require(total_size <= MAX_INVENTORY_SIZE, "qualification inventory exceeds size limit")
        actual[name] = entry
    require(list(actual) == sorted(actual), "inputFiles inventory is not sorted")
    require(set(actual) == set(expected), "inputFiles is not the exact referenced evidence closure")

    resolved: dict[str, bytes] = {}
    for name, entry in actual.items():
        current = root
        for part in PurePosixPath(name).parts:
            current = current / part
            try:
                info = current.lstat()
            except OSError as error:
                raise QualificationError(f"inventory member is missing: {name}") from error
            require(not current.is_symlink(), f"inventory path contains a symlink: {name}")
            if current != root / name:
                require(current.is_dir() and info.st_uid == os.getuid()
                        and info.st_mode & 0o777 == 0o700
                        and info.st_dev == root.stat().st_dev,
                        f"inventory parent is not a private retained directory: {name}")
        require(current.is_file(), f"inventory member is not a regular file: {name}")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(current, flags)
        except OSError as error:
            raise QualificationError(f"inventory member could not be opened safely: {name}") from error
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode), f"inventory member is not a regular file: {name}")
            require(info.st_nlink == 1 and info.st_uid == os.getuid()
                    and info.st_dev == root.stat().st_dev,
                    f"inventory member has unsafe ownership or storage: {name}")
            require(info.st_size == entry["size"], f"inventory size differs: {name}")
            content = stream.read(MAX_FILE_SIZE + 1)
        require(len(content) == entry["size"], f"inventory member changed size: {name}")
        require(digest_bytes(content) == entry["sha256"] == expected[name],
                f"inventory digest differs: {name}")
        try:
            json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
            raise QualificationError(f"inventory member is not valid JSON: {name}") from error
        resolved[name] = content
    return resolved


def provider_tool_fields(split_provider_schema: bool) -> dict[str, set[str]]:
    """Return one exact closed receipt shape selected by the checked-in source lock."""
    fields = {
        "docker": {"version", "sha256", "buildxVersion", "buildxSHA256",
                   "engineVersion", "engineCommit", "engineApiVersion",
                   "engineSHA256", "engineEvidence"},
        "dockerCompose": {"version", "sha256", "bottleSHA256"},
        "appleStock": {"version", "commit", "containerSHA256", "apiServerSHA256",
                       "apiServerEvidence"},
        "colima": {"version", "sha256"},
        "vscode": {"version", "commit", "archiveSHA256", "vsixSHA256", "launcherSHA256"},
    }
    if split_provider_schema:
        fields["containerRuntime"] = {
            "repository", "commit", "releaseTag", "archiveSHA256", "containerSHA256",
            "apiServerSHA256", "preparationSHA256", "inventorySHA256", "provenanceSHA256",
            "provenancePreparationSHA256", "runtimePayloadSHA256", "nativeCompiledChainSHA256",
            "apiServerEvidence"}
        fields["containerCompose"] = {
            "repository", "version", "commit", "archiveSHA256", "provenanceSHA256",
            "composeSHA256", "preparationSHA256", "inventorySHA256", "signedAndNotarized",
            "distributionReady", "identityEvidence"}
    else:
        fields["containerCompose"] = {
            "version", "commit", "containerSHA256", "composeSHA256", "apiServerSHA256",
            "apiServerEvidence"}
    return fields


def validate_receipt(receipt: dict[str, Any], repository: Path,
                     expected_source_commit: str) -> tuple[set[str], set[str]]:
    """Check schema, source/harness identity, exact fixture universe, and restoration."""

    required = {
        "schemaVersion", "scope", "executedLocally", "status", "sourceCommit",
        "sourceTree", "sourceDirty", "controllerSHA256", "parityHarnessSHA256",
        "finalizationProvenanceSHA256", "archiveSHA256", "trustedStateSHA256",
        "submissionID", "fixtureCounts", "restoration", "guardCleared", "enginePins",
        "providerTools", "laneResults", "comparisons", "cleanup",
        "serviceJournalReceipts", "activeRuntimeProofs", "startupPreflights", "inputFiles",
    }
    require(set(receipt) == required, "qualification receipt fields do not match schema 1")
    require(receipt.get("schemaVersion") == 1
            and receipt.get("scope") == "local-native-package-parity-qualification"
            and receipt.get("executedLocally") is True
            and receipt.get("status") == "passed",
            "qualification receipt is not a passed local schema-1 run")
    require(receipt.get("sourceCommit") == expected_source_commit
            and COMMIT.fullmatch(str(receipt.get("sourceCommit", ""))) is not None,
            "qualification source commit differs from the requested commit")
    require(COMMIT.fullmatch(str(receipt.get("sourceTree", ""))) is not None
            and receipt.get("sourceDirty") is False,
            "qualification source tree is malformed or dirty")
    for field in ("controllerSHA256", "parityHarnessSHA256", "finalizationProvenanceSHA256",
                  "archiveSHA256", "trustedStateSHA256"):
        require(isinstance(receipt.get(field), str)
                and SHA256.fullmatch(receipt[field]) is not None,
                f"qualification {field} is invalid")
    controller = repository / "Tools/parity/qualify_finalized_package.py"
    require(controller.is_file() and not controller.is_symlink()
            and digest_file(controller) == receipt["controllerSHA256"],
            "qualification controller digest differs from the maintained controller source")
    require(isinstance(receipt.get("submissionID"), str)
            and re.fullmatch(r"[0-9a-fA-F-]{36}", receipt["submissionID"]) is not None,
            "qualification notary submission ID is invalid")
    require(receipt.get("guardCleared") is True,
            "qualification host guard is not clear")

    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository,
                                check=True, capture_output=True, text=True).stdout.strip()
        tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=repository,
                              check=True, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"],
                               cwd=repository, check=True, capture_output=True,
                               text=True).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise QualificationError("could not verify the current Git checkout") from error
    require(commit == expected_source_commit == receipt["sourceCommit"],
            "current Git commit differs from the qualification")
    require(tree == receipt["sourceTree"], "current Git tree differs from the qualification")
    require(not dirty, "current Git checkout is not clean")

    manifest_path = repository / "Tests/Parity/manifest.json"
    manifest = load_manifest(manifest_path)
    pins = manifest.get("referencePins", {})
    require(receipt.get("enginePins") == pins.get("docker"),
            "qualification Docker engine pins differ from the checked-in manifest")
    _, provider_selection = locked_provider_format(repository)
    split_provider_schema = provider_selection["format"] == "signed-compose-q-runtime"
    providers = receipt.get("providerTools")
    provider_fields = provider_tool_fields(split_provider_schema)
    require(isinstance(providers, dict) and set(providers) == set(provider_fields),
            "qualification provider tool inventory is incomplete")
    for name, tool in providers.items():
        require(isinstance(tool, dict) and set(tool) == provider_fields[name],
                f"qualification {name} tool fields do not match schema 1")
        if "version" in provider_fields[name]:
            require(isinstance(tool.get("version"), str) and tool["version"],
                    f"qualification {name} version is missing")
        for key, value in tool.items():
            if key.lower().endswith("sha256"):
                require(isinstance(value, str) and SHA256.fullmatch(value) is not None,
                        f"qualification {name}.{key} is invalid")
            elif key == "commit":
                require(isinstance(value, str) and COMMIT.fullmatch(value) is not None,
                        f"qualification {name} commit is invalid")
        for key in ("engineEvidence", "apiServerEvidence", "identityEvidence"):
            if key in tool:
                validate_file_reference(tool[key], f"qualification {name} {key}")
    require(providers["docker"].get("version") == pins["docker"]["cliVersion"]
            and providers["docker"].get("sha256") == pins["docker"]["cliSHA256"]
            and providers["docker"].get("buildxVersion") == pins["docker"]["buildxVersion"]
            and providers["docker"].get("buildxSHA256") == pins["docker"]["buildxSHA256"]
            and providers["docker"].get("engineVersion") == pins["docker"]["engineVersion"]
            and providers["docker"].get("engineCommit") == pins["docker"]["engineCommit"]
            and providers["docker"].get("engineApiVersion") == pins["docker"]["engineApiVersion"]
            and providers["docker"].get("engineSHA256") == pins["docker"]["engineSHA256"],
            "qualification Docker CLI differs from checked-in pins")
    require(providers["dockerCompose"].get("version") == pins["docker"]["composeVersion"]
            and providers["dockerCompose"].get("sha256") == pins["docker"]["composeSHA256"]
            and providers["dockerCompose"].get("bottleSHA256") == pins["docker"]["composeBottleSHA256"],
            "qualification Docker Compose differs from checked-in pins")
    require(providers["appleStock"].get("version") == pins["appleContainer"]["stableVersion"]
            and providers["appleStock"].get("commit") == pins["appleContainer"]["stableCommit"],
            "qualification stock Apple provider differs from checked-in pins")
    if split_provider_schema:
        runtime_pin = pins.get("containerRuntime")
        compose_pin = pins["containerCompose"]
        runtime = providers["containerRuntime"]
        compose = providers["containerCompose"]
        require(isinstance(runtime_pin, dict)
                and set(runtime_pin) == {"repository", "stableCommit"}
                and runtime.get("repository") == runtime_pin.get("repository")
                and runtime.get("commit") == runtime_pin.get("stableCommit")
                and isinstance(compose_pin, dict)
                and set(compose_pin) == {"source", "stableVersion", "stableCommit"}
                and compose.get("repository") == compose_pin.get("source")
                and compose.get("version") == compose_pin.get("stableVersion")
                and compose.get("commit") == compose_pin.get("stableCommit"),
                "qualification Compose and Container runtime commits are not independently pinned")
        require(compose.get("signedAndNotarized") is True
                and type(compose.get("distributionReady")) is bool,
                "qualification signed Compose evidence has invalid distribution status")
    else:
        require(providers["containerCompose"].get("version") == pins["containerCompose"]["stableVersion"]
                and providers["containerCompose"].get("commit") == pins["containerCompose"]["stableCommit"],
                "qualification container-compose provider differs from checked-in pins")
    vscode = pins["vscode"]
    require(providers["vscode"].get("version") == vscode["version"]
            and providers["vscode"].get("commit") == vscode["commit"]
            and providers["vscode"].get("archiveSHA256") == vscode["archiveSHA256"]
            and providers["vscode"].get("vsixSHA256")
            == vscode["devContainersExtension"]["vsixSHA256"],
            "qualification VS Code inputs differ from checked-in pins")
    parity_directory = repository / "Tools/parity"
    sys.path.insert(0, str(parity_directory))
    try:
        from run_lane import parity_harness_sha256

        expected_harness = parity_harness_sha256(repository)
    except (ImportError, OSError, ValueError) as error:
        raise QualificationError("could not verify the maintained parity harness") from error
    require(receipt.get("parityHarnessSHA256") == expected_harness,
            "qualification parity harness differs from the checked-in source")
    cli_fixtures = expected_fixtures(manifest_path, "cli")
    vscode_fixtures = expected_fixtures(manifest_path, "vscode")
    counts = receipt.get("fixtureCounts")
    require(counts == {"cliPerLane": len(cli_fixtures), "vscodePerLane": len(vscode_fixtures),
                       "laneCount": len(LANES),
                       "totalLaneFixtureResults": (len(cli_fixtures) + len(vscode_fixtures)) * len(LANES)},
            "qualification fixture counts differ from the implemented manifest")
    restoration = receipt.get("restoration")
    require(restoration == {lane: "restored" for lane in LANES},
            "qualification did not restore all runtime lanes")

    lane_results = receipt.get("laneResults")
    require(isinstance(lane_results, dict) and set(lane_results) == set(LANES),
            "qualification does not contain exactly the three parity lanes")
    for lane in LANES:
        value = lane_results[lane]
        require(isinstance(value, dict) and set(value) == {"cli", "vscode"},
                f"{lane} result suites are incomplete")
        for suite in ("cli", "vscode"):
            row = value[suite]
            expected_fields = {"status", "results", "fingerprint"}
            if suite == "vscode":
                expected_fields.add("security")
            require(isinstance(row, dict) and set(row) == expected_fields
                    and row.get("status") == "passed",
                    f"{lane} {suite} result did not pass")
            validate_file_reference(row.get("results"), f"{lane} {suite} results")
            validate_file_reference(row.get("fingerprint"), f"{lane} {suite} fingerprint")
            if suite == "vscode":
                security = row.get("security")
                require(isinstance(security, dict)
                        and set(security) == {"status", "path", "sha256"}
                        and security.get("status") == "passed",
                        f"{lane} {suite} security evidence reference is incomplete")
                validate_file_reference({"path": security["path"],
                                         "sha256": security["sha256"]},
                                        f"{lane} {suite} security")
    comparisons = receipt.get("comparisons")
    require(isinstance(comparisons, dict)
            and set(comparisons) == {"cli", "vscode", "releaseManifest"}
            and all(isinstance(value, dict)
                    and set(value) == {"status", "path", "sha256"}
                    and value.get("status") == "passed"
                    and SHA256.fullmatch(str(value.get("sha256", ""))) is not None
                    for value in comparisons.values()),
            "qualification comparisons or release manifest did not pass")
    cleanup = receipt.get("cleanup")
    require(isinstance(cleanup, dict) and set(cleanup) == {*LANES, "host"}
            and all(isinstance(cleanup[lane], dict)
                    and cleanup[lane].get("status") == "restored" for lane in LANES)
            and isinstance(cleanup["host"], dict)
            and cleanup["host"].get("status") == "restored"
            and cleanup["host"].get("hostGuardCleared") is True
            and receipt.get("guardCleared") is True,
            "qualification cleanup evidence is incomplete or failed")
    for lane in LANES:
        expected_cleanup_fields = {"status", "cliCleanupComplete", "vscodeCleanupComplete",
                                   "path", "sha256"}
        if lane == "docker":
            expected_cleanup_fields.add("colima")
        else:
            expected_cleanup_fields.update({"providerStopped", "serviceRestored",
                                            "serviceJournalReceiptSHA256"})
        row = cleanup[lane]
        require(set(row) == expected_cleanup_fields
                and row.get("cliCleanupComplete") is True
                and row.get("vscodeCleanupComplete") is True
                and SHA256.fullmatch(str(row.get("sha256", ""))) is not None,
                f"{lane} cleanup receipt reference is incomplete")
        if lane == "docker":
            require(isinstance(row.get("colima"), dict)
                    and set(row["colima"]) == {"initial", "final", "restored",
                                                "startedByController"}
                    and row["colima"].get("restored") is True
                    and isinstance(row["colima"].get("startedByController"), bool)
                    and row["colima"].get("initial") == row["colima"].get("final"),
                    "Docker cleanup receipt does not bind the original Colima state")
        else:
            require(row.get("providerStopped") is True
                    and row.get("serviceRestored") is True
                    and SHA256.fullmatch(str(row.get("serviceJournalReceiptSHA256", ""))) is not None,
                    f"{lane} cleanup receipt does not bind service restoration")
        validate_file_reference({"path": row.get("path"), "sha256": row.get("sha256")},
                                f"{lane} cleanup")
    host = cleanup["host"]
    require(set(host) == {"status", "initialColima", "finalColima", "hostGuardCleared",
                          "path", "sha256"}
            and host.get("initialColima") == host.get("finalColima")
            and SHA256.fullmatch(str(host.get("sha256", ""))) is not None,
            "host cleanup reference is incomplete")
    validate_file_reference({"path": host.get("path"), "sha256": host.get("sha256")},
                            "host cleanup")
    journals = receipt.get("serviceJournalReceipts")
    require(isinstance(journals, dict) and set(journals) == {"apple-stock", "container-compose"}
            and all(isinstance(value, dict)
                    and set(value) == {"path", "sha256", "ownerSHA256", "records", "seal"}
                    and isinstance(value.get("ownerSHA256"), str)
                    and SHA256.fullmatch(value["ownerSHA256"]) is not None
                    and type(value.get("records")) is int and value["records"] > 0
                    and isinstance(value.get("seal"), str)
                    and SHA256.fullmatch(value["seal"]) is not None
                    for value in journals.values()),
            "qualification service restoration journal is incomplete")
    for lane, journal in journals.items():
        validate_file_reference({"path": journal["path"], "sha256": journal["sha256"]},
                                f"{lane} service journal")
    proofs = receipt.get("activeRuntimeProofs")
    preflights = receipt.get("startupPreflights")
    native_lanes = {"apple-stock", "container-compose"}
    require(isinstance(proofs, dict) and set(proofs) == native_lanes
            and isinstance(preflights, dict) and set(preflights) == native_lanes,
            "qualification native startup or activation proof is incomplete")
    for lane in native_lanes:
        validate_file_reference(proofs[lane], f"{lane} activation proof")
        row = preflights[lane]
        require(isinstance(row, dict) and set(row) == {"startup", "journal"},
                f"{lane} startup proof references are incomplete")
        for kind in ("startup", "journal"):
            validate_file_reference(row[kind], f"{lane} {kind} proof")

    return cli_fixtures, vscode_fixtures


def authenticate_finalization(repository: Path, finalized_directory: Path,
                              trusted_provenance_sha256: str, accepted_state: Path,
                              expected_source_commit: str, receipt: dict[str, Any]) -> None:
    """Use maintained read-only finalization checks and bind accepted IDs."""

    release_directory = repository / "Tools/release"
    sys.path.insert(0, str(release_directory))
    try:
        import prepare_finalized_package

        proof, _ = prepare_finalized_package.read_provenance(
            finalized_directory, trusted_provenance_sha256
        )
        signer, finalizer, _ = prepare_finalized_package.load_helpers(repository)
        prepare_finalized_package.verify_state(
            proof, accepted_state, proof["trustedStateSHA256"], signer, finalizer
        )
        prepare_finalized_package.verify_tool_identity(proof, repository)
    except (OSError, ValueError, KeyError, ImportError, subprocess.SubprocessError) as error:
        raise QualificationError("finalized package or accepted notary state failed admission") from error
    require(proof.get("sourceCommit") == expected_source_commit == receipt["sourceCommit"],
            "finalized package source differs from qualification")
    require(proof.get("runtimeProfile") == "stock",
            "qualification package is not the finalized stock profile")
    bindings = {
        "finalizationProvenanceSHA256": trusted_provenance_sha256,
        "archiveSHA256": proof.get("archiveSHA256"),
        "trustedStateSHA256": proof.get("trustedStateSHA256"),
        "submissionID": proof.get("submissionID"),
    }
    for field, value in bindings.items():
        require(receipt.get(field) == value, f"qualification {field} differs from finalized package")


def provider_helper_identity(repository: Path, lane: str) -> dict[str, Any]:
    """Re-admit provider helper hashes from the checked-in lock and retained inventory."""

    testing = repository / "Tools/testing"
    bazel = repository / "Tools/bazel"
    sys.path.insert(0, str(bazel))
    sys.path.insert(0, str(testing))
    prepare_releases = importlib.import_module("prepare_releases")
    released_engine = importlib.import_module("released_engine")
    require(Path(prepare_releases.__file__).resolve() == (bazel / "prepare_releases.py").resolve(strict=True)
            and Path(released_engine.__file__).resolve() == (testing / "released_engine.py").resolve(strict=True),
            "provider inventory helpers resolved outside the checked-in source")
    lock = load_json(bazel / "releases.lock.json", "checked-in provider release lock")
    try:
        retained = ACCOUNT_HOME / "Library/Application Support/ContainerFamily/retained/workflow"
        # Historical CAS replay must not depend on whichever runtime is active
        # today. The checked-in lock and immutable preparation are its authority.
        selected = released_engine.admit_provider_runtime_source(lock, lane, retained)
        asset = selected["asset"]
        prepared = prepare_releases.require_retained(
            asset, retained / "release-objects" / asset["sha256"],
            retained / "prepared-releases", retained / "prepared-receipts")
        require(selected["prepared"] == prepared,
                f"{lane} prepared runtime differs from its authenticated release")
        native_activation = importlib.import_module("native_activation")
        require(Path(native_activation.__file__).resolve() ==
                (testing / "native_activation.py").resolve(strict=True),
                "activation identity helper resolved outside the checked-in source")
        activation_spec = native_activation.specification(prepared, lane)
        activation_receipt = digest_bytes(json.dumps(
            activation_spec, sort_keys=True, separators=(",", ":")).encode())
        specification = {"schemaVersion": 1, "assetSHA256": asset["sha256"],
                         "layout": prepare_releases.layout(asset)}
        preparation = prepared["preparationSHA256"]
        receipt_path = retained / "prepared-receipts" / f"{preparation}.json"
        info = receipt_path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and info.st_nlink == 1 and info.st_mode & 0o777 == 0o600
                and info.st_dev == retained.stat().st_dev and info.st_size <= MAX_INVENTORY_SIZE,
                f"{lane} retained provider receipt is not a private standalone file")
        prepared_receipt = load_json(receipt_path, f"{lane} retained provider receipt")
        require(prepared_receipt.get("specification") == specification
                and isinstance(prepared_receipt.get("inventory"), dict),
                f"{lane} retained provider receipt does not match the checked-in asset")
        helpers = {
            "container-core-images": "libexec/container/plugins/container-core-images/bin/container-core-images",
            "machine-apiserver": "libexec/container/plugins/machine-apiserver/bin/machine-apiserver",
        }
        helper_evidence = {}
        for name, relative in helpers.items():
            inventory_path = (Path("Payload") / relative if specification["layout"]["format"] == "pkg"
                              else Path(relative)).as_posix()
            item = prepared_receipt["inventory"].get(inventory_path)
            require(isinstance(item, dict) and item.get("kind") == "file"
                    and isinstance(item.get("mode"), int) and item["mode"] & 0o111
                    and isinstance(item.get("sha256"), str)
                    and SHA256.fullmatch(item["sha256"]) is not None,
                    f"{lane} retained inventory omits an executable provider helper")
            helper_evidence[name] = {"path": relative, "sha256": item["sha256"]}
        return {
            "assetSHA256": asset["sha256"],
            "preparationSHA256": preparation,
            "preparedReceiptSHA256": digest_file(receipt_path),
            "inventorySHA256": prepared["inventorySHA256"],
            "activationReceiptSHA256": activation_receipt,
            "activeInventorySHA256": digest_bytes(json.dumps(
                activation_spec["inventory"], sort_keys=True, separators=(",", ":")).encode()),
            "helperExecutables": helper_evidence,
        }
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise QualificationError(f"{lane} locked provider helper inventory could not be admitted") from error


def locked_provider_format(repository: Path) -> tuple[dict, dict]:
    """Resolve the closed provider-input schema from the checked-in source lock."""
    bazel = repository / "Tools/bazel"
    sys.path.insert(0, str(bazel))
    try:
        import prepare_releases
        require(Path(prepare_releases.__file__).resolve() ==
                (bazel / "prepare_releases.py").resolve(strict=True),
                "release input adapter resolved outside the checked-in source")
        lock = load_json(bazel / "releases.lock.json", "checked-in provider release lock")
        return lock, prepare_releases.select_compose_runtime_assets(lock)
    except (ImportError, OSError, ValueError) as error:
        raise QualificationError("could not select locked Compose/runtime input schema") from error
    finally:
        sys.path.remove(str(bazel))


def authenticate_active_runtime_preflights(receipt: dict[str, Any], inventory: dict[str, bytes],
                                                  repository: Path) -> None:
    """Replay sealed startup and activation proof from immutable prepared releases."""
    tools = receipt["providerTools"]
    for lane in ("apple-stock", "container-compose"):
        helper = provider_helper_identity(repository, lane)
        proof_path = receipt["activeRuntimeProofs"][lane]["path"]
        startup_path = receipt["startupPreflights"][lane]["startup"]["path"]
        journal_path = receipt["startupPreflights"][lane]["journal"]["path"]
        require(all(path in inventory for path in (proof_path, startup_path, journal_path)),
                f"{lane} startup or activation proof is missing")
        proof = parse_json_object(inventory[proof_path],
                                  f"{lane} activation proof")
        expected = {"schemaVersion": 1, "lane": lane,
                    "archiveSHA256": helper["assetSHA256"],
                    "preparationSHA256": helper["preparationSHA256"],
                    "preparedInventorySHA256": helper["inventorySHA256"],
                    "activationReceiptSHA256": helper["activationReceiptSHA256"],
                    "activeInventorySHA256": helper["activeInventorySHA256"],
                    "preparedReceiptSHA256": helper["preparedReceiptSHA256"]}
        require(set(proof) == set(expected) | {"sourceCommit"}
                and all(proof.get(key) == value for key, value in expected.items())
                and COMMIT.fullmatch(str(proof.get("sourceCommit", ""))) is not None,
                f"{lane} activation proof differs from immutable prepared release")
        startup = parse_json_object(inventory[startup_path],
                                    f"{lane} startup preflight")
        journal = parse_json_object(inventory[journal_path],
                                    f"{lane} startup journal")
        provider = tools["appleStock" if lane == "apple-stock" else
                         ("containerRuntime" if "containerRuntime" in tools else "containerCompose")]
        require(proof["sourceCommit"] == provider["commit"],
                f"{lane} activation source commit differs from the pinned provider")
        require(set(startup) == {"schemaVersion", "lane", "status", "sourceCommit",
                                 "providerRuntimeActivation", "apiServerSHA256",
                                 "containerSHA256", "apiReadiness", "serviceState",
                                 "serviceRestored", "primaryFailureType", "primaryFailureSHA256",
                                 "primaryFailureRetentionErrorType", "cleanupFailureType",
                                 "startupJournalSHA256"}
                and startup.get("schemaVersion") == 1 and startup.get("lane") == lane
                and startup.get("status") == "passed"
                and startup.get("sourceCommit") == receipt["sourceCommit"]
                and startup.get("providerRuntimeActivation") == {
                    key: proof[key] for key in ("archiveSHA256", "sourceCommit", "preparationSHA256",
                                                "preparedInventorySHA256", "activationReceiptSHA256",
                                                "activeInventorySHA256")}
                and startup.get("apiServerSHA256") == provider["apiServerSHA256"]
                and startup.get("containerSHA256") == provider["containerSHA256"]
                and startup.get("apiReadiness") == "passed"
                and startup.get("serviceState") == "restored"
                and startup.get("serviceRestored") is True
                and startup.get("startupJournalSHA256") == receipt["startupPreflights"][lane]["journal"]["sha256"]
                and startup.get("primaryFailureType") is None
                and startup.get("primaryFailureSHA256") is None
                and startup.get("primaryFailureRetentionErrorType") is None
                and startup.get("cleanupFailureType") is None,
                f"{lane} startup preflight did not prove restored readiness")
        require(set(journal) == {"schemaVersion", "lane", "status", "ownerSHA256", "records", "seal"}
                and journal.get("schemaVersion") == 1 and journal.get("lane") == lane
                and journal.get("status") == "restored"
                and isinstance(journal.get("ownerSHA256"), str)
                and SHA256.fullmatch(journal["ownerSHA256"]) is not None
                and type(journal.get("records")) is int and journal["records"] > 0
                and isinstance(journal.get("seal"), str)
                and SHA256.fullmatch(journal["seal"]) is not None,
                f"{lane} startup journal proof is incomplete")


def authenticate_provider_evidence(receipt: dict[str, Any], inventory: dict[str, bytes],
                                   repository: Path) -> None:
    """Bind sanitized provider/API observations to manifest pins and exact frontends."""

    tools = receipt["providerTools"]
    docker = tools["docker"]
    compose = tools["dockerCompose"]
    docker_evidence = parse_json_object(inventory[docker["engineEvidence"]["path"]],
                                        "Docker oracle engine evidence")
    require(docker_evidence == {
        "schemaVersion": 1,
        "source": "docker-oracle",
        "clientVersion": docker["version"],
        "dockerCLISHA256": docker["sha256"],
        "buildxVersion": docker["buildxVersion"],
        "buildxSHA256": docker["buildxSHA256"],
        "engineVersion": docker["engineVersion"],
        "engineCommit": docker["engineCommit"],
        "engineApiVersion": docker["engineApiVersion"],
        "engineSHA256": docker["engineSHA256"],
        "composeVersion": compose["version"],
        "composeSHA256": compose["sha256"],
        "bottleSHA256": compose["bottleSHA256"],
    }, "Docker engine evidence differs from pinned provider metadata")

    for lane, name in (("apple-stock", "appleStock"),):
        provider = tools[name]
        evidence = parse_json_object(inventory[provider["apiServerEvidence"]["path"]],
                                     f"{lane} provider/API evidence")
        expected = {
            "schemaVersion": 1,
            "lane": lane,
            "providerVersion": provider["version"],
            "providerCommit": provider["commit"],
            "containerSHA256": provider["containerSHA256"],
            "apiServerSHA256": provider["apiServerSHA256"],
            "preparedProvider": provider_helper_identity(repository, lane),
        }
        require(evidence == expected,
                f"{lane} provider/API evidence differs from its pins and binaries")

    _, selection = locked_provider_format(repository)
    if selection["format"] == "signed-compose-q-runtime":
        runtime = tools["containerRuntime"]
        compose = tools["containerCompose"]
        runtime_evidence = parse_json_object(
            inventory[runtime["apiServerEvidence"]["path"]], "Container runtime/API evidence")
        expected_runtime_evidence = {
            "schemaVersion": 1, "lane": "container-compose",
            "providerRepository": runtime["repository"], "providerCommit": runtime["commit"],
            "releaseTag": runtime["releaseTag"], "archiveSHA256": runtime["archiveSHA256"],
            "containerSHA256": runtime["containerSHA256"],
            "apiServerSHA256": runtime["apiServerSHA256"],
            "preparedProvider": provider_helper_identity(repository, "container-compose"),
        }
        require(runtime_evidence == expected_runtime_evidence,
                "Container runtime/API evidence differs from the independently locked runtime")
        compose_evidence = parse_json_object(
            inventory[compose["identityEvidence"]["path"]], "Compose frontend evidence")
        expected_compose_evidence = {
            "schemaVersion": 1, "source": compose["repository"], "version": compose["version"],
            "commit": compose["commit"], "archiveSHA256": compose["archiveSHA256"],
            "provenanceSHA256": compose["provenanceSHA256"],
            "composeSHA256": compose["composeSHA256"],
            "signedAndNotarized": compose["signedAndNotarized"],
            "distributionReady": compose["distributionReady"],
        }
        require(compose_evidence == expected_compose_evidence,
                "signed Compose evidence differs from its pinned frontend identity")
        retained = ACCOUNT_HOME / "Library/Application Support/ContainerFamily/retained/workflow"
        bazel = repository / "Tools/bazel"
        sys.path.insert(0, str(bazel))
        try:
            import prepare_releases
            lock = load_json(bazel / "releases.lock.json", "checked-in provider release lock")
            admitted = prepare_releases.admit_locked_compose_runtime(
                lock, retained / "release-objects", retained / "prepared-releases",
                retained / "prepared-receipts")
        except (ImportError, OSError, ValueError, KeyError, TypeError) as error:
            raise QualificationError("could not re-admit locked Compose/runtime release inputs") from error
        finally:
            sys.path.remove(str(bazel))
        require(admitted.get("format") == selection["format"]
                and admitted["containerRuntime"]["repository"] == runtime["repository"]
                and admitted["containerRuntime"]["commit"] == runtime["commit"]
                and admitted["containerRuntime"]["archiveSHA256"] == runtime["archiveSHA256"]
                and admitted["containerRuntime"]["containerSHA256"] == runtime["containerSHA256"]
                and admitted["containerRuntime"]["apiServerSHA256"] == runtime["apiServerSHA256"]
                and admitted["containerRuntime"]["preparationSHA256"] == runtime["preparationSHA256"]
                and admitted["containerRuntime"]["inventorySHA256"] == runtime["inventorySHA256"]
                and admitted["containerRuntimeProvenanceSHA256"] == runtime["provenanceSHA256"]
                and admitted["containerRuntimeProvenancePreparationSHA256"]
                == runtime["provenancePreparationSHA256"]
                and admitted["qRuntimeProvenance"]["runtimePayloadSHA256"]
                == runtime["runtimePayloadSHA256"]
                and admitted["qRuntimeProvenance"]["nativeCompiledChainSHA256"]
                == runtime["nativeCompiledChainSHA256"]
                and admitted["containerCompose"]["repository"] == compose["repository"]
                and admitted["containerCompose"]["commit"] == compose["commit"]
                and admitted["containerCompose"]["version"] == compose["version"]
                and admitted["containerCompose"]["archiveSHA256"] == compose["archiveSHA256"]
                and admitted["containerCompose"]["preparationSHA256"] == compose["preparationSHA256"]
                and admitted["containerCompose"]["inventorySHA256"] == compose["inventorySHA256"]
                and admitted["composeProviderSHA256"] == compose["composeSHA256"]
                and admitted["composeProvenanceSHA256"] == compose["provenanceSHA256"]
                and admitted["signedAndNotarized"] is compose["signedAndNotarized"]
                and admitted["distributionReady"] is compose["distributionReady"],
                "qualification provider tools differ from the authenticated lock-selected payloads")
    else:
        provider = tools["containerCompose"]
        evidence = parse_json_object(inventory[provider["apiServerEvidence"]["path"]],
                                     "container-compose provider/API evidence")
        expected = {
            "schemaVersion": 1, "lane": "container-compose",
            "providerVersion": provider["version"], "providerCommit": provider["commit"],
            "containerSHA256": provider["containerSHA256"],
            "apiServerSHA256": provider["apiServerSHA256"],
            "preparedProvider": provider_helper_identity(repository, "container-compose"),
            "composeVersion": provider["version"], "composeSHA256": provider["composeSHA256"],
        }
        require(evidence == expected,
                "legacy container-compose provider/API evidence differs from its pins and binaries")


def expected_provider_hashes(receipt: dict[str, Any], lane: str) -> dict[str, str]:
    """Return the exact provider executable map required in each lane result."""

    if lane == "docker":
        return {}
    provider = receipt["providerTools"]["appleStock" if lane == "apple-stock"
                                        else ("containerRuntime" if "containerRuntime"
                                              in receipt["providerTools"] else "containerCompose")]
    hashes = {"DEVCONTAINER_CONTAINER_BIN": provider["containerSHA256"]}
    if lane == "container-compose":
        compose = receipt["providerTools"]["containerCompose"]
        hashes["DEVCONTAINER_COMPOSE_BIN"] = compose["composeSHA256"]
    return hashes


def authenticate_result(path: Path, lane: str, suite: str, expected_ids: set[str],
                        receipt: dict[str, Any]) -> None:
    """Require exact passed fixtures and finalized package identity in one lane."""

    result = load_json(path, f"{lane} {suite} results")
    require(result.get("backend") == lane and result.get("status") == "passed",
            f"{lane} {suite} lane status is not passed")
    rows = result.get("fixtures")
    require(isinstance(rows, list), f"{lane} {suite} fixture rows are missing")
    identifiers = [row.get("id") for row in rows if isinstance(row, dict)]
    require(len(identifiers) == len(rows) and len(set(identifiers)) == len(identifiers)
            and set(identifiers) == expected_ids,
            f"{lane} {suite} does not contain the exact implemented fixture set")
    require(all(row.get("status") == "passed" for row in rows),
            f"{lane} {suite} contains a failed fixture")
    require(result.get("finalizedPackage") is not None,
            f"{lane} {suite} result omits finalized package identity")
    identity = result["finalizedPackage"]
    require(isinstance(identity, dict)
            and identity.get("sourceCommit") == receipt["sourceCommit"]
            and identity.get("archiveSHA256") == receipt["archiveSHA256"]
            and identity.get("finalizationProvenanceSHA256") == receipt["finalizationProvenanceSHA256"]
            and identity.get("trustedStateSHA256") == receipt["trustedStateSHA256"],
            f"{lane} {suite} result is bound to a different finalized package")
    require(result.get("parityHarnessSHA256") == receipt["parityHarnessSHA256"],
            f"{lane} {suite} result is bound to a different parity harness")
    require(result.get("providerBinarySHA256") == expected_provider_hashes(receipt, lane),
            f"{lane} {suite} result uses a different provider binary")
    if suite == "cli":
        require(result.get("cleanupDifferences") == [],
                f"{lane} CLI cleanup did not complete cleanly")


def public_comparison(result: dict[str, Any]) -> dict[str, Any]:
    """Require the maintained comparison's exact path-free package identity."""

    identity = result.get("finalizedPackage")
    require(isinstance(identity, dict) and set(identity) == PUBLIC_PACKAGE_IDENTITY_FIELDS,
            "comparison finalized identity is incomplete or contains local paths")
    return result


def compare_and_publish(repository: Path, receipt_bytes: bytes,
                        receipt: dict[str, Any], inventory: dict[str, bytes],
                        output_directory: Path, cli_ids: set[str],
                        vscode_ids: set[str]) -> None:
    """Replay maintained comparisons in fresh scratch and emit five official reports."""

    require(not output_directory.exists(), "output directory must be fresh")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="local-parity-import-",
                                     dir=output_directory.parent) as temporary:
        scratch = Path(temporary)
        for lane in LANES:
            for suite, base in (("cli", scratch), ("vscode", scratch / "vscode")):
                row = receipt["laneResults"][lane][suite]
                results_path = row["results"]["path"]
                fingerprint_path = row["fingerprint"]["path"]
                destination = base / lane
                destination.mkdir(parents=True, exist_ok=True)
                (destination / "results.json").write_bytes(inventory[results_path])
                (destination / "fingerprint.json").write_bytes(inventory[fingerprint_path])
                expected_ids = cli_ids if suite == "cli" else vscode_ids
                authenticate_result(destination / "results.json", lane, suite,
                                    expected_ids, receipt)
                if suite == "vscode":
                    security = row.get("security")
                    require(isinstance(security, dict)
                            and security.get("status") == "passed",
                            f"{lane} VS Code security evidence is missing or failed")
                    security_payload = parse_json_object(inventory[security["path"]],
                                                         f"{lane} VS Code security evidence")
                    require(security_payload.get("status") == "passed"
                            and security_payload.get("detectedNames") == []
                            and isinstance(security_payload.get("removedFiles"), list)
                            and set(security_payload) == {"status", "detectedNames", "removedFiles"},
                            f"{lane} VS Code security scan did not pass cleanly")
            cleanup = receipt["cleanup"][lane]
            cleanup_payload = parse_json_object(inventory[cleanup["path"]],
                                                f"{lane} cleanup receipt")
            require(cleanup_payload.get("schemaVersion") == 1
                    and cleanup_payload.get("lane") == lane
                    and cleanup_payload.get("status") == "restored"
                    and cleanup_payload.get("cliCleanupComplete") is True
                    and cleanup_payload.get("vscodeCleanupComplete") is True,
                    f"{lane} cleanup receipt does not prove restoration")
            journal = receipt["serviceJournalReceipts"].get(lane)
            if lane == "docker":
                colima = cleanup_payload.get("colima")
                require(isinstance(colima, dict) and colima.get("restored") is True
                        and colima.get("initial") == colima.get("final"),
                        "Docker lane did not restore the original Colima state")
                require(set(cleanup_payload) == {"schemaVersion", "lane", "status",
                                                 "cliCleanupComplete", "vscodeCleanupComplete",
                                                 "colima"}
                        and cleanup_payload["colima"] == cleanup["colima"],
                        "Docker cleanup receipt differs from its authenticated summary")
            else:
                require(isinstance(journal, dict), f"{lane} service journal receipt is missing")
                require(cleanup_payload.get("providerStopped") is True
                        and cleanup_payload.get("serviceRestored") is True
                        and cleanup_payload.get("initialColima")
                        == receipt["cleanup"]["host"]["initialColima"]
                        and cleanup_payload.get("finalColima")
                        == receipt["cleanup"]["host"]["finalColima"]
                        and cleanup_payload.get("serviceJournalReceiptSHA256") == journal.get("sha256"),
                        f"{lane} provider or service restoration is incomplete")
                journal_payload = parse_json_object(inventory[journal["path"]],
                                                    f"{lane} service journal receipt")
                require(journal_payload.get("schemaVersion") == 1
                        and journal_payload.get("lane") == lane
                        and journal_payload.get("status") == "restored"
                        and journal_payload.get("ownerSHA256") == journal.get("ownerSHA256")
                        and journal_payload.get("records") == journal.get("records")
                        and journal_payload.get("seal") == journal.get("seal")
                        and set(journal_payload) == {"schemaVersion", "lane", "status",
                                                     "ownerSHA256", "records", "seal"},
                        f"{lane} service journal did not prove restored host state")
                require(set(cleanup_payload) == {"schemaVersion", "lane", "status",
                                                 "cliCleanupComplete", "vscodeCleanupComplete",
                                                 "initialColima", "finalColima",
                                                 "providerStopped", "serviceRestored",
                                                 "serviceJournalReceiptSHA256"}
                        and cleanup_payload["initialColima"]
                        == receipt["cleanup"]["host"]["initialColima"]
                        and cleanup_payload["finalColima"]
                        == receipt["cleanup"]["host"]["finalColima"]
                        and cleanup_payload["serviceJournalReceiptSHA256"]
                        == cleanup["serviceJournalReceiptSHA256"],
                        f"{lane} cleanup receipt differs from its authenticated summary")
        host_cleanup = receipt["cleanup"]["host"]
        host_payload = parse_json_object(inventory[host_cleanup["path"]],
                                         "host cleanup receipt")
        require(host_payload.get("schemaVersion") == 1
                and host_payload.get("status") == "restored"
                and host_payload.get("hostGuardCleared") is True
                and host_payload.get("initialColima") == host_payload.get("finalColima")
                and isinstance(host_payload.get("initialServiceSetSHA256"), str)
                and SHA256.fullmatch(host_payload["initialServiceSetSHA256"]) is not None
                and host_payload.get("initialServiceSetSHA256")
                == host_payload.get("finalServiceSetSHA256")
                and type(host_payload.get("initialServiceCount")) is int
                and host_payload["initialServiceCount"] >= 0
                and host_payload.get("initialServiceCount")
                == host_payload.get("finalServiceCount")
                and host_payload.get("restoration") == {lane: "restored" for lane in LANES}
                and set(host_payload) == {
                    "schemaVersion", "status", "initialColima", "finalColima",
                    "initialServiceSetSHA256", "finalServiceSetSHA256",
                    "initialServiceCount", "finalServiceCount", "hostGuardCleared",
                    "restoration",
                }
                and host_payload.get("initialColima") == host_cleanup["initialColima"]
                and host_payload.get("finalColima") == host_cleanup["finalColima"]
                and host_payload.get("hostGuardCleared") == host_cleanup["hostGuardCleared"],
                "host cleanup did not clear its guard or restore Colima")

        manifest_path = repository / "Tests/Parity/manifest.json"
        try:
            validate_manifest(load_manifest(manifest_path), release=True)
        except (OSError, ValueError, ParityError) as error:
            raise QualificationError("current release parity manifest is invalid") from error

        generated: dict[str, tuple[dict[str, Any], str]] = {}
        for suite, root, ids in (("cli", scratch, cli_ids),
                                 ("vscode", scratch / "vscode", vscode_ids)):
            result, markdown = compare(root, ids, suite)
            require(result.get("status") == "passed"
                    and result.get("evidenceStatus") == "passed"
                    and result.get("timingStatus") == "passed"
                    and result.get("functionalParityStatus") == "passed",
                    f"replayed {suite} parity comparison did not pass")
            expected_comparison = receipt["comparisons"][suite]
            saved = parse_json_object(inventory[expected_comparison["path"]],
                                      f"retained {suite} comparison")
            require(saved == result,
                    f"replayed {suite} comparison differs from retained comparison")
            result["localQualification"] = {
                "receiptSHA256": digest_bytes(receipt_bytes),
                "executedLocally": True,
                "sourceCommit": receipt["sourceCommit"],
                "controllerSHA256": receipt["controllerSHA256"],
            }
            result = public_comparison(result)
            generated[suite] = (result, markdown)

        manifest_result = receipt["comparisons"]["releaseManifest"]
        recorded_manifest = parse_json_object(inventory[manifest_result["path"]],
                                              "retained release manifest validation")
        require(recorded_manifest == {
            "schemaVersion": 1,
            "status": "passed",
            "sourceCommit": receipt["sourceCommit"],
            "manifestSHA256": digest_file(repository / "Tests/Parity/manifest.json"),
            "validator": "Tools/parity/validate_manifest.py --release",
            "exitCode": 0,
        },
                "retained release manifest validation did not pass")

        staged_output = scratch / "official-output"
        staged_output.mkdir()
        atomic_json(staged_output / "comparison.json", generated["cli"][0])
        (staged_output / "matrix.md").write_text(generated["cli"][1], encoding="utf-8")
        vscode_output = staged_output / "vscode"
        vscode_output.mkdir()
        atomic_json(vscode_output / "comparison.json", generated["vscode"][0])
        (vscode_output / "matrix.md").write_text(generated["vscode"][1], encoding="utf-8")
        (staged_output / "local-qualification.json").write_bytes(receipt_bytes)
        os.rename(staged_output, output_directory)


def verify_local_qualification(repository: Path, qualification_directory: Path,
                               trusted_qualification_sha256: str,
                               expected_source_commit: str,
                               finalized_directory: Path,
                               finalization_provenance_sha256: str,
                               accepted_state: Path,
                               output_directory: Path) -> None:
    """Authenticate a local qualification and regenerate clean comparison reports."""

    require(SHA256.fullmatch(trusted_qualification_sha256) is not None,
            "trusted qualification SHA-256 is invalid")
    require(SHA256.fullmatch(finalization_provenance_sha256) is not None,
            "trusted finalization SHA-256 is invalid")
    require(COMMIT.fullmatch(expected_source_commit) is not None,
            "expected source commit is invalid")
    require(repository.is_absolute() and repository.is_dir() and not repository.is_symlink(),
            "repository path must be an existing non-symlink absolute directory")
    require(qualification_directory.is_absolute() and qualification_directory.is_dir()
            and not qualification_directory.is_symlink(),
            "qualification directory must be an existing non-symlink absolute directory")
    require(repository.resolve(strict=True) == repository
            and qualification_directory.resolve(strict=True) == qualification_directory,
            "repository or qualification path contains a symlink alias")
    root_info = validate_retained_location(qualification_directory,
                                          trusted_qualification_sha256)
    require(finalized_directory.is_absolute() and not finalized_directory.is_symlink()
            and accepted_state.is_absolute() and not accepted_state.is_symlink(),
            "finalized package and accepted state paths must be absolute and non-symlink")
    receipt_path = qualification_directory / RECEIPT_NAME
    require(receipt_path.is_file() and not receipt_path.is_symlink(),
            f"qualification receipt is missing: {RECEIPT_NAME}")
    try:
        descriptor = os.open(receipt_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise QualificationError("qualification receipt could not be opened safely") from error
    with os.fdopen(descriptor, "rb") as stream:
        receipt_info = os.fstat(stream.fileno())
        require(stat.S_ISREG(receipt_info.st_mode)
                and receipt_info.st_uid == os.getuid()
                and receipt_info.st_mode & 0o777 == 0o600
                and receipt_info.st_nlink == 1
                and receipt_info.st_dev == root_info.st_dev
                and receipt_info.st_size <= 1024 * 1024,
                "qualification receipt is not a private bounded standalone file")
        receipt_bytes = stream.read(1024 * 1024 + 1)
    require(len(receipt_bytes) == receipt_info.st_size,
            "qualification receipt changed size while reading")
    require(digest_bytes(receipt_bytes) == trusted_qualification_sha256,
            "qualification receipt differs from independently trusted SHA-256")
    try:
        receipt = json.loads(receipt_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise QualificationError("qualification receipt is unreadable JSON") from error
    require(isinstance(receipt, dict), "qualification receipt must be a JSON object")
    canonical_bytes = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
    require(receipt_bytes == canonical_bytes, "qualification receipt is not canonical JSON")
    cli_ids, vscode_ids = validate_receipt(receipt, repository, expected_source_commit)
    inventory = inventory_files(qualification_directory, receipt)
    authenticate_provider_evidence(receipt, inventory, repository)
    authenticate_active_runtime_preflights(receipt, inventory, repository)
    authenticate_finalization(repository, finalized_directory,
                             finalization_provenance_sha256, accepted_state,
                             expected_source_commit, receipt)
    compare_and_publish(repository, receipt_bytes, receipt, inventory,
                        output_directory, cli_ids, vscode_ids)


def parse_args() -> argparse.Namespace:
    """Parse the exact-source evidence importer inputs."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--qualification-directory", required=True, type=Path)
    parser.add_argument("--trusted-qualification-sha256", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--finalized-directory", required=True, type=Path)
    parser.add_argument("--finalization-provenance-sha256", required=True)
    parser.add_argument("--accepted-state", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    """Run the verifier with a concise fail-closed diagnostic."""

    args = parse_args()
    try:
        verify_local_qualification(
            args.repository, args.qualification_directory,
            args.trusted_qualification_sha256, args.expected_source_commit,
            args.finalized_directory, args.finalization_provenance_sha256,
            args.accepted_state, args.output_directory,
        )
    except (OSError, ValueError, ParityError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"verified local qualification and regenerated reports: {args.output_directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
