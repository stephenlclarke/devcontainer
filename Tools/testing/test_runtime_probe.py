"""API readiness proof and interrupted-probe recovery without host services."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from case_evidence import canonical, digest
from runtime_probe import NAME, probe_api, probe_diagnostics, require_probe_stopped
from service_journal import ServiceJournal


class RuntimeProbeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.counter = 0

    def fixture(self, payload=b"[]\n"):
        self.counter += 1
        root = self.base / str(self.counter)
        root.mkdir(mode=0o700)
        cli = root / "install/bin/container"
        cli.parent.mkdir(parents=True)
        cli.write_text("fixture executable; never executed")
        journal = ServiceJournal(root / "journal.sqlite", {"root": str(root)}, create=True)
        child = Mock()
        child.process.pid = 123
        child.process.wait.return_value = 0
        child.start.side_effect = lambda arguments, home, output, **kwargs: output.write(payload)
        return root, cli, journal, child

    def test_empty_inventory_commits_ready_only_after_verified_stop_and_identity(self):
        root, cli, journal, child = self.fixture()
        verify = Mock()
        with patch("runtime_probe.OwnedProcess", return_value=child):
            probe_api(root, cli, journal, verify)
        self.assertEqual(verify.call_count, 2)
        self.assertEqual(child.start.call_args.args[:2], ([str(cli), "list", "--all", "--format", "json"], root))
        self.assertEqual(child.start.call_args.kwargs, {"provider_install": cli.parent.parent})
        child.process.wait.assert_called_once_with(timeout=10)
        child.stop.assert_called_once()
        records = journal.records()
        require_probe_stopped(records)
        self.assertEqual(records[NAME + ".log"], b"[]\n")
        ready = json.loads(records[NAME + "-ready.json"])
        self.assertEqual(ready, {"emptyInventory": True, "intentSHA256": digest(records[NAME + "-intent.json"])})
        self.assertLess(list(records).index(NAME + "-stopped.json"), list(records).index(NAME + "-ready.json"))

    def test_error_nonempty_malformed_and_truncated_responses_cannot_be_ready(self):
        for payload, code in [(b"[]", 1), (b"[{}]", 0), (b"{}", 0), (b"not json", 0),
                              (b"[]" + b" " * 1024**2, 0)]:
            with self.subTest(payload_bytes=len(payload), exit_code=code):
                root, cli, journal, child = self.fixture(payload)
                child.process.wait.return_value = code
                with patch("runtime_probe.OwnedProcess", return_value=child):
                    with self.assertRaises(ValueError):
                        probe_api(root, cli, journal, Mock())
                child.stop.assert_called_once()
                require_probe_stopped(journal.records())
                self.assertNotIn(NAME + "-ready.json", journal.records())

    def test_timeout_is_not_retried_and_retains_stopped_diagnostics(self):
        root, cli, journal, child = self.fixture(b"startup stalled\n")
        child.process.wait.side_effect = subprocess.TimeoutExpired("fixture", 10)
        verify = Mock()
        with patch("runtime_probe.OwnedProcess", return_value=child):
            with self.assertRaises(subprocess.TimeoutExpired):
                probe_api(root, cli, journal, verify)
        child.start.assert_called_once()
        child.stop.assert_called_once()
        require_probe_stopped(journal.records())
        self.assertEqual(journal.records()[NAME + ".log"], b"startup stalled\n")
        self.assertNotIn(NAME + "-ready.json", journal.records())

    def test_uncertain_spawn_or_surviving_child_preserves_quarantine(self):
        for interrupted in (False, True):
            root, cli, journal, child = self.fixture()
            if interrupted:
                child.start.side_effect = KeyboardInterrupt()
            child.stop.side_effect = ValueError("uncertain child lifetime")
            with patch("runtime_probe.OwnedProcess", return_value=child):
                with self.assertRaisesRegex(ValueError, "uncertain child"):
                    probe_api(root, cli, journal, Mock())
            with self.assertRaisesRegex(ValueError, "reconciliation"):
                require_probe_stopped(journal.records())
            self.assertNotIn(NAME + "-ready.json", journal.records())
            self.assertTrue(root.exists())

    def test_replaced_service_after_response_is_not_ready(self):
        root, cli, journal, child = self.fixture()
        verify = Mock(side_effect=[None, ValueError("service replaced")])
        with patch("runtime_probe.OwnedProcess", return_value=child):
            with self.assertRaisesRegex(ValueError, "service replaced"):
                probe_api(root, cli, journal, verify)
        require_probe_stopped(journal.records())
        self.assertNotIn(NAME + "-ready.json", journal.records())

    def test_duplicate_attempt_and_noncanonical_cli_fail_before_spawn(self):
        root, cli, journal, child = self.fixture()
        alias = root / "alias"
        alias.symlink_to(cli.parent, target_is_directory=True)
        with patch("runtime_probe.OwnedProcess", return_value=child):
            for executable in (alias / "container", cli.with_name("missing"), Path("container")):
                with self.assertRaisesRegex(ValueError, "canonical"):
                    probe_api(root, executable, journal, Mock())
            child.start.assert_not_called()
            probe_api(root, cli, journal, Mock())
            with self.assertRaisesRegex(ValueError, "already attempted"):
                probe_api(root, cli, journal, Mock())
            child.start.assert_called_once()

    def test_retention_failure_resumes_without_running_probe_again(self):
        root, cli, journal, child = self.fixture()
        with patch("runtime_probe.OwnedProcess", return_value=child), \
                patch("runtime_probe.diagnostic_snapshot", side_effect=OSError("retention interrupted")):
            with self.assertRaisesRegex(OSError, "retention interrupted"):
                probe_api(root, cli, journal, Mock())
        require_probe_stopped(journal.records())
        probe_diagnostics(root, journal)
        self.assertEqual(journal.records()[NAME + ".log"], b"[]\n")
        self.assertNotIn(NAME + "-ready.json", journal.records())
        child.start.assert_called_once()

    def test_stop_proof_is_intent_bound_and_legacy_transactions_stay_readable(self):
        require_probe_stopped({})
        intent = canonical({"arguments": ["fixture"]})
        for receipt in (None, {}, {"verifiedStopped": True, "intentSHA256": "wrong"},
                        {"verifiedStopped": 1, "intentSHA256": digest(intent)}):
            records = {NAME + "-intent.json": intent, NAME + "-stopped.json": canonical(receipt)}
            with self.assertRaisesRegex(ValueError, "reconciliation"):
                require_probe_stopped(records)
