"""Tests for process-isolated Swift sanitizer shards."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parent
SHARD_RUNNER = TOOLS / "run-swift-test-shards.sh"
TEST_RUNNER = TOOLS / "run-swift-test.sh"


class SwiftTestShardRunnerTests(unittest.TestCase):
    def make_executable(self, path: Path, body: str) -> Path:
        path.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n" + body,
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def fixture(self, root: Path) -> tuple[Path, Path]:
        bundle = self.make_executable(root / "PackageTests", "exit 0\n")
        runner = self.make_executable(
            root / "bundle-runner.sh",
            'if [[ "${*: -1}" == "--list-tests" ]]; then\n'
            "  printf '%s\\n' 'TargetB.Suite/test()' 'TargetA.Suite/test()' "
            "'TargetA.Other/test()'\n"
            "  exit 0\n"
            "fi\n"
            "filter=''\n"
            "while (( $# > 0 )); do\n"
            "  if [[ \"$1\" == --filter ]]; then filter=\"$2\"; shift 2; continue; fi\n"
            "  shift\n"
            "done\n"
            'printf "%s\\n" "$filter" >>"$INVOCATIONS"\n'
            'if [[ "$filter" == "^$FAIL_TARGET\\." ]]; then\n'
            "  printf 'fixture shard failure\\n'\n"
            "  exit 23\n"
            "fi\n"
            'printf "Test run with 1 test in %s passed after 0.1 seconds.\\n" "$filter"\n',
        )
        return bundle, runner

    def run_fixture(
        self,
        root: Path,
        bundle: Path,
        bundle_runner: Path,
        *,
        fail_target: str = "",
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "FAIL_TARGET": fail_target,
                "INVOCATIONS": str(root / "invocations"),
                "SWIFT_TEST_ATTEMPTS": "1",
                "SWIFT_TEST_RESULT_LOG": str(root / "swift-asan.log"),
            }
        )
        return subprocess.run(
            [
                str(SHARD_RUNNER),
                str(TEST_RUNNER),
                str(bundle_runner),
                str(bundle),
                "--sanitize=address",
                "--no-parallel",
            ],
            env=environment,
            capture_output=True,
            text=True,
        )

    def test_discovers_sorts_and_runs_each_target_in_a_fresh_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            bundle, bundle_runner = self.fixture(root)

            result = self.run_fixture(root, bundle, bundle_runner)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (root / "invocations").read_text(encoding="utf-8"),
                "^TargetA\\.\n^TargetB\\.\n",
            )
            aggregate = (root / "swift-asan.log").read_text(encoding="utf-8")
            self.assertIn("===== TargetA =====", aggregate)
            self.assertIn("===== TargetB =====", aggregate)
            self.assertEqual(aggregate.count("Test run with 1 test"), 2)

    def test_stops_at_first_failing_target_and_retains_its_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            bundle, bundle_runner = self.fixture(root)

            result = self.run_fixture(
                root,
                bundle,
                bundle_runner,
                fail_target="TargetB",
            )

            self.assertEqual(result.returncode, 23)
            aggregate = (root / "swift-asan.log").read_text(encoding="utf-8")
            self.assertIn("===== TargetB =====", aggregate)
            self.assertIn("fixture shard failure", aggregate)


if __name__ == "__main__":
    unittest.main()
