"""Tests for durable Swift test log retention."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parent
RUNNER = TOOLS / "run-swift-test.sh"


class SwiftTestRunnerTests(unittest.TestCase):
    def make_command(self, root: Path, body: str) -> Path:
        command = root / "fixture-command.sh"
        command.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n" + body,
            encoding="utf-8",
        )
        command.chmod(command.stat().st_mode | stat.S_IXUSR)
        return command

    def run_fixture(
        self,
        root: Path,
        command: Path,
        log: Path,
        *,
        attempts: int = 1,
        timeout_seconds: int = 0,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "FIXTURE_ROOT": str(root),
                "SWIFT_TEST_ATTEMPTS": str(attempts),
                "SWIFT_TEST_RESULT_LOG": str(log),
                "SWIFT_TEST_TIMEOUT_SECONDS": str(timeout_seconds),
            }
        )
        return subprocess.run(
            [str(RUNNER), str(command)],
            env=environment,
            capture_output=True,
            text=True,
        )

    def test_log_survives_command_deleting_its_parent_build_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            log = root / ".build" / "swift-asan.log"
            command = self.make_command(
                root,
                'rm -rf "$FIXTURE_ROOT/.build"\n'
                "printf 'Test run with 75 tests passed after 1.0 seconds.\\n'\n",
            )

            result = self.run_fixture(root, command, log)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(log.is_file())
            self.assertIn("75 tests passed", log.read_text(encoding="utf-8"))

    def test_failure_status_and_complete_output_are_retained(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            log = root / ".build" / "swift-tsan.log"
            command = self.make_command(
                root,
                "printf 'sanitizer failure evidence\\n'\nexit 23\n",
            )

            result = self.run_fixture(root, command, log)

            self.assertEqual(result.returncode, 23)
            self.assertEqual(
                log.read_text(encoding="utf-8"),
                "sanitizer failure evidence\n",
            )

    def test_timeout_terminates_process_group_and_retains_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            log = root / ".build" / "swift-coverage.log"
            command = self.make_command(
                root,
                "printf 'test command started\\n'\n"
                "sleep 30 &\n"
                'child_pid="$!"\n'
                'printf "%s\\n" "$child_pid" >"$FIXTURE_ROOT/child-pid"\n'
                'wait "$child_pid"\n',
            )

            result = self.run_fixture(
                root,
                command,
                log,
                timeout_seconds=2,
            )

            self.assertEqual(result.returncode, 124)
            retained_output = log.read_text(encoding="utf-8")
            self.assertIn("test command started", retained_output)
            self.assertIn(
                "Swift test command timed out after 2 seconds.",
                retained_output,
            )
            child_pid = int(
                (root / "child-pid").read_text(encoding="utf-8").strip()
            )
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.01)
            else:
                self.fail(f"timed-out child process {child_pid} survived")

    def test_timeout_is_retried_once_and_can_recover(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            log = root / ".build" / "swift-coverage.log"
            command = self.make_command(
                root,
                'attempt_file="$FIXTURE_ROOT/attempt"\n'
                'attempt="$(cat "$attempt_file" 2>/dev/null || printf 0)"\n'
                'attempt="$((attempt + 1))"\n'
                'printf "%s\\n" "$attempt" >"$attempt_file"\n'
                'if [[ "$attempt" -eq 1 ]]; then\n'
                "  sleep 30\n"
                "fi\n"
                "printf 'Test run with 123 tests passed after 1.0 seconds.\\n'\n",
            )

            result = self.run_fixture(
                root,
                command,
                log,
                attempts=2,
                timeout_seconds=2,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("a 2-second timeout", result.stderr)
            self.assertIn(
                "123 tests passed",
                log.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                (root / "attempt").read_text(encoding="utf-8"),
                "2\n",
            )


if __name__ == "__main__":
    unittest.main()
