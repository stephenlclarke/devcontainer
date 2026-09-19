"""Candidate D01 reuses strict reference observations with owned native commands."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from case_evidence import canonical
from devcontainer_candidate import (CandidateCommands, DevcontainerCandidate, DevcontainerBuildCandidate,
                                   DevcontainerUsersCandidate, DevcontainerLifecycleCandidate)
from devcontainer_reference import IMAGE, WORKSPACE, created_id
from guest_fixture import OWNER_LABEL
from guest_runtime import ReleasedGuest, require_guest_cleanup
from host_runtime import deadline
from service_journal import ServiceJournal
import test_devcontainer_reference as reference_tests
import test_devcontainer_build_reference as build_reference_tests
import test_devcontainer_users_reference as users_reference_tests
import test_devcontainer_lifecycle_reference as lifecycle_reference_tests


class CandidateTests(reference_tests.ReferenceTests):
    def reopen(self):
        self.vm.container = "/prepared/container"
        self.vm.close = Mock()
        self.vm.prepare_cleanup = Mock()
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

    def test_real_guest_cleanup_owns_only_one_deadline(self):
        self.start()
        guest = ReleasedGuest(self.inputs, "D01-image-config", self.root, self.owner,
                              Mock(journal=self.vm.journal), "/usr/bin/true", self.vm.socket)
        guest.guest = self.fixture
        guest.cleanup()
        self.assertIsNone(self.server.guest)
        self.vm.close.assert_called_once()

    def test_slow_cleanup_response_obeys_whole_phase_deadline(self):
        self.start()
        self.server.drip = True
        guest = ReleasedGuest(self.inputs, "D01-image-config", self.root, self.owner,
                              Mock(journal=self.vm.journal), "/usr/bin/true", self.vm.socket)
        guest.guest = self.fixture

        def short_bound(seconds):
            self.assertEqual(seconds, 45)
            return deadline(0.08)

        started = time.monotonic()
        with patch("guest_runtime.deadline", side_effect=short_bound), self.assertRaises(TimeoutError):
            guest.cleanup()
        self.assertLess(time.monotonic() - started, 1)
        self.assertIsNotNone(self.server.guest)
        self.assertNotIn("devcontainer-removed.json", self.vm.journal.records())

    def test_exec_log_failure_is_reconciled_before_guest_cleanup(self):
        self.fixture.setup()
        runner = CandidateCommands(self.root, self.vm.socket, Mock(journal=self.vm.journal), "/usr/bin/true")
        original = self.vm.command

        def command(name, arguments, **kwargs):
            if name != "devcontainer-exec":
                result = original(name, arguments, **kwargs)
                self.vm.journal.put(name + "-stopped.json", canonical({"verifiedStopped": True}))
                return result
            with patch("devcontainer_candidate.diagnostic_snapshot", side_effect=OSError("retention failure")):
                return runner.command(name, ["/usr/bin/true"], timeout=2)

        self.vm.journal.put("devcontainer-image-pull-stopped.json", canonical({"verifiedStopped": True}))
        self.vm.command = command
        with self.assertRaisesRegex(OSError, "retention failure"):
            self.fixture.operation()
        self.assertTrue(runner.uncertain)
        self.fixture.vm = runner
        self.fixture.cleanup()
        self.assertIsNone(self.server.guest)
        self.assertFalse(runner.uncertain)
        self.assertFalse(runner.pending_logs)


class BuildCandidateTests(build_reference_tests.BuildReferenceTests):
    test_slow_cleanup_response_obeys_whole_phase_deadline = CandidateTests.test_slow_cleanup_response_obeys_whole_phase_deadline

    def reopen(self):
        super().reopen()
        self.vm.container = "/prepared/container"
        self.vm.close, self.vm.prepare_cleanup = Mock(), Mock()
        self.inputs["devcontainerCandidate"] = {"executables": {"devcontainer": "/prepared/devcontainer"}}
        self.before_build = Mock()
        return DevcontainerBuildCandidate(self.vm, self.inputs, self.owner, before_build=self.before_build)

    def test_full_contract_uses_exact_pins_isolated_paths_and_verified_cleanup(self):
        self.assertEqual(self.start(), {"build_arg": "from-devcontainer", "build_target": "development",
                                       "post_create": "dockerfile-post-create", "workspace": WORKSPACE})
        self.before_build.assert_called_once()
        self.assertEqual(self.commands[0][1], ["/prepared/container", "image", "pull", "--arch", "arm64", IMAGE])
        self.assertEqual([item[2] for item in self.commands], [120, 240, 60])
        for _, arguments, _ in self.commands[1:]:
            self.assertEqual(arguments[2], "/prepared/devcontainer")
            self.assertNotIn("--docker-path", arguments)
        self.assertIn("--buildkit", self.commands[1][1])
        self.assertNotIn("--buildkit", self.commands[2][1])
        self.fixture.cleanup()
        self.vm.close.assert_called_once()
        self.assertIsNone(self.server.guest)

    def test_changed_builder_prevents_up_and_preserves_cleanup(self):
        self.fixture.setup()
        self.before_build.side_effect = ValueError("builder changed")
        with self.assertRaisesRegex(ValueError, "builder changed"):
            self.fixture.operation()
        self.assertEqual([item[0] for item in self.commands], ["devcontainer-image-pull"])
        self.fixture.cleanup()
        self.vm.close.assert_called_once()

    def test_build_candidate_preserves_native_uuid(self):
        identifier = "ABCDEF01-1234-5678-abcd-123456789012"
        with patch.object(reference_tests, "ID", identifier):
            self.start()
            self.assertEqual(self.fixture.identifier, identifier)
            self.fixture.cleanup()


class UsersCandidateTests(users_reference_tests.UsersReferenceTests):
    test_slow_cleanup_response_obeys_whole_phase_deadline = CandidateTests.test_slow_cleanup_response_obeys_whole_phase_deadline
    test_changed_builder_prevents_up_and_preserves_cleanup = BuildCandidateTests.test_changed_builder_prevents_up_and_preserves_cleanup
    test_build_candidate_preserves_native_uuid = BuildCandidateTests.test_build_candidate_preserves_native_uuid

    def reopen(self):
        super().reopen()
        self.vm.container = "/prepared/container"
        self.vm.close, self.vm.prepare_cleanup = Mock(), Mock()
        self.inputs["devcontainerCandidate"] = {"executables": {"devcontainer": "/prepared/devcontainer"}}
        self.before_build = Mock()
        return DevcontainerUsersCandidate(self.vm, self.inputs, self.owner, before_build=self.before_build)

    def test_full_contract_uses_exact_pins_isolated_paths_and_verified_cleanup(self):
        self.assertEqual(self.start(), users_reference_tests.EXPECTED)
        self.before_build.assert_called_once()
        self.assertEqual(self.commands[0][1], ["/prepared/container", "image", "pull", "--arch", "arm64", IMAGE])
        self.assertEqual([item[2] for item in self.commands], [120, 240, 60])
        for _, arguments, _ in self.commands[1:]:
            self.assertEqual(arguments[2], "/prepared/devcontainer")
            self.assertNotIn("--docker-path", arguments)
        self.assertIn("--buildkit", self.commands[1][1])
        self.assertEqual(self.fixture.workspace.name, "D03-users-environment")
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.fixture.workspace.stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.fixture.workspace / "probe.sh").stat().st_mode & 0o777, 0o644)
        self.fixture.cleanup()
        self.vm.close.assert_called_once()
        self.assertIsNone(self.server.guest)


class LifecycleCandidateTests(lifecycle_reference_tests.LifecycleReferenceTests):
    test_slow_cleanup_response_obeys_whole_phase_deadline = CandidateTests.test_slow_cleanup_response_obeys_whole_phase_deadline
    test_native_uuid_is_preserved_not_normalized = CandidateTests.test_native_uuid_is_preserved_not_normalized

    def reopen(self):
        super().reopen()
        self.vm.container = "/prepared/container"
        self.vm.close, self.vm.prepare_cleanup = Mock(), Mock()
        self.inputs["devcontainerCandidate"] = {"executables": {"devcontainer": "/prepared/devcontainer"}}
        return DevcontainerLifecycleCandidate(self.vm, self.inputs, self.owner)

    def test_full_contract_uses_exact_pins_isolated_paths_and_verified_cleanup(self):
        self.assertEqual(self.start(), lifecycle_reference_tests.EXPECTED)
        self.assertEqual(self.commands[0][1], ["/prepared/container", "image", "pull", "--arch", "arm64", IMAGE])
        self.assertEqual([item[2] for item in self.commands], [120, 120, 60])
        for _, arguments, _ in self.commands[1:]:
            self.assertEqual(arguments[2], "/prepared/devcontainer")
            self.assertNotIn("--docker-path", arguments)
            self.assertNotIn("--buildkit", arguments)
        self.assertEqual(self.fixture.workspace.name, "D04-lifecycle-hooks")
        self.assertFalse((self.fixture.workspace / ".lifecycle-host").exists())
        self.fixture.cleanup()
        self.vm.close.assert_called_once()
        self.assertIsNone(self.server.guest)


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
        self.runner.close()
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
        self.runner.close()
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
        self.assertFalse(self.runner.uncertain)
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
        self.runner.close()
        records = self.journal.records()
        records["devcontainer-plan.json"] = b"{}"
        with self.assertRaisesRegex(ValueError, "D01 resources"):
            require_guest_cleanup(records)
        records["devcontainer-removed.json"] = b'{"verifiedAbsent":true}'
        require_guest_cleanup(records)
        del records["devcontainer-up-stderr.log"]
        with self.assertRaisesRegex(ValueError, "diagnostics"):
            require_guest_cleanup(records)

    def test_foreground_attachment_outlives_up_and_closes_after_resource_removal(self):
        marker = self.root / "resource-present"
        marker.touch()
        helper = "from pathlib import Path; import sys,time; p=Path(sys.argv[1]);\nwhile p.exists(): time.sleep(.01)"
        parent = "import subprocess,sys; subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]); print('created')"
        self.addCleanup(marker.unlink, missing_ok=True)
        for name in ("devcontainer-up", "devcontainer-reuse", "devcontainer-rebuild"):
            self.assertEqual(self.runner.command(name, [sys.executable, "-c", parent, helper, str(marker)],
                                                 timeout=2), b"created\n")
            self.assertNotIn(name + "-stopped.json", self.journal.records())
        self.assertFalse(self.runner.uncertain)
        removal = threading.Timer(0.05, lambda: marker.unlink(missing_ok=True))
        removal.start()
        try:
            self.runner.close()
        finally:
            removal.join()
        for name in ("devcontainer-up", "devcontainer-reuse", "devcontainer-rebuild"):
            self.assertIn(name + "-stopped.json", self.journal.records())
            self.assertEqual(self.journal.records()[name + ".log"], b"created\n")
        self.assertFalse(self.runner.pending_logs)


if __name__ == "__main__":
    unittest.main()
