"""Tests for durable Swift test log retention."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
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
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "FIXTURE_ROOT": str(root),
                "SWIFT_TEST_ATTEMPTS": "1",
                "SWIFT_TEST_RESULT_LOG": str(log),
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


if __name__ == "__main__":
    unittest.main()
