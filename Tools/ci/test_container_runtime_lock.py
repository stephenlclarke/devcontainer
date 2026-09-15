"""Behavioral tests for the shared Container runtime lock."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LOCK_SCRIPT = ROOT / "Tools" / "ci" / "container-runtime-lock.sh"


class ContainerRuntimeLockTests(unittest.TestCase):
    def run_lock_shell(
        self,
        source: str,
        lock_file: Path,
        timeout: int = 3,
    ) -> subprocess.CompletedProcess[str]:
        """Run one isolated Bash lock client."""
        environment = os.environ.copy()
        environment.update(
            {
                "CONTAINER_RUNTIME_LOCK_FILE": str(lock_file),
                "CONTAINER_RUNTIME_LOCK_TIMEOUT_SECONDS": str(timeout),
            }
        )
        return subprocess.run(
            ["bash", "-c", source],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout + 5,
            env=environment,
        )

    def test_acquire_and_release_manage_ownership_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock_file = Path(directory) / "runtime.lock"
            result = self.run_lock_shell(
                f"""
set -euo pipefail
source {str(LOCK_SCRIPT)!r}
acquire_container_runtime_lock
[[ "${{CONTAINER_RUNTIME_LOCK_HELD}}" == "1" ]]
[[ -n "${{CONTAINER_RUNTIME_LOCK_KEEPER_PID}}" ]]
release_container_runtime_lock
[[ -z "${{CONTAINER_RUNTIME_LOCK_HELD:-}}" ]]
[[ -z "${{CONTAINER_RUNTIME_LOCK_KEEPER_PID:-}}" ]]
""",
                lock_file,
            )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_contender_times_out_while_another_process_owns_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock_file = root / "runtime.lock"
            ready = root / "ready"
            release = root / "release"
            environment = os.environ.copy()
            environment.update(
                {
                    "CONTAINER_RUNTIME_LOCK_FILE": str(lock_file),
                    "CONTAINER_RUNTIME_LOCK_TIMEOUT_SECONDS": "5",
                }
            )
            holder = subprocess.Popen(
                [
                    "bash",
                    "-c",
                    f"""
set -euo pipefail
source {str(LOCK_SCRIPT)!r}
acquire_container_runtime_lock
printf 'ready\n' >{str(ready)!r}
while [[ ! -f {str(release)!r} ]]; do sleep 0.05; done
release_container_runtime_lock
""",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
            )
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(ready.exists(), "lock holder did not become ready")

                started = time.monotonic()
                contender = self.run_lock_shell(
                    f"source {str(LOCK_SCRIPT)!r}; acquire_container_runtime_lock",
                    lock_file,
                    timeout=1,
                )
                elapsed = time.monotonic() - started
                self.assertNotEqual(contender.returncode, 0)
                self.assertGreaterEqual(elapsed, 0.8)
                self.assertIn("timed out after 1s", contender.stderr)
            finally:
                release.touch()
                holder_stdout, holder_stderr = holder.communicate(timeout=5)

            self.assertEqual(
                holder.returncode,
                0,
                f"{holder_stdout}\n{holder_stderr}",
            )


if __name__ == "__main__":
    unittest.main()
