"""Candidate admission preserves source provenance and never becomes release proof."""

import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from prepare_candidate import PRODUCTS, SCOPE, admit_candidate, main, prepare_candidate, retained_candidate
from release_inputs import canonical
from retain_evidence import digest


class CandidateAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name).resolve()
        self.retained, self.scratch = root / "retained", root / "scratch"
        self.retained.mkdir(mode=0o700)
        self.scratch.mkdir()
        (self.scratch / "tmp").mkdir()
        self.database = self.retained / "bazel-evidence.sqlite"
        self.invocation = "candidate-fixture"
        self.lock = b'{"pins":[]}'
        self.receipt = {"schemaVersion": 1, "kind": "unsigned-native-candidate", "version": "1.2.3",
                        "commit": "a" * 40, "runtimeProfile": "stock", "architecture": "arm64",
                        "compilationMode": "opt", "distributionReady": False,
                        "dependencyLockSHA256": digest(self.lock),
                        "products": {name: digest(name.encode()) for name in PRODUCTS}}
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as tar:
            files = {"bin/" + name: (name.encode(), 0o755) for name in PRODUCTS}
            files.update({"share/devcontainer/candidate.json": (canonical(self.receipt).encode(), 0o644),
                          "share/devcontainer/Package.resolved": (self.lock, 0o644)})
            for path, (data, mode) in files.items():
                entry = tarfile.TarInfo("devcontainer-1.2.3/" + path)
                entry.size, entry.mode = len(data), mode
                tar.addfile(entry, io.BytesIO(data))
        payload = archive.getvalue()
        self.receipt.update(archiveSHA256=digest(payload), archiveSize=len(payload))
        inputs = {"schema": 1, "commit": "a" * 40, "dirty": False,
                  "files": {"Package.stock.resolved": {"sha256": digest(self.lock)}}}
        self.contents = {"inputs-before.json": canonical(inputs).encode(), "inputs-after.json": canonical(inputs).encode(),
                         "outcome.json": b'{"bazel_exit_code":0,"validation_exit_code":0}',
                         "artifact:candidate_archive.json": canonical(self.receipt).encode(),
                         "artifact:candidate_archive.tar.gz": payload}
        self.save()

    def save(self, exit_code=0):
        with sqlite3.connect(self.database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS invocations (id TEXT PRIMARY KEY, manifest TEXT, exit_code INTEGER)")
            db.execute("CREATE TABLE IF NOT EXISTS blobs (sha256 TEXT PRIMARY KEY, bytes BLOB)")
            for data in self.contents.values():
                db.execute("INSERT OR REPLACE INTO blobs VALUES (?,?)", (digest(data), data))
            db.execute("INSERT OR REPLACE INTO invocations VALUES (?,?,?)",
                       (self.invocation, canonical({name: digest(data) for name, data in self.contents.items()}), exit_code))

    def prepare(self):
        return prepare_candidate(self.retained, self.scratch, self.invocation)

    def upgrade_runtime(self):
        """Version-two fixture is independent of the preserved legacy receipt."""
        self.invocation = "candidate-v2-fixture"
        runtime = {"node": b"node-fixture", "NODE-LICENSE.txt": b"license", "runtime-lock.json": b"locked-runtime"}
        runtime.update({"cli/" + name: name.encode() for name in (
            "devcontainer.js", "dist/spec-node/devContainersSpecCLI.js", "scripts/updateUID.Dockerfile",
            "package.json", "LICENSE.txt", "ThirdPartyNotices.txt")})
        self.receipt.update(schemaVersion=2, referenceRuntime={
            "nodeVersion": "24.21.0", "cliVersion": "0.88.0", "lockSHA256": digest(runtime["runtime-lock.json"]),
            "files": {name: digest(data) for name, data in runtime.items()}})
        self.receipt["products"]["devcontainer-docker"] = digest(b"frontend")
        embedded = {key: value for key, value in self.receipt.items() if key not in {"archiveSHA256", "archiveSize"}}
        output = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(self.contents["artifact:candidate_archive.tar.gz"]), mode="r:gz") as original, \
                tarfile.open(fileobj=output, mode="w:gz") as archive:
            files = {entry.name.removeprefix("devcontainer-1.2.3/"): (original.extractfile(entry).read(), entry.mode)
                     for entry in original}
            files["share/devcontainer/candidate.json"] = (canonical(embedded).encode(), 0o644)
            files["bin/devcontainer-docker"] = (b"frontend", 0o755)
            files.update({"libexec/devcontainer/reference/" + name: (data, 0o755 if name == "node" else 0o644)
                          for name, data in runtime.items()})
            for name, (data, mode) in files.items():
                entry = tarfile.TarInfo("devcontainer-1.2.3/" + name)
                entry.size, entry.mode = len(data), mode
                archive.addfile(entry, io.BytesIO(data))
        payload = output.getvalue()
        self.receipt.update(archiveSHA256=digest(payload), archiveSize=len(payload))
        self.contents["artifact:candidate_archive.tar.gz"] = payload
        self.contents["artifact:candidate_archive.json"] = canonical(self.receipt).encode()
        inputs = json.loads(self.contents["inputs-before.json"])
        inputs["files"]["Tools/bazel/devcontainers-cli.lock.json"] = {"sha256": digest(runtime["runtime-lock.json"])}
        self.contents["inputs-before.json"] = self.contents["inputs-after.json"] = canonical(inputs).encode()
        self.save()

    def test_private_runtime_admission_keeps_legacy_identity_and_survives_scratch_removal(self):
        legacy = self.prepare()
        self.upgrade_runtime()
        current = self.prepare()
        self.assertNotEqual(legacy["preparationSHA256"], current["preparationSHA256"])
        self.assertEqual(set(current["executables"]), PRODUCTS | {"devcontainer-docker", "reference-node"})
        shutil.rmtree(self.scratch)
        self.assertEqual(current, admit_candidate(self.retained, self.invocation, "stock"))
        self.assertTrue(Path(legacy["root"]).is_dir())

    def test_private_runtime_lock_is_bound_to_source(self):
        self.upgrade_runtime()
        self.receipt["referenceRuntime"]["lockSHA256"] = "0" * 64
        self.contents["artifact:candidate_archive.json"] = canonical(self.receipt).encode()
        self.save()
        with self.assertRaisesRegex(ValueError, "private runtime differs"):
            self.prepare()

    def test_private_runtime_tampering_is_not_repaired(self):
        self.upgrade_runtime()
        current = self.prepare()
        node = Path(current["executables"]["reference-node"])
        node.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "bytes or modes"):
            admit_candidate(self.retained, self.invocation, "stock")

    def test_private_runtime_payload_must_match_declared_digests(self):
        self.upgrade_runtime()
        original = dict(self.contents)
        for member in ("node", "cli/devcontainer.js", "runtime-lock.json", "NODE-LICENSE.txt"):
            with self.subTest(member=member):
                self.invocation = "altered-runtime-" + member.replace("/", "-")
                output = io.BytesIO()
                with tarfile.open(fileobj=io.BytesIO(original["artifact:candidate_archive.tar.gz"]), mode="r:gz") as source, \
                        tarfile.open(fileobj=output, mode="w:gz") as target:
                    for entry in source:
                        data = source.extractfile(entry).read()
                        if entry.name == "devcontainer-1.2.3/libexec/devcontainer/reference/" + member:
                            data = b"changed-runtime-payload"
                        entry.size = len(data)
                        target.addfile(entry, io.BytesIO(data))
                payload = output.getvalue()
                self.contents["artifact:candidate_archive.tar.gz"] = payload
                receipt = dict(self.receipt, archiveSHA256=digest(payload), archiveSize=len(payload))
                self.contents["artifact:candidate_archive.json"] = canonical(receipt).encode()
                self.save()
                with self.assertRaisesRegex(ValueError, "private runtime digests"):
                    self.prepare()

    def test_preparation_reuses_assets_and_admission_survives_all_scratch_removal(self):
        first = self.prepare()
        self.assertEqual(first, self.prepare())
        self.assertEqual(first["scope"], SCOPE)
        self.assertEqual(first["sourceCommit"], "a" * 40)
        self.assertEqual(set(first["executables"]), PRODUCTS)
        shutil.rmtree(self.scratch)
        with patch("prepare_candidate.restore_candidate", side_effect=AssertionError("must not restore")):
            self.assertEqual(first, admit_candidate(self.retained, self.invocation, "stock"))
        with self.assertRaisesRegex(ValueError, "profile"):
            admit_candidate(self.retained, self.invocation, "enhanced")

    def test_missing_failed_or_corrupt_evidence_is_rejected_before_extraction(self):
        with self.assertRaisesRegex(ValueError, "successful"):
            retained_candidate(self.database, "missing")
        self.save(exit_code=1)
        with self.assertRaisesRegex(ValueError, "successful"):
            self.prepare()
        self.save()
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE blobs SET bytes=x'00'")
        with self.assertRaisesRegex(ValueError, "corrupt"):
            self.prepare()
        self.assertFalse((self.scratch / "restored").exists())

    def test_dirty_changed_or_unspecified_source_is_rejected(self):
        original = dict(self.contents)
        for change, both in [({"dirty": True}, True), ({"commit": "unspecified"}, True), ({"commit": "b" * 40}, False)]:
            with self.subTest(change=change, both=both):
                self.contents = dict(original)
                inputs = json.loads(original["inputs-before.json"])
                inputs.update(change)
                self.contents["inputs-after.json"] = canonical(inputs).encode()
                if both:
                    self.contents["inputs-before.json"] = canonical(inputs).encode()
                self.save()
                with self.assertRaisesRegex(ValueError, "clean unchanged"):
                    self.prepare()

    def test_receipt_and_dependency_identity_are_bound_to_source_and_bytes(self):
        for field, value in [("commit", "b" * 40), ("distributionReady", True), ("runtimeProfile", "unknown"),
                             ("version", "../../escape"), ("archiveSHA256", "0" * 64), ("archiveSize", 0),
                             ("products", {}), ("dependencyLockSHA256", "0" * 64)]:
            with self.subTest(field=field):
                self.contents["artifact:candidate_archive.json"] = canonical(dict(self.receipt, **{field: value})).encode()
                self.save()
                with self.assertRaises(ValueError):
                    self.prepare()

    def test_failed_validation_or_absent_source_evidence_cannot_be_admitted(self):
        self.contents["outcome.json"] = b'{"bazel_exit_code":0,"validation_exit_code":1}'
        self.save()
        with self.assertRaisesRegex(ValueError, "validation"):
            self.prepare()
        del self.contents["inputs-before.json"]
        self.save()
        with self.assertRaisesRegex(ValueError, "Missing or corrupt"):
            self.prepare()

    def test_embedded_metadata_and_products_must_match_external_receipt(self):
        self.receipt["products"]["devcontainer"] = "0" * 64
        self.contents["artifact:candidate_archive.json"] = canonical(self.receipt).encode()
        self.save()
        with self.assertRaisesRegex(ValueError, "Embedded candidate identity"):
            self.prepare()

    def test_product_and_lock_payloads_are_verified_beyond_archive_integrity(self):
        original = dict(self.contents)
        for member, message in [("bin/devcontainer", "product digests"),
                                ("share/devcontainer/Package.resolved", "dependency lock")]:
            with self.subTest(member=member):
                self.invocation = "payload-" + member
                rewritten = io.BytesIO()
                with tarfile.open(fileobj=io.BytesIO(original["artifact:candidate_archive.tar.gz"]), mode="r:gz") as source, \
                        tarfile.open(fileobj=rewritten, mode="w:gz") as target:
                    for entry in source:
                        payload = b"wrong" if entry.name == "devcontainer-1.2.3/" + member else source.extractfile(entry).read()
                        entry.size = len(payload)
                        target.addfile(entry, io.BytesIO(payload))
                self.contents["artifact:candidate_archive.tar.gz"] = rewritten.getvalue()
                receipt = dict(self.receipt, archiveSHA256=digest(rewritten.getvalue()), archiveSize=len(rewritten.getvalue()))
                self.contents["artifact:candidate_archive.json"] = canonical(receipt).encode()
                self.save()
                with self.assertRaisesRegex(ValueError, message):
                    self.prepare()

    def test_command_checks_internal_storage_before_preparation(self):
        with patch("sys.argv", ["prepare-candidate", self.invocation]), patch("prepare_candidate.Path.stat") as info, \
                patch("prepare_candidate.prepare_candidate", return_value={"scope": SCOPE}) as prepare, \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            info.side_effect = [SimpleNamespace(st_dev=2), SimpleNamespace(st_dev=1)]
            with self.assertRaisesRegex(ValueError, "internal retained"):
                main()
            prepare.assert_not_called()
            info.side_effect = [SimpleNamespace(st_dev=1), SimpleNamespace(st_dev=1)]
            main()
            self.assertEqual(json.loads(output.getvalue()), {"scope": SCOPE})
            self.assertEqual(prepare.call_args.args[2], self.invocation)

    def test_pending_publication_and_changed_files_are_not_repaired(self):
        prepared = self.prepare()
        root = Path(prepared["root"])
        pending = root.parent / (root.name + ".pending.json")
        pending.write_text("unfinished")
        with self.assertRaisesRegex(ValueError, "unfinished"):
            admit_candidate(self.retained, self.invocation, "stock")
        pending.unlink()
        program = Path(prepared["executables"]["devcontainer"])
        program.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "bytes or modes"):
            admit_candidate(self.retained, self.invocation, "stock")
        with self.assertRaisesRegex(ValueError, "bytes or modes"):
            self.prepare()
        self.assertEqual(program.read_bytes(), b"changed")

    def test_missing_preparation_is_read_only_and_aliases_are_rejected(self):
        with self.assertRaises(FileNotFoundError):
            admit_candidate(self.retained, self.invocation, "stock")
        self.assertFalse((self.retained / "prepared-candidates").exists())
        alias = self.retained / "alias.sqlite"
        alias.symlink_to(self.database)
        with self.assertRaisesRegex(ValueError, "aliased"):
            retained_candidate(alias, self.invocation)
        (self.scratch / "prepared-candidates").symlink_to(self.scratch / "tmp")
        with self.assertRaisesRegex(ValueError, "Aliased"):
            self.prepare()


if __name__ == "__main__":
    unittest.main()
