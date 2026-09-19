"""Candidate D01 reuses strict reference observations with owned native commands."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from case_evidence import canonical
from devcontainer_candidate import CandidateCommands, DevcontainerCandidate
from devcontainer_reference import IMAGE, WORKSPACE, created_id
from guest_fixture import OWNER_LABEL
from guest_runtime import require_guest_cleanup
from service_journal import ServiceJournal
import test_devcontainer_reference as reference_tests


class CandidateTests(reference_tests.ReferenceTests):
    def reopen(self):
        self.vm.container = "/prepared/container"
        self.vm.close = Mock()
        self.inputs["devcontainerCandidate"] = {"executables": {"devcontainer": "/prepared/devcontainer"}}
        return DevcontainerCandidate(self.vm, self.inputs, self.owner)

    def test_full_contract_uses_exact_pins_isolated_paths_and_verified_cleanup(self):
        self.assertEqual(self.start(), {"environment": "image-config", "workspace": WORKSPACE,
                                       "post_create": "post-create", "uid": "0"})
        self.assertEqual(self.commands[0][1], ["/prepared/container", "image", "pull", "--arch", "arm64", IMAGE])
        self.assertEqual([item[2] for item in self.commands], [120, 120, 60])
        for _, args, _ in self.commands[1:]:
            self.assertEqual(args[:3], ["/usr/bin/env", "DOCKER_HOST=unix://" + str(self.vm.socket), "/prepared/devcontainer"])
            self.assertNotIn("--docker-path", args)
            self.assertIn(OWNER_LABEL + "=" + self.fixture.owner, args)
        self.fixture.cleanup()
        self.vm.close.assert_called_once()
        self.assertIsNone(self.server.guest)

    def test_native_uuid_is_preserved_not_normalized(self):
        identifier = "ABCDEF01-1234-5678-abcd-123456789012"
        with patch.object(reference_tests, "ID", identifier):
            self.start()
            self.assertEqual(self.fixture.identifier, identifier)
            self.assertEqual(self.fixture.recovery_plan(), identifier)
            self.fixture.cleanup()
        with self.assertRaisesRegex(ValueError, "immutable"):
            created_id(canonical({"outcome": "success", "containerId": identifier}))


class CandidateCommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.journal = ServiceJournal(self.root / "journal.sqlite", {"root": str(self.root)}, create=True)
        self.runtime = Mock(journal=self.journal)
        self.runner = CandidateCommands(self.root, self.root / "socket", self.runtime, "/usr/bin/true")

    def test_real_child_preserves_streams_environment_and_durable_shutdown(self):
        script = 'printf "%s\\n" "$CONTAINER_APP_ROOT"; printf "diagnostic" >&2'
        with patch.dict(os.environ, NODE_OPTIONS="must-not-leak", DOCKER_HOST="must-not-leak"):
            output = self.runner.command("devcontainer-up", ["/bin/sh", "-c", script], timeout=2, separate_output=True)
        self.assertEqual(output, (str(self.root / "container") + "\n").encode())
        records = self.journal.records()
        self.assertEqual(records["devcontainer-up-stderr.log"], b"diagnostic")
        self.assertEqual(json.loads(records["devcontainer-up-exit.json"])["code"], 0)
        self.assertGreater(json.loads(records["devcontainer-up-exit.json"])["durationNS"], 0)
        self.assertFalse(self.runner.uncertain)
        self.runner.close()
        require_guest_cleanup(records)
        with self.assertRaisesRegex(ValueError, "repeated"):
            self.runner.command("devcontainer-up", ["/usr/bin/false"])

    def test_nonzero_result_keeps_diagnostics_and_known_process_completion(self):
        with self.assertRaisesRegex(RuntimeError, "failed"):
            self.runner.command("devcontainer-up", ["/bin/sh", "-c", "printf failure >&2; exit 7"], timeout=2)
        records = self.journal.records()
        self.assertEqual(json.loads(records["devcontainer-up-exit.json"])["code"], 7)
        self.assertEqual(records["devcontainer-up-stderr.log"], b"failure")
        self.assertFalse(self.runner.uncertain)
        self.runner.close()

    def test_timeout_stops_child_but_never_invents_command_outcome(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.runner.command("devcontainer-up", ["/bin/sleep", "10"], timeout=0.03)
        self.runner.close()
        self.assertTrue(self.runner.uncertain)
        records = self.journal.records()
        self.assertIn("devcontainer-up-stopped.json", records)
        self.assertNotIn("devcontainer-up-exit.json", records)
        self.assertFalse(self.runner.pending_logs)

    def test_oversized_stream_is_not_used_as_successful_result(self):
        with self.assertRaisesRegex(ValueError, "exceeded"):
            self.runner.command("devcontainer-exec", ["/usr/bin/head", "-c", "1048577", "/dev/zero"], timeout=2)
        self.assertTrue(json.loads(self.journal.records()["devcontainer-exec-log.json"])["truncated"])
        self.assertEqual(len(self.journal.records()["devcontainer-exec.log"]), 1024**2)

    def test_transient_log_failure_can_finish_retention_without_reexecution(self):
        with patch("devcontainer_candidate.diagnostic_snapshot", side_effect=OSError("retention failure")), \
                self.assertRaisesRegex(OSError, "retention"):
            self.runner.command("devcontainer-up", ["/usr/bin/true"], timeout=2)
        self.assertTrue(self.runner.uncertain)
        self.runner.close()
        self.assertFalse(self.runner.uncertain)
        self.assertFalse(self.runner.pending_logs)
        require_guest_cleanup(self.journal.records())

    def test_pending_spawn_and_failed_stop_cannot_record_safe_shutdown(self):
        with patch("devcontainer_candidate.OwnedProcess") as owned:
            owned.return_value.start.side_effect = KeyboardInterrupt()
            owned.return_value.stop.side_effect = RuntimeError("unknown child")
            with self.assertRaisesRegex(ValueError, "shutdown"):
                self.runner.command("devcontainer-up", ["/usr/bin/true"], timeout=2)
        self.assertTrue(self.runner.uncertain)
        self.assertNotIn("devcontainer-up-stopped.json", self.journal.records())

    def test_recovery_requires_workload_closure_and_both_diagnostics(self):
        self.runner.command("devcontainer-up", ["/usr/bin/true"], timeout=2)
        records = self.journal.records()
        records["devcontainer-plan.json"] = b"{}"
        with self.assertRaisesRegex(ValueError, "D01 resources"):
            require_guest_cleanup(records)
        records["devcontainer-removed.json"] = b'{"verifiedAbsent":true}'
        require_guest_cleanup(records)
        del records["devcontainer-up-stderr.log"]
        with self.assertRaisesRegex(ValueError, "diagnostics"):
            require_guest_cleanup(records)


if __name__ == "__main__":
    unittest.main()
