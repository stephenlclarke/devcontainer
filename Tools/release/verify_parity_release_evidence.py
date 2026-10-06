#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Admit exact-source runtime parity evidence into native release assets."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import re
import shutil
import stat
import sys
import zipfile
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any


SHA256 = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
ARTIFACT = "parity-comparison"
ARTIFACT_FILES = {
    "comparison.json", "matrix.md", "vscode/comparison.json", "vscode/matrix.md",
    "local-qualification.json",
}
PERFORMANCE_POLICY = {
    "durationMetric": "fixture wall-clock seconds",
    "oracle": "docker",
    "targetFactor": 1.0,
    "target": "completed candidate duration is at most 1x Docker",
    "investigationFactor": 2.5,
    "investigationRule": "completed candidate duration is greater than 2.5x Docker",
    "failureFactor": 10.0,
    "failureRule": (
        "lane failure, incomplete evidence, missing or invalid timing, or completed "
        "candidate duration at least 10x Docker; timing failure does not alter functional parity"
    ),
}


class EvidenceError(ValueError):
    """Raised when parity evidence cannot be bound to the release candidate."""


def digest(data: bytes | Path) -> str:
    """Hash bytes or a file without loading large files into memory."""
    if isinstance(data, Path):
        result = hashlib.sha256()
        with data.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                result.update(block)
        return result.hexdigest()
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError) as error:
        raise EvidenceError(f"cannot read JSON evidence: {path.name}") from error


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def parity_harness_sha256(repository: Path) -> str:
    """Use the same explicit harness closure recorded by every native lane."""
    runner = repository / "Tools/parity/run_lane.py"
    _require(runner.is_file() and not runner.is_symlink(), "parity runner source is missing")
    try:
        declarations = [node.value for node in ast.parse(runner.read_bytes()).body
                        if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == "PARITY_HARNESS"
                                for target in node.targets)]
        _require(len(declarations) == 1, "parity runner must declare one harness closure")
        files = ast.literal_eval(declarations[0])
    except (SyntaxError, ValueError, TypeError) as error:
        raise EvidenceError("parity runner harness closure is not a literal tuple") from error
    _require(isinstance(files, tuple) and bool(files)
             and all(isinstance(relative, str) for relative in files)
             and len(files) == len(set(files)), "parity runner harness closure is invalid")
    digest_value = hashlib.sha256()
    for relative in files:
        _require(not PurePosixPath(relative).is_absolute()
                 and all(part not in {"", ".", ".."} for part in relative.split("/"))
                 and "\\" not in relative, "parity harness source path is invalid")
        path = repository / relative
        _require(path.is_file() and not path.is_symlink(), f"parity harness source is missing: {relative}")
        digest_value.update(relative.encode("utf-8") + b"\0")
        digest_value.update(bytes.fromhex(digest(path)))
    return digest_value.hexdigest()


def expected_fixture_ids(repository: Path, suite: str) -> list[str]:
    """Resolve the release manifest's exact CLI/V01 inventories."""
    parity_directory = str(repository / "Tools/parity")
    if parity_directory not in sys.path:
        sys.path.insert(0, parity_directory)
    path = repository / "Tools/parity/compare_results.py"
    spec = importlib.util.spec_from_file_location("parity_compare_for_release", path)
    _require(spec is not None and spec.loader is not None, "maintained parity comparator is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        ids = sorted(module.expected_fixtures(repository / "Tests/Parity/manifest.json", suite))
    except (OSError, ValueError, RuntimeError) as error:
        raise EvidenceError("parity manifest cannot define the release fixture inventory") from error
    expected_count = 27 if suite == "cli" else 1
    _require(len(ids) == expected_count, f"release {suite} fixture inventory must contain {expected_count} entries")
    return ids


def _validate_run(run: dict[str, Any], expected_sha: str, expected_run_id: str) -> dict[str, Any]:
    _require(run.get("id") == int(expected_run_id), "parity run ID differs from the selected run")
    _require(run.get("event") == "push" and run.get("head_branch") == "main",
             "parity authority must be a main-branch push")
    _require(run.get("head_sha") == expected_sha, "parity run source differs from PUBLISH_SHA")
    _require(run.get("status") == "completed" and run.get("conclusion") == "success",
             "parity run did not complete successfully")
    _require(run.get("path") == ".github/workflows/parity.yml",
             "parity run is not from the maintained parity workflow")
    return {
        "runId": run["id"], "runNumber": run.get("run_number"),
        "event": run["event"], "headBranch": run["head_branch"],
        "headSHA": run["head_sha"], "status": run["status"],
        "conclusion": run["conclusion"], "workflowPath": run["path"],
        "htmlUrl": run.get("html_url", ""),
    }


def _validate_artifact(artifacts: dict[str, Any], expected_id: str, expected_digest: str,
                       run_id: str, source_sha: str) -> dict[str, Any]:
    _require(SHA256.fullmatch(expected_digest) is not None, "parity artifact SHA-256 is malformed")
    entries = artifacts.get("artifacts")
    _require(isinstance(entries, list), "parity artifact listing is malformed")
    matches = [entry for entry in entries if entry.get("name") == ARTIFACT]
    _require(len(matches) == 1, "successful parity run must have exactly one comparison artifact")
    item = matches[0]
    _require(str(item.get("id")) == expected_id, "downloaded parity artifact ID differs from the run listing")
    linked_run = item.get("workflow_run")
    _require(isinstance(linked_run, dict) and linked_run.get("id") == int(run_id)
             and linked_run.get("head_branch") == "main" and linked_run.get("head_sha") == source_sha,
             "comparison artifact is not linked to the exact source run")
    _require(item.get("expired") is False, "parity comparison artifact is expired")
    _require(type(item.get("size_in_bytes")) is int and 0 < item["size_in_bytes"] <= 64 * 1024 * 1024,
             "parity comparison artifact exceeds the evidence size limit")
    _require(item.get("digest") == "sha256:" + expected_digest,
             "parity artifact digest differs from GitHub's artifact record")
    return {"artifactId": item["id"], "name": item["name"], "digest": item["digest"],
            "sizeInBytes": item.get("size_in_bytes"), "createdAt": item.get("created_at")}


def extract_authenticated_artifact(archive_path: Path, destination: Path,
                                   expected_digest: str) -> None:
    """Verify GitHub's pinned ZIP digest, then extract its five safe files."""
    _require(archive_path.is_file() and not archive_path.is_symlink()
             and archive_path.stat().st_nlink == 1, "downloaded parity ZIP is absent or linked")
    _require(archive_path.stat().st_size <= 64 * 1024 * 1024,
             "downloaded parity ZIP exceeds the evidence size limit")
    _require(digest(archive_path) == expected_digest,
             "downloaded parity ZIP bytes differ from GitHub's artifact SHA-256")
    expected_names = ARTIFACT_FILES
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            names = [member.filename for member in members]
            _require(len(names) == len(set(names)) and set(names) == expected_names,
                     "parity ZIP has missing, duplicate or unexpected members")
            for member in members:
                raw_parts = member.filename.split("/")
                safe = (not member.is_dir() and "\\" not in member.filename
                        and not member.filename.startswith("/")
                        and all(part not in {"", ".", ".."} for part in raw_parts)
                        and PurePosixPath(member.filename).as_posix() == member.filename)
                mode = member.external_attr >> 16
                kind = stat.S_IFMT(mode)
                _require(safe and kind in {0, stat.S_IFREG},
                         "parity ZIP contains an unsafe path or non-regular member")
                _require(member.file_size <= 32 * 1024 * 1024,
                         "parity ZIP member exceeds the evidence size limit")
            _require(sum(member.file_size for member in members) <= 64 * 1024 * 1024,
                     "parity ZIP exceeds the evidence size limit")
            _require(not destination.exists() and not destination.is_symlink(),
                     "parity extraction destination already exists")
            destination.mkdir(mode=0o700, parents=True)
            for member in members:
                target = destination.joinpath(*member.filename.split("/"))
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                data = archive.read(member)  # also checks the ZIP CRC
                _require(len(data) == member.file_size, "parity ZIP member size is inconsistent")
                with target.open("xb") as stream:
                    stream.write(data)
                target.chmod(0o600)
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise EvidenceError("downloaded parity artifact is not a valid bounded ZIP") from error


def _validate_comparison(value: dict[str, Any], matrix: str, suite: str,
                         expected_ids: list[str], package_identity: dict[str, Any],
                         expected_harness: str) -> None:
    _require(value.get("schemaVersion") == 3 and value.get("suite") == suite,
             f"{suite} comparison schema or suite is unsupported")
    _require(value.get("status") == "passed" and value.get("evidenceStatus") == "passed",
             f"{suite} comparison did not pass")
    _require(value.get("functionalParityStatus") == "passed" and value.get("timingStatus") == "passed",
             f"{suite} functional or timing qualification failed")
    _require(value.get("requireZeroFunctionalDifferences") is True,
             f"{suite} comparison does not require zero functional differences")
    _require(value.get("evidenceErrors") == [], f"{suite} comparison contains evidence errors")
    _require(value.get("performancePolicy") == PERFORMANCE_POLICY,
             f"{suite} comparison changed the established performance policy")
    _require(value.get("expectedFixtures") == expected_ids,
             f"{suite} comparison fixture inventory differs from the release manifest")
    _require(value.get("finalizedPackage") == package_identity,
             f"{suite} comparison names a different finalized package")
    _require(value.get("parityHarnessSHA256") == expected_harness,
             f"{suite} comparison harness identity differs from current source")
    fixtures = value.get("fixtures")
    _require(isinstance(fixtures, list) and len(fixtures) == len(expected_ids),
             f"{suite} comparison is missing or adding fixture results")
    _require([item.get("id") for item in fixtures] == expected_ids,
             f"{suite} fixture result order or identity differs from the manifest")
    for fixture in fixtures:
        _require(fixture.get("statuses") == {"docker": "passed", "apple-stock": "passed",
                                              "container-compose": "passed"},
                 f"{suite}/{fixture.get('id')} contains a failed lane or cleanup result")
        _require(fixture.get("functionalEquivalent") is True
                 and fixture.get("functionalDifferences") == []
                 and fixture.get("timingEvidenceValid") is True
                 and fixture.get("timingDifferences") == []
                 and fixture.get("performanceAcceptancePassed") is True
                 and fixture.get("performanceFailures") == []
                 and fixture.get("equivalent") is True and fixture.get("differences") == [],
                 f"{suite}/{fixture.get('id')} contains a difference or invalid timing")
    rows = [line for line in matrix.splitlines() if line.startswith("| ") and not line.startswith("| ---")]
    row_ids = [line.split("|", 2)[1].strip() for line in rows[1:]]
    _require(row_ids == expected_ids, f"{suite} Markdown matrix differs from JSON fixture inventory")


def validate(repository: Path, run_json: Path, artifact_json: Path, run_id: str, artifact_id: str,
             artifact_digest: str, artifact_archive: Path, artifact_directory: Path, publish_sha: str,
             archive: Path, checksum: Path, context_path: Path, verification_path: Path,
             finalization_provenance: Path, finalization_sha: str, output_directory: Path,
             qualification_sha: str) -> dict[str, Any]:
    """Validate the official download and bind parity to the exact signed package."""
    _require(COMMIT.fullmatch(publish_sha) is not None, "PUBLISH_SHA must be a full lowercase commit SHA")
    _require(SHA256.fullmatch(finalization_sha) is not None
             and digest(finalization_provenance) == finalization_sha,
             "native finalization provenance checksum differs")
    run = read_json(run_json)
    artifact_listing = read_json(artifact_json)
    _require(isinstance(run, dict) and isinstance(artifact_listing, dict), "parity authority metadata is malformed")
    run_receipt = _validate_run(run, publish_sha, run_id)
    artifact_receipt = _validate_artifact(artifact_listing, artifact_id, artifact_digest,
                                          run_id, publish_sha)

    _require(artifact_directory.parent.is_dir() and not artifact_directory.parent.is_symlink(),
             "parity extraction parent is missing or linked")
    extract_authenticated_artifact(artifact_archive, artifact_directory, artifact_digest)
    expected_names = ARTIFACT_FILES
    _require(artifact_directory.is_dir() and not artifact_directory.is_symlink(), "downloaded parity artifact is missing")
    actual_names = {path.relative_to(artifact_directory).as_posix() for path in artifact_directory.rglob("*") if path.is_file()}
    _require(actual_names == expected_names, "downloaded comparison artifact file set is incomplete or unexpected")

    context = read_json(context_path)
    verification = read_json(verification_path)
    proof = read_json(finalization_provenance)
    _require(isinstance(context, dict) and isinstance(verification, dict) and isinstance(proof, dict),
             "signed package evidence is malformed")
    _require(archive.is_file() and checksum.is_file(), "signed package archive or checksum is missing")
    archive_sha = digest(archive)
    _require(checksum.read_text(encoding="utf-8") == f"{archive_sha}  {archive.name}\n",
             "signed package checksum does not match the archive")
    _require(context.get("asset") == archive.name and context.get("commit") == publish_sha,
             "signed package context is not bound to the release source")
    _require(context.get("lane") in {"current", "stable"}, "signed package context has an unsupported lane")
    _require(verification == {"archive": archive.name, "commit": publish_sha,
                              "lane": context["lane"], "notarized": True,
                              "sha256": archive_sha, "version": context.get("productVersion")},
             "maintained package verification differs from release context")
    _require(proof.get("schema") == 1 and proof.get("scope") == "signed-notarized-package-assembly"
             and proof.get("distributionReady") is False,
             "finalization provenance is invalid or claims distribution authority")
    _require(proof.get("runtimeProfile") == "stock" and proof.get("sourceCommit") == publish_sha
             and proof.get("archive") == archive.name and proof.get("archiveSHA256") == archive_sha
             and proof.get("archiveSize") == archive.stat().st_size
             and proof.get("packageContextSHA256") == digest(context_path)
             and proof.get("packageVerificationSHA256") == digest(verification_path),
             "finalization provenance differs from the signed stock release package")

    # The identity is copied from the independently produced comparisons, then every
    # field derivable from the published archive/proof is checked against source bytes.
    cli = read_json(artifact_directory / "comparison.json")
    _require(isinstance(cli, dict), "CLI comparison is malformed")
    identity = cli.get("finalizedPackage")
    _require(isinstance(identity, dict), "CLI comparison lacks finalized-package provenance")
    _require(identity.get("scope") == "finalized-native-package-runtime-input"
             and identity.get("kind") == "signed-notarized-native-package"
             and identity.get("runtimeProfile") == "stock"
             and identity.get("sourceCommit") == publish_sha
             and identity.get("candidateReceiptSHA256") == proof.get("candidateReceiptSHA256")
             and identity.get("finalizationProvenanceSHA256") == finalization_sha
             and identity.get("trustedStateSHA256") == proof.get("trustedStateSHA256")
             and identity.get("archiveSHA256") == archive_sha
             and identity.get("archiveSize") == archive.stat().st_size,
             "comparison does not identify the exact finalized source, archive and provenance")
    harness = cli.get("parityHarnessSHA256")
    _require(isinstance(harness, str) and SHA256.fullmatch(harness) is not None
             and harness == parity_harness_sha256(repository),
             "comparison parity-harness identity differs from this source commit")
    local_path = artifact_directory / "local-qualification.json"
    _require(SHA256.fullmatch(qualification_sha) is not None
             and digest(local_path) == qualification_sha,
             "local qualification checksum differs from trusted configuration")
    local = read_json(local_path)
    _require(isinstance(local, dict) and local.get("schemaVersion") == 1
             and local.get("scope") == "local-native-package-parity-qualification"
             and local.get("status") == "passed" and local.get("executedLocally") is True
             and local.get("sourceDirty") is False and local.get("sourceCommit") == publish_sha
             and local.get("parityHarnessSHA256") == harness
             and local.get("controllerSHA256") == digest(repository / "Tools/parity/qualify_finalized_package.py")
             and local.get("finalizationProvenanceSHA256") == finalization_sha
             and local.get("archiveSHA256") == archive_sha
             and local.get("trustedStateSHA256") == proof.get("trustedStateSHA256")
             and local.get("submissionID") == proof.get("submissionID")
             and local.get("fixtureCounts") == {"cliPerLane": 27, "vscodePerLane": 1,
                                                "laneCount": 3, "totalLaneFixtureResults": 84}
             and local.get("guardCleared") is True
             and local.get("restoration") == {"docker": "restored", "apple-stock": "restored",
                                               "container-compose": "restored"},
             "local qualification is stale, incomplete or lacks verified restoration")
    local_identity = {"receiptSHA256": qualification_sha, "executedLocally": True,
                      "sourceCommit": publish_sha, "controllerSHA256": local["controllerSHA256"]}

    for suite, json_name, matrix_name in (
        ("cli", "comparison.json", "matrix.md"),
        ("vscode", "vscode/comparison.json", "vscode/matrix.md"),
    ):
        value = read_json(artifact_directory / json_name)
        _require(isinstance(value, dict), f"{suite} comparison is malformed")
        _require(value.get("localQualification") == local_identity,
                 f"{suite} comparison differs from authenticated local qualification")
        _validate_comparison(value, (artifact_directory / matrix_name).read_text(encoding="utf-8"),
                             suite, expected_fixture_ids(repository, suite), identity, harness)

    output_directory.mkdir(parents=True, exist_ok=True)
    files = {
        "runtime-parity-cli-comparison.json": artifact_directory / "comparison.json",
        "runtime-parity-cli-matrix.md": artifact_directory / "matrix.md",
        "runtime-parity-vscode-comparison.json": artifact_directory / "vscode/comparison.json",
        "runtime-parity-vscode-matrix.md": artifact_directory / "vscode/matrix.md",
        "local-runtime-qualification.json": local_path,
    }
    file_hashes: dict[str, str] = {}
    for name, source in files.items():
        destination = output_directory / name
        shutil.copyfile(source, destination)
        file_hashes[name] = digest(destination)
    receipt = {
        "schemaVersion": 1,
        "scope": "successful-native-runtime-parity-release-evidence",
        "releaseAuthority": False,
        "sourceCommit": publish_sha,
        "packageLane": context["lane"],
        "packageAsset": archive.name,
        "packageArchiveSHA256": archive_sha,
        "finalizationProvenanceSHA256": finalization_sha,
        "candidateReceiptSHA256": proof["candidateReceiptSHA256"],
        "trustedStateSHA256": proof["trustedStateSHA256"],
        "parityHarnessSHA256": harness,
        "localExecution": local_identity,
        "workflowRole": "authenticate local execution and recompute comparisons",
        "performancePolicy": PERFORMANCE_POLICY,
        "fixtureCounts": {"cliPerLane": 27, "vscodePerLane": 1, "laneCount": 3, "totalLaneFixtureResults": 84},
        "workflowRun": run_receipt,
        "comparisonArtifact": artifact_receipt,
        "comparisonFilesSHA256": file_hashes,
    }
    (output_directory / "runtime-parity-provenance.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--run-json", type=Path, required=True)
    parser.add_argument("--artifact-json", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument("--artifact-digest", required=True)
    parser.add_argument("--artifact-directory", type=Path, required=True)
    parser.add_argument("--artifact-archive", type=Path, required=True)
    parser.add_argument("--publish-sha", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--checksum", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--verification", type=Path, required=True)
    parser.add_argument("--finalization-provenance", type=Path, required=True)
    parser.add_argument("--finalization-sha256", required=True)
    parser.add_argument("--qualification-sha256", required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = validate(args.repository, args.run_json, args.artifact_json, args.run_id, args.artifact_id,
                           args.artifact_digest, args.artifact_archive, args.artifact_directory, args.publish_sha,
                           args.archive, args.checksum, args.context, args.verification,
                           args.finalization_provenance, args.finalization_sha256,
                           args.output_directory, args.qualification_sha256)
    except (EvidenceError, OSError, ValueError, KeyError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
