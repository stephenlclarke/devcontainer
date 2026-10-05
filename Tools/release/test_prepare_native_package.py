"""Portable admission tests for the unsigned native staging adapter."""

from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import shutil
from unittest.mock import patch


REPOSITORY = Path(os.environ.get("DEVCONTAINER_SOURCE", Path(__file__).resolve().parents[2])).resolve()
sys.path.insert(0, str(REPOSITORY / "Tools/bazel"))
sys.path.insert(0, str(REPOSITORY / "Tools/release"))
import test_prepare_candidate as candidate_test  # noqa: E402
import layered_build  # noqa: E402
import prepare_candidate  # noqa: E402
import prepare_releases  # noqa: E402


SCRIPT = Path(__file__).with_name("prepare-native-package.py")
SPEC = importlib.util.spec_from_file_location("prepare_native_package", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class NativeStageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = candidate_test.CandidateAdmissionTests(
            "test_private_runtime_admission_keeps_legacy_identity_and_survives_scratch_removal")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.upgrade_runtime()
        (self.fixture.scratch / "native-packages").mkdir()
        self.root = self.fixture.retained.parent
        self.repository = self.root / "repository"
        (self.repository / "Tools/release").mkdir(parents=True)
        (self.repository / "Tools/bazel/artifacts").mkdir(parents=True)
        for group in MODULE.LOWER_GROUPS - {"argument-parser"}:
            (self.repository / "Tools/bazel/artifacts" / f"{group}-stock.lock.json").write_bytes(b"lower-lock")
        (self.repository / "Tools/bazel/artifacts/argument-parser.lock.json").write_bytes(b"argument-lock")
        (self.repository / "Tools/release/dependency-licenses.json").write_text('{"schemaVersion":1,"licenses":{}}')
        self.lock = self.fixture.lock
        (self.repository / "Package.stock.resolved").write_bytes(self.lock)
        self.source = json.loads(self.fixture.contents["inputs-before.json"])
        self.events = self._events()
        self.fixture.contents["events.json"] = self.events
        self.fixture.save()
        self.proof = self.root / "devcontainer/compiled-consumer"
        self.proof.mkdir(parents=True)
        self.proof_path = self.proof / "stock-compiled-consumer.json"
        self._write_proof()
        self.destination = self.fixture.scratch / "native-packages/native-stage"
        self.bundle = self.root / "legal-bundle"
        self.bundle_sha = "a" * 64

    def _events(self):
        files = [{"name": name, "digest": MODULE.digest(data)} for key, data in self.fixture.contents.items()
                 if key.startswith("artifact:") for name in [key.removeprefix("artifact:")]]
        records = [
            {"started": {"command": "test"}},
            {"id": {"namedSet": {"id": "0"}}, "namedSetOfFiles": {"files": files}},
            {"id": {"targetCompleted": {"label": "//:candidate_archive"}},
             "completed": {"success": True, "outputGroup": [{"name": "default", "fileSets": [{"id": "0"}]}]}},
            {"id": {"buildFinished": {}}, "finished": {"overallSuccess": True}},
        ]
        return b"\n".join(json.dumps(item).encode() for item in records) + b"\n"

    def _write_proof(self):
        build = self.fixture.scratch / "invocations/run.build"
        query = self.fixture.scratch / "invocations/run.query"
        files = [{"name": name, "digest": sha} for name, sha in sorted(self.fixture.receipt["products"].items())]
        records = [{"started": {"command": "build", "uuid": "build-fixture"}},
                   {"id": {"namedSet": {"id": "0"}}, "namedSetOfFiles": {"files": files}},
                   {"id": {"targetCompleted": {"label": "//:products"}},
                    "completed": {"success": True, "outputGroup": [{"name": "default", "fileSets": [{"id": "0"}]}]}},
                   {"id": {"buildFinished": {}}, "finished": {"overallSuccess": True}}]
        build_events = b"\n".join(json.dumps(item).encode() for item in records) + b"\n"
        query_events = b"\n".join(json.dumps(item).encode() for item in
                                  [{"started": {"command": "aquery", "uuid": "query-fixture"}},
                                   {"id": {"buildFinished": {}}, "finished": {"overallSuccess": True}}]) + b"\n"
        graph_bytes = b"exact configured action graph"
        for identifier, path, events, extra in (("build-fixture", build, build_events, {}),
                                                ("query-fixture", query, query_events,
                                                 {"aquery.stdout.log": graph_bytes})):
            owner = {"schemaVersion": 1, "kind": "bazel-invocation", "directory": path.name,
                     "workspaceKey": MODULE.digest(str(self.repository).encode())}
            contents = {"owner.json": json.dumps(owner).encode(), "events.json": events,
                        "inputs-before.json": self.fixture.contents["inputs-before.json"],
                        "inputs-after.json": self.fixture.contents["inputs-before.json"],
                        "outcome.json": b'{"bazel_exit_code":0,"validation_exit_code":0}', **extra}
            with sqlite3.connect(self.fixture.database) as db:
                for value in contents.values():
                    db.execute("INSERT OR REPLACE INTO blobs VALUES (?,?)", (MODULE.digest(value), value))
                db.execute("INSERT OR REPLACE INTO invocations VALUES (?,?,?)",
                           (identifier, json.dumps({name: MODULE.digest(value) for name, value in contents.items()}), 0))
        graph = {"products": ["//:" + name for name in sorted(MODULE.PRODUCTS)],
                 "linkedArchives": {"archive-" + name: {"group": name, "sha256": "a" * 64}
                                    for name in sorted(MODULE.LOWER_GROUPS)}}
        data = {"schema": 1, "profile": "stock", "developmentProof": False, "releaseQualified": False,
                "source": {"source": self.source["commit"], "dirty": False,
                           "inputsSHA256": MODULE.digest(self.fixture.contents["inputs-before.json"]),
                           "buildEventsSHA256": MODULE.digest(build_events),
                           "queryEventsSHA256": MODULE.digest(query_events)},
                "graph": graph,
                "locks": {name: MODULE.digest(b"lower-lock") for name in
                          MODULE.LOWER_GROUPS - {"argument-parser"}},
                "archives": {name: "a" * 64 for name in MODULE.LOWER_GROUPS - {"argument-parser"}},
                "argumentParserLockSHA256": MODULE.digest(b"argument-lock"),
                "queryStdoutSHA256": MODULE.digest(graph_bytes),
                "actionGraphSHA256": MODULE.digest(graph_bytes)}
        self.proof_path.write_text(json.dumps(data))
        source_file = self.proof / "source.json"
        source_file.write_text(json.dumps(self.source))
        controller = {"schema": 1, "scope": "released-layer-consumption", "status": "passed",
                      "developmentProof": False, "sourceCommit": self.source["commit"],
                      "sourceSHA256": MODULE.digest(source_file),
                      "profiles": {"stock": {"status": "passed", "proof": str(self.proof_path),
                                             "proofSHA256": MODULE.digest(self.proof_path),
                                             "commands": [{"operation": "build", "status": "passed",
                                                           "invocation": str(build)},
                                                          {"operation": "aquery", "status": "passed",
                                                           "invocation": str(query)}]}}}
        (self.proof / "consumers.json").write_text(json.dumps(controller))

    def test_schema2_four_products_and_private_runtime_are_staged_without_compiler(self):
        scripts = []
        metadata_args = {}

        def metadata(_root, script, arguments):
            scripts.append(script)
            metadata_args[script] = arguments
            Path(arguments[arguments.index("--output") + 1]).write_text("{}")

        def context_run(command, **kwargs):
            self.assertEqual(command[0], sys.executable)
            self.assertEqual(Path(command[1]).name, "package-context.py")
            kwargs["stdout"].write(json.dumps({"commit": self.source["commit"],
                                                "productVersion": "1.2.3"}).encode())

        with patch.object(MODULE, "modules", return_value=(layered_build, prepare_candidate, prepare_releases)), \
                patch.object(layered_build, "snapshot", return_value=self.source), \
                patch.object(MODULE, "admit_legal_bundle", return_value=(
                    {"bundleSHA256": self.bundle_sha},
                    {"THIRD-PARTY-NOTICES.txt": b"authenticated notices",
                     "dependency-licenses.json": (json.dumps({"schemaVersion": 1, "licenses": {}},
                                                                indent=2, sort_keys=True) + "\n").encode()})), \
                patch.object(MODULE, "run_metadata", side_effect=metadata), \
                patch.object(MODULE.subprocess, "run", side_effect=context_run), \
                patch.object(MODULE.subprocess, "check_output", return_value=b"1700000000\n"):
            result = MODULE.stage(self.repository, self.fixture.retained, self.fixture.scratch,
                                  self.fixture.invocation, "stock", self.proof_path, self.destination,
                                  "development", legal_bundle=self.bundle, legal_sha256=self.bundle_sha,
                                  ssd_root=self.root)
        self.assertEqual(set(result["candidateProducts"]), MODULE.PRODUCTS)
        self.assertEqual(result["candidateTerminalLaunchers"], self.fixture.receipt["terminalLaunchers"])
        self.assertEqual(result["goSDKLicenseSHA256"], self.fixture.receipt["goSDKLicenseSHA256"])
        self.assertEqual(set(result["privateRuntime"]["files"]), set(self.fixture.receipt["referenceRuntime"]["files"]))
        self.assertEqual(result["selectedLockSHA256"], MODULE.digest(self.lock))
        self.assertEqual(set(scripts), {"write-build-info.py", "render-package-readme.py", "write-sbom.py"})
        self.assertEqual(result["sourceDateEpoch"], 1700000000)
        sbom_args = metadata_args["write-sbom.py"]
        self.assertEqual(sbom_args[sbom_args.index("--source-date-epoch") + 1], "1700000000")
        self.assertEqual(Path(sbom_args[sbom_args.index("--reference-runtime-root") + 1]).relative_to(
            Path(sbom_args[sbom_args.index("--output") + 1]).parents[2]),
            Path("libexec/devcontainer/reference"))
        self.assertFalse(result["distributionReady"])
        self.assertTrue(result["legalCompleteness"])
        self.assertEqual(result["legalBundle"]["bundleSHA256"], self.bundle_sha)
        self.assertEqual(set(result["privateRuntimeLegalFiles"]),
                         {"NODE-LICENSE.txt", "cli/LICENSE.txt", "cli/ThirdPartyNotices.txt"})
        self.assertEqual((self.destination / "devcontainer-1.2.3/share/devcontainer/THIRD-PARTY-NOTICES.txt").read_bytes(),
                         b"authenticated notices")
        self.assertTrue((self.destination / "devcontainer-1.2.3/bin/devcontainer-docker").is_file())
        self.assertTrue((self.destination / "devcontainer-1.2.3/libexec/devcontainer/reference/node").is_file())
        self.assertTrue((self.destination / "devcontainer-1.2.3/libexec/devcontainer/terminal-launcher/devcontainer-terminal-linux-arm64").is_file())
        self.assertTrue((self.destination / "devcontainer-1.2.3/libexec/devcontainer/terminal-launcher/devcontainer-terminal-linux-amd64").is_file())
        self.assertTrue((self.destination / "devcontainer-1.2.3/libexec/devcontainer/terminal-launcher/GO-LICENSE.txt").is_file())
        tree = self.destination / "devcontainer-1.2.3"
        expected_payload = {name: {field: entry[field] for field in ("sha256", "size", "mode")}
                            for name, entry in prepare_releases.inventory(tree).items()
                            if entry["kind"] == "file"}
        self.assertEqual(result["unsignedPayloadInventory"], expected_payload)
        self.assertLessEqual({"share/devcontainer/THIRD-PARTY-NOTICES.txt",
                              "share/devcontainer/dependency-licenses.selected.json",
                              "share/devcontainer/build-info.json",
                              "share/devcontainer/devcontainer.spdx.json",
                              "bin/devcontainer", "libexec/devcontainer/reference/NODE-LICENSE.txt"},
                             set(expected_payload))
        self.assertTrue(all(set(entry) == {"sha256", "size", "mode"} for entry in expected_payload.values()))
        self.assertFalse(any(name.startswith("devcontainer-1.2.3/") for name in expected_payload))
        notices = tree / "share/devcontainer/THIRD-PARTY-NOTICES.txt"
        notices.write_bytes(b"changed after stage")
        self.assertNotEqual(MODULE.payload_inventory(prepare_releases, tree), result["unsignedPayloadInventory"])
        self.assertFalse((self.fixture.scratch / "invocations/run.build").exists())
        self.assertFalse((self.fixture.scratch / "invocations/run.query").exists())

    def test_rejects_failed_dirty_or_corrupt_retained_candidate(self):
        self.fixture.save(exit_code=1)
        with self.assertRaisesRegex(ValueError, "successful"):
            MODULE.retained_bytes(self.fixture.database, self.fixture.invocation)
        self.fixture.save()
        dirty = dict(self.source, dirty=True)
        with patch.object(MODULE, "modules", return_value=(layered_build, prepare_candidate, prepare_releases)), \
                patch.object(layered_build, "snapshot", return_value=dirty):
            with self.assertRaisesRegex(ValueError, "clean source"):
                MODULE.stage(self.repository, self.fixture.retained, self.fixture.scratch,
                             self.fixture.invocation, "stock", self.proof_path, self.destination,
                             "development", legal_bundle=self.bundle, legal_sha256=self.bundle_sha,
                             ssd_root=self.root)
        with MODULE.closing(MODULE.sqlite3.connect(self.fixture.database)) as db:
            db.execute("UPDATE blobs SET bytes=x'00' WHERE sha256=?",
                       (MODULE.digest(self.fixture.contents["events.json"]),))
            db.commit()
        with self.assertRaisesRegex(ValueError, "corrupt"):
            MODULE.retained_bytes(self.fixture.database, self.fixture.invocation)

    def test_proof_must_match_profile_source_and_product_closure(self):
        inputs_sha = MODULE.digest(self.fixture.contents["inputs-before.json"])
        MODULE.admit_proof(self.proof_path, "stock", self.source["commit"], inputs_sha,
                           self.fixture.receipt["products"], self.fixture.retained,
                           self.fixture.scratch, self.repository, self.source)
        for profile, commit, sha in (("enhanced", self.source["commit"], inputs_sha),
                                     ("stock", "b" * 40, inputs_sha), ("stock", self.source["commit"], "0" * 64)):
            with self.subTest(profile=profile, commit=commit, sha=sha), self.assertRaises(ValueError):
                MODULE.admit_proof(self.proof_path, profile, commit, sha,
                                   self.fixture.receipt["products"], self.fixture.retained,
                                   self.fixture.scratch, self.repository, self.source)
        proof = json.loads(self.proof_path.read_text())
        proof["graph"]["products"].pop()
        self.proof_path.write_text(json.dumps(proof))
        with self.assertRaises(ValueError):
            MODULE.admit_proof(self.proof_path, "stock", self.source["commit"], inputs_sha,
                               self.fixture.receipt["products"], self.fixture.retained,
                               self.fixture.scratch, self.repository, self.source)

    def test_bep_output_hashes_and_fresh_destination_fail_closed(self):
        contents = {"events.json": self.events,
                    "artifact:candidate_archive.json": self.fixture.contents["artifact:candidate_archive.json"],
                    "artifact:candidate_archive.tar.gz": self.fixture.contents["artifact:candidate_archive.tar.gz"]}
        MODULE.admit_archive_events(contents)
        contents["artifact:candidate_archive.tar.gz"] = b"wrong"
        with self.assertRaisesRegex(ValueError, "BEP output hashes"):
            MODULE.admit_archive_events(contents)
        self.destination.mkdir()
        with self.assertRaisesRegex(ValueError, "fresh path"):
            MODULE.stage(self.repository, self.fixture.retained, self.fixture.scratch,
                         self.fixture.invocation, "stock", self.proof_path, self.destination,
                         "development", legal_bundle=self.bundle, legal_sha256=self.bundle_sha,
                         ssd_root=self.root)

    def test_profile_and_pending_preparation_are_rejected_before_metadata(self):
        with patch.object(MODULE, "modules", return_value=(layered_build, prepare_candidate, prepare_releases)), \
                patch.object(layered_build, "snapshot", return_value=self.source), \
                patch.object(MODULE, "run_metadata", side_effect=AssertionError("metadata must not run")):
            with self.assertRaisesRegex(ValueError, "source or profile differs"):
                MODULE.stage(self.repository, self.fixture.retained, self.fixture.scratch,
                             self.fixture.invocation, "enhanced", self.proof_path, self.destination,
                             "development", legal_bundle=self.bundle, legal_sha256=self.bundle_sha,
                             ssd_root=self.root)
            prepared = self.fixture.prepare()
            pending = self.fixture.retained / "prepared-candidates" / (prepared["preparationSHA256"] + ".pending.json")
            pending.write_text("pending")
            with self.assertRaisesRegex(ValueError, "Pending release ownership"):
                MODULE.stage(self.repository, self.fixture.retained, self.fixture.scratch,
                             self.fixture.invocation, "stock", self.proof_path, self.destination,
                             "development", legal_bundle=self.bundle, legal_sha256=self.bundle_sha,
                             ssd_root=self.root)

    def test_aliased_proof_is_not_accepted(self):
        alias = self.root / "proof-alias.json"
        alias.symlink_to(self.proof_path)
        inputs_sha = MODULE.digest(self.fixture.contents["inputs-before.json"])
        with self.assertRaisesRegex(ValueError, "aliased"):
            MODULE.admit_proof(alias, "stock", self.source["commit"],
                               inputs_sha,
                               self.fixture.receipt["products"], self.fixture.retained,
                               self.fixture.scratch, self.repository, self.source)

    def test_missing_or_corrupt_retained_compiled_bytes_fail_after_ssd_cleanup(self):
        inputs_sha = MODULE.digest(self.fixture.contents["inputs-before.json"])
        proof = json.loads(self.proof_path.read_text())
        events_sha = proof["source"]["buildEventsSHA256"]
        with sqlite3.connect(self.fixture.database) as db:
            db.execute("UPDATE blobs SET bytes=x'00' WHERE sha256=?", (events_sha,))
        with self.assertRaisesRegex(ValueError, "missing or corrupt"):
            MODULE.admit_proof(self.proof_path, "stock", self.source["commit"], inputs_sha,
                               self.fixture.receipt["products"], self.fixture.retained,
                               self.fixture.scratch, self.repository, self.source)

    def test_staging_cannot_write_into_foreign_scratch_namespace(self):
        foreign = self.fixture.scratch / "restored/foreign"
        foreign.parent.mkdir()
        with self.assertRaisesRegex(ValueError, "fresh path on enrolled SSD"):
            MODULE.stage(self.repository, self.fixture.retained, self.fixture.scratch,
                         self.fixture.invocation, "stock", self.proof_path, foreign,
                         "development", legal_bundle=self.bundle, legal_sha256=self.bundle_sha,
                         ssd_root=self.root)

    def test_selected_stock_ledger_is_exact_subset_and_missing_declarations_fail(self):
        lock = self.root / "stock.json"
        lock.write_text(json.dumps({"pins": [{"identity": "one", "location": "https://example.com/one.git",
                                              "state": {"revision": "a" * 40, "version": "1.0.0"}}]}))
        ledger = self.repository / "Tools/release/dependency-licenses.json"
        ledger.write_text(json.dumps({"schemaVersion": 1, "licenses": {"one": "MIT", "enhanced-only": "Apache-2.0"}}))
        output = self.root / "selected.json"
        selected = MODULE.selected_licenses(self.repository, lock, output)
        self.assertEqual(selected["licenses"], {"one": "MIT"})
        ledger.write_text(json.dumps({"schemaVersion": 1, "licenses": {"enhanced-only": "Apache-2.0"}}))
        with self.assertRaisesRegex(ValueError, "reviewed license"):
            MODULE.selected_licenses(self.repository, lock, output)

    def _legal_fixture(self):
        tools = self.repository / "Tools/release"
        for name in ("dependency_metadata.py", "write-third-party-notices.py"):
            shutil.copyfile(REPOSITORY / "Tools/release" / name, tools / name)
        shutil.copyfile(REPOSITORY / "Tools/release/prepare-native-legal.py",
                        tools / "prepare-native-legal.py")
        identity = "one"
        lock = self.root / "selected-lock.json"
        lock.write_text(json.dumps({"pins": [{"identity": identity,
                                             "location": "https://github.com/example/one.git",
                                             "state": {"revision": "a" * 40, "version": "1.0.0"}}]}))
        reviewed = tools / "dependency-licenses.json"
        reviewed.write_text(json.dumps({"schemaVersion": 1, "licenses": {identity: "MIT", "enhanced-only": "Apache-2.0"}}))
        self.bundle.mkdir()
        selected_path = self.bundle / "dependency-licenses.json"
        MODULE.selected_licenses(self.repository, lock, selected_path)
        license_text = "Permission is hereby granted, free of charge, to use this software.\n"
        blob = license_text.encode()
        row = {"identity": identity, "revision": "a" * 40,
               "location": "https://github.com/example/one.git", "version": "1.0.0",
               "license": "MIT", "rootTree": "b" * 40,
               "files": [{"name": "LICENSE", "text": license_text,
                          "sha256": MODULE.digest(blob),
                          "gitBlob": hashlib.sha1(b"blob " + str(len(blob)).encode() + b"\0" + blob).hexdigest()}]}
        notices = MODULE.legal_notices([row])
        (self.bundle / "THIRD-PARTY-NOTICES.txt").write_bytes(notices)
        manifest = {"schemaVersion": 1, "kind": "source-pinned-legal-bundle",
                    "resolvedSHA256": MODULE.digest(lock),
                    "reviewedLedgerSHA256": MODULE.digest(reviewed),
                    "selectedLedgerSHA256": MODULE.digest(selected_path),
                    "noticesSHA256": MODULE.digest(notices),
                    "policySHA256": {name: MODULE.digest(tools / name) for name in
                                     ("dependency_metadata.py", "write-third-party-notices.py")},
                    "collectorSHA256": MODULE.digest(tools / "prepare-native-legal.py"),
                    "dependencies": [row]}
        manifest_path = self.bundle / "legal.json"
        manifest_path.write_text(json.dumps(manifest))
        return lock, selected_path, manifest, MODULE.digest(manifest_path)

    def test_source_pinned_legal_bundle_replays_exact_selected_closure(self):
        lock, selected, manifest, sha = self._legal_fixture()
        admitted, contents = MODULE.admit_legal_bundle(
            self.repository, self.fixture.retained, self.bundle, sha, lock, selected)
        self.assertEqual(admitted["dependencies"], ["one"])
        self.assertEqual(admitted["collectorSHA256"], manifest["collectorSHA256"])
        self.assertEqual(contents["THIRD-PARTY-NOTICES.txt"], MODULE.legal_notices(manifest["dependencies"]))
        with self.assertRaisesRegex(ValueError, "trusted caller SHA"):
            MODULE.admit_legal_bundle(self.repository, self.fixture.retained, self.bundle,
                                      "0" * 64, lock, selected)

    def test_legal_bundle_rejects_wrong_pin_policy_blob_or_notice(self):
        lock, selected, manifest, _ = self._legal_fixture()
        original = json.dumps(manifest)
        path = self.bundle / "legal.json"
        def check_failure(pattern):
            path.write_text(json.dumps(manifest))
            manifest_sha = MODULE.digest(path)
            with self.assertRaisesRegex(ValueError, pattern):
                MODULE.admit_legal_bundle(self.repository, self.fixture.retained, self.bundle,
                                          manifest_sha, lock, selected)
        manifest["dependencies"][0]["revision"] = "c" * 40
        check_failure("identity")
        manifest = json.loads(original)
        manifest["dependencies"][0]["identity"] = "other"
        check_failure("identity")
        manifest = json.loads(original)
        manifest["dependencies"][0]["files"][0]["text"] += "tampered"
        check_failure("Git blob")
        manifest = json.loads(original)
        manifest["policySHA256"]["dependency_metadata.py"] = "0" * 64
        check_failure("policy")
        manifest = json.loads(original)
        path.write_text(json.dumps(manifest))
        (self.bundle / "THIRD-PARTY-NOTICES.txt").write_bytes(b"notices replaced")
        manifest_sha = MODULE.digest(path)
        with self.assertRaisesRegex(ValueError, "policy differs"):
            MODULE.admit_legal_bundle(self.repository, self.fixture.retained, self.bundle,
                                      manifest_sha, lock, selected)
        (self.bundle / "THIRD-PARTY-NOTICES.txt").write_bytes(MODULE.legal_notices(manifest["dependencies"]))
        (self.bundle / "dependency-licenses.json").write_text('{"schemaVersion":1,"licenses":{"other":"MIT"}}')
        manifest_sha = MODULE.digest(path)
        with self.assertRaisesRegex(ValueError, "policy differs"):
            MODULE.admit_legal_bundle(self.repository, self.fixture.retained, self.bundle,
                                      manifest_sha, lock, selected)

    def test_legal_bundle_rejects_alias_and_collector_drift(self):
        lock, selected, manifest, sha = self._legal_fixture()
        alias = self.root / "legal-alias"
        alias.symlink_to(self.bundle)
        with self.assertRaisesRegex(ValueError, "aliased"):
            MODULE.admit_legal_bundle(self.repository, self.fixture.retained, alias, sha, lock, selected)
        manifest["collectorSHA256"] = "0" * 64
        path = self.bundle / "legal.json"
        path.write_text(json.dumps(manifest))
        manifest_sha = MODULE.digest(path)
        with self.assertRaisesRegex(ValueError, "policy differs"):
            MODULE.admit_legal_bundle(self.repository, self.fixture.retained, self.bundle,
                                      manifest_sha, lock, selected)

    def test_source_date_epoch_is_nonnegative_and_commit_bound(self):
        with patch.object(MODULE.subprocess, "check_output", return_value=b"1700000000\n") as git:
            self.assertEqual(MODULE.source_date_epoch(self.repository, self.source["commit"]), 1700000000)
            git.assert_called_once_with(["/usr/bin/git", "-C", str(self.repository),
                                         "show", "-s", "--format=%ct", self.source["commit"]])
        with patch.object(MODULE.subprocess, "check_output", return_value=b"-1\n"):
            with self.assertRaisesRegex(ValueError, "invalid date epoch"):
                MODULE.source_date_epoch(self.repository, self.source["commit"])


if __name__ == "__main__":
    unittest.main()
