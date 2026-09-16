"""Owned-only scratch cleanup with durable evidence and no recursive deletion."""

import json
import fcntl
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

from hygiene import cleanup, register, remove_verified, verify_retained
from retain_evidence import retain


class HygieneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "invocations").mkdir()
        (self.root / "locks").mkdir()
        self.run = self.root / "invocations/run.fixture"
        self.run.mkdir()
        self.database = self.root / "evidence.sqlite"
        register(self.run, "/fixture/repository", self.root)
        for name in ["inputs-before.json", "inputs-after.json", "outcome.json"]:
            (self.run / name).write_text("{}")
        (self.run / "events.json").write_text(json.dumps({"started": {"uuid": "fixture"}}) + "\n" + json.dumps({"finished": {"exitCode": {"code": 0}}}))

    def test_dry_run_preserves_and_verified_removal_keeps_database(self) -> None:
        retain(self.run / "events.json", self.database, self.root)
        report = cleanup(self.root, self.database, 0, False)
        self.assertEqual(report[0]["eligible"], str(self.run))
        self.assertTrue(self.run.exists())
        removed = remove_verified(self.run, self.database, self.root, 2**62)
        self.assertEqual(removed["recoverableFrom"], "fixture")
        self.assertFalse(self.run.exists())
        self.assertTrue(self.database.exists())

    def test_unretained_unowned_new_and_changed_runs_are_preserved(self) -> None:
        self.assertIn("preserved", cleanup(self.root, self.database, 0, False)[0])
        retain(self.run / "events.json", self.database, self.root)
        self.assertIn("preserved", cleanup(self.root, self.database, 14, False)[0])
        (self.run / "outcome.json").write_text("changed")
        with self.assertRaisesRegex(ValueError, "differs"):
            remove_verified(self.run, self.database, self.root, 2**62)
        (self.run / "owner.json").unlink()
        self.assertIn("preserved", cleanup(self.root, self.database, 0, False)[0])

    def test_extra_files_subdirectories_and_symlinks_are_never_deleted(self) -> None:
        retain(self.run / "events.json", self.database, self.root)
        extra = self.run / "user-file"
        extra.write_text("keep")
        with self.assertRaisesRegex(ValueError, "Unknown"):
            remove_verified(self.run, self.database, self.root, 2**62)
        extra.unlink()
        extra.mkdir()
        with self.assertRaisesRegex(ValueError, "Unknown"):
            remove_verified(self.run, self.database, self.root, 2**62)
        extra.rmdir()
        (self.run / "inputs-before.json").unlink()
        (self.run / "inputs-before.json").symlink_to(self.run / "inputs-after.json")
        with self.assertRaisesRegex(ValueError, "Unknown"):
            remove_verified(self.run, self.database, self.root, 2**62)

    def test_corrupt_retained_bytes_and_path_escapes_prevent_cleanup(self) -> None:
        retain(self.run / "events.json", self.database, self.root)
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE blobs SET bytes=?", (b"corrupt",))
        with self.assertRaisesRegex(ValueError, "integrity"):
            remove_verified(self.run, self.database, self.root, 2**62)
        with self.assertRaisesRegex(ValueError, "owned invocation"):
            verify_retained(self.root, self.database, self.root, 2**62)

    def test_cleanup_respects_the_same_lease_as_the_real_launcher(self) -> None:
        retain(self.run / "events.json", self.database, self.root)
        marker = json.loads((self.run / "owner.json").read_text())
        lease = self.root / "locks" / (marker["workspaceKey"] + ".lock")
        with lease.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run(["/usr/bin/lockf", "-k", "-t", "0", str(lease), "/usr/bin/true"], capture_output=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            report = cleanup(self.root, self.database, 0, True)
            self.assertEqual(report[0]["reason"], "Workspace lease is active")
            self.assertTrue(self.run.exists())
        report = cleanup(self.root, self.database, 0, True)
        self.assertEqual(report[0]["removed"], str(self.run))

    def test_partial_scratch_removal_can_resume_from_retained_bytes(self) -> None:
        retain(self.run / "events.json", self.database, self.root)
        (self.run / "inputs-before.json").unlink()
        removed = remove_verified(self.run, self.database, self.root, 2**62)
        self.assertEqual(removed["recoverableFrom"], "fixture")
        self.assertFalse(self.run.exists())
