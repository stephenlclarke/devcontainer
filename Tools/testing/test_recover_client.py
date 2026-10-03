"""Captured Engine recovery rejects legacy/reused PIDs and uncertain prior signals."""

from contextlib import ExitStack
import copy
import signal
from unittest.mock import Mock, patch
import unittest

from case_evidence import canonical, digest
from recover_client import captured_client, client_running, recover_engine, stop_captured_client, retain_engine_diagnostics
import test_recover_apple_build as helpers


def capture(root="/case"):
    return {"pid": 42, "parent": 10, "group": 42, "started": "original", "program": "/engine",
            "arguments": ["/engine", "--container", "/container", "--socket", root + "/engine.sock",
                          "--state", root + "/state.sqlite"]}


def artifacts(root="/case"):
    return {"process-incarnation.json": capture(root), "process.json": {"pid": 42, "root": root},
            "process-intent.json": {"program": "/engine"}}


class ClientIdentityTests(unittest.TestCase):
    def test_only_original_complete_capture_grants_authority(self):
        self.assertEqual(captured_client(artifacts(), {"root": "/case"}), capture())
        for mutation in (None, {}, {**capture(), "started": ""}, {**capture(), "pid": -1},
                         {**capture(), "parent": 0}, {**capture(), "program": "/other"},
                         {**capture(), "group": 43}, {**capture(), "arguments": []}):
            value = {**artifacts(), "process-incarnation.json": mutation}
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                captured_client(value, {"root": "/case"})
        value = artifacts()
        value["process-incarnation.json"]["arguments"][-1] = "/foreign/state"
        with self.assertRaisesRegex(ValueError, "arguments"):
            captured_client(value, {"root": "/case"})

    def test_reparented_original_is_allowed_but_replacement_or_descendants_are_not(self):
        expected = capture()
        self.assertFalse(client_running(expected, {}))
        self.assertTrue(client_running(expected, {42: expected}))
        self.assertTrue(client_running(expected, {42: {**expected, "parent": 1}}))
        for key, value in (("started", "replacement"), ("program", "/other"), ("group", 99), ("parent", 99)):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "incarnation"):
                client_running(expected, {42: {**expected, key: value}})
        with self.assertRaisesRegex(ValueError, "descendants"):
            client_running(expected, {43: {"pid": 43, "group": 42}})
        for descendants in (
            {43: {"pid": 43, "parent": 42, "group": 43}},
            {43: {"pid": 43, "parent": 42, "group": 43}, 44: {"pid": 44, "parent": 43, "group": 44}},
        ):
            with self.subTest(descendants=descendants), self.assertRaisesRegex(ValueError, "descendants"):
                client_running(expected, {42: expected, **descendants})

    def test_one_signal_is_preceded_by_intent_and_absence_is_durably_verified(self):
        journal = Mock()
        journal.records.return_value = {}
        expected = capture()
        def signal_after_journal(pid, number):
            journal.put.assert_called_once_with("engine-stop-intent.json", canonical(expected))
            self.assertEqual((pid, number), (42, signal.SIGTERM))
        with patch("recover_client.process_inventory", side_effect=[{42: expected}, {42: expected}, {}]), \
                patch("recover_client.os.kill", side_effect=signal_after_journal) as kill:
            self.assertFalse(stop_captured_client(expected, journal, apply=True))
        kill.assert_called_once()
        journal.put.assert_called_with("engine-stop-verified.json", canonical({
            "incarnationSHA256": digest(canonical(expected)), "absent": True}))

    def test_report_is_read_only_and_missing_process_can_reconcile_lost_signal_response(self):
        journal = Mock()
        expected = capture()
        journal.records.return_value = {"engine-stop-intent.json": canonical(expected)}
        with patch("recover_client.process_inventory", return_value={42: expected}), patch("recover_client.os.kill") as kill:
            self.assertTrue(stop_captured_client(expected, journal, apply=False))
            journal.put.assert_not_called()
            with self.assertRaisesRegex(ValueError, "second signal"):
                stop_captured_client(expected, journal, apply=True)
            kill.assert_not_called()
        with patch("recover_client.process_inventory", return_value={}), patch("recover_client.os.kill") as kill:
            self.assertFalse(stop_captured_client(expected, journal, apply=True))
            kill.assert_not_called()

    def test_changed_intent_reappearance_and_boundary_pid_reuse_are_refused(self):
        journal = Mock()
        expected = capture()
        for records in ({"engine-stop-intent.json": b"{}"}, {"engine-stop-verified.json": b"{}"}):
            journal.records.return_value = records
            with patch("recover_client.process_inventory", return_value={42: expected}), \
                    patch("recover_client.os.kill") as kill, self.assertRaises(ValueError):
                stop_captured_client(expected, journal, apply=True)
            kill.assert_not_called()
        journal.records.return_value = {}
        with patch("recover_client.process_inventory", side_effect=[{42: expected}, {42: {**expected, "started": "new"}}]), \
                patch("recover_client.os.kill") as kill, self.assertRaisesRegex(ValueError, "incarnation"):
            stop_captured_client(expected, journal, apply=True)
        kill.assert_not_called()

    def test_exit_during_signal_and_wait_timeout_never_escalate_or_retry(self):
        journal = Mock()
        journal.records.return_value = {}
        expected = capture()
        with patch("recover_client.process_inventory", side_effect=[{42: expected}, {42: expected}, {}]), \
                patch("recover_client.os.kill", side_effect=ProcessLookupError):
            stop_captured_client(expected, journal, apply=True)
        journal.reset_mock()
        with patch("recover_client.process_inventory", return_value={42: expected}), \
                patch("recover_client.os.kill") as kill, patch("recover_client.time.sleep", side_effect=TimeoutError), \
                self.assertRaises(TimeoutError):
            stop_captured_client(expected, journal, apply=True)
        kill.assert_called_once_with(42, signal.SIGTERM)
        journal.put.assert_called_once_with("engine-stop-intent.json", canonical(expected))


class ClientRecoveryTransactionTests(unittest.TestCase):
    def setUp(self):
        helpers.AppleBuildResourceTransactionTests.setUp(self)
        (self.root / "engine.log").write_bytes(b"private fixture log")

    def test_engine_diagnostics_are_private_resumable_and_required_before_disposal(self):
        self.journal.put("engine-stop-verified.json", canonical({"absent": True}))
        retain_engine_diagnostics(self.root, self.journal, apply=False)
        self.assertNotIn("engine-recovery.log", self.journal.records())
        self.journal.put("engine-recovery.log", b"private fixture log")
        retain_engine_diagnostics(self.root, self.journal, apply=True)
        self.assertIn("engine-recovery-log.json", self.journal.records())
        (self.root / "engine.log").unlink()
        retain_engine_diagnostics(self.root, self.journal, apply=True)

    def test_missing_changed_or_invalid_engine_diagnostics_preserve_root(self):
        retain_engine_diagnostics(self.root, self.journal, apply=True)
        self.assertNotIn("engine-recovery.log", self.journal.records())
        self.journal.put("engine-stop-intent.json", canonical(capture(str(self.root))))
        self.journal.put("engine-recovery.log", b"changed")
        with self.assertRaisesRegex(ValueError, "Partial Engine"):
            retain_engine_diagnostics(self.root, self.journal, apply=True)
        (self.root / "engine.log").unlink()
        with self.assertRaises(FileNotFoundError):
            retain_engine_diagnostics(self.root, self.journal, apply=True)
        self.journal.put("engine-recovery-log.json", b"{}")
        with self.assertRaisesRegex(ValueError, "diagnostics"):
            retain_engine_diagnostics(self.root, self.journal, apply=True)
        self.assertTrue(self.root.exists())

    def test_closed_resources_are_required_before_signal_and_provider_stays_untouched(self):
        value = artifacts(str(self.root))
        releases = [{"executables": {"devcontainer-engine": "/engine"}}, {"executables": {"container": "/container"}}]
        with ExitStack() as stack:
            stack.enter_context(patch("recover_client.verify_case_evidence", return_value=value))
            admission = stack.enter_context(patch("recover_client.admit_original", return_value=(releases, {})))
            gates = [stack.enter_context(patch("recover_client." + name)) for name in (
                "require_guest_cleanup", "require_previous_commands_closed", "require_keychain_stopped", "require_probe_stopped")]
            stop = stack.enter_context(patch("recover_client.stop_captured_client", return_value=True))
            report = recover_engine(self.retained, self.owner, self.guard, apply=False)
            self.assertEqual(report["status"], "ready-to-stop-captured-engine")
            self.assertFalse(report["providerRestored"])
            for gate in gates:
                gate.assert_called_once()
            stop.return_value = False
            self.assertEqual(recover_engine(self.retained, self.owner, self.guard, apply=True)["status"], "captured-engine-stopped")
            stop.reset_mock()
            gates[0].side_effect = ValueError("resources uncertain")
            with self.assertRaisesRegex(ValueError, "resources uncertain"):
                recover_engine(self.retained, self.owner, self.guard, apply=True)
            stop.assert_not_called()
            gates[0].side_effect = None
            wrong = copy.deepcopy(releases)
            wrong[0]["executables"]["devcontainer-engine"] = "/other"
            admission.return_value = (wrong, {})
            with self.assertRaisesRegex(ValueError, "admitted binaries"):
                recover_engine(self.retained, self.owner, self.guard, apply=True)
            stop.assert_not_called()
        self.assertTrue(self.guard.path.exists())
        self.runtime.restore.assert_not_called()

    def test_legacy_process_wrong_fixture_and_replaced_marker_never_grant_authority(self):
        with self.assertRaisesRegex(ValueError, "spawn-time"):
            recover_engine(self.retained, self.owner, self.guard, apply=True)
        self.owner["identity"]["fixture"] = "E01-engine-negotiation"
        with self.assertRaisesRegex(ValueError, "Apple E04"):
            recover_engine(self.retained, self.owner, self.guard, apply=True)
        self.owner["identity"]["fixture"] = "E04-image-build"
        (self.root / "owner.json").write_bytes(b"{}")
        with self.assertRaisesRegex(ValueError, "ownership"):
            recover_engine(self.retained, self.owner, self.guard, apply=True)
        self.signal.assert_not_called()
