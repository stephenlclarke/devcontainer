"""Atomic retention, deduplication and corrupt-evidence rejection."""

import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from retain_evidence import artifact_paths, digest, evidence_paths, restore_candidate, retain


class RetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name).resolve()
        self.database = self.root / "evidence.sqlite"
        self.events = self.root / "events.json"
        self.xml = self.root / "test.xml"
        self.xml.write_text("<testsuite><testcase/></testsuite>")
        self.records = [
            {"started": {"uuid": "test-invocation"}},
            {"id": {"testResult": {"label": "//:unit"}}, "testResult": {"testActionOutput": [{"name": "test.xml", "uri": self.xml.as_uri()}]}},
            {"finished": {"exitCode": {"code": 0}}},
        ]

    def write_events(self) -> None:
        self.events.write_text("\n".join(json.dumps(row) for row in self.records))

    def test_repeat_is_idempotent_and_bytes_survive_scratch_removal(self) -> None:
        self.write_events()
        for _ in range(2):
            self.assertEqual(retain(self.events, self.database, self.root), "test-invocation")
        self.xml.unlink()
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM invocations").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM blobs").fetchone()[0], 2)
            self.assertIn(b"<testsuite><testcase/></testsuite>", [row[0] for row in db.execute("SELECT bytes FROM blobs")])

    def test_changed_outputs_cannot_replace_a_sealed_invocation(self) -> None:
        self.write_events()
        retain(self.events, self.database, self.root)
        self.xml.write_text("changed")
        with self.assertRaisesRegex(ValueError, "different evidence"):
            retain(self.events, self.database, self.root)
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM blobs").fetchone()[0], 2)

    def test_corrupt_store_fails_closed(self) -> None:
        self.write_events()
        retain(self.events, self.database, self.root)
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE blobs SET bytes=?", (b"corrupt",))
        with self.assertRaisesRegex(ValueError, "integrity"):
            retain(self.events, self.database, self.root)

    def test_incomplete_run_does_not_create_receipt(self) -> None:
        self.records.pop()
        self.write_events()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            retain(self.events, self.database, self.root)
        self.assertFalse(self.database.exists())

    def test_diagnostic_without_uuid_is_not_mislabelled_as_build(self) -> None:
        self.records[0] = {"started": {"command": "query"}}
        self.write_events()
        self.assertTrue(retain(self.events, self.database, self.root).startswith("diagnostic-"))
        self.records[0] = {"started": {"command": "build"}}
        self.write_events()
        with self.assertRaisesRegex(ValueError, "missing its identity"):
            retain(self.events, self.database, self.root)

    def test_unsanitized_environment_cannot_be_retained_or_echoed(self) -> None:
        for value in ["--client_env=UNRELATED_SECRET=fixture-secret",
                      {"optionName": "client_env", "optionValue": "UNRELATED_SECRET=fixture-secret"}]:
            self.records.append({"diagnostic": value})
            self.write_events()
            with self.assertRaisesRegex(ValueError, "Unsafe inherited environment") as error:
                retain(self.events, self.database, self.root)
            self.assertNotIn("fixture-secret", str(error.exception))
            self.assertFalse(self.database.exists())
            self.records.pop()

    def test_external_network_and_symlink_escapes_are_rejected(self) -> None:
        link = self.root / "escape"
        link.symlink_to("/etc/hosts")
        for uri in ("https://example.invalid/test.xml", "file:///etc/hosts", link.as_uri(), (self.root / "missing").as_uri()):
            with self.subTest(uri=uri):
                self.records[1]["testResult"]["testActionOutput"][0]["uri"] = uri
                with self.assertRaises(ValueError):
                    evidence_paths(self.records, self.root)

    def add_candidate(self) -> None:
        archive = self.root / "candidate_archive.tar.gz"
        receipt = self.root / "candidate_archive.json"
        archive.write_bytes(b"candidate fixture")
        receipt.write_text(json.dumps({"archiveSHA256": digest(archive.read_bytes())}))
        self.records.extend([
            {"id": {"namedSet": {"id": "candidate-files"}}, "namedSetOfFiles": {"files": [
                {"uri": archive.as_uri()}, {"uri": receipt.as_uri()},
            ]}},
            {"id": {"targetCompleted": {"label": "//:candidate_archive"}}, "completed": {"success": True, "outputGroup": [
                {"name": "default", "fileSets": [{"id": "candidate-files"}]},
            ]}},
        ])

    def test_candidate_survives_output_removal_and_restores_without_a_build(self) -> None:
        self.add_candidate()
        self.write_events()
        retain(self.events, self.database, self.root)
        (self.root / "candidate_archive.tar.gz").unlink()
        (self.root / "candidate_archive.json").unlink()
        (self.root / "tmp").mkdir()
        output = restore_candidate(self.database, "test-invocation", self.root)
        self.assertEqual((output / "candidate_archive.tar.gz").read_bytes(), b"candidate fixture")
        self.assertEqual(restore_candidate(self.database, "test-invocation", self.root), output)
        (output / "candidate_archive.tar.gz").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            restore_candidate(self.database, "test-invocation", self.root)

    def test_artifact_set_must_be_complete_local_and_unambiguous(self) -> None:
        self.add_candidate()
        group = self.records[-2]["namedSetOfFiles"]
        before = group["files"].pop()
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            artifact_paths(self.records, self.root)
        group["files"].append(before)
        group["files"][0]["uri"] = "file:///etc/hosts"
        with self.assertRaisesRegex(ValueError, "local to"):
            artifact_paths(self.records, self.root)

    def test_build_cannot_claim_a_retained_candidate_without_any_outputs(self) -> None:
        self.add_candidate()
        self.records[-1]["completed"]["outputGroup"] = []
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            artifact_paths(self.records, self.root)
        self.records[0]["started"]["command"] = "cquery"
        self.assertEqual(artifact_paths(self.records, self.root), {})

    def test_restore_rejects_failed_run_and_corrupt_blobs(self) -> None:
        self.add_candidate()
        self.write_events()
        retain(self.events, self.database, self.root)
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE invocations SET exit_code=1")
        with self.assertRaisesRegex(ValueError, "successful"):
            restore_candidate(self.database, "test-invocation", self.root)
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE invocations SET exit_code=0")
            db.execute("UPDATE blobs SET bytes=?", (b"corrupt",))
        with self.assertRaisesRegex(ValueError, "corrupt"):
            restore_candidate(self.database, "test-invocation", self.root)


if __name__ == "__main__":
    unittest.main()
