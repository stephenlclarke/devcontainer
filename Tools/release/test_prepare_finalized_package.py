"""Portable checks for admission of a finalized signed native package."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tarfile
import tempfile
from types import ModuleType
import unittest
import zipfile
from unittest.mock import patch


REPOSITORY = Path(os.environ.get("DEVCONTAINER_SOURCE", Path(__file__).resolve().parents[2])).resolve()
sys.path.insert(0, str(REPOSITORY / "Tools/release"))
sys.path.insert(0, str(REPOSITORY / "Tools/bazel"))
import test_reference_runtime_sbom as sbom_tests  # noqa: E402
import test_verify_package as verifier_tests  # noqa: E402


SCRIPT = Path(__file__).with_name("prepare_finalized_package.py")
SPEC = importlib.util.spec_from_file_location("prepare_finalized_package", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
COMMIT = verifier_tests.COMMIT
VERSION = verifier_tests.VERSION
SUBMISSION = "01234567-89ab-cdef-0123-456789abcdef"
TEAM = "TEAM123456"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


class FinalizedAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.harness = sbom_tests.RuntimeSBOMTests()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)
        self.finalized = self.root / "finalized" / "release-1"
        self.finalized.mkdir(parents=True)
        self.state = self.root / "notary-state"
        self.state.mkdir(mode=0o700)
        self.scratch = self.root / "ssd-scratch"
        self.scratch.mkdir(mode=0o700)
        self.retained = self.root / "retained"
        self.retained.mkdir(mode=0o700)
        self.evidence = self.retained / "signature-evidence"
        self.evidence.mkdir(mode=0o700)
        self.receipt = self._package()

    def _package(self) -> dict:
        harness = self.harness
        product_hashes = {name: digest(name.encode()) for name in (
            "devcontainer", "devcontainer-engine", "devcontainer-compose", "devcontainer-docker")}
        harness.candidate["products"] = product_hashes
        candidate_archive = b"retained original unsigned candidate archive"
        receipt = {**harness.candidate, "archiveSHA256": digest(candidate_archive),
                   "archiveSize": len(candidate_archive)}
        resolved = harness.resolved
        global_ledger = json.loads((REPOSITORY / "Tools/release/dependency-licenses.json").read_bytes())
        identities = [pin["identity"] for pin in json.loads(resolved)["pins"]]
        selected_ledger = encoded({"schemaVersion": 1,
                                   "licenses": {name: global_ledger["licenses"][name]
                                                for name in sorted(identities)}})

        def add_selected_ledger(payload: dict) -> None:
            payload[f"devcontainer-{VERSION}/share/devcontainer/dependency-licenses.selected.json"] = (
                selected_ledger, 0o644)

        archive, checksum = harness.archive(add_selected_ledger)
        verification = self.root / "initial-verification.json"
        product_root = harness.root / "final-product" / f"devcontainer-{VERSION}"
        product_root.mkdir(parents=True)
        with tarfile.open(archive, "r:gz") as source:
            for member in source:
                if not member.isfile():
                    continue
                path = product_root / "/".join(member.name.split("/")[1:])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(source.extractfile(member).read())
                path.chmod(0o755 if member.mode & 0o111 else 0o644)
        context = {"asset": "devcontainer-release-arm64.tar.gz", "commit": COMMIT,
                   "formulaVersion": VERSION, "lane": "stable",
                   "productVersion": VERSION, "releaseTag": VERSION}
        subprocess.run([
            sys.executable, str(REPOSITORY / "Tools/release/verify-package.py"),
            "--archive", str(archive), "--checksum", str(checksum),
            "--expected-version", VERSION, "--expected-lane", "stable", "--expected-commit", COMMIT,
            "--resolved", str(product_root / "share/devcontainer/Package.resolved"),
            "--license-manifest", str(product_root / "share/devcontainer/dependency-licenses.selected.json"),
            "--require-notarization", "--output", str(verification),
        ], check=True, cwd=REPOSITORY)
        report = json.loads(verification.read_bytes())
        final_tree, signed_tree = {}, {}
        files = {}
        with tarfile.open(archive, "r:gz") as source:
            for member in source:
                if member.isfile():
                    name = "/".join(member.name.split("/")[1:])
                    data = source.extractfile(member).read()
                    row = {"sha256": digest(data), "size": len(data), "mode": member.mode}
                    final_tree[name] = row
                    files[name] = data
                    if name != "share/devcontainer/notarization.json":
                        signed_tree[name] = row
        unsigned_tree = dict(signed_tree)
        stage_provenance = {
            "schema": 1, "scope": "unsigned-native-package-stage", "sourceCommit": COMMIT,
            "profile": "stock", "candidateAssetSHA256": receipt["archiveSHA256"],
            "candidateProducts": product_hashes,
            "selectedLockSHA256": receipt["dependencyLockSHA256"],
            "privateRuntime": receipt["referenceRuntime"], "legalCompleteness": True,
            "distributionReady": False, "signingComplete": False, "notarizationComplete": False,
            "unsignedPayloadInventory": unsigned_tree,
        }
        stage_sha = digest(encoded(stage_provenance))
        (self.state / "stage-provenance.json").write_bytes(encoded(stage_provenance))
        (self.state / "candidate-receipt.json").write_bytes(encoded(receipt))
        (self.state / "owner.lock").write_bytes(b"")
        submitted = self.state / "submitted.zip"
        with zipfile.ZipFile(submitted, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
            for name, data in files.items():
                if name != "share/devcontainer/notarization.json":
                    zipped.writestr(f"devcontainer-{VERSION}/{name}", data)
        signatures = {}
        import native_signing
        for relative in native_signing.BINARIES:
            signatures[relative] = {"sha256": signed_tree[relative]["sha256"],
                                    "teamIdentifier": TEAM,
                                    "authority": "Developer ID Application: Fixture",
                                    "entitlements": native_signing.JIT if relative == native_signing.NODE else {}}
        acceptance = self.state / "acceptance.json"
        acceptance.write_bytes(encoded({"archiveSHA256": digest(submitted.read_bytes()),
                                        "id": SUBMISSION, "status": "Accepted"}))
        record = {
            "schema": 1, "phase": "accepted", "notary": {"status": "Accepted", "id": SUBMISSION},
            "submissionID": SUBMISSION, "archiveSHA256": digest(submitted.read_bytes()),
            "archiveSize": submitted.stat().st_size, "candidateSHA256": digest((self.state / "candidate-receipt.json").read_bytes()),
            "candidateCommit": COMMIT, "runtimeProfile": "stock", "stageProvenanceSHA256": stage_sha,
            "acceptanceSHA256": digest(acceptance.read_bytes()), "stage": str(self.root / f"devcontainer-{VERSION}"),
            "evidence": str(acceptance), "teamIdentifier": TEAM,
            "unsignedTree": unsigned_tree, "signedTree": signed_tree, "signatures": signatures,
        }
        (self.state / "state.json").write_bytes(encoded(record))
        state_sha = digest((self.state / "state.json").read_bytes())
        name = archive.name
        (self.finalized / name).write_bytes(archive.read_bytes())
        (self.finalized / (name + ".sha256")).write_bytes(checksum.read_bytes())
        (self.finalized / "package-context.json").write_bytes(encoded(context))
        (self.finalized / "package-verification.json").write_bytes(encoded(report))
        proof = {
            "schema": 1, "scope": "signed-notarized-package-assembly", "distributionReady": False,
            "sourceCommit": COMMIT, "runtimeProfile": "stock", "candidateReceiptSHA256": record["candidateSHA256"],
            "stageProvenanceSHA256": stage_sha, "trustedStateSHA256": state_sha,
            "acceptedEvidenceSHA256": record["acceptanceSHA256"], "submittedZIPSHA256": record["archiveSHA256"],
            "submittedZIPSize": record["archiveSize"], "submissionID": SUBMISSION,
            "signedTree": signed_tree, "finalTree": final_tree, "archive": name,
            "archiveSHA256": digest(archive.read_bytes()), "archiveSize": archive.stat().st_size,
            "checksumSHA256": digest(checksum.read_bytes()),
            "packageContextSHA256": digest(encoded(context)), "packageVerificationSHA256": digest(encoded(report)),
            "sourceDateEpoch": 0,
            "releaseToolSHA256": {tool: MODULE.digest(REPOSITORY / "Tools/release" / tool)
                                  for tool in MODULE.TOOLS},
        }
        (self.finalized / "native-finalization-provenance.json").write_bytes(encoded(proof))
        self.proof_sha = digest((self.finalized / "native-finalization-provenance.json").read_bytes())
        self.proof = proof
        self.record = record
        self.report = report
        return receipt

    def _admit(self, provider_lane: str = "apple-stock", **overrides) -> dict:
        original = MODULE.load_helpers

        def helpers(repository: Path):
            signer, finalizer, prepare_releases = original(repository)
            signer.verify_signatures = lambda _stage, _state, _team, _prefix: self.record["signatures"]
            return signer, finalizer, prepare_releases

        values = {
            "repository": REPOSITORY, "finalized": self.finalized,
            "trusted_provenance_sha256": self.proof_sha, "expected_source_commit": COMMIT,
            "provider_lane": provider_lane, "state": self.state, "scratch_root": self.scratch,
            "retained_root": self.retained, "evidence_root": self.evidence,
        }
        values.update(overrides)
        with patch.object(MODULE, "require_production_storage"), patch.object(MODULE, "load_helpers", side_effect=helpers):
            return MODULE.admit_finalized_package(**values)

    def test_same_stock_package_is_admitted_for_both_provider_lanes(self) -> None:
        apple = self._admit("apple-stock")
        compose = self._admit("container-compose")
        self.assertEqual(apple["scope"], "finalized-native-package-runtime-input")
        self.assertEqual(apple["kind"], "signed-notarized-native-package")
        self.assertEqual(apple["providerLane"], "apple-stock")
        self.assertEqual(compose["providerLane"], "container-compose")
        self.assertEqual(apple["archiveSHA256"], compose["archiveSHA256"])
        self.assertEqual(apple["preparationSHA256"], compose["preparationSHA256"])
        self.assertEqual(apple["root"], compose["root"])
        self.assertEqual(apple["runtimeProfile"], "stock")
        self.assertEqual(apple["productionBinarySHA256"], compose["productionBinarySHA256"])
        self.assertEqual(apple, self._admit("apple-stock"))
        self.assertEqual(set(apple["executables"]), {
            "devcontainer", "devcontainer-engine", "devcontainer-compose", "devcontainer-docker"})
        self.assertTrue(Path(apple["pluginExecutable"]).is_file())
        self.assertEqual(apple["referenceRuntime"]["files"]["node"],
                         apple["productionBinarySHA256"]["libexec/devcontainer/reference/node"])
        self.assertNotEqual(apple["referenceRuntime"]["files"], apple["referenceRuntime"]["candidateFiles"])
        self.assertEqual(apple["signatureInventorySHA256"], compose["signatureInventorySHA256"])
        self.assertTrue(all(Path(path).is_file() for path in apple["executables"].values()))

    def test_cached_prepare_releases_from_another_checkout_is_rejected(self) -> None:
        cached = ModuleType("prepare_releases")
        cached.__file__ = str(self.root / "other-checkout" / "Tools/bazel/prepare_releases.py")
        with patch.dict(sys.modules, {"prepare_releases": cached}):
            with self.assertRaisesRegex(ValueError, "different repository"):
                MODULE.load_helpers(REPOSITORY)

    def test_wrong_trusted_proof_or_source_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "provenance checksum"):
            self._admit(trusted_provenance_sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "source or stock runtime"):
            self._admit(expected_source_commit="f" * 40)

    def test_tampered_archive_is_rejected_before_extraction(self) -> None:
        archive = self.finalized / self.proof["archive"]
        archive.write_bytes(archive.read_bytes() + b"tampered")
        with self.assertRaisesRegex(ValueError, "bind the promoted files"):
            self._admit()

    def test_finalized_member_symlink_is_rejected(self) -> None:
        report = self.finalized / "package-verification.json"
        saved = self.root / "verification-target.json"
        saved.write_bytes(report.read_bytes())
        report.unlink()
        report.symlink_to(saved)
        with self.assertRaisesRegex(ValueError, "aliased"):
            self._admit()

    def test_final_tree_extension_and_linked_output_are_rejected(self) -> None:
        proof_path = self.finalized / "native-finalization-provenance.json"
        self.proof["finalTree"]["extra/payload"] = {"sha256": "0" * 64, "size": 0, "mode": 0o644}
        proof_path.write_bytes(encoded(self.proof))
        self.proof_sha = digest(proof_path.read_bytes())
        with self.assertRaisesRegex(ValueError, "signed payload closure"):
            self._admit()
        self.proof["finalTree"].pop("extra/payload")
        proof_path.write_bytes(encoded(self.proof))
        self.proof_sha = digest(proof_path.read_bytes())
        archive = self.finalized / self.proof["archive"]
        saved = self.root / "linked-archive-target.tar.gz"
        saved.write_bytes(archive.read_bytes())
        archive.unlink()
        archive.symlink_to(saved)
        with self.assertRaisesRegex(ValueError, "aliased"):
            self._admit()

    def test_wrong_tool_identity_and_provider_profile_are_rejected(self) -> None:
        self.proof["releaseToolSHA256"]["native_signing.py"] = "0" * 64
        proof_path = self.finalized / "native-finalization-provenance.json"
        proof_path.write_bytes(encoded(self.proof))
        self.proof_sha = digest(proof_path.read_bytes())
        with self.assertRaisesRegex(ValueError, "different release helper bundle"):
            self._admit()
        self.proof["releaseToolSHA256"]["native_signing.py"] = MODULE.digest(REPOSITORY / "Tools/release/native_signing.py")
        self.proof["runtimeProfile"] = "enhanced"
        proof_path.write_bytes(encoded(self.proof))
        self.proof_sha = digest(proof_path.read_bytes())
        with self.assertRaisesRegex(ValueError, "source or stock runtime"):
            self._admit()

    def test_signature_readback_mismatch_is_rejected(self) -> None:
        original = MODULE.load_helpers

        def helpers(repository: Path):
            signer, finalizer, prep = original(repository)
            signer.verify_signatures = lambda *_args: {}
            return signer, finalizer, prep

        with patch.object(MODULE, "require_production_storage"), patch.object(MODULE, "load_helpers", side_effect=helpers):
            with self.assertRaisesRegex(ValueError, "signatures differ"):
                MODULE.admit_finalized_package(REPOSITORY, self.finalized, self.proof_sha, COMMIT,
                                               "apple-stock", self.state, self.scratch,
                                               self.retained, self.evidence)

    def test_storage_policy_rejects_a_nonfinalized_directory(self) -> None:
        signer, _, _ = MODULE.load_helpers(REPOSITORY)
        with self.assertRaisesRegex(ValueError, "beneath internal retained finalized storage"):
            MODULE.require_production_storage(REPOSITORY, self.finalized, self.state,
                                              self.scratch, self.retained, self.evidence,
                                              "0" * 64, signer)

    def test_storage_policy_loads_consumer_and_checks_enrolled_ssd_identity(self) -> None:
        home = self.root / "home"
        home.mkdir()
        final_root = home / "Library/Application Support/ContainerFamily/retained/devcontainer/finalized"
        finalized = final_root / "release-1"
        finalized.mkdir(parents=True, mode=0o700)
        finalized.chmod(0o700)
        admission_root = home / "Library/Application Support/ContainerFamily/retained/devcontainer/finalized-admissions"
        retained = admission_root / "run"
        evidence = retained / "signature-evidence"
        state = self.root / "accepted-state"
        state.mkdir(mode=0o700)
        state.chmod(0o700)
        scratch = self.scratch
        state_record = {"stage": str(self.root / "stage"), "evidence": str(self.root / "acceptance.json")}
        (state / "state.json").write_bytes(encoded(state_record))
        (state / "state.json").chmod(0o600)
        identity_path = home / "Library/Application Support/ContainerFamily/retained/workflow/ssd-volume.uuid"
        identity_path.parent.mkdir(parents=True)
        expected_uuid = "884BCCCF-5C0C-4A9B-B412-04FD1C1A6895"
        identity_path.write_text(expected_uuid + "\n", encoding="ascii")
        identity_path.chmod(0o600)
        volume = plistlib.dumps({"MountPoint": "/Volumes/SSD", "Internal": False,
                                 "VolumeUUID": expected_uuid})
        signer, _, _ = MODULE.load_helpers(REPOSITORY)
        original_stat = Path.stat

        def external_scratch_stat(path: Path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            if path == scratch:
                values = list(result)
                values[2] = result.st_dev + 1
                return os.stat_result(values)
            return result

        disk_info = type("DiskInfo", (), {"stdout": volume})()
        with patch.object(MODULE.Path, "home", return_value=home), \
                patch.object(MODULE.Path, "stat", external_scratch_stat), \
                patch.object(MODULE.subprocess, "run", return_value=disk_info), \
                patch.object(signer, "validate_storage") as validate_stage:
            disk_info.stdout = plistlib.dumps({"MountPoint": "/Volumes/SSD", "Internal": False,
                                               "VolumeUUID": "00000000-0000-0000-0000-000000000000"})
            with self.assertRaises(ValueError):
                MODULE.require_production_storage(REPOSITORY, finalized, state, scratch,
                                                  retained, evidence, MODULE.digest(state / "state.json"), signer)
            validate_stage.assert_not_called()
            self.assertFalse(retained.exists())
            self.assertFalse(evidence.exists())
            disk_info.stdout = volume
            MODULE.require_production_storage(REPOSITORY, finalized, state, scratch,
                                              retained, evidence, MODULE.digest(state / "state.json"), signer)
        validate_stage.assert_called_once_with(Path(state_record["stage"]), state, scratch,
                                               Path(state_record["evidence"]))
        self.assertEqual(finalized.stat().st_mode & 0o777, 0o700)
        self.assertEqual(retained.stat().st_mode & 0o777, 0o700)
        self.assertEqual(evidence.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
