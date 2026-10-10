#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
from pathlib import Path
import io
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("recover_notary_profile.py")
SPEC = importlib.util.spec_from_file_location("recover_notary_profile", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


class RecoveryContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = {
            "GITHUB_REPOSITORY": "stephenlclarke/devcontainer",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_SHA": "a" * 40,
            "RECOVERY_EXPECTED_CONTROL_SHA": "a" * 40,
            "RECOVERY_PROFILE": "container-only-unattended",
            "RUNNER_NAME": "devcontainer-release-StevesM5Pro",
            "DEVCONTAINER_NOTARY_APPLE_ID": "user@example.test",
            "DEVCONTAINER_NOTARY_TEAM_ID": "4MEB7MUTAV",
            "DEVCONTAINER_NOTARY_PASSWORD": "test-app-password",
        }

    def validate(self, environment: dict[str, str]) -> None:
        recovery.validate_context(
            environment,
            username="sclarke",
            uid=501,
            hostname="StevesM5Pro.local",
            home=Path("/Users/sclarke"),
            keychain_owner_uid=501,
        )

    def test_accepts_only_exact_repository_main_control_and_host(self) -> None:
        self.validate(self.environment)
        for name, value in (
            ("GITHUB_REPOSITORY", "other/repository"),
            ("GITHUB_REF", "refs/heads/feature"),
            ("GITHUB_SHA", "not-a-sha"),
            ("RECOVERY_EXPECTED_CONTROL_SHA", "b" * 40),
            ("RECOVERY_PROFILE", "container-only-release"),
            ("RUNNER_NAME", "another-runner"),
        ):
            with self.subTest(name=name):
                altered = dict(self.environment, **{name: value})
                with self.assertRaises(recovery.RecoveryError):
                    self.validate(altered)

    def test_rejects_unexpected_account_uid_or_machine(self) -> None:
        for username, uid, hostname, home, owner in (
            ("other", 501, "StevesM5Pro", Path("/Users/sclarke"), 501),
            ("sclarke", 502, "StevesM5Pro", Path("/Users/sclarke"), 501),
            ("sclarke", 501, "other-host", Path("/Users/sclarke"), 501),
            ("sclarke", 501, "StevesM5Pro", Path("/Users/other"), 501),
            ("sclarke", 501, "StevesM5Pro", Path("/Users/sclarke"), 502),
        ):
            with self.subTest(uid=uid, hostname=hostname, owner=owner):
                with self.assertRaises(recovery.RecoveryError):
                    recovery.validate_context(
                        self.environment,
                        username=username,
                        uid=uid,
                        hostname=hostname,
                        home=home,
                        keychain_owner_uid=owner,
                    )

    def test_rejects_missing_or_partial_secrets_without_echoing_values(self) -> None:
        for names in (
            (),
            ("DEVCONTAINER_NOTARY_APPLE_ID",),
            (
                "DEVCONTAINER_NOTARY_APPLE_ID",
                "DEVCONTAINER_NOTARY_TEAM_ID",
            ),
        ):
            with self.subTest(names=names):
                altered = dict(self.environment)
                for name in recovery.NOTARY_SECRETS:
                    altered.pop(name, None)
                for name in names:
                    altered[name] = "private-value"
                with self.assertRaises(recovery.RecoveryError) as caught:
                    self.validate(altered)
                self.assertNotIn("private-value", str(caught.exception))

    def test_rejects_unexpected_notary_team(self) -> None:
        altered = dict(self.environment, DEVCONTAINER_NOTARY_TEAM_ID="WRONGTEAM")
        with self.assertRaisesRegex(recovery.RecoveryError, "signing authority"):
            self.validate(altered)


class RecoveryCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = {
            "GITHUB_REPOSITORY": "stephenlclarke/devcontainer",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_SHA": "a" * 40,
            "RECOVERY_EXPECTED_CONTROL_SHA": "a" * 40,
            "RECOVERY_PROFILE": "container-only-unattended",
            "RUNNER_NAME": "devcontainer-release-StevesM5Pro",
            "DEVCONTAINER_NOTARY_APPLE_ID": "user@example.test",
            "DEVCONTAINER_NOTARY_TEAM_ID": "4MEB7MUTAV",
            "DEVCONTAINER_NOTARY_PASSWORD": "test-app-password",
        }
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.keychain = Path(self.directory.name) / "login.keychain-db"
        self.keychain.touch()

    def run_recovery(self, runner):
        with patch.object(recovery, "validate_context"):
            return recovery.recover_profile(
                self.environment,
                command_runner=runner,
                keychain=self.keychain,
            )

    def test_stores_then_histories_fixed_profile_without_exposing_output(self) -> None:
        calls: list[tuple[str, ...]] = []
        secret_output = b"test-app-password user@example.test 4MEB7MUTAV"

        def runner(arguments, timeout):
            calls.append(tuple(arguments))
            if (
                arguments[0].endswith("/security")
                and arguments[1] == "default-keychain"
            ):
                return subprocess.CompletedProcess(
                    arguments, 0, b'"' + str(self.keychain).encode() + b'"\n', b""
                )
            if arguments[2] == "store-credentials":
                self.assertEqual(timeout, 60)
                self.assertIn("--validate", arguments)
                self.assertIn("--keychain", arguments)
                return subprocess.CompletedProcess(
                    arguments, 0, secret_output, secret_output
                )
            return subprocess.CompletedProcess(
                arguments, 0, b'{"history": []}', secret_output
            )

        self.assertIsNone(self.run_recovery(runner))
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[1][3], "container-only-unattended")
        self.assertIn(str(self.keychain), calls[1])
        self.assertEqual(calls[2][2:4], ("history", "--keychain-profile"))

    def test_validation_failure_stops_before_history_and_suppresses_output(
        self,
    ) -> None:
        calls: list[tuple[str, ...]] = []
        secret_output = b"test-app-password invalid credential payload"

        def runner(arguments, timeout):
            calls.append(tuple(arguments))
            if (
                arguments[0].endswith("/security")
                and arguments[1] == "default-keychain"
            ):
                return subprocess.CompletedProcess(
                    arguments, 0, b'"' + str(self.keychain).encode() + b'"\n', b""
                )
            return subprocess.CompletedProcess(
                arguments, 65, secret_output, secret_output
            )

        with self.assertRaises(recovery.RecoveryError) as caught:
            self.run_recovery(runner)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("test-app-password", str(caught.exception))
        self.assertNotIn("invalid credential payload", str(caught.exception))

    def test_history_must_succeed_with_json_history_array(self) -> None:
        calls: list[tuple[str, ...]] = []

        def runner(arguments, timeout):
            calls.append(tuple(arguments))
            if (
                arguments[0].endswith("/security")
                and arguments[1] == "default-keychain"
            ):
                return subprocess.CompletedProcess(
                    arguments, 0, b'"' + str(self.keychain).encode() + b'"\n', b""
                )
            if arguments[2] == "store-credentials":
                return subprocess.CompletedProcess(arguments, 0, b"", b"")
            return subprocess.CompletedProcess(arguments, 0, b'{"items": []}', b"")

        with self.assertRaisesRegex(
            recovery.RecoveryError, "history validation failed"
        ):
            self.run_recovery(runner)

    def test_default_keychain_mismatch_stops_before_credentials(self) -> None:
        calls: list[tuple[str, ...]] = []

        def runner(arguments, timeout):
            calls.append(tuple(arguments))
            return subprocess.CompletedProcess(
                arguments, 0, b'"/tmp/other.keychain-db"', b""
            )

        with self.assertRaisesRegex(recovery.RecoveryError, "default keychain"):
            self.run_recovery(runner)
        self.assertEqual(len(calls), 1)

    def test_wrong_team_stops_before_keychain_commands(self) -> None:
        environment = dict(
            self.environment, DEVCONTAINER_NOTARY_TEAM_ID="WRONGTEAM"
        )
        calls = []
        with self.assertRaisesRegex(recovery.RecoveryError, "signing authority"):
            recovery.recover_profile(
                environment,
                command_runner=lambda *args: calls.append(args),
                keychain=self.keychain,
            )
        self.assertEqual(calls, [])

    def test_missing_and_symlink_keychains_fail_before_commands(self) -> None:
        calls = []
        missing = self.keychain.with_name("missing.keychain-db")
        with self.assertRaisesRegex(recovery.RecoveryError, "unavailable"):
            recovery.recover_profile(
                self.environment,
                command_runner=lambda *args: calls.append(args),
                keychain=missing,
            )
        symlink = self.keychain.with_name("symlink.keychain-db")
        symlink.symlink_to(self.keychain)
        with self.assertRaisesRegex(recovery.RecoveryError, "not a regular file"):
            recovery.recover_profile(
                self.environment,
                command_runner=lambda *args: calls.append(args),
                keychain=symlink,
            )
        self.assertEqual(calls, [])

    def test_invalid_utf8_default_keychain_output_fails_closed(self) -> None:
        calls = []

        def runner(arguments, timeout):
            calls.append(tuple(arguments))
            return subprocess.CompletedProcess(arguments, 0, b"\xff", b"")

        with self.assertRaisesRegex(recovery.RecoveryError, "identify"):
            self.run_recovery(runner)
        self.assertEqual(len(calls), 1)

    def test_security_store_and_history_deadlines_and_launch_errors_are_suppressed(
        self,
    ) -> None:
        secret = self.environment[recovery.NOTARY_SECRETS[2]]
        for phase in ("security", "store", "history"):
            for failure in (
                OSError(secret),
                subprocess.TimeoutExpired("notarytool", 60, output=secret),
            ):
                with self.subTest(phase=phase, failure=type(failure).__name__):
                    calls = []

                    def runner(arguments, timeout):
                        calls.append(tuple(arguments))
                        if arguments[0].endswith("/security"):
                            if phase == "security":
                                raise failure
                            return subprocess.CompletedProcess(
                                arguments,
                                0,
                                b'"' + str(self.keychain).encode() + b'"\n',
                                b"",
                            )
                        if arguments[2] == "store-credentials":
                            if phase == "store":
                                raise failure
                            return subprocess.CompletedProcess(arguments, 0, b"", b"")
                        if phase == "history":
                            raise failure
                        return subprocess.CompletedProcess(
                            arguments, 0, b'{"history": []}', b""
                        )

                    with self.assertRaises(recovery.RecoveryError) as caught:
                        self.run_recovery(runner)
                    self.assertNotIn(secret, str(caught.exception))
                    self.assertEqual(
                        len(calls), {"security": 1, "store": 2, "history": 3}[phase]
                    )

    @patch.object(recovery.subprocess, "run")
    def test_command_runner_closes_stdin_captures_output_and_times_out(
        self, run
    ) -> None:
        run.return_value = subprocess.CompletedProcess(("tool",), 0, b"", b"")

        recovery._run_command(("tool",), 17)

        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs["timeout"], 17)
        self.assertEqual(run.call_args.kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.PIPE)


class RecoveryWorkflowTests(unittest.TestCase):
    def test_workflow_is_manual_narrow_and_has_no_release_side_effects(self) -> None:
        workflow = (
            Path(__file__).parents[2]
            / ".github"
            / "workflows"
            / "recover-notary-profile.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("inputs.expected_control_sha == github.sha", workflow)
        self.assertIn("environment: release", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("devcontainer-release-StevesM5Pro", workflow)
        self.assertIn("devcontainer-designated-mbp", workflow)
        self.assertIn(
            "RECOVERY_PROFILE: ${{ vars.DEVCONTAINER_NOTARY_PROFILE }}", workflow
        )
        self.assertIn("secrets.DEVCONTAINER_NOTARY_APPLE_ID", workflow)
        self.assertIn("secrets.DEVCONTAINER_NOTARY_TEAM_ID", workflow)
        self.assertIn("secrets.DEVCONTAINER_NOTARY_PASSWORD", workflow)
        credential_step = workflow[workflow.index("      - name: Store and validate") :]
        for variable in (
            "GITHUB_REPOSITORY:",
            "GITHUB_REF:",
            "GITHUB_SHA:",
            "RUNNER_NAME:",
        ):
            self.assertNotIn(variable, credential_step)
        for forbidden in (
            "notarytool submit",
            "security unlock-keychain",
            "upload-artifact",
            "codesign",
            "make package",
            "run: gh ",
        ):
            self.assertNotIn(forbidden, workflow)
        helper = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"store-credentials"', helper)
        self.assertIn('"history"', helper)
        self.assertNotIn("unlock-keychain", helper)
        self.assertNotIn('"submit"', helper)

    @patch.object(recovery, "recover_profile", side_effect=RuntimeError("private-value"))
    def test_unexpected_errors_are_suppressed(self, recover_profile) -> None:
        with patch.object(recovery.sys, "stderr") as stderr:
            self.assertEqual(recovery.main(), 1)
        self.assertNotIn("private-value", str(stderr.write.call_args))

    @patch.object(recovery, "recover_profile", side_effect=recovery.RecoveryError("safe failure"))
    def test_expected_error_is_reported_without_traceback(self, recover_profile) -> None:
        output = io.StringIO()
        with patch.object(recovery.sys, "stderr", output):
            self.assertEqual(recovery.main(), 1)
        self.assertEqual(output.getvalue(), "Notary profile recovery failed: safe failure\n")

    @patch.object(recovery, "recover_profile")
    def test_success_reports_only_generic_confirmation(self, recover_profile) -> None:
        output = io.StringIO()
        with patch.object(recovery.sys, "stdout", output):
            self.assertEqual(recovery.main(), 0)
        self.assertEqual(
            output.getvalue(),
            "Notary profile validated in the designated login keychain.\n",
        )


if __name__ == "__main__":
    unittest.main()
