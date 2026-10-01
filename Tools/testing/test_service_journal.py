"""Real crash-reopen/private-journal tests with simulated launchd mutations."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from service_journal import ServiceJournal
from service_switch import API, ServiceSwitch, snapshot
from test_service_switch import FakeLaunchd
import plistlib


class ServiceJournalTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name).resolve()
        self.owned = self.root / "owned"
        self.owned.mkdir(mode=0o700)
        self.path = self.root / "private.sqlite"
        self.owner = {"root": str(self.owned), "case": "fixture"}
        self.journal = ServiceJournal(self.path, self.owner, create=True)

    def test_reopen_preserves_immutable_bytes_and_receipt_does_not_expose_them(self):
        self.journal.put("service-original.plist", b"fixture-secret")
        reopened = ServiceJournal(self.path, self.owner)
        self.assertEqual(reopened.records()["service-original.plist"], b"fixture-secret")
        self.assertNotIn("fixture-secret", json.dumps(reopened.receipt()))
        before = reopened.receipt()
        reopened.put("service-original.plist", b"fixture-secret")
        self.assertEqual(reopened.receipt(), before)
        with self.assertRaisesRegex(ValueError, "immutable"):
            reopened.put("service-original.plist", b"changed")

    def test_owner_swap_cannot_reuse_existing_journal(self):
        other = dict(self.owner, case="different")
        with self.assertRaises(ValueError):
            ServiceJournal(self.path, other)
        with self.assertRaises(FileExistsError):
            ServiceJournal(self.path, self.owner, create=True)

    def test_process_loss_rolls_back_uncommitted_append_and_retains_committed_bytes(self):
        self.journal.put("committed.txt", b"durable fixture")
        before = self.journal.receipt()
        # Simulate a worker dying between an entry update and its head commit.
        # No real services are involved; SQLite must recover its hot journal.
        script = ("import os,sqlite3,sys; "
                  "db=sqlite3.connect(sys.argv[1]); db.execute('PRAGMA synchronous=FULL'); "
                  "db.execute('BEGIN IMMEDIATE'); "
                  "db.execute(\"UPDATE entries SET payload=x'00' WHERE sequence=2\"); "
                  "os._exit(19)")
        worker = subprocess.run([sys.executable, "-c", script, str(self.path)], capture_output=True,
                                timeout=5, env={"PATH": "/usr/bin:/bin", "TMPDIR": str(self.root)})
        self.assertEqual(worker.returncode, 19)
        reopened = ServiceJournal(self.path, self.owner)
        self.assertEqual(reopened.receipt(), before)
        self.assertEqual(reopened.records()["committed.txt"], b"durable fixture")
        reopened.put("recovered.txt", b"next durable entry")
        self.assertEqual(reopened.receipt()["records"], before["records"] + 1)

    def test_bytes_row_deletion_reorder_and_head_tampering_are_rejected(self):
        mutations = ["UPDATE entries SET payload=x'00' WHERE sequence=2", "DELETE FROM entries WHERE sequence=2",
                     "UPDATE entries SET sequence=5 WHERE sequence=2", "UPDATE head SET sequence=0",
                     "INSERT INTO head SELECT * FROM head"]
        for index, mutation in enumerate(mutations):
            path = self.root / f"corrupt-{index}.sqlite"
            journal = ServiceJournal(path, self.owner, create=True)
            journal.put("entry.txt", b"original")
            with sqlite3.connect(path) as db:
                db.execute(mutation)
            with self.assertRaises(ValueError):
                journal.records()

    def test_journal_and_parent_permissions_links_and_missing_files_fail_closed(self):
        self.path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "ownership"):
            self.journal.records()
        with self.assertRaisesRegex(ValueError, "0600"):
            ServiceJournal(self.path, self.owner)
        self.path.chmod(0o600)
        alias = self.root / "alias.sqlite"
        alias.symlink_to(self.path)
        with self.assertRaises(OSError):
            ServiceJournal(alias, self.owner)
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.journal.records()
        self.assertFalse(self.path.exists())
        self.root.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "0700"):
            ServiceJournal(self.path, self.owner, create=True)
        self.root.chmod(0o700)

    def test_entry_names_types_and_size_are_bounded(self):
        for name, data in [("../escape", b"bytes"), ("entry", "text"), ("entry", b"x" * (8 * 1024**2 + 1))]:
            with self.assertRaisesRegex(ValueError, "Invalid"):
                self.journal.put(name, data)

    def test_actual_journal_can_restore_after_worker_loss_during_registration(self):
        old = self.root / "original.plist"
        old.write_bytes(plistlib.dumps({"Label": API, "ProgramArguments": ["/original/api", "start"]}))
        selected = self.owned / "selected.plist"
        selected.write_bytes(plistlib.dumps({"Label": API, "ProgramArguments": ["/released/api", "start"]}))
        launchd = FakeLaunchd()
        launchd.bootstrap(old)
        prior = snapshot(launchd, {API: self.root})
        switch = ServiceSwitch(launchd, prior, self.owned, self.journal.put)
        switch.prepare()
        launchd.fail = ("bootstrap-after", API)
        with self.assertRaises(RuntimeError):
            switch.install(selected)
        launchd.fail = None
        reopened = ServiceJournal(self.path, self.owner)
        recovered = reopened.recover_switch(launchd, self.owned)
        recovered.restore()
        self.assertEqual(launchd.inspect(API)["path"], str(old))
        self.assertEqual(reopened.records()["service-original-0000.plist"], old.read_bytes())
        foreign = self.root / "other"
        with self.assertRaisesRegex(ValueError, "Recovery root"):
            reopened.recover_switch(launchd, foreign)

    def test_recovery_cannot_guess_missing_snapshot_or_payload(self):
        launchd = FakeLaunchd()
        with self.assertRaisesRegex(ValueError, "No committed"):
            self.journal.recover_switch(launchd, self.owned)
        self.journal.put("service-originals.plist", plistlib.dumps([
            {"label": API, "path": "/missing.plist", "program": "/original/api", "sha256": "0" * 64}]))
        with self.assertRaisesRegex(ValueError, "missing or corrupt"):
            self.journal.recover_switch(launchd, self.owned)


if __name__ == "__main__":
    unittest.main()
