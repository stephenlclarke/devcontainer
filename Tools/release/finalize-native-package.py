#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# https://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Assemble an internally retained package from one accepted native notary state.

This step does not qualify runtime behavior or authorize publication.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time


SHA = re.compile(r"[0-9a-f]{64}")
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
FINAL_ROOT = Path.home() / "Library/Application Support/ContainerFamily/retained/devcontainer/finalized"


def digest(path: Path) -> str:
    """Hash one retained file without loading a submitted ZIP into memory."""
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def canonical_json(value: dict) -> bytes:
    """Serialize a public proof without nonfinite numbers or changing key order."""
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def source_epoch(provenance: dict) -> int:
    """Use only the stager's hash-bound commit timestamp, never a live checkout."""
    value = provenance.get("sourceDateEpoch")
    if type(value) is not int or value < 0:
        raise ValueError("Authenticated stage lacks a nonnegative source date epoch")
    return value


def load_tool(path: Path, name: str):
    """Load the reviewed maintained helper from the selected repository."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("Maintained release helper is unavailable: " + path.name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def admitted_state(state: Path, trusted_sha: str, signer) -> tuple[dict, dict, dict, bytes]:
    """Check the durable accepted state, exact retained authority, and submitted ZIP."""
    if not SHA.fullmatch(trusted_sha) or digest(state / "state.json") != trusted_sha:
        raise ValueError("Trusted accepted-state checksum differs")
    record = json.loads((state / "state.json").read_bytes())
    if (record.get("schema") != 1 or record.get("phase") != "accepted"
            or record.get("notary", {}).get("status") != "Accepted"
            or not isinstance(record.get("submissionID"), str)
            or not UUID.fullmatch(record["submissionID"])
            or record["notary"].get("id") != record["submissionID"]
            or not isinstance(record.get("archiveSHA256"), str)
            or not SHA.fullmatch(record["archiveSHA256"])
            or not isinstance(record.get("candidateSHA256"), str)
            or not SHA.fullmatch(record["candidateSHA256"])
            or not isinstance(record.get("stageProvenanceSHA256"), str)
            or not SHA.fullmatch(record["stageProvenanceSHA256"])):
        raise ValueError("State is not one accepted, source-bound submission")
    receipt_path = state / "candidate-receipt.json"
    if digest(receipt_path) != record["candidateSHA256"]:
        raise ValueError("Retained candidate receipt differs")
    receipt = json.loads(receipt_path.read_bytes())
    if (receipt.get("commit") != record.get("candidateCommit")
            or receipt.get("runtimeProfile") != record.get("runtimeProfile")):
        raise ValueError("Candidate source or profile differs from accepted state")
    signer.authenticate_provenance(state / "stage-provenance.json",
                                   record["stageProvenanceSHA256"], receipt, record["unsignedTree"])
    signer.require_signed_payload(record["unsignedTree"], record["signedTree"])
    if set(record.get("signatures", {})) != set(signer.BINARIES):
        raise ValueError("Accepted state lacks the exact six signed executables")
    for name in signer.BINARIES:
        signature = record["signatures"][name]
        if (signature.get("sha256") != record["signedTree"][name]["sha256"]
                or signature.get("teamIdentifier") != record.get("teamIdentifier")
                or signature.get("entitlements") != (signer.JIT if name == signer.NODE else {})):
            raise ValueError("Retained signature inventory differs")
    archive = state / "submitted.zip"
    if (digest(archive) != record["archiveSHA256"]
            or archive.stat().st_size != record.get("archiveSize")):
        raise ValueError("Submitted notary ZIP differs")
    stage = Path(record["stage"])
    signer.verify_zip(archive, stage, record["signedTree"])
    evidence_path = Path(record["evidence"])
    if digest(evidence_path) != record.get("acceptanceSHA256"):
        raise ValueError("Accepted notary evidence checksum differs")
    acceptance = evidence_path.read_bytes()
    if json.loads(acceptance) != {"archiveSHA256": record["archiveSHA256"],
                                  "id": record["submissionID"], "status": "Accepted"}:
        raise ValueError("Accepted notary evidence has unexpected fields or identity")
    if stage.exists() and signer.inventory(stage, evidence_path) != record["signedTree"]:
        raise ValueError("Original signed stage differs from submitted ZIP")
    return record, receipt, json.loads((state / "stage-provenance.json").read_bytes()), acceptance


def normalized_inventory(tree: Path, signed: dict, acceptance: bytes, signer) -> dict:
    """Keep signed file bytes exact while applying portable package modes."""
    before = signer.inventory(tree, tree / "share/devcontainer/notarization.json")
    if before != signed:
        raise ValueError("Restored signed ZIP tree differs")
    tree.chmod(0o755)
    for path in sorted(tree.rglob("*")):
        if path.is_symlink():
            raise ValueError("Signed package contains a symbolic link")
        path.chmod(0o755 if path.is_dir() or path.relative_to(tree).as_posix() in signer.BINARIES else 0o644)
    notarization = tree / "share/devcontainer/notarization.json"
    if notarization.exists() or notarization.is_symlink():
        raise ValueError("Notarization file was already present in signed tree")
    notarization.write_bytes(acceptance)
    notarization.chmod(0o644)
    after = signer.inventory(tree, tree / ".nonexistent-acceptance-exclusion")
    if set(after) != set(signed) | {"share/devcontainer/notarization.json"}:
        raise ValueError("Final package file closure differs")
    for name, entry in signed.items():
        if (after[name]["sha256"] != entry["sha256"] or after[name]["size"] != entry["size"]
                or after[name]["mode"] != (0o755 if name in signer.BINARIES else 0o644)):
            raise ValueError("Signed payload bytes or normalized mode differ: " + name)
    if after["share/devcontainer/notarization.json"] != {
            "sha256": hashlib.sha256(acceptance).hexdigest(), "size": len(acceptance), "mode": 0o644}:
        raise ValueError("Embedded acceptance bytes differ")
    return after


def verify_tar_closure(archive_path: Path, tree: Path, inventory: dict, epoch: int) -> None:
    """Bind the normalized archive bytes to exactly the admitted file inventory."""
    found = {}
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            parts = member.name.split("/")
            if not parts or parts[0] != tree.name or any(part in {"", ".", ".."} for part in parts):
                raise ValueError("Final archive has an unsafe member")
            if (member.uid, member.gid, member.uname, member.gname, member.mtime) != (0, 0, "root", "wheel", epoch):
                raise ValueError("Final archive metadata is not normalized")
            if member.isfile():
                name = "/".join(parts[1:])
                source = archive.extractfile(member)
                if name in found or source is None:
                    raise ValueError("Final archive has a duplicate or unreadable file")
                contents = source.read()
                found[name] = {"sha256": hashlib.sha256(contents).hexdigest(),
                               "size": len(contents), "mode": member.mode}
            elif not member.isdir() or member.mode != 0o755:
                raise ValueError("Final archive has an unexpected entry type or directory mode")
    if found != inventory:
        raise ValueError("Final archive payload closure differs")


def promote_completed_files(source: Path, destination: Path, expected: dict[str, str]) -> None:
    """Copy only completed SSD outputs, then atomically expose their verified set."""
    archives = [name for name in expected if name.endswith(".tar.gz")]
    if len(archives) != 1 or set(expected) != {
            archives[0], archives[0] + ".sha256", "package-context.json",
            "package-verification.json", "native-finalization-provenance.json"}:
        raise ValueError("Completed SSD output must contain the exact five files")
    if set(path.name for path in source.iterdir()) != set(expected):
        raise ValueError("Completed SSD output set differs")
    for name, checksum in expected.items():
        path = source / name
        if (not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1
                or digest(path) != checksum):
            raise ValueError("Completed SSD output changed: " + name)
    with tempfile.TemporaryDirectory(prefix="native-final-internal-", dir=destination.parent) as temporary:
        pending = Path(temporary)
        for name, checksum in expected.items():
            target = pending / name
            with (source / name).open("rb") as read, target.open("xb") as write:
                shutil.copyfileobj(read, write)
                write.flush()
                os.fsync(write.fileno())
            if digest(target) != checksum:
                raise ValueError("Internal completed-output copy differs: " + name)
        descriptor = os.open(pending, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if destination.exists() or destination.is_symlink():
            raise ValueError("Final output was created concurrently")
        os.rename(pending, destination)
        descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    if {path.name: digest(path) for path in destination.iterdir()} != expected:
        raise ValueError("Promoted completed output differs")


def finalize(args: argparse.Namespace) -> dict:
    """Assemble, verify, and atomically retain one accepted signed package."""
    repository = args.repository.resolve(strict=True)
    release_tools = repository / "Tools/release"
    signer = load_tool(release_tools / "native_signing.py", "native_signing_finalizer")
    archiver = load_tool(release_tools / "create-reproducible-archive.py", "reproducible_archive_finalizer")
    state, scratch, output = args.state_directory, args.scratch_directory, args.output_directory
    signer.physical(state)
    if (not state.is_dir() or state.stat().st_uid != os.getuid()
            or stat.S_IMODE(state.stat().st_mode) != 0o700):
        raise ValueError("Accepted notary state is not private and owned")
    for name in ("state.json", "candidate-receipt.json", "stage-provenance.json", "submitted.zip", "owner.lock"):
        path = state / name
        if not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1:
            raise ValueError("Accepted state file is missing, linked or shared: " + name)
    if not SHA.fullmatch(args.trusted_state_sha256) or digest(state / "state.json") != args.trusted_state_sha256:
        raise ValueError("Trusted accepted-state checksum differs")
    initial = json.loads((state / "state.json").read_bytes())
    evidence = Path(initial["evidence"])
    stage = Path(initial["stage"])
    signer.validate_storage(stage, state, scratch, evidence)
    signer.physical(output)
    if not output.is_relative_to(FINAL_ROOT) or output == FINAL_ROOT or output.exists() or output.is_symlink():
        raise ValueError("Final output must be fresh beneath internal retained devcontainer/finalized")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.parent.stat().st_dev != Path.home().stat().st_dev:
        raise ValueError("Final output is not on internal retained storage")
    with (state / "owner.lock").open("r+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        record, receipt, provenance, acceptance = admitted_state(state, args.trusted_state_sha256, signer)
        context = provenance["packageContext"]
        if (context.get("commit") != receipt["commit"] or context.get("productVersion") != receipt["version"]
                or context.get("lane") != provenance["lane"]
                or not isinstance(context.get("asset"), str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.tar\.gz", context["asset"])):
            raise ValueError("Selected package context differs from candidate")
        epoch = source_epoch(provenance)
        with tempfile.TemporaryDirectory(prefix="native-final-", dir=scratch) as temporary:
            workspace = Path(temporary)
            tree = workspace / stage.name
            signer.restore_signed_tree(state / "submitted.zip", tree, record["signedTree"])
            check_prefix = "final-" + str(time.time_ns())
            signatures = signer.verify_signatures(tree, state, record["teamIdentifier"], check_prefix + "-verify")
            if signatures != record["signatures"]:
                raise ValueError("Rehydrated signed executable identities differ from accepted state")
            signer.smoke(tree, state, scratch, receipt, check_prefix + "-smoke")
            inventory = normalized_inventory(tree, record["signedTree"], acceptance, signer)
            with tempfile.TemporaryDirectory(prefix="native-final-outputs-", dir=workspace) as pending_text:
                pending = Path(pending_text)
                archive = pending / context["asset"]
                archiver.create_archive(tree, archive, epoch)
                verify_tar_closure(archive, tree, inventory, epoch)
                checksum = pending / (archive.name + ".sha256")
                checksum.write_text(digest(archive) + "  " + archive.name + "\n", encoding="utf-8")
                selected_lock = tree / "share/devcontainer/Package.resolved"
                selected_ledger = tree / "share/devcontainer/dependency-licenses.selected.json"
                verification = pending / "package-verification.json"
                subprocess.run([sys.executable, str(release_tools / "verify-package.py"),
                                "--archive", str(archive), "--checksum", str(checksum),
                                "--expected-version", receipt["version"], "--expected-lane", context["lane"],
                                "--expected-commit", receipt["commit"], "--resolved", str(selected_lock),
                                "--license-manifest", str(selected_ledger), "--require-notarization",
                                "--output", str(verification)], check=True, timeout=120,
                               env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
                result = json.loads(verification.read_bytes())
                if result.get("notarized") is not True or result.get("sha256") != digest(archive):
                    raise ValueError("Maintained package verifier did not admit the final archive")
                package_context = pending / "package-context.json"
                package_context.write_bytes(canonical_json(context))
                proof = {"schema": 1, "scope": "signed-notarized-package-assembly", "distributionReady": False,
                         "sourceCommit": receipt["commit"], "runtimeProfile": receipt["runtimeProfile"],
                         "candidateReceiptSHA256": record["candidateSHA256"],
                         "stageProvenanceSHA256": record["stageProvenanceSHA256"],
                         "trustedStateSHA256": args.trusted_state_sha256,
                         "acceptedEvidenceSHA256": record["acceptanceSHA256"],
                         "submittedZIPSHA256": record["archiveSHA256"], "submittedZIPSize": record["archiveSize"],
                         "submissionID": record["submissionID"], "signedTree": record["signedTree"],
                         "finalTree": inventory, "archive": archive.name, "archiveSHA256": digest(archive),
                         "archiveSize": archive.stat().st_size, "checksumSHA256": digest(checksum),
                         "packageContextSHA256": digest(package_context),
                         "packageVerificationSHA256": digest(verification), "sourceDateEpoch": epoch,
                         "releaseToolSHA256": {name: digest(release_tools / name) for name in
                                               ("native_signing.py", "create-reproducible-archive.py", "verify-package.py")}}
                (pending / "native-finalization-provenance.json").write_bytes(canonical_json(proof))
                promoted_hashes = {path.name: digest(path) for path in pending.iterdir()}
                promote_completed_files(pending, output, promoted_hashes)
        if ({path.name: digest(path) for path in output.iterdir()} != promoted_hashes
                or digest(state / "state.json") != args.trusted_state_sha256
                or digest(evidence) != record["acceptanceSHA256"]):
            raise ValueError("Promoted final package differs")
        return proof


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--state-directory", required=True, type=Path)
    parser.add_argument("--trusted-state-sha256", required=True)
    parser.add_argument("--scratch-directory", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    args = parser.parse_args()
    try:
        proof = finalize(args)
    except (OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise SystemExit("Native finalization failed; preserved accepted state: " + type(error).__name__) from error
    print(json.dumps(proof, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
