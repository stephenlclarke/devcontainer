"""Boundary tests for attaching authenticated native parity evidence to releases."""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import verify_parity_release_evidence as evidence


REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE = "a" * 40
ARTIFACT_ID = "8421"
HARNESS_SHA = "d" * 64


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ParityReleaseEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.artifact = self.root / "downloaded"
        (self.artifact / "vscode").mkdir(parents=True)
        self.output = self.root / "release-assets"
        self.artifact_zip = self.root / "artifact.zip"
        self.extracted = self.root / "verified-artifact"
        self.package = self.root / "package"
        self.package.mkdir()
        self.archive = self.package / "devcontainer-1.0.1.tar.gz"
        self.archive.write_bytes(b"small authenticated fixture archive")
        self.checksum = self.package / (self.archive.name + ".sha256")
        self.checksum.write_text(f"{sha(self.archive.read_bytes())}  {self.archive.name}\n")
        self.context = self.package / "package-context.json"
        self.context.write_text(json.dumps({"asset": self.archive.name, "commit": SOURCE,
                                            "lane": "current", "productVersion": "1.0.1"}))
        self.verification = self.package / "package-verification.json"
        self.verification.write_text(json.dumps({"archive": self.archive.name, "commit": SOURCE,
                                                 "lane": "current", "notarized": True,
                                                 "sha256": sha(self.archive.read_bytes()), "version": "1.0.1"}))
        self.proof = self.package / "native-finalization-provenance.json"
        self.proof.write_text(json.dumps({"schema": 1, "scope": "signed-notarized-package-assembly",
                                          "distributionReady": False, "sourceCommit": SOURCE,
                                          "runtimeProfile": "stock", "candidateReceiptSHA256": "e" * 64,
                                          "trustedStateSHA256": "f" * 64, "archive": self.archive.name,
                                          "archiveSHA256": sha(self.archive.read_bytes()),
                                          "archiveSize": self.archive.stat().st_size,
                                          "packageContextSHA256": sha(self.context.read_bytes()),
                                          "packageVerificationSHA256": sha(self.verification.read_bytes())}))
        self.finalization_sha = sha(self.proof.read_bytes())
        self.identity = {
            "scope": "finalized-native-package-runtime-input",
            "kind": "signed-notarized-native-package",
            "sourceCommit": SOURCE,
            "runtimeProfile": "stock",
            "candidateReceiptSHA256": "e" * 64,
            "finalizationProvenanceSHA256": self.finalization_sha,
            "trustedStateSHA256": "f" * 64,
            "archiveSHA256": sha(self.archive.read_bytes()),
            "archiveSize": self.archive.stat().st_size,
            "preparationSHA256": "1" * 64,
            "inventorySHA256": "2" * 64,
            "productionBinarySHA256": {
                name: "3" * 64 for name in (
                    "bin/devcontainer", "bin/devcontainer-compose", "bin/devcontainer-engine",
                    "bin/devcontainer-docker",
                    "libexec/container/plugins/devcontainer/bin/devcontainer",
                    "libexec/devcontainer/reference/node",
                )
            },
            "signatureInventorySHA256": "4" * 64,
            "referenceRuntimeFiles": {name: "5" * 64 for name in (
                "node", "NODE-LICENSE.txt", "runtime-lock.json", "cli/devcontainer.js",
                "cli/dist/spec-node/devContainersSpecCLI.js", "cli/scripts/updateUID.Dockerfile",
                "cli/package.json", "cli/LICENSE.txt", "cli/ThirdPartyNotices.txt",
            )},
        }
        self.run_json = self.root / "run.json"
        self.run_document = {"id": 6001, "run_number": 12, "event": "push",
                             "head_branch": "main", "head_sha": SOURCE,
                             "status": "completed", "conclusion": "success",
                             "path": ".github/workflows/parity.yml"}
        self.run_json.write_text(json.dumps(self.run_document))
        self.artifact_json = self.root / "artifacts.json"
        self.artifact_json.write_text(json.dumps({"artifacts": [{"id": int(ARTIFACT_ID),
            "name": "parity-comparison", "digest": "sha256:" + "0" * 64,
            "expired": False, "size_in_bytes": 2048, "created_at": "2026-10-01T12:00:00Z",
            "workflow_run": {"id": 6001, "head_branch": "main", "head_sha": SOURCE}}]}))
        self.fixture_ids = {suite: evidence.expected_fixture_ids(REPOSITORY, suite)
                            for suite in ("cli", "vscode")}
        for suite, json_name, matrix_name in (
            ("cli", "comparison.json", "matrix.md"),
            ("vscode", "vscode/comparison.json", "vscode/matrix.md"),
        ):
            payload = {
                "schemaVersion": 3, "suite": suite, "status": "passed", "evidenceStatus": "passed",
                "evidenceErrors": [], "expectedFixtures": self.fixture_ids[suite],
                "requireZeroFunctionalDifferences": True, "functionalParityStatus": "passed",
                "timingStatus": "passed", "performanceTargetMet": True,
                "performanceInvestigationRequired": False,
                "performancePolicy": evidence.PERFORMANCE_POLICY,
                "finalizedPackage": self.identity, "parityHarnessSHA256": HARNESS_SHA,
                "fixtures": [{"id": fixture, "statuses": {"docker": "passed", "apple-stock": "passed",
                    "container-compose": "passed"}, "functionalEquivalent": True,
                    "functionalDifferences": [], "timingEvidenceValid": True, "timingDifferences": [],
                    "performanceAcceptancePassed": True, "performanceFailures": [], "equivalent": True,
                    "differences": []} for fixture in self.fixture_ids[suite]],
            }
            (self.artifact / json_name).write_text(json.dumps(payload))
            matrix = "# parity\n\n| Fixture | Docker | Stock | Provider |\n| --- | --- | --- | --- |\n"
            matrix += "".join(f"| {fixture} | passed | passed | passed |\n" for fixture in self.fixture_ids[suite])
            (self.artifact / matrix_name).write_text(matrix)
        self.patch = mock.patch.object(evidence, "parity_harness_sha256", return_value=HARNESS_SHA)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.temporary.cleanup)

    def create_artifact_zip(self) -> str:
        if self.artifact_zip.exists():
            self.artifact_zip.unlink()
        with zipfile.ZipFile(self.artifact_zip, "w", zipfile.ZIP_DEFLATED) as output:
            for source in sorted(path for path in self.artifact.rglob("*") if path.is_file()):
                output.write(source, source.relative_to(self.artifact).as_posix())
        artifact_digest = sha(self.artifact_zip.read_bytes())
        listing = json.loads(self.artifact_json.read_text())
        listing["artifacts"][0]["digest"] = "sha256:" + artifact_digest
        self.artifact_json.write_text(json.dumps(listing))
        return artifact_digest

    def validate(self, artifact_digest: str | None = None) -> dict:
        if self.extracted.exists():
            shutil.rmtree(self.extracted)
        actual_digest = self.create_artifact_zip()
        return evidence.validate(REPOSITORY, self.run_json, self.artifact_json, "6001", ARTIFACT_ID,
                                 artifact_digest or actual_digest, self.artifact_zip, self.extracted,
                                 SOURCE, self.archive, self.checksum,
                                 self.context, self.verification, self.proof, self.finalization_sha, self.output)

    def test_rejects_tampered_download_bytes_against_api_digest(self) -> None:
        artifact_digest = self.create_artifact_zip()
        with self.artifact_zip.open("ab") as stream:
            stream.write(b"tampered")
        with self.assertRaisesRegex(evidence.EvidenceError, "ZIP bytes differ"):
            evidence.validate(REPOSITORY, self.run_json, self.artifact_json, "6001", ARTIFACT_ID,
                              artifact_digest, self.artifact_zip, self.extracted, SOURCE, self.archive,
                              self.checksum, self.context, self.verification, self.proof,
                              self.finalization_sha, self.output)

    def test_rejects_zip_symlink_member_even_when_api_digest_matches(self) -> None:
        regular_names = ("matrix.md", "vscode/comparison.json", "vscode/matrix.md")
        with zipfile.ZipFile(self.artifact_zip, "w") as output:
            link = zipfile.ZipInfo("comparison.json")
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            output.writestr(link, "not a regular file")
            for name in regular_names:
                output.writestr(name, "{}")
        actual = sha(self.artifact_zip.read_bytes())
        listing = json.loads(self.artifact_json.read_text())
        listing["artifacts"][0]["digest"] = "sha256:" + actual
        self.artifact_json.write_text(json.dumps(listing))
        with self.assertRaisesRegex(evidence.EvidenceError, "unsafe path or non-regular"):
            evidence.validate(REPOSITORY, self.run_json, self.artifact_json, "6001", ARTIFACT_ID,
                              actual, self.artifact_zip, self.extracted, SOURCE, self.archive,
                              self.checksum, self.context, self.verification, self.proof,
                              self.finalization_sha, self.output)

    def test_accepts_exact_run_artifact_package_and_writes_release_contract(self) -> None:
        receipt = self.validate()
        self.assertEqual(receipt["fixtureCounts"]["totalLaneFixtureResults"], 84)
        self.assertEqual(len(list(self.output.iterdir())), 5)
        self.assertEqual((self.output / "runtime-parity-cli-comparison.json").read_bytes(),
                         (self.artifact / "comparison.json").read_bytes())
        self.assertFalse(json.loads((self.output / "runtime-parity-provenance.json").read_text())
                         ["releaseAuthority"])

    def test_rejects_other_event_branch_source_or_incomplete_run(self) -> None:
        cases = (("event", "workflow_dispatch", "main-branch push"),
                 ("head_branch", "feature", "main-branch push"),
                 ("head_sha", "9" * 40, "source differs"),
                 ("conclusion", "failure", "did not complete successfully"))
        for key, value, message in cases:
            with self.subTest(field=key):
                run = dict(self.run_document)
                run[key] = value
                self.run_json.write_text(json.dumps(run))
                with self.assertRaisesRegex(evidence.EvidenceError, message):
                    self.validate()

    def test_rejects_mixed_artifact_id_or_digest(self) -> None:
        with self.assertRaisesRegex(evidence.EvidenceError, "artifact ID"):
            evidence.validate(REPOSITORY, self.run_json, self.artifact_json, "6001", "9999",
                              "0" * 64, self.artifact_zip, self.extracted, SOURCE, self.archive, self.checksum,
                              self.context, self.verification, self.proof, self.finalization_sha, self.output)
        self.create_artifact_zip()
        with self.assertRaisesRegex(evidence.EvidenceError, "artifact digest"):
            evidence.validate(REPOSITORY, self.run_json, self.artifact_json, "6001", ARTIFACT_ID,
                              "9" * 64, self.artifact_zip, self.extracted, SOURCE, self.archive, self.checksum,
                              self.context, self.verification, self.proof, self.finalization_sha, self.output)

    def test_rejects_artifact_linked_to_another_successful_source_run(self) -> None:
        listing = json.loads(self.artifact_json.read_text())
        listing["artifacts"][0]["workflow_run"]["head_sha"] = "9" * 40
        self.artifact_json.write_text(json.dumps(listing))
        with self.assertRaisesRegex(evidence.EvidenceError, "not linked to the exact source run"):
            self.validate()

    def test_rejects_missing_or_extra_downloaded_artifact_files(self) -> None:
        (self.artifact / "matrix.md").unlink()
        with self.assertRaisesRegex(evidence.EvidenceError, "ZIP has missing"):
            self.validate()

    def test_rejects_source_or_finalization_proof_mismatch(self) -> None:
        with self.assertRaisesRegex(evidence.EvidenceError, "provenance checksum"):
            evidence.validate(REPOSITORY, self.run_json, self.artifact_json, "6001", ARTIFACT_ID,
                              self.create_artifact_zip(), self.artifact_zip, self.extracted, SOURCE,
                              self.archive, self.checksum, self.context, self.verification,
                              self.proof, "9" * 64, self.output)
        context = json.loads(self.context.read_text())
        context["commit"] = "9" * 40
        self.context.write_text(json.dumps(context))
        with self.assertRaisesRegex(evidence.EvidenceError, "context"):
            self.validate()

    def test_rejects_failure_difference_cleanup_failure_and_timing_policy_drift(self) -> None:
        path = self.artifact / "vscode/comparison.json"
        value = json.loads(path.read_text())
        value["fixtures"][0]["statuses"]["apple-stock"] = "failed"
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(evidence.EvidenceError, "failed lane or cleanup"):
            self.validate()
        value["fixtures"][0]["statuses"]["apple-stock"] = "passed"
        value["fixtures"][0]["differences"] = ["runtime cleanup failed"]
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(evidence.EvidenceError, "difference or invalid timing"):
            self.validate()
        value["fixtures"][0]["differences"] = []
        value["performancePolicy"]["failureFactor"] = 11.0
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(evidence.EvidenceError, "performance policy"):
            self.validate()

    def test_rejects_wrong_inventory_package_archive_and_harness(self) -> None:
        path = self.artifact / "comparison.json"
        value = json.loads(path.read_text())
        value["expectedFixtures"] = value["expectedFixtures"][:-1]
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(evidence.EvidenceError, "fixture inventory"):
            self.validate()
        value["expectedFixtures"] = self.fixture_ids["cli"]
        value["finalizedPackage"]["archiveSHA256"] = "0" * 64
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(evidence.EvidenceError, "exact finalized source"):
            self.validate()
        value["finalizedPackage"] = self.identity
        value["parityHarnessSHA256"] = "0" * 64
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(evidence.EvidenceError, "harness identity"):
            self.validate()


if __name__ == "__main__":
    unittest.main()
