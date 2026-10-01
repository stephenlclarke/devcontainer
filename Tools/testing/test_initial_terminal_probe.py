"""Explicit initial-size oracle using the real owned Unix-socket test server."""

import json
from pathlib import Path
import time
import unittest
from unittest.mock import Mock, patch

from case_evidence import contract_observations
from initial_terminal_probe import DIMENSIONS, OUTPUT, InitialTerminalSizeFixture
import test_foreground_probe as foreground_helpers


class InitialTerminalTests(unittest.TestCase):
    stop = foreground_helpers.ForegroundTests.stop

    def reopen(self):
        return InitialTerminalSizeFixture(self.socket, self.owner, self.server.image, "1.54", self.journal)

    def setUp(self):
        foreground_helpers.ForegroundTests.setUp(self)
        self.server.initial_output = OUTPUT

    def test_first_observation_precedes_input_and_no_resize_is_issued(self):
        contract = Path(__file__).parents[2] / "Tests/Parity/fixtures/E15-initial-terminal-size/contract.json"
        expected = contract_observations(json.loads(contract.read_text())["expected"])
        self.assertEqual(self.fixture.operation(), expected)
        self.assertEqual(self.journal.records()["initial-terminal-output.log"], OUTPUT)
        capture = json.loads(self.journal.records()["initial-terminal-capture.json"])
        self.assertEqual(capture, {"observedBytes": len(OUTPUT), "retainedBytes": len(OUTPUT), "truncated": False})
        intent = json.loads(self.journal.records()["container-intent.json"])
        self.assertEqual(intent["foreground"]["initialConsoleSize"], list(DIMENSIONS))
        self.assertFalse(any("/resize" in path for _, path in self.server.routes))
        self.assertEqual(self.fixture.cleanup(), {"status": "passed", "remainingOwnedResources": []})
        self.assertEqual(self.reopen().cleanup()["status"], "passed")

    def test_wrong_initial_guest_size_is_retained_and_fails(self):
        self.server.initial_output = b"initial-size:0 0\n"
        with self.assertRaisesRegex(ValueError, "First guest terminal size"):
            self.fixture.operation()
        self.assertEqual(self.journal.records()["initial-terminal-output.log"], self.server.initial_output)
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_inspection_cannot_silently_ignore_requested_dimensions(self):
        create = self.fixture.create
        def changed():
            result = create()
            result["HostConfig"]["ConsoleSize"] = [0, 0]
            return result
        with patch.object(self.fixture, "create", side_effect=changed), \
                self.assertRaisesRegex(ValueError, "Inspection does not retain"):
            self.fixture.operation()
        self.assertFalse(any(path.endswith("/start") for _, path in self.server.routes))
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_process_must_not_run_before_attachments_are_installed(self):
        create = self.fixture.create
        def changed():
            result = create()
            result["State"]["Status"] = "running"
            return result
        with patch.object(self.fixture, "create", side_effect=changed), \
                self.assertRaisesRegex(ValueError, "before subscriptions"):
            self.fixture.operation()
        self.assertFalse(any(path.endswith("/start") for _, path in self.server.routes))
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_wrong_exit_cannot_pass_despite_correct_size(self):
        self.server.exit_code = 0
        with self.assertRaisesRegex(ValueError, "real exit status"):
            self.fixture.operation()
        self.assertEqual(self.fixture.cleanup()["status"], "passed")


class InitialTerminalReadTests(unittest.TestCase):
    def probe(self):
        probe = InitialTerminalSizeFixture.__new__(InitialTerminalSizeFixture)
        probe.journal = Mock()
        return probe

    def test_fragmented_observation_uses_one_absolute_deadline(self):
        probe, connection = self.probe(), Mock()
        connection.recv.side_effect = [b"37 ", b"113\n"]
        end = time.monotonic() + 1
        probe.receive_initial_size(connection, b"initial-size:", end)
        self.assertTrue(all(0 < call.args[0] <= 1 for call in connection.settimeout.call_args_list))
        probe.journal.put.assert_any_call("initial-terminal-output.log", OUTPUT)

    def test_eof_and_timeout_retain_partial_output(self):
        for error in (None, TimeoutError("deadline")):
            with self.subTest(error=error):
                probe, connection = self.probe(), Mock()
                connection.recv.return_value = b""
                connection.recv.side_effect = error
                with self.assertRaises((ValueError, TimeoutError)):
                    probe.receive_initial_size(connection, b"initial-", time.monotonic() + 1)
                probe.journal.put.assert_any_call("initial-terminal-output.log", b"initial-")

    def test_oversized_or_extra_output_is_never_accepted_or_retained_unbounded(self):
        for initial in (b"x" * 100, OUTPUT + b"trailing", b"113 37\n"):
            with self.subTest(initial=initial):
                probe, connection = self.probe(), Mock()
                with self.assertRaisesRegex(ValueError, "First guest terminal size"):
                    probe.receive_initial_size(connection, initial, time.monotonic() + 1)
                connection.recv.assert_not_called()
                probe.journal.put.assert_any_call("initial-terminal-output.log", initial[:64])
                capture = json.loads(probe.journal.put.call_args.args[1])
                self.assertEqual(capture, {"observedBytes": len(initial), "retainedBytes": min(len(initial), 64),
                                           "truncated": len(initial) > 64})

    def test_socket_overflow_is_detected_with_one_bounded_extra_byte(self):
        probe, connection = self.probe(), Mock()
        connection.recv.side_effect = [b"x" * 64, b"x"]
        with self.assertRaisesRegex(ValueError, "First guest terminal size"):
            probe.receive_initial_size(connection, b"", time.monotonic() + 1)
        probe.journal.put.assert_any_call("initial-terminal-output.log", b"x" * 64)
        self.assertEqual(json.loads(probe.journal.put.call_args.args[1]),
                         {"observedBytes": 65, "retainedBytes": 64, "truncated": True})
        self.assertEqual([call.args[0] for call in connection.recv.call_args_list], [65, 1])


if __name__ == "__main__":
    unittest.main()
