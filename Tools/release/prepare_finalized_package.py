#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Admit one finalized, accepted native package as a runtime input."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid


SHA = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
FINAL_FILES = {"package-context.json", "package-verification.json", "native-finalization-provenance.json"}
TOOLS = ("native_signing.py", "create-reproducible-archive.py", "verify-package.py")
PRODUCTS = ("devcontainer", "devcontainer-engine", "devcontainer-compose", "devcontainer-docker")
PROOF_FIELDS = {
    "schema", "scope", "distributionReady", "sourceCommit", "runtimeProfile",
    "candidateReceiptSHA256", "stageProvenanceSHA256", "trustedStateSHA256",
    "acceptedEvidenceSHA256", "submittedZIPSHA256", "submittedZIPSize", "submissionID",
    "signedTree", "finalTree", "archive", "archiveSHA256", "archiveSize", "checksumSHA256",
    "packageContextSHA256", "packageVerificationSHA256", "sourceDateEpoch", "releaseToolSHA256",
}


def digest(path: Path | bytes) -> str:
    """Hash bytes or a file without loading the package archive into memory."""
    if isinstance(path, bytes):
        return hashlib.sha256(path).hexdigest()
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def physical(path: Path, *, exists: bool = True) -> Path:
    """Require an absolute path with no symbolic-link aliases."""
    if not path.is_absolute() or path.is_symlink() or path.resolve(strict=exists) != path:
        raise ValueError("Path is absent or aliased: " + str(path))
    return path


def load_tool(path: Path, name: str):
    """Load a maintained repository helper by its exact source path."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("Maintained helper is unavailable: " + path.name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_helpers(repository: Path):
    """Load the current preparation, signing, and finalization APIs."""
    release = repository / "Tools/release"
    bazel = repository / "Tools/bazel"
    sys.path[:] = [str(bazel), str(release)] + [path for path in sys.path
                                               if path not in {str(bazel), str(release)}]
    signer = load_tool(release / "native_signing.py", "native_signing_admission")
    finalizer = load_tool(release / "finalize-native-package.py", "native_finalizer_admission")
    import prepare_releases  # pylint: disable=import-outside-toplevel
    cached_source = Path(getattr(prepare_releases, "__file__", "")).resolve()
    if cached_source != (bazel / "prepare_releases.py").resolve(strict=True):
        raise ValueError("Cached package-preparation helper is from a different repository")
    return signer, finalizer, prepare_releases


def read_provenance(directory: Path, trusted_sha256: str) -> tuple[dict, dict[str, Path]]:
    """Authenticate the exact five-file finalizer result and its public proof."""
    physical(directory)
    proof_path = directory / "native-finalization-provenance.json"
    physical(proof_path)
    if not SHA.fullmatch(trusted_sha256) or digest(proof_path) != trusted_sha256:
        raise ValueError("Trusted finalization provenance checksum differs")
    proof = json.loads(proof_path.read_bytes())
    hash_fields = ("candidateReceiptSHA256", "stageProvenanceSHA256", "trustedStateSHA256",
                   "acceptedEvidenceSHA256", "submittedZIPSHA256", "archiveSHA256", "checksumSHA256",
                   "packageContextSHA256", "packageVerificationSHA256")
    if (not isinstance(proof, dict) or set(proof) != PROOF_FIELDS or proof.get("schema") != 1
            or proof.get("scope") != "signed-notarized-package-assembly" or proof.get("distributionReady") is not False
            or not COMMIT.fullmatch(proof.get("sourceCommit", ""))
            or proof.get("runtimeProfile") not in {"stock", "enhanced"}
            or any(not SHA.fullmatch(proof.get(key, "")) for key in hash_fields)
            or any(type(proof.get(key)) is not int or proof[key] <= 0 for key in ("submittedZIPSize", "archiveSize"))
            or not isinstance(proof.get("submissionID"), str)
            or re.fullmatch(r"[0-9a-fA-F-]{36}", proof["submissionID"]) is None
            or type(proof.get("sourceDateEpoch")) is not int or proof["sourceDateEpoch"] < 0):
        raise ValueError("Finalization proof has an unsupported scope or schema")
    archive_name = proof.get("archive")
    if (not isinstance(archive_name, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.tar\.gz", archive_name) is None):
        raise ValueError("Finalized archive name is invalid")
    files = {name: directory / name for name in FINAL_FILES}
    files[archive_name] = directory / archive_name
    files[archive_name + ".sha256"] = directory / (archive_name + ".sha256")
    if {path.name for path in directory.iterdir()} != set(files):
        raise ValueError("Finalized directory must contain exactly the five promoted files")
    for name, path in files.items():
        physical(path)
        info = path.stat()
        if (not path.is_file() or info.st_nlink != 1 or info.st_uid != os.getuid()
                or info.st_dev != Path.home().stat().st_dev):
            raise ValueError("Finalized member is not a private regular file: " + name)
    if (proof.get("archiveSHA256") != digest(files[archive_name])
            or proof.get("archiveSize") != files[archive_name].stat().st_size
            or proof.get("checksumSHA256") != digest(files[archive_name + ".sha256"])
            or proof.get("packageContextSHA256") != digest(files["package-context.json"])
            or proof.get("packageVerificationSHA256") != digest(files["package-verification.json"])):
        raise ValueError("Finalization proof does not bind the promoted files")
    checksum = files[archive_name + ".sha256"].read_text(encoding="utf-8")
    if checksum != proof["archiveSHA256"] + "  " + archive_name + "\n":
        raise ValueError("Finalized archive checksum file differs")
    context = json.loads(files["package-context.json"].read_bytes())
    report = json.loads(files["package-verification.json"].read_bytes())
    if (not isinstance(context, dict)
            or set(context) != {"asset", "commit", "formulaVersion", "lane", "productVersion", "releaseTag"}
            or context.get("asset") != archive_name or context.get("commit") != proof["sourceCommit"]
            or context.get("lane") not in {"development", "current", "stable"}
            or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", context.get("productVersion", "")) is None
            or report != {"archive": archive_name, "commit": proof["sourceCommit"], "lane": context["lane"],
                          "notarized": True, "sha256": proof["archiveSHA256"], "version": context["productVersion"]}):
        raise ValueError("Finalized package context or verifier report differs")
    return proof, files


def require_inventory(tree: object) -> dict:
    """Validate finalizer's exact regular-file inventory shape."""
    if not isinstance(tree, dict) or not tree:
        raise ValueError("Finalized payload inventory is missing")
    for name, row in tree.items():
        if (not isinstance(name, str) or name.startswith("/")
                or any(part in {"", ".", ".."} for part in name.split("/"))
                or not isinstance(row, dict) or set(row) != {"sha256", "size", "mode"}
                or not isinstance(row["sha256"], str) or not SHA.fullmatch(row["sha256"])
                or type(row["size"]) is not int or row["size"] < 0
                or type(row["mode"]) is not int or not 0 <= row["mode"] <= 0o777):
            raise ValueError("Finalized payload inventory is invalid")
    return tree


def require_production_storage(repository: Path, finalized: Path, state: Path, scratch: Path,
                               retained: Path, evidence_root: Path, trusted_state_sha256: str, signer) -> None:
    """Keep durable package and signature evidence internal and extraction on SSD."""
    home = Path.home()
    final_root = home / "Library/Application Support/ContainerFamily/retained/devcontainer/finalized"
    admission_root = home / "Library/Application Support/ContainerFamily/retained/devcontainer/finalized-admissions"
    physical(finalized)
    physical(retained, exists=False)
    physical(evidence_root, exists=False)
    if not finalized.is_relative_to(final_root) or finalized == final_root:
        raise ValueError("Finalized package must be beneath internal retained finalized storage")
    if finalized.stat().st_dev != Path.home().stat().st_dev or finalized.stat().st_uid != os.getuid():
        raise ValueError("Finalized package must be user-owned internal storage")
    if finalized.stat().st_mode & 0o777 != 0o700:
        raise ValueError("Finalized package directory must be private")
    if not retained.is_relative_to(admission_root) or not evidence_root.is_relative_to(retained):
        raise ValueError("Admission payload and signature evidence must be beneath internal retained storage")
    if not SHA.fullmatch(trusted_state_sha256) or digest(state / "state.json") != trusted_state_sha256:
        raise ValueError("Trusted accepted-state checksum differs before storage setup")
    proof_state = json.loads((state / "state.json").read_bytes())
    if state.stat().st_uid != os.getuid() or state.stat().st_mode & 0o777 != 0o700:
        raise ValueError("Accepted state must be user-owned and private")
    consumer = load_tool(repository / "Tools/release/consume-native-finalized-package.py",
                         "native_finalized_consumer_admission")
    identity_path = consumer.SSD_IDENTITY
    physical(identity_path)
    if (not identity_path.is_file() or identity_path.stat().st_uid != os.getuid()
            or identity_path.stat().st_nlink != 1):
        raise ValueError("Trusted enrolled SSD identity file is missing or unsafe")
    expected_uuid = identity_path.read_text(encoding="ascii").strip().upper()
    if re.fullmatch(r"[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}", expected_uuid) is None:
        raise ValueError("Trusted enrolled SSD identity is malformed")
    result = subprocess.run(["/usr/sbin/diskutil", "info", "-plist", "/Volumes/SSD"],
                            check=True, capture_output=True, timeout=10,
                            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    consumer.validate_ssd_identity(expected_uuid, result.stdout)
    # The signer may create scratch; authenticate the volume before any write.
    signer.validate_storage(Path(proof_state["stage"]), state, scratch, Path(proof_state["evidence"]))
    for path in (retained, evidence_root):
        if path.exists():
            physical(path)
            info = path.stat()
            if info.st_dev != home.stat().st_dev or info.st_uid != os.getuid():
                raise ValueError("Admission retention path must be user-owned internal storage")
            if path.lstat().st_mode & 0o777 != 0o700:
                raise ValueError("Admission retention directories must be private")
        else:
            path.mkdir(mode=0o700, parents=True)
    if scratch.stat().st_dev == home.stat().st_dev:
        raise ValueError("Package extraction scratch must be on a separate SSD filesystem")


def verify_state(proof: dict, state: Path, trusted_state_sha256: str, signer, finalizer) -> tuple[dict, dict]:
    """Re-authenticate accepted notary state, ZIP, receipt and signature records."""
    physical(state)
    if not SHA.fullmatch(trusted_state_sha256) or digest(state / "state.json") != trusted_state_sha256:
        raise ValueError("Trusted accepted-state checksum differs")
    if proof.get("trustedStateSHA256") != trusted_state_sha256:
        raise ValueError("Finalization proof names a different accepted state")
    record, receipt, stage_provenance, acceptance = finalizer.admitted_state(
        state, trusted_state_sha256, signer)
    expected = {
        "candidateReceiptSHA256": record["candidateSHA256"],
        "stageProvenanceSHA256": record["stageProvenanceSHA256"],
        "acceptedEvidenceSHA256": record["acceptanceSHA256"],
        "submittedZIPSHA256": record["archiveSHA256"],
        "submittedZIPSize": record["archiveSize"],
        "submissionID": record["submissionID"],
        "signedTree": record["signedTree"],
    }
    if any(proof.get(key) != value for key, value in expected.items()):
        raise ValueError("Finalization proof differs from accepted signing state")
    if (receipt.get("schemaVersion") != 2 or receipt.get("kind") != "unsigned-native-candidate"
            or receipt.get("distributionReady") is not False
            or receipt.get("runtimeProfile") != "stock"
            or receipt.get("commit") != proof.get("sourceCommit")
            or set(receipt.get("products", {})) != set(PRODUCTS)):
        raise ValueError("Accepted source receipt is not the stock four-product candidate")
    return record, receipt


def verify_tool_identity(proof: dict, repository: Path) -> None:
    """Require the finalizer's current release-helper identity bundle."""
    actual = {name: digest(repository / "Tools/release" / name) for name in TOOLS}
    if proof.get("releaseToolSHA256") != actual:
        raise ValueError("Finalized package was admitted by a different release helper bundle")


def verify_package_report(repository: Path, product_root: Path, archive: Path, checksum: Path,
                          context: dict, report_path: Path) -> dict:
    """Rerun the maintained verifier using the packaged selected lock and license ledger."""
    shared = product_root / "share/devcontainer"
    resolved = shared / "Package.resolved"
    ledger = shared / "dependency-licenses.selected.json"
    if not resolved.is_file() or not ledger.is_file():
        raise ValueError("Finalized package omits its selected lock or license ledger")
    subprocess.run([
        sys.executable, str(repository / "Tools/release/verify-package.py"),
        "--archive", str(archive), "--checksum", str(checksum),
        "--expected-version", context["productVersion"], "--expected-lane", context["lane"],
        "--expected-commit", context["commit"], "--resolved", str(resolved),
        "--license-manifest", str(ledger), "--require-notarization", "--output", str(report_path),
    ], cwd=repository, check=True, timeout=120,
       env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
    return json.loads(report_path.read_bytes())


def admit_finalized_package(repository: Path, finalized: Path, trusted_provenance_sha256: str,
                            expected_source_commit: str, provider_lane: str, state: Path,
                            scratch_root: Path, retained_root: Path, evidence_root: Path) -> dict:
    """Admit the same accepted stock archive as a provider-independent runtime input."""
    repository = physical(repository)
    finalized, state = physical(finalized), physical(state)
    scratch_root = physical(scratch_root, exists=False)
    if not COMMIT.fullmatch(expected_source_commit):
        raise ValueError("Expected source commit must be an exact lowercase Git SHA")
    if provider_lane not in {"apple-stock", "container-compose"}:
        raise ValueError("Unsupported native runtime provider lane")
    signer, finalizer, prepare_releases = load_helpers(repository)
    proof, files = read_provenance(finalized, trusted_provenance_sha256)
    require_production_storage(repository, finalized, state, scratch_root, retained_root,
                               evidence_root, proof.get("trustedStateSHA256", ""), signer)
    context = json.loads(files["package-context.json"].read_bytes())
    if (proof.get("sourceCommit") != expected_source_commit
            or proof.get("runtimeProfile") != "stock"
            or proof.get("archive") != context.get("asset")
            or context.get("commit") != expected_source_commit):
        raise ValueError("Finalized package source or stock runtime profile differs")
    tree = require_inventory(proof.get("finalTree"))
    if set(tree) != set(proof.get("signedTree", {})) | {"share/devcontainer/notarization.json"}:
        raise ValueError("Final package inventory differs from the signed payload closure")
    verify_tool_identity(proof, repository)
    record, receipt = verify_state(proof, state, proof.get("trustedStateSHA256", ""), signer, finalizer)
    archive = files[proof["archive"]]
    asset = {"repository": "local/devcontainer-candidate", "tag": context["productVersion"],
             "name": "candidate_archive_v2.tar.gz", "sha256": proof["archiveSHA256"],
             "size": proof["archiveSize"]}
    retained_root = physical(retained_root)
    evidence_root = physical(evidence_root, exists=False)
    receipts = retained_root / "receipts"
    prepared_root = retained_root / "prepared"
    signature_evidence = evidence_root / (trusted_provenance_sha256 + "-" + provider_lane + "-" + uuid.uuid4().hex)
    with tempfile.TemporaryDirectory(prefix="finalized-admission-", dir=scratch_root) as temporary:
        workspace = Path(temporary)
        extracted_root = workspace / "prepared"
        extracted_root.mkdir(mode=0o700)
        ssd_receipts = workspace / "receipts"
        ssd_receipts.mkdir(mode=0o700)
        prepared = prepare_releases.prepare(asset, archive, extracted_root, ssd_receipts)
        product_root = Path(prepared["root"]) / ("devcontainer-" + context["productVersion"])
        specification = {"schemaVersion": 1, "assetSHA256": asset["sha256"],
                         "layout": prepare_releases.layout(asset)}
        preparation_key = hashlib.sha256(prepare_releases.canonical(specification).encode()).hexdigest()
        retained_receipt = json.loads((ssd_receipts / (preparation_key + ".json")).read_bytes())
        prepared_receipt = prepare_releases.validate_prepared(Path(prepared["root"]),
                                                              specification, retained_receipt)
        finalizer.verify_tar_closure(archive, product_root, tree, proof["sourceDateEpoch"])
        shared = product_root / "share/devcontainer"
        embedded = json.loads((shared / "candidate.json").read_bytes())
        original_candidate = {key: value for key, value in receipt.items()
                              if key not in {"archiveSHA256", "archiveSize"}}
        if (embedded != original_candidate
                or digest((shared / "Package.resolved").read_bytes()) != receipt["dependencyLockSHA256"]):
            raise ValueError("Original candidate receipt or selected lock changed in finalized package")
        product_tree = prepared_receipt["inventory"]
        prefix = product_root.name + "/"
        for name, row in tree.items():
            if product_tree.get(prefix + name) != {"mode": row["mode"], "kind": "file",
                                                   "size": row["size"], "sha256": row["sha256"]}:
                raise ValueError("Prepared package differs from finalized payload: " + name)
        report = workspace / "verification.json"
        if verify_package_report(repository, product_root, archive, files[proof["archive"] + ".sha256"],
                                 context, report) != json.loads(files["package-verification.json"].read_bytes()):
            raise ValueError("Maintained package verifier report differs from finalization")
        signature_evidence.mkdir(mode=0o700)
        signatures = signer.verify_signatures(product_root, signature_evidence,
                                               record["teamIdentifier"], "admit-" + str(time.time_ns()))
        if signatures != record["signatures"]:
            raise ValueError("Extracted package signatures differ from accepted state")
        for directory in (receipts, prepared_root):
            if not directory.exists():
                directory.mkdir(mode=0o700)
            physical(directory)
            if directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o777 != 0o700:
                raise ValueError("Prepared package retention is not private and user-owned")
        receipt_destination = receipts / (preparation_key + ".json")
        receipt_bytes = (prepare_releases.canonical(retained_receipt) + "\n").encode()
        if receipt_destination.exists():
            physical(receipt_destination)
            if receipt_destination.read_bytes() != receipt_bytes:
                raise ValueError("Retained preparation receipt differs from SSD validation")
        else:
            receipt_destination.write_bytes(receipt_bytes)
            receipt_destination.chmod(0o600)
        retained = prepare_releases.retain_prepared(asset, archive, extracted_root,
                                                    prepared_root, receipts)
    # The output and accepted state may not change while the package was checked.
    proof_after, files_after = read_provenance(finalized, trusted_provenance_sha256)
    record_after, receipt_after = verify_state(proof_after, state, proof_after["trustedStateSHA256"],
                                               signer, finalizer)
    if (proof_after != proof or {key: str(value) for key, value in files_after.items()}
            != {key: str(value) for key, value in files.items()}
            or record_after != record or receipt_after != receipt):
        raise ValueError("Finalized package or accepted state changed during admission")
    verify_tool_identity(proof, repository)
    final_tree = retained["root"]
    prefix = "devcontainer-" + context["productVersion"] + "/"
    if any(relative not in tree for relative in signer.BINARIES):
        raise ValueError("Finalized package lacks a signed native executable")
    binaries = {relative: tree[relative]["sha256"] for relative in signer.BINARIES}
    executables = {name: str(Path(final_tree) / prefix / relative) for relative, name in (
        ("bin/devcontainer", "devcontainer"), ("bin/devcontainer-engine", "devcontainer-engine"),
        ("bin/devcontainer-compose", "devcontainer-compose"), ("bin/devcontainer-docker", "devcontainer-docker"),
        )}
    plugin_executable = str(Path(final_tree) / prefix / signer.PLUGIN)
    reference = dict(receipt["referenceRuntime"])
    reference["root"] = str(Path(final_tree) / prefix / "libexec/devcontainer/reference")
    reference["paths"] = {name: str(Path(reference["root"]) / name) for name in signer.REFERENCE_FILES}
    reference["candidateFiles"] = dict(reference["files"])
    reference["files"] = {
        name: tree["libexec/devcontainer/reference/" + name]["sha256"]
        for name in signer.REFERENCE_FILES
    }
    signature_inventory_sha256 = digest(json.dumps(record["signatures"], sort_keys=True,
                                                   separators=(",", ":")).encode())
    return {"schemaVersion": 1, "scope": "finalized-native-package-runtime-input",
            "kind": "signed-notarized-native-package", "distributionReady": False,
            "providerLane": provider_lane, "sourceCommit": expected_source_commit,
            "runtimeProfile": "stock", "candidateReceiptSHA256": proof["candidateReceiptSHA256"],
            "finalizationProvenanceSHA256": trusted_provenance_sha256,
            "trustedStateSHA256": proof["trustedStateSHA256"],
            "archiveSHA256": proof["archiveSHA256"], "archiveSize": proof["archiveSize"],
            "preparationSHA256": retained["preparationSHA256"],
            "inventorySHA256": retained["inventorySHA256"], "root": final_tree,
            "executables": executables, "pluginExecutable": plugin_executable,
            "productionBinarySHA256": binaries, "referenceRuntime": reference,
            "signatureInventorySHA256": signature_inventory_sha256,
            "signatureEvidenceRoot": str(evidence_root)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("finalized_directory", type=Path)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--trusted-provenance-sha256", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--provider-lane", choices=("apple-stock", "container-compose"), required=True)
    parser.add_argument("--state-directory", required=True, type=Path)
    parser.add_argument("--scratch-root", type=Path, default=Path("/Volumes/SSD/cf/finalized-admission"))
    parser.add_argument("--retained-root", type=Path, default=Path.home() / "Library/Application Support/ContainerFamily/retained/devcontainer/finalized-admissions")
    parser.add_argument("--signature-evidence-root", type=Path, default=Path.home() / "Library/Application Support/ContainerFamily/retained/devcontainer/finalized-admissions/signature-evidence")
    args = parser.parse_args()
    result = admit_finalized_package(args.repository, args.finalized_directory,
                                     args.trusted_provenance_sha256, args.expected_source_commit,
                                     args.provider_lane, args.state_directory, args.scratch_root,
                                     args.retained_root, args.signature_evidence_root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
