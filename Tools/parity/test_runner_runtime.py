# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "Tools" / "parity" / "runner-runtime.sh"


class RunnerRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.log = self.root / "operations.log"
        self.stock = self.make_runtime("stock")
        self.compose = self.make_runtime("compose")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_runtime(self, name: str) -> Path:
        executable = self.root / name
        executable.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
state="${0}.state"
printf '%s %s\\n' "$(basename "$0")" "$*" >> "$MOCK_RUNTIME_LOG"
case "${1:-} ${2:-}" in
  "system status")
    value="unregistered"
    [[ ! -f "$state" ]] || value="$(<"$state")"
    printf '{"status":"%s"}\\n' "$value"
    ;;
  "system start")
    if [[ "${MOCK_START_FAIL_ONCE:-0}" == "1" && ! -f "${state}.failed" ]]; then
      : > "${state}.failed"
      exit 17
    fi
    printf 'running\\n' > "$state"
    ;;
  "system stop")
    printf 'unregistered\\n' > "$state"
    ;;
  *)
    exit 2
    ;;
esac
""",
            encoding="utf-8",
        )
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        return executable

    def run_script(
        self,
        operation: str,
        lane: str,
        extra_environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "DEVCONTAINER_RUNTIME_STOCK_BIN": str(self.stock),
                "DEVCONTAINER_RUNTIME_COMPOSE_BIN": str(self.compose),
                "DEVCONTAINER_RUNTIME_SKIP_SUDO": "1",
                "MOCK_RUNTIME_LOG": str(self.log),
            }
        )
        environment.update(extra_environment or {})
        return subprocess.run(
            [str(SCRIPT), operation, lane],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_starts_and_stops_selected_stock_runtime(self) -> None:
        started = self.run_script("start", "apple-stock")
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertEqual((self.root / "stock.state").read_text().strip(), "running")

        stopped = self.run_script("stop", "apple-stock")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertEqual(
            (self.root / "stock.state").read_text().strip(),
            "unregistered",
        )

    def test_docker_lane_stops_both_apple_distributions(self) -> None:
        (self.root / "stock.state").write_text("running\n", encoding="utf-8")
        (self.root / "compose.state").write_text("running\n", encoding="utf-8")

        result = self.run_script("start", "docker")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "stock.state").read_text().strip(), "unregistered")
        self.assertEqual(
            (self.root / "compose.state").read_text().strip(),
            "unregistered",
        )

    def test_retries_an_interrupted_runtime_start(self) -> None:
        result = self.run_script(
            "start",
            "container-compose",
            {"MOCK_START_FAIL_ONCE": "1"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.root / "compose.state").read_text().strip(),
            "running",
        )
        operations = self.log.read_text(encoding="utf-8")
        self.assertEqual(operations.count("compose system start"), 2)

    def test_rejects_unknown_lane_without_runtime_changes(self) -> None:
        result = self.run_script("start", "unknown")

        self.assertEqual(result.returncode, 1)
        self.assertIn("unsupported lane", result.stderr)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
