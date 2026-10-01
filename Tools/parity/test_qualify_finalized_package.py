#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# SPDX-License-Identifier: Apache-2.0
"""Inert lifecycle and sealing-boundary tests for final-package qualification."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import qualify_finalized_package as qualify


class SuiteLifecycleTests(unittest.TestCase):
    def test_nonzero_cli_with_complete_cleanup_still_runs_vscode(self) -> None:
        calls = []
        cli = subprocess.CompletedProcess(["cli"], 1, "", "fixture failed")
        vscode = subprocess.CompletedProcess(["vscode"], 0, "", "")

        actual_cli, actual_vscode, passed = qualify.run_suite_pair(
            lambda: (calls.append("cli"), cli)[1],
            lambda: (calls.append("vscode"), vscode)[1],
            lambda: True, lambda: True)

        self.assertEqual(calls, ["cli", "vscode"])
        self.assertIs(actual_cli, cli)
        self.assertIs(actual_vscode, vscode)
        self.assertFalse(passed)

    def test_incomplete_cli_cleanup_prevents_vscode(self) -> None:
        calls = []
        with self.assertRaisesRegex(RuntimeError, "CLI cleanup is incomplete"):
            qualify.run_suite_pair(
                lambda: (calls.append("cli"), subprocess.CompletedProcess(["cli"], 1))[1],
                lambda: calls.append("vscode"), lambda: False, lambda: True)
        self.assertEqual(calls, ["cli"])

    def test_docker_profile_stays_running_when_suite_cleanup_is_uncertain(self) -> None:
        self.assertFalse(qualify.docker_stop_allowed(
            started_here=True, suites_started=True, suite_cleanup_complete=False))
        self.assertFalse(qualify.docker_restore_is_safe(
            started_here=True, suites_started=True, suite_cleanup_complete=False,
            initial="stopped", final="running"))

    def test_docker_can_stop_owned_profile_before_any_suite_started(self) -> None:
        self.assertTrue(qualify.docker_stop_allowed(
            started_here=True, suites_started=False, suite_cleanup_complete=False))
        self.assertTrue(qualify.docker_restore_is_safe(
            started_here=True, suites_started=False, suite_cleanup_complete=False,
            initial="stopped", final="stopped"))

    def test_host_restoration_rejects_partial_lane_or_uncleared_guard(self) -> None:
        cleanup = {lane: {"status": "restored"} for lane in qualify.LANES}
        with self.assertRaisesRegex(RuntimeError, "guard"):
            qualify.require_host_restoration(cleanup, "stopped", "stopped", False)
        cleanup["container-compose"]["status"] = "uncertain"
        with self.assertRaisesRegex(RuntimeError, "lanes"):
            qualify.require_host_restoration(cleanup, "stopped", "stopped", True)

    def test_cancelled_campaign_clears_guard_only_after_mutated_lanes_restore(self) -> None:
        cleanup = {"docker": {"status": "restored"}, "apple-stock": {"status": "restored"},
                   "container-compose": {"status": "not-started"}}
        self.assertTrue(qualify.host_can_clear_guard(cleanup, "stopped", "stopped", "same", "same"))
        # Restoration permits another campaign; it cannot qualify absent suites.
        with self.assertRaisesRegex(RuntimeError, "lanes"):
            qualify.require_host_restoration(cleanup, "stopped", "stopped", True)

    def test_guard_remains_when_any_started_lane_or_host_identity_is_uncertain(self) -> None:
        cleanup = {lane: {"status": "restored"} for lane in qualify.LANES}
        for status in ("active", "uncertain", None):
            with self.subTest(status=status):
                cleanup["apple-stock"]["status"] = status
                self.assertFalse(qualify.host_can_clear_guard(
                    cleanup, "stopped", "stopped", "same", "same"))
        cleanup["apple-stock"]["status"] = "restored"
        self.assertFalse(qualify.host_can_clear_guard(cleanup, "stopped", "running", "same", "same"))
        self.assertFalse(qualify.host_can_clear_guard(cleanup, "stopped", "stopped", "before", "after"))


class TimeoutOwnershipTests(unittest.TestCase):
    def test_timeout_sends_term_then_kill_to_the_new_session_process_group(self) -> None:
        process = mock.Mock()
        process.pid = 9123
        process.stdout = None
        process.stderr = None
        process.wait.side_effect = [subprocess.TimeoutExpired(["runner"], 1), None]
        with (mock.patch.object(qualify.os, "killpg") as killpg,
              mock.patch.object(qualify, "process_group_exists", side_effect=[True, False, False, False])):
            qualify.terminate_process_group(process, grace=0.01, reap_grace=0.01)
        self.assertEqual(killpg.call_args_list, [
            mock.call(9123, signal.SIGTERM), mock.call(9123, signal.SIGKILL)])

    def test_timeout_with_surviving_descendant_group_is_not_reported_clean(self) -> None:
        process = mock.Mock()
        process.pid = 8123
        process.stdout = None
        process.stderr = None
        process.wait.side_effect = [None]
        with (mock.patch.object(qualify.os, "killpg"),
              mock.patch.object(qualify, "process_group_exists", return_value=True),
              self.assertRaisesRegex(RuntimeError, "did not disappear")):
            qualify.terminate_process_group(process, grace=0, reap_grace=0)

    def test_timed_out_command_uses_new_session_and_retains_logs_without_success_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            process = mock.Mock()
            process.pid = 1234
            process.returncode = -15
            process.wait.side_effect = subprocess.TimeoutExpired(["runner"], 1)
            with (mock.patch.object(qualify.subprocess, "Popen", return_value=process) as popen,
                  mock.patch.object(qualify, "terminate_process_group", side_effect=RuntimeError("group remains")),
                  self.assertRaisesRegex(RuntimeError, "group remains")):
                qualify.command_outcome(["runner"], env={}, timeout=1, capture_directory=root / "logs")
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            self.assertTrue((root / "logs/stdout.log").is_file())
            self.assertFalse((root / "logs/controller-command.json").exists())

    def test_sigterm_is_converted_to_catchable_cleanup_exception_and_restored(self) -> None:
        path = Path(__file__).resolve().parents[2] / "Tools/testing/host_runtime.py"
        sys.path.insert(0, str(path.parent))
        spec = importlib.util.spec_from_file_location("qualification_host_runtime", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)
        previous = signal.getsignal(signal.SIGTERM)
        with runtime.cancellation():
            handler = signal.getsignal(signal.SIGTERM)
            with self.assertRaises(KeyboardInterrupt):
                handler(signal.SIGTERM, None)
        self.assertIs(signal.getsignal(signal.SIGTERM), previous)


class AdmissionBoundaryTests(unittest.TestCase):
    def test_package_admission_failure_happens_before_evidence_or_runtime_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            app = root / "VS Code.app"
            code = app / "Contents/MacOS/Code"
            code.parent.mkdir(parents=True)
            code.write_text("launcher")
            vsix = root / "extension.vsix"
            vsix.write_bytes(b"fixture")
            evidence = root / "new-evidence"
            values = argparse.Namespace(
                repository=root, ssd_root=root, retained_root=root,
                qualification_directory=root, evidence=evidence, campaign="test",
                source_commit="a" * 40, finalized_directory=root,
                provenance_sha256="b" * 64, state_sha256="c" * 64,
                accepted_state=root, docker_bin=root, docker_compose_bin=root,
                stock_container_bin=root, compose_container_bin=root,
                compose_provider_bin=root, colima_bin=root, vscode_bin=root,
                vscode_app=app, vscode_vsix=vsix, stock_container_sha256="d" * 64,
                stock_api_sha256="e" * 64, compose_container_sha256="f" * 64,
                compose_api_sha256="0" * 64, compose_provider_sha256="1" * 64,
                colima_sha256="2" * 64, execute=True,
            )
            manifest = {"referencePins": {"vscode": {"devContainersExtension": {"vsixSHA256": "3" * 64}}}}
            with (mock.patch.object(qualify, "parse_args", return_value=values),
                  mock.patch.object(qualify, "validate_inputs", return_value={}),
                  mock.patch.object(qualify, "sha256", return_value="3" * 64),
                  mock.patch.object(qualify, "admit_package_before_runtime",
                                    side_effect=ValueError("package rejected")),
                  mock.patch.object(qualify.subprocess, "check_output") as host_command,
                  self.assertRaisesRegex(ValueError, "package rejected")):
                values._manifest = manifest
                qualify.main()
            self.assertFalse(evidence.exists())
            host_command.assert_not_called()


class ProviderPinTests(unittest.TestCase):
    def test_stopped_colima_needs_no_docker_context_or_existing_socket(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            profile = home / ".colima/default"
            profile.mkdir(parents=True)
            with mock.patch.object(qualify, "run") as command:
                endpoint = qualify.default_colima_endpoint(home)
            self.assertEqual(endpoint, "unix://" + str(profile / "docker.sock"))
            self.assertFalse((profile / "docker.sock").exists())
            self.assertFalse((home / ".docker/contexts").exists())
            command.assert_not_called()

    def test_colima_endpoint_rejects_aliased_or_writable_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            parent = home / ".colima"
            parent.mkdir()
            target = home / "other-profile"
            target.mkdir()
            profile = parent / "default"
            profile.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "canonical"):
                qualify.default_colima_endpoint(home)
            profile.unlink()
            profile.mkdir(mode=0o777)
            profile.chmod(0o777)
            with self.assertRaisesRegex(ValueError, "protected"):
                qualify.default_colima_endpoint(home)

    def test_provider_versions_and_commits_must_appear_in_actual_json_output(self) -> None:
        pin = {"stableVersion": "1.2.3", "stableCommit": "a" * 40}
        self.assertTrue(qualify.output_contains_pin(
            json.dumps({"version": "1.2.3", "commit": "a" * 40}), pin))
        self.assertFalse(qualify.output_contains_pin(
            json.dumps({"version": "1.2.4", "commit": "a" * 40}), pin))


if __name__ == "__main__":
    unittest.main()
