#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Focused tests for local qualification receipt admission."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

from compare_results import compare, expected_fixtures
from parity_lib import LANES
from qualify_finalized_package import seal_qualification
from run_lane import parity_harness_sha256
from verify_local_qualification import (
    QualificationError,
    authenticate_result,
    authenticate_provider_evidence,
    inventory_files,
    expected_provider_hashes,
    public_comparison,
    validate_retained_location,
    validate_receipt,
    verify_local_qualification,
)


REPOSITORY = Path(__file__).parents[2]


def sha(value: bytes) -> str:
    """Hash fixture bytes."""

    return hashlib.sha256(value).hexdigest()


def completed(command: list[str], stdout: str) -> subprocess.CompletedProcess[str]:
    """Build deterministic mocked git command output."""

    return subprocess.CompletedProcess(command, 0, stdout, "")


class VerifyLocalQualificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPOSITORY, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        self.tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=REPOSITORY, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        manifest = json.loads((REPOSITORY / "Tests/Parity/manifest.json").read_text())
        pins = manifest["referencePins"]
        digest = "a" * 64
        self.receipt = {
            "schemaVersion": 1,
            "scope": "local-native-package-parity-qualification",
            "executedLocally": True,
            "status": "passed",
            "sourceCommit": self.commit,
            "sourceTree": self.tree,
            "sourceDirty": False,
            "controllerSHA256": sha((REPOSITORY / "Tools/parity/qualify_finalized_package.py").read_bytes()),
            "parityHarnessSHA256": parity_harness_sha256(REPOSITORY),
            "finalizationProvenanceSHA256": digest,
            "archiveSHA256": digest,
            "trustedStateSHA256": digest,
            "submissionID": "11111111-2222-4333-8444-555555555555",
            "fixtureCounts": {
                "cliPerLane": 27,
                "vscodePerLane": 1,
                "laneCount": 3,
                "totalLaneFixtureResults": 84,
            },
            "restoration": {lane: "restored" for lane in LANES},
            "guardCleared": True,
            "enginePins": pins["docker"],
            "providerTools": {
                "docker": {"sha256": pins["docker"]["cliSHA256"],
                           "version": pins["docker"]["cliVersion"],
                           "engineVersion": pins["docker"]["engineVersion"],
                           "engineCommit": pins["docker"]["engineCommit"],
                           "engineApiVersion": pins["docker"]["engineApiVersion"],
                           "engineSHA256": pins["docker"]["engineSHA256"],
                           "engineEvidence": {"path": "inputs/docker-engine.json",
                                              "sha256": digest}},
                "dockerCompose": {"sha256": pins["docker"]["composeSHA256"],
                                  "bottleSHA256": pins["docker"]["composeBottleSHA256"],
                                  "version": pins["docker"]["composeVersion"]},
                "appleStock": {"containerSHA256": digest,
                               "apiServerSHA256": digest,
                               "apiServerEvidence": {"path": "inputs/apple-stock-api.json",
                                                     "sha256": digest},
                               "version": pins["appleContainer"]["stableVersion"],
                               "commit": pins["appleContainer"]["stableCommit"]},
                "containerCompose": {"containerSHA256": digest,
                                     "composeSHA256": digest,
                                     "apiServerSHA256": digest,
                                     "apiServerEvidence": {"path": "inputs/compose-api.json",
                                                           "sha256": digest},
                                     "version": pins["containerCompose"]["stableVersion"],
                                     "commit": pins["containerCompose"]["stableCommit"]},
                "colima": {"sha256": digest, "version": "1.0.0"},
                "vscode": {"launcherSHA256": digest, "version": pins["vscode"]["version"],
                           "commit": pins["vscode"]["commit"],
                           "archiveSHA256": pins["vscode"]["archiveSHA256"],
                           "vsixSHA256": pins["vscode"]["devContainersExtension"]["vsixSHA256"]},
            },
            "laneResults": {
                lane: {suite: {"status": "passed", "results": {"path": f"inputs/{lane}-{suite}.json",
                                                                      "sha256": digest},
                               "fingerprint": {"path": f"inputs/{lane}-{suite}-fingerprint.json",
                                                "sha256": digest}}
                       for suite in ("cli", "vscode")}
                for lane in LANES
            },
            "comparisons": {
                name: {"status": "passed", "path": f"inputs/{name}-comparison.json",
                      "sha256": digest}
                for name in ("cli", "vscode", "releaseManifest")
            },
            "cleanup": {
                lane: {"status": "restored", "cliCleanupComplete": True,
                      "vscodeCleanupComplete": True,
                      "path": f"inputs/{lane}-cleanup.json", "sha256": digest}
                for lane in LANES
            },
            "serviceJournalReceipts": {
                lane: {"path": f"inputs/{lane}-journal.json",
                      "sha256": digest}
                for lane in ("apple-stock", "container-compose")
            },
            "inputFiles": [],
        }
        for lane in LANES:
            self.receipt["laneResults"][lane]["vscode"]["security"] = {
                "status": "passed", "path": f"inputs/{lane}-security.json",
                "sha256": digest,
            }
        for lane in ("apple-stock", "container-compose"):
            self.receipt["serviceJournalReceipts"][lane].update(
                ownerSHA256=digest, records=1, seal=digest,
            )
        self.receipt["cleanup"]["host"] = {
            "status": "restored", "hostGuardCleared": True,
            "initialColima": "stopped", "finalColima": "stopped",
            "path": "inputs/host-cleanup.json", "sha256": digest,
        }
        self.receipt["cleanup"]["docker"]["colima"] = {
            "initial": "stopped", "final": "stopped", "restored": True,
            "startedByController": False,
        }
        for lane in ("apple-stock", "container-compose"):
            self.receipt["cleanup"][lane].update(
                providerStopped=True, serviceRestored=True,
                serviceJournalReceiptSHA256=digest,
            )
        references = []

        def visit(value: object) -> None:
            if isinstance(value, dict):
                if "path" in value and "sha256" in value:
                    references.append({"path": value["path"], "sha256": value["sha256"],
                                       "size": 1})
                for key, child in value.items():
                    if key != "inputFiles":
                        visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit({key: value for key, value in self.receipt.items() if key != "inputFiles"})
        self.receipt["inputFiles"] = sorted(references, key=lambda item: item["path"])

    def _mock_git(self, command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        if command[-1] == "HEAD":
            return completed(command, self.commit + "\n")
        if command[-1] == "HEAD^{tree}":
            return completed(command, self.tree + "\n")
        return completed(command, "")

    def test_valid_exact_source_receipt_is_admitted(self) -> None:
        with patch("verify_local_qualification.subprocess.run", side_effect=self._mock_git):
            cli, vscode = validate_receipt(self.receipt, REPOSITORY, self.commit)
        self.assertEqual(len(cli), 27)
        self.assertEqual(len(vscode), 1)

    def test_stale_source_commit_is_rejected(self) -> None:
        receipt = dict(self.receipt, sourceCommit="f" * 40)
        with self.assertRaisesRegex(QualificationError, "source commit"):
            validate_receipt(receipt, REPOSITORY, self.commit)

    def test_trusted_receipt_with_wrong_controller_digest_is_rejected(self) -> None:
        receipt = dict(self.receipt, controllerSHA256="f" * 64)
        with self.assertRaisesRegex(QualificationError, "controller digest"):
            validate_receipt(receipt, REPOSITORY, self.commit)

    def test_incomplete_lane_universe_is_rejected(self) -> None:
        receipt = dict(self.receipt)
        receipt["laneResults"] = dict(self.receipt["laneResults"])
        del receipt["laneResults"]["container-compose"]
        with patch("verify_local_qualification.subprocess.run", side_effect=self._mock_git):
            with self.assertRaisesRegex(QualificationError, "three parity lanes"):
                validate_receipt(receipt, REPOSITORY, self.commit)

    def test_uncleared_host_guard_is_rejected(self) -> None:
        receipt = dict(self.receipt, guardCleared=False)
        with self.assertRaisesRegex(QualificationError, "host guard"):
            validate_receipt(receipt, REPOSITORY, self.commit)

    def test_retained_receipt_requires_private_content_addressed_directory(self) -> None:
        digest = "a" * 64
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "qualifications"
            leaf = root / digest
            leaf.mkdir(parents=True, mode=0o700)
            root.chmod(0o700)
            leaf.chmod(0o700)
            with patch("verify_local_qualification.QUALIFICATION_ROOT", root):
                self.assertEqual(validate_retained_location(leaf, digest).st_uid,
                                 os.getuid())
                with self.assertRaisesRegex(QualificationError, "content-addressed store"):
                    validate_retained_location(root / ("b" * 64), digest)
                leaf.chmod(0o755)
                with self.assertRaisesRegex(QualificationError, "not private"):
                    validate_retained_location(leaf, digest)

    def test_incomplete_host_restoration_is_rejected(self) -> None:
        receipt = dict(self.receipt)
        receipt["cleanup"] = dict(self.receipt["cleanup"])
        receipt["cleanup"]["apple-stock"] = dict(self.receipt["cleanup"]["apple-stock"])
        receipt["cleanup"]["apple-stock"]["status"] = "failed"
        with patch("verify_local_qualification.subprocess.run", side_effect=self._mock_git):
            with self.assertRaisesRegex(QualificationError, "cleanup evidence"):
                validate_receipt(receipt, REPOSITORY, self.commit)

    def test_inventory_requires_exact_authenticated_json_closure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "inputs" / "result.json"
            path.parent.mkdir(mode=0o700)
            content = b'{"status":"passed"}\n'
            path.write_bytes(content)
            descriptor = {"path": "inputs/result.json", "sha256": sha(content)}
            receipt = {
                "laneResults": {"docker": {"cli": {"results": descriptor}}},
                "inputFiles": [{**descriptor, "size": len(content)}],
            }
            admitted = inventory_files(root, receipt)
            self.assertEqual(admitted["inputs/result.json"], content)
            path.write_bytes(b'{"status":"failed"}\n')
            with self.assertRaisesRegex(QualificationError, "digest differs"):
                inventory_files(root, receipt)

    def test_inventory_rejects_symlink_members(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "outside.json"
            source.write_text("{}")
            inputs = root / "inputs"
            inputs.mkdir(mode=0o700)
            (inputs / "linked.json").symlink_to(source)
            content = source.read_bytes()
            descriptor = {"path": "inputs/linked.json", "sha256": sha(content)}
            receipt = {"comparisons": {"cli": descriptor},
                       "inputFiles": [{**descriptor, "size": len(content)}]}
            with self.assertRaisesRegex(QualificationError, "symlink"):
                inventory_files(root, receipt)

    def test_inventory_rejects_path_traversal_and_unreferenced_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            content = b"{}"
            descriptor = {"path": "inputs/../escape.json", "sha256": sha(content)}
            receipt = {"laneResults": {"docker": {"cli": {"results": descriptor}}},
                       "inputFiles": [{**descriptor, "size": len(content)}]}
            with self.assertRaisesRegex(QualificationError, "unsafe component"):
                inventory_files(root, receipt)

            receipt["laneResults"]["docker"]["cli"]["results"]["path"] = "inputs/safe.json"
            receipt["inputFiles"] = [{"path": "inputs/unreferenced.json",
                                      "sha256": sha(content), "size": len(content)}]
            with self.assertRaisesRegex(QualificationError, "exact referenced evidence closure"):
                inventory_files(root, receipt)

    def test_lane_results_must_bind_package_and_harness(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result.json"
            path.write_text(json.dumps({
                "backend": "docker", "status": "passed", "fixtures": [
                    {"id": "D01", "status": "passed"}], "cleanupDifferences": [],
                "finalizedPackage": {"sourceCommit": self.commit,
                                     "archiveSHA256": self.receipt["archiveSHA256"],
                                     "finalizationProvenanceSHA256": self.receipt["finalizationProvenanceSHA256"],
                                     "trustedStateSHA256": self.receipt["trustedStateSHA256"]},
                "parityHarnessSHA256": self.receipt["parityHarnessSHA256"],
                "providerBinarySHA256": {},
            }))
            authenticate_result(path, "docker", "cli", {"D01"}, self.receipt)
            payload = json.loads(path.read_text())
            payload["parityHarnessSHA256"] = "0" * 64
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(QualificationError, "different parity harness"):
                authenticate_result(path, "docker", "cli", {"D01"}, self.receipt)

    def test_provider_api_evidence_binds_frontend_hashes_and_pins(self) -> None:
        tools = self.receipt["providerTools"]
        docker = tools["docker"]
        compose = tools["dockerCompose"]
        apple = tools["appleStock"]
        container_compose = tools["containerCompose"]
        documents = {
            docker["engineEvidence"]["path"]: {
                "schemaVersion": 1, "source": "docker-oracle",
                "clientVersion": docker["version"], "dockerCLISHA256": docker["sha256"],
                "engineVersion": docker["engineVersion"], "engineCommit": docker["engineCommit"],
                "engineApiVersion": docker["engineApiVersion"], "engineSHA256": docker["engineSHA256"],
                "composeVersion": compose["version"], "composeSHA256": compose["sha256"],
                "bottleSHA256": compose["bottleSHA256"],
            },
            apple["apiServerEvidence"]["path"]: {
                "schemaVersion": 1, "lane": "apple-stock",
                "providerVersion": apple["version"], "providerCommit": apple["commit"],
                "containerSHA256": apple["containerSHA256"],
                "apiServerSHA256": apple["apiServerSHA256"],
            },
            container_compose["apiServerEvidence"]["path"]: {
                "schemaVersion": 1, "lane": "container-compose",
                "providerVersion": container_compose["version"],
                "providerCommit": container_compose["commit"],
                "containerSHA256": container_compose["containerSHA256"],
                "apiServerSHA256": container_compose["apiServerSHA256"],
                "composeVersion": container_compose["version"],
                "composeSHA256": container_compose["composeSHA256"],
            },
        }
        inventory = {path: json.dumps(value).encode() for path, value in documents.items()}
        authenticate_provider_evidence(self.receipt, inventory)
        apple["apiServerSHA256"] = "0" * 64
        with self.assertRaisesRegex(QualificationError, "apple-stock provider/API"):
            authenticate_provider_evidence(self.receipt, inventory)

    def test_provider_frontend_digests_match_their_exact_lane_maps(self) -> None:
        tools = self.receipt["providerTools"]
        self.assertEqual(expected_provider_hashes(self.receipt, "docker"), {})
        self.assertEqual(expected_provider_hashes(self.receipt, "apple-stock"), {
            "DEVCONTAINER_CONTAINER_BIN": tools["appleStock"]["containerSHA256"],
        })
        self.assertEqual(expected_provider_hashes(self.receipt, "container-compose"), {
            "DEVCONTAINER_CONTAINER_BIN": tools["containerCompose"]["containerSHA256"],
            "DEVCONTAINER_COMPOSE_BIN": tools["containerCompose"]["composeSHA256"],
        })

    def test_public_comparison_rejects_local_package_paths(self) -> None:
        fields = {
            "scope": "finalized-native-package-runtime-input",
            "kind": "signed-notarized-native-package",
            "sourceCommit": self.commit,
            "runtimeProfile": "stock",
            "candidateReceiptSHA256": "a" * 64,
            "finalizationProvenanceSHA256": "b" * 64,
            "trustedStateSHA256": "c" * 64,
            "archiveSHA256": "d" * 64,
            "archiveSize": 10,
            "preparationSHA256": "e" * 64,
            "inventorySHA256": "f" * 64,
            "signatureInventorySHA256": "0" * 64,
            "productionBinarySHA256": {"bin/devcontainer": "1" * 64},
            "referenceRuntimeFiles": {"node": "2" * 64},
        }
        report = public_comparison({"finalizedPackage": fields})
        encoded = json.dumps(report)
        self.assertEqual(report["finalizedPackage"]["referenceRuntimeFiles"],
                         {"node": "2" * 64})
        fields["root"] = "/Users/private/retained/package"
        with self.assertRaisesRegex(QualificationError, "contains local paths"):
            public_comparison({"finalizedPackage": fields})

    def test_sealed_producer_receipt_replays_through_importer(self) -> None:
        import qualify_finalized_package as producer

        manifest_path = REPOSITORY / "Tests/Parity/manifest.json"
        manifest = json.loads(manifest_path.read_text())
        pins = manifest["referencePins"]
        source_tree = self.tree
        harness = parity_harness_sha256(REPOSITORY)
        package_identity = {
            "scope": "finalized-native-package-runtime-input",
            "kind": "signed-notarized-native-package",
            "sourceCommit": self.commit,
            "runtimeProfile": "stock",
            "candidateReceiptSHA256": "1" * 64,
            "finalizationProvenanceSHA256": "2" * 64,
            "trustedStateSHA256": "3" * 64,
            "archiveSHA256": "4" * 64,
            "archiveSize": 1024,
            "preparationSHA256": "5" * 64,
            "inventorySHA256": "6" * 64,
            "signatureInventorySHA256": "7" * 64,
            "productionBinarySHA256": {
                key: "8" * 64 for key in (
                    "bin/devcontainer", "bin/devcontainer-compose", "bin/devcontainer-engine",
                    "bin/devcontainer-docker",
                    "libexec/container/plugins/devcontainer/bin/devcontainer",
                    "libexec/devcontainer/reference/node",
                )
            },
            "referenceRuntimeFiles": {
                key: "9" * 64 for key in (
                    "node", "NODE-LICENSE.txt", "runtime-lock.json", "cli/devcontainer.js",
                    "cli/dist/spec-node/devContainersSpecCLI.js", "cli/scripts/updateUID.Dockerfile",
                    "cli/package.json", "cli/LICENSE.txt", "cli/ThirdPartyNotices.txt",
                )
            },
        }
        proof = {
            "archiveSHA256": package_identity["archiveSHA256"],
            "trustedStateSHA256": package_identity["trustedStateSHA256"],
            "submissionID": "11111111-2222-4333-8444-555555555555",
        }
        provider_hashes = {
            "docker": pins["docker"]["cliSHA256"],
            "dockerCompose": pins["docker"]["composeSHA256"],
            "stockContainer": "a" * 64,
            "stockAPIServer": "b" * 64,
            "composeContainer": "c" * 64,
            "composeProvider": "d" * 64,
            "composeAPIServer": "e" * 64,
            "colima": "f" * 64,
            "vscode": "0" * 64,
        }
        tools = {
            "docker": {
                "version": pins["docker"]["cliVersion"],
                "sha256": provider_hashes["docker"],
                "engineVersion": pins["docker"]["engineVersion"],
                "engineCommit": pins["docker"]["engineCommit"],
                "engineApiVersion": pins["docker"]["engineApiVersion"],
                "engineSHA256": pins["docker"]["engineSHA256"],
            },
            "dockerCompose": {
                "version": pins["docker"]["composeVersion"],
                "sha256": provider_hashes["dockerCompose"],
                "bottleSHA256": pins["docker"]["composeBottleSHA256"],
            },
            "appleStock": {
                "version": pins["appleContainer"]["stableVersion"],
                "commit": pins["appleContainer"]["stableCommit"],
                "containerSHA256": provider_hashes["stockContainer"],
                "apiServerSHA256": provider_hashes["stockAPIServer"],
            },
            "containerCompose": {
                "version": pins["containerCompose"]["stableVersion"],
                "commit": pins["containerCompose"]["stableCommit"],
                "containerSHA256": provider_hashes["composeContainer"],
                "composeSHA256": provider_hashes["composeProvider"],
                "apiServerSHA256": provider_hashes["composeAPIServer"],
            },
            "colima": {"version": "colima version 1.0", "sha256": provider_hashes["colima"]},
            "vscode": {
                "version": pins["vscode"]["version"], "commit": pins["vscode"]["commit"],
                "archiveSHA256": pins["vscode"]["archiveSHA256"],
                "vsixSHA256": pins["vscode"]["devContainersExtension"]["vsixSHA256"],
                "launcherSHA256": provider_hashes["vscode"],
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            evidence = root / "evidence"
            retained = root / "retained" / "devcontainer"
            retained.mkdir(parents=True, mode=0o700)
            evidence.mkdir(mode=0o700)

            def write_json(path: Path, value: dict) -> bytes:
                path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                data = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
                path.write_bytes(data)
                return data

            expected_cli = expected_fixtures(manifest_path, "cli")
            expected_vscode = expected_fixtures(manifest_path, "vscode")
            provider_maps = {
                "docker": {},
                "apple-stock": {"DEVCONTAINER_CONTAINER_BIN": provider_hashes["stockContainer"]},
                "container-compose": {
                    "DEVCONTAINER_CONTAINER_BIN": provider_hashes["composeContainer"],
                    "DEVCONTAINER_COMPOSE_BIN": provider_hashes["composeProvider"],
                },
            }
            for lane in LANES:
                for suite, ids, base in (
                    ("cli", expected_cli, evidence / lane),
                    ("vscode", expected_vscode, evidence / "vscode" / lane),
                ):
                    provider_map = provider_maps[lane]
                    result = {
                        "backend": lane, "status": "passed",
                        "fixtures": [
                            {"id": identifier, "status": "passed", "durationSeconds": 0.05,
                             "observations": {"cleanup": "true"} if suite == "vscode"
                             else {"result": "ok"}}
                            for identifier in sorted(ids)
                        ],
                        "finalizedPackage": package_identity,
                        "parityHarnessSHA256": harness,
                        "providerBinarySHA256": provider_map,
                    }
                    if suite == "cli":
                        result["cleanupDifferences"] = []
                    fingerprint = {
                        "backend": lane, "finalizedPackage": package_identity,
                        "parityHarnessSHA256": harness,
                        "providerBinarySHA256": provider_map,
                    }
                    if lane == "apple-stock":
                        fingerprint.update({
                            "containerDistribution": "apple",
                            "commands": {"container": {"stdout": json.dumps({
                                "version": pins["appleContainer"]["stableVersion"],
                                "commit": pins["appleContainer"]["stableCommit"],
                            })}},
                        })
                    elif lane == "container-compose":
                        fingerprint["commands"] = {
                            "container": {"stdout": json.dumps({
                                "version": pins["appleContainer"]["stableVersion"],
                                "commit": pins["appleContainer"]["stableCommit"],
                            })},
                            "containerCompose": {"stdout": json.dumps({
                                "version": pins["containerCompose"]["stableVersion"],
                                "commit": pins["containerCompose"]["stableCommit"],
                            })},
                        }
                    write_json(base / "results.json", result)
                    write_json(base / "fingerprint.json", fingerprint)
                    if suite == "vscode":
                        write_json(base / "security-scan.json", {
                            "status": "passed", "detectedNames": [], "removedFiles": [],
                        })

            comparisons = {}
            for suite, base, ids in (
                ("cli", evidence, expected_cli),
                ("vscode", evidence / "vscode", expected_vscode),
            ):
                comparison, _ = compare(base, ids, suite)
                self.assertEqual(comparison.get("status"), "passed", comparison.get("evidenceErrors"))
                write_json(base / "comparison.json", comparison)
                comparisons[suite] = comparison
            manifest_digest = sha(manifest_path.read_bytes())
            release_manifest = {
                "schemaVersion": 1, "status": "passed", "sourceCommit": self.commit,
                "manifestSHA256": manifest_digest,
                "validator": "Tools/parity/validate_manifest.py --release", "exitCode": 0,
            }
            write_json(evidence / "release-manifest.json", release_manifest)
            comparisons["releaseManifest"] = release_manifest
            for lane in LANES:
                if lane == "docker":
                    lane_cleanup = {
                        "status": "restored", "cliCleanupComplete": True,
                        "vscodeCleanupComplete": True,
                        "colima": {"initial": "stopped", "final": "stopped",
                                   "restored": True, "startedByController": False},
                    }
                    input_cleanup = {
                        "schemaVersion": 1, "lane": lane, "status": "restored",
                        "cliCleanupComplete": True, "vscodeCleanupComplete": True,
                        "colima": lane_cleanup["colima"],
                    }
                else:
                    journal = {
                        "schemaVersion": 1, "lane": lane, "status": "restored",
                        "ownerSHA256": "a" * 64, "records": 1, "seal": "b" * 64,
                    }
                    journal_bytes = write_json(evidence / f"{lane}-service-journal-receipt.json", journal)
                    lane_cleanup = {
                        "status": "restored", "cliCleanupComplete": True,
                        "vscodeCleanupComplete": True, "providerStopped": True,
                        "serviceRestored": True,
                        "serviceJournalReceiptSHA256": sha(journal_bytes),
                    }
                    input_cleanup = {
                        "schemaVersion": 1, "lane": lane, "status": "restored",
                        "cliCleanupComplete": True, "vscodeCleanupComplete": True,
                        "initialColima": "stopped", "finalColima": "stopped",
                        "providerStopped": True, "serviceRestored": True,
                        "serviceJournalReceiptSHA256": lane_cleanup["serviceJournalReceiptSHA256"],
                    }
                producer.write_json(evidence / f"{lane}-cleanup.json", input_cleanup)
                self.receipt["cleanup"][lane] = lane_cleanup
            service_digest = "c" * 64
            host_payload = {
                "schemaVersion": 1, "status": "restored",
                "initialColima": "stopped", "finalColima": "stopped",
                "initialServiceSetSHA256": service_digest,
                "finalServiceSetSHA256": service_digest,
                "initialServiceCount": 2, "finalServiceCount": 2,
                "hostGuardCleared": True,
                "restoration": {lane: "restored" for lane in LANES},
            }
            write_json(evidence / "host-cleanup.json", host_payload)
            write_json(evidence / "providers/docker.json", {
                "schemaVersion": 1, "source": "docker-oracle",
                "clientVersion": tools["docker"]["version"],
                "dockerCLISHA256": tools["docker"]["sha256"],
                "engineVersion": tools["docker"]["engineVersion"],
                "engineCommit": tools["docker"]["engineCommit"],
                "engineApiVersion": tools["docker"]["engineApiVersion"],
                "engineSHA256": tools["docker"]["engineSHA256"],
                "composeVersion": tools["dockerCompose"]["version"],
                "composeSHA256": tools["dockerCompose"]["sha256"],
                "bottleSHA256": tools["dockerCompose"]["bottleSHA256"],
            })
            write_json(evidence / "providers/appleStock.json", {
                "schemaVersion": 1, "lane": "apple-stock",
                "providerVersion": tools["appleStock"]["version"],
                "providerCommit": tools["appleStock"]["commit"],
                "containerSHA256": tools["appleStock"]["containerSHA256"],
                "apiServerSHA256": tools["appleStock"]["apiServerSHA256"],
            })
            write_json(evidence / "providers/containerCompose.json", {
                "schemaVersion": 1, "lane": "container-compose",
                "providerVersion": tools["containerCompose"]["version"],
                "providerCommit": tools["containerCompose"]["commit"],
                "containerSHA256": tools["containerCompose"]["containerSHA256"],
                "apiServerSHA256": tools["containerCompose"]["apiServerSHA256"],
                "composeVersion": tools["containerCompose"]["version"],
                "composeSHA256": tools["containerCompose"]["composeSHA256"],
            })
            provider_tools = {
                "docker": {
                    "version": tools["docker"]["version"],
                    "sha256": tools["docker"]["sha256"],
                    "engineVersion": tools["docker"]["engineVersion"],
                    "engineCommit": tools["docker"]["engineCommit"],
                    "engineApiVersion": tools["docker"]["engineApiVersion"],
                    "engineSHA256": tools["docker"]["engineSHA256"],
                },
                "dockerCompose": tools["dockerCompose"],
                "appleStock": tools["appleStock"],
                "containerCompose": tools["containerCompose"],
                "colima": tools["colima"],
                "vscode": tools["vscode"],
            }
            args = SimpleNamespace(
                qualification_directory=retained / "qualifications", source_commit=self.commit,
                _source_tree=source_tree, _parity_harness_sha256=harness,
                provenance_sha256="2" * 64, _manifest=manifest,
                _provider_hashes=provider_hashes,
            )
            package_proof = {
                "archiveSHA256": proof["archiveSHA256"],
                "trustedStateSHA256": proof["trustedStateSHA256"],
                "submissionID": proof["submissionID"],
            }
            qualification_directory, receipt_digest = seal_qualification(
                args, evidence, self.receipt["cleanup"], host_payload, comparisons,
                provider_tools, package_proof,
            )
            fake_home = root / "account-home"
            fake_home.mkdir(mode=0o700)
            finalized = root / "finalized"
            finalized.mkdir(mode=0o700)
            accepted = root / "accepted-state"
            accepted.mkdir(mode=0o700)
            output = root / "official-output"
            with (
                patch("verify_local_qualification.ACCOUNT_HOME", fake_home),
                patch("verify_local_qualification.QUALIFICATION_ROOT",
                      retained / "qualifications"),
                patch("verify_local_qualification.subprocess.run", side_effect=self._mock_git),
                patch("verify_local_qualification.authenticate_finalization") as admit_package,
                patch.dict(os.environ, {"HOME": str(root / "untrusted-home")}),
            ):
                verify_local_qualification(
                    REPOSITORY, qualification_directory, receipt_digest, self.commit,
                    finalized, "2" * 64, accepted, output,
                )
            admit_package.assert_called_once()
            self.assertEqual(
                json.loads((output / "local-qualification.json").read_text()),
                json.loads((qualification_directory / "qualification.json").read_text()),
            )
            cli_report = json.loads((output / "comparison.json").read_text())
            vscode_report = json.loads((output / "vscode/comparison.json").read_text())
            self.assertEqual(cli_report["localQualification"]["receiptSHA256"], receipt_digest)
            self.assertTrue(vscode_report["localQualification"]["executedLocally"])
            self.assertTrue((output / "matrix.md").is_file())
            self.assertTrue((output / "vscode/matrix.md").is_file())


if __name__ == "__main__":
    unittest.main()
