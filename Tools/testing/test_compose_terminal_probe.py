"""Exercise real host PTY IO without a VM; never fabricate terminal output."""

import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from case_evidence import contract_observations
from compose_foreground_probe import PROCESS, TERMINAL_SIZE_FIXTURE
from compose_terminal_probe import ComposeTerminalSizeFixture
from foreground_probe import ForegroundFixture
import test_compose_foreground_probe as foreground_helpers
import test_guest_fixture as helpers


class ComposeTerminalSizeTests(unittest.TestCase):
    stop = helpers.GuestFixtureTests.stop

    def reopen(self):
        return ComposeTerminalSizeFixture(self.socket, self.owner, self.server.image, "1.54", self.journal,
                                          root=self.root, executable="/owned/compose", runtime=Mock())

    def setUp(self):
        helpers.GuestFixtureTests.setUp(self)
        self.addCleanup(self.release_terminal)

    def release_terminal(self):
        self.fixture.child.stop()
        if self.fixture.master is not None:
            os.close(self.fixture.master)
            self.fixture.master = None
        if self.fixture.capture is not None:
            self.fixture.capture.close()
            self.fixture.capture = None

    def guest(self):
        value = foreground_helpers.ComposeForegroundTests.guest(self)
        value["Config"].update(Cmd=list(self.fixture.command), Tty=True)
        return value

    def run_cli(self, script=None):
        original = self.fixture.child.start
        original_auto_remove = ForegroundFixture.require_auto_removed

        def start(arguments, root, output, **kwargs):
            self.cli_arguments = arguments
            self.assertTrue(os.isatty(output))
            self.assertTrue(os.isatty(kwargs["stdin"]))
            self.server.guest = self.guest()
            # A guest PTY starts with ONLCR enabled. Model that discipline, not
            # its bytes: `stty size` still reads the actual host PTY dimensions.
            command = "exec 2>&1; stty opost onlcr; " + (self.fixture.command[2] if script is None else script)
            original(["/bin/sh", "-c", command], root, output, **kwargs)

        def auto_remove(fixture):
            self.assertEqual(fixture.child.process.returncode, 17)
            self.server.guest = None
            original_auto_remove(fixture)

        with patch.object(self.fixture.child, "start", side_effect=start), \
                patch.object(ForegroundFixture, "require_auto_removed", auto_remove):
            return self.fixture.operation()

    def test_real_terminal_dimensions_input_merged_output_and_exit(self):
        observed = self.run_cli()
        contract = json.loads((Path(__file__).parents[2] / "Tests/Parity/fixtures" /
                               TERMINAL_SIZE_FIXTURE / "contract.json").read_text())
        self.assertEqual(observed, contract_observations(contract["expected"]))
        self.assertNotIn("-T", self.cli_arguments)
        self.assertNotIn("--no-tty", self.cli_arguments)
        self.assertEqual(self.fixture.cleanup()["remainingOwnedResources"], [])
        self.assertIsNone(self.fixture.master)
        records = self.journal.records()
        self.assertTrue(records[PROCESS + ".log"].startswith(b"37 113\r\n"))
        self.assertEqual(json.loads(records["compose-terminal-initial.json"])["columns"], 113)
        self.assertEqual(json.loads(records[PROCESS + "-exit.json"])["code"], 17)

    def test_wrong_initial_size_is_not_repaired_or_normalized(self):
        with self.assertRaisesRegex(ValueError, "unexpected foreground"):
            self.run_cli("stty rows 24 cols 80; " + self.fixture.command[2])
        self.assertEqual(self.fixture.cleanup()["status"], "passed")
        self.assertTrue(self.journal.records()[PROCESS + ".log"].startswith(b"24 80\r\n"))
        self.assertNotIn("compose-terminal-initial.json", self.journal.records())

    def test_nonterminal_guest_configuration_is_rejected(self):
        original = self.guest

        def without_tty():
            value = original()
            value["Config"]["Tty"] = False
            return value

        with patch.object(self, "guest", side_effect=without_tty):
            with self.assertRaisesRegex(ValueError, "configuration changed"):
                self.run_cli()
        # A mismatched resource is deliberately not deleted by fixture cleanup.
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            self.fixture.cleanup()
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))
        self.assertIsNone(self.fixture.master)

    def test_wrong_terminal_exit_is_not_success(self):
        with self.assertRaisesRegex(ValueError, "lost the guest exit"):
            self.run_cli(self.fixture.command[2].replace("exit 17", "exit 0"))
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_terminal_echo_or_extra_guest_bytes_fail(self):
        with self.assertRaisesRegex(ValueError, "changed or duplicated"):
            self.run_cli(self.fixture.command[2].replace("exit 17", "printf extra; exit 17"))
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_early_terminal_close_is_bounded_and_recoverable(self):
        with self.assertRaisesRegex(ValueError, "unexpected foreground"):
            self.run_cli("printf 'closed\\n'; sleep 0.1")
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_spawn_failure_closes_pty_during_cleanup(self):
        with patch.object(self.fixture.child, "start", side_effect=FileNotFoundError("missing executable")):
            with self.assertRaises(FileNotFoundError):
                self.fixture.operation()
        descriptor = self.fixture.master
        with self.assertRaisesRegex(ValueError, "Uncertain creation"):
            self.fixture.cleanup()
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    def test_replaced_log_path_cannot_redirect_terminal_writes(self):
        target = self.root / "outside-log"
        target.write_bytes(b"must remain unchanged")
        self.fixture.capture = self.fixture.output.open("xb", buffering=0)
        self.fixture.output.unlink()
        self.fixture.output.symlink_to(target)
        self.fixture.master, writer = os.pipe()
        try:
            os.write(writer, b"terminal bytes")
            self.fixture.drain()
        finally:
            os.close(writer)
        self.assertEqual(target.read_bytes(), b"must remain unchanged")
        self.assertEqual(os.fstat(self.fixture.capture.fileno()).st_size, len(b"terminal bytes"))
        with self.assertRaisesRegex(ValueError, "canonical"):
            self.fixture.snapshot(self.fixture.output)

    def test_oversized_terminal_output_still_records_diagnostics_and_removes_owned_guest(self):
        script = self.fixture.command[2].replace("exit 17", "head -c 1200000 /dev/zero; sleep 30")
        with self.assertRaisesRegex(ValueError, "diagnostic bound"):
            self.run_cli(script)
        self.assertEqual(self.fixture.cleanup()["remainingOwnedResources"], [])
        self.assertIsNone(self.fixture.master)
        self.assertIsNone(self.fixture.capture)
        records = self.journal.records()
        self.assertTrue(json.loads(records[PROCESS + "-log.json"])["truncated"])
        self.assertTrue(json.loads(records[PROCESS + "-stopped.json"])["verifiedStopped"])
