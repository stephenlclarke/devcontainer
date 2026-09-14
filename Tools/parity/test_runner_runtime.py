# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "Tools" / "parity" / "runner-runtime.sh"


class RunnerRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.log = self.root / "operations.log"
        self.stock = self.make_runtime("stock")
        self.compose = self.make_runtime("compose")
        self.colima = self.make_colima()
        self.launchctl = self.make_launchctl()

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
    if [[ "${MOCK_RUNTIME_HANG_STATUS:-0}" == "1" ]]; then
      while :; do
        sleep 60
      done
    fi
    value="unregistered"
    [[ ! -f "$state" ]] || value="$(<"$state")"
    printf '{"status":"%s"}\\n' "$value"
    ;;
  "system start")
    if [[ "${MOCK_RUNTIME_HANG_START_ONCE:-0}" == "1" && ! -f "${state}.hung" ]]; then
      : > "${state}.hung"
      (
        trap '' TERM INT
        while :; do
          sleep 60
        done
      ) &
      resistant_child=$!
      printf '%s\\n' "$resistant_child" > "${state}.child"
      trap 'exit 143' TERM INT
      wait "$resistant_child"
    fi
    if [[ "${MOCK_START_FAIL_ONCE:-0}" == "1" && ! -f "${state}.failed" ]]; then
      : > "${state}.failed"
      exit 17
    fi
    printf 'running\\n' > "$state"
    ;;
  "system stop")
    if [[ "${MOCK_RUNTIME_HANG_STOP:-0}" == "1" ]]; then
      while :; do
        sleep 60
      done
    fi
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

    def make_colima(self) -> Path:
        executable = self.root / "colima"
        executable.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
state="${0}.state"
printf 'colima %s\\n' "$*" >> "$MOCK_RUNTIME_LOG"
case "${1:-}" in
  start)
    if [[ "${MOCK_COLIMA_HANG_ONCE:-0}" == "1" && ! -f "${state}.hung" ]]; then
      : > "${state}.hung"
      (
        trap '' TERM INT
        while :; do
          sleep 60
        done
      ) &
      resistant_child=$!
      printf '%s\n' "$resistant_child" > "${state}.child"
      trap 'exit 143' TERM INT
      wait "$resistant_child"
    fi
    if [[ "${MOCK_COLIMA_FAIL_ALWAYS:-0}" == "1" ]]; then
      exit 17
    fi
    if [[ "${MOCK_COLIMA_FAIL_ONCE:-0}" == "1" && ! -f "${state}.failed" ]]; then
      : > "${state}.failed"
      exit 17
    fi
    printf 'running\\n' > "$state"
    ;;
  status)
    if [[ "${MOCK_COLIMA_HANG_STATUS:-0}" == "1" ]]; then
      while :; do
        sleep 60
      done
    fi
    [[ -f "$state" && "$(<"$state")" == "running" ]]
    ;;
  stop)
    if [[ "${MOCK_COLIMA_STOP_STAYS_RUNNING:-0}" != "1" ]]; then
      printf 'unregistered\n' > "$state"
    fi
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

    def make_launchctl(self) -> Path:
        executable = self.root / "launchctl"
        executable.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
printf 'launchctl %s\\n' "$*" >> "$MOCK_RUNTIME_LOG"
case "${1:-}" in
  print)
    if [[ -f "$MOCK_LAUNCHCTL_STATE" ]]; then
      printf '0 1 sh.brew.container\\n'
      printf '0 - com.apple.container.apiserver\\n'
    elif [[ "${2:-}" == */*/* ]]; then
      exit 113
    fi
    ;;
  bootout)
    if [[ "${MOCK_LAUNCHCTL_SURVIVE:-0}" != "1" ]]; then
      rm -f "$MOCK_LAUNCHCTL_STATE"
    fi
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
                "DEVCONTAINER_RUNTIME_COLIMA_BIN": str(self.colima),
                "DEVCONTAINER_RUNTIME_LAUNCHCTL_BIN": str(self.launchctl),
                "DEVCONTAINER_RUNTIME_SKIP_SUDO": "1",
                "CONTAINER_APP_ROOT": str(self.root / "runtime-root"),
                "CONTAINER_INSTALL_ROOT": str(self.root / "install-root"),
                "CONTAINER_SERVICE_NAMESPACE": "com.apple.container",
                "XDG_CONFIG_HOME": str(self.root / "runtime-root" / "xdg"),
                "MOCK_RUNTIME_LOG": str(self.log),
                "MOCK_LAUNCHCTL_STATE": str(self.root / "launchctl.state"),
            }
        )
        environment.update(extra_environment or {})
        if extra_environment and "CONTAINER_APP_ROOT" in extra_environment:
            if "XDG_CONFIG_HOME" not in extra_environment:
                environment["XDG_CONFIG_HOME"] = (
                    f"{extra_environment['CONTAINER_APP_ROOT']}/xdg"
                )
        return subprocess.run(
            [str(SCRIPT), operation, lane],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
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
        self.assertEqual(
            (self.root / "colima.state").read_text().strip(),
            "running",
        )

    def test_candidate_lanes_do_not_require_colima(self) -> None:
        result = self.run_script(
            "start",
            "apple-stock",
            {"DEVCONTAINER_RUNTIME_COLIMA_BIN": str(self.root / "missing")},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        operations = self.log.read_text(encoding="utf-8").splitlines()
        self.assertFalse(
            any(operation.startswith("colima ") for operation in operations),
            operations,
        )
        self.assertIn(
            (
                f"stock system start --app-root {self.root / 'runtime-root'} "
                f"--install-root {self.root / 'install-root'} "
                "--enable-kernel-install --timeout 120"
            ),
            operations,
        )

    def test_fails_closed_and_bounds_a_hung_post_start_status(self) -> None:
        started_at = time.monotonic()
        result = self.run_script(
            "start",
            "apple-stock",
            {
                "DEVCONTAINER_RUNTIME_APPLE_COMMAND_TIMEOUT_SECONDS": "1",
                "MOCK_RUNTIME_HANG_STATUS": "1",
            },
        )

        self.assertEqual(result.returncode, 1)
        self.assertLess(time.monotonic() - started_at, 10)
        self.assertIn("runtime did not reach running state", result.stderr)
        operations = self.log.read_text(encoding="utf-8")
        self.assertNotIn("colima start", operations)

    def test_bounds_and_retries_a_hung_apple_start(self) -> None:
        started_at = time.monotonic()
        result = self.run_script(
            "start",
            "apple-stock",
            {
                "DEVCONTAINER_RUNTIME_APPLE_COMMAND_TIMEOUT_SECONDS": "1",
                "MOCK_RUNTIME_HANG_START_ONCE": "1",
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(time.monotonic() - started_at, 10)
        operations = self.log.read_text(encoding="utf-8")
        self.assertEqual(operations.count("stock system start"), 2)
        child_id = int((self.root / "stock.state.child").read_text().strip())
        for _ in range(20):
            try:
                os.kill(child_id, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail(f"timed-out Apple runtime descendant survived: {child_id}")

    def test_bounds_a_hung_apple_stop(self) -> None:
        (self.root / "stock.state").write_text("running\n", encoding="utf-8")
        (self.root / "launchctl.state").touch()
        started_at = time.monotonic()
        result = self.run_script(
            "stop",
            "apple-stock",
            {
                "DEVCONTAINER_RUNTIME_APPLE_COMMAND_TIMEOUT_SECONDS": "1",
                "MOCK_RUNTIME_HANG_STOP": "1",
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(time.monotonic() - started_at, 5)
        operations = self.log.read_text(encoding="utf-8")
        self.assertIn(
            f"launchctl bootout gui/{os.getuid()}/sh.brew.container",
            operations,
        )

    def test_fails_closed_when_forced_runtime_cleanup_cannot_deregister(self) -> None:
        (self.root / "stock.state").write_text("running\n", encoding="utf-8")
        (self.root / "launchctl.state").touch()
        result = self.run_script(
            "stop",
            "apple-stock",
            {
                "DEVCONTAINER_RUNTIME_APPLE_COMMAND_TIMEOUT_SECONDS": "1",
                "MOCK_LAUNCHCTL_SURVIVE": "1",
                "MOCK_RUNTIME_HANG_STOP": "1",
            },
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("runtime stop command failed or timed out", result.stderr)

    def test_candidate_lanes_stop_a_running_docker_oracle_before_start(self) -> None:
        (self.root / "colima.state").write_text("running\n", encoding="utf-8")

        result = self.run_script("start", "apple-stock")

        self.assertEqual(result.returncode, 0, result.stderr)
        operations = self.log.read_text(encoding="utf-8").splitlines()
        self.assertLess(
            operations.index("colima stop"),
            next(
                index
                for index, operation in enumerate(operations)
                if operation.startswith("stock system start --app-root ")
            ),
        )
        self.assertEqual(
            (self.root / "colima.state").read_text(encoding="utf-8").strip(),
            "unregistered",
        )

    def test_candidate_lane_fails_if_docker_oracle_cannot_stop(self) -> None:
        (self.root / "colima.state").write_text("running\n", encoding="utf-8")

        result = self.run_script(
            "start",
            "apple-stock",
            {"MOCK_COLIMA_STOP_STAYS_RUNNING": "1"},
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("Colima remained running after stop", result.stderr)
        operations = self.log.read_text(encoding="utf-8")
        self.assertNotIn("stock system start", operations)

    def test_candidate_lane_fails_closed_when_colima_status_times_out(self) -> None:
        started_at = time.monotonic()
        result = self.run_script(
            "start",
            "apple-stock",
            {
                "DEVCONTAINER_RUNTIME_COLIMA_COMMAND_TIMEOUT_SECONDS": "1",
                "MOCK_COLIMA_HANG_STATUS": "1",
            },
        )

        self.assertEqual(result.returncode, 1)
        self.assertLess(time.monotonic() - started_at, 10)
        self.assertIn("Colima status timed out before stop", result.stderr)
        self.assertNotIn("stock system start", self.log.read_text(encoding="utf-8"))

    def test_retries_an_interrupted_colima_start(self) -> None:
        result = self.run_script(
            "start",
            "docker",
            {"MOCK_COLIMA_FAIL_ONCE": "1"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        operations = self.log.read_text(encoding="utf-8")
        self.assertEqual(operations.count("colima start"), 2)

    def test_terminates_and_retries_a_hung_colima_start(self) -> None:
        started_at = time.monotonic()
        result = self.run_script(
            "start",
            "docker",
            {
                "DEVCONTAINER_RUNTIME_COLIMA_COMMAND_TIMEOUT_SECONDS": "1",
                "MOCK_COLIMA_HANG_ONCE": "1",
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(time.monotonic() - started_at, 10)
        operations = self.log.read_text(encoding="utf-8")
        self.assertEqual(operations.count("colima start"), 2)
        child_id = int((self.root / "colima.state.child").read_text().strip())
        for _ in range(20):
            try:
                os.kill(child_id, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail(f"timed-out Colima descendant survived: {child_id}")

    def test_reuses_an_already_running_colima(self) -> None:
        (self.root / "colima.state").write_text("running\n", encoding="utf-8")

        result = self.run_script("start", "docker")

        self.assertEqual(result.returncode, 0, result.stderr)
        operations = self.log.read_text(encoding="utf-8")
        self.assertNotIn("colima start", operations)
        self.assertEqual(operations.count("colima status"), 1)

    def test_stops_the_docker_oracle_after_its_lane(self) -> None:
        (self.root / "colima.state").write_text("running\n", encoding="utf-8")

        result = self.run_script("stop", "docker")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.root / "colima.state").read_text(encoding="utf-8").strip(),
            "unregistered",
        )
        operations = self.log.read_text(encoding="utf-8")
        self.assertIn("colima status", operations)
        self.assertIn("colima stop", operations)

    def test_fails_closed_when_colima_cannot_start(self) -> None:
        result = self.run_script(
            "start",
            "docker",
            {"MOCK_COLIMA_FAIL_ALWAYS": "1"},
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("Colima did not reach running state", result.stderr)
        operations = self.log.read_text(encoding="utf-8")
        self.assertEqual(operations.count("colima start"), 3)

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

    def test_rejects_an_unmarked_existing_runtime_root(self) -> None:
        runtime_root = self.root / "existing-runtime"
        runtime_root.mkdir()
        (runtime_root / "user-data").write_text("keep\n", encoding="utf-8")

        result = self.run_script(
            "start",
            "apple-stock",
            {"CONTAINER_APP_ROOT": str(runtime_root)},
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("exists without its parity marker", result.stderr)
        self.assertTrue((runtime_root / "user-data").is_file())

    def test_rejects_user_configuration_outside_the_runtime_root(self) -> None:
        result = self.run_script(
            "start",
            "apple-stock",
            {"XDG_CONFIG_HOME": str(self.root / "ambient-config")},
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("must be owned by the parity runtime root", result.stderr)

    def test_replaces_stale_lane_configuration_with_binary_defaults(self) -> None:
        runtime_root = self.root / "runtime-root"
        runtime_root.mkdir()
        (runtime_root / ".devcontainer-parity-runtime-root").write_text(
            "devcontainer parity runtime root v1\n",
            encoding="utf-8",
        )
        configuration = runtime_root / "config" / "config.toml"
        configuration.parent.mkdir()
        configuration.write_text(
            '[build]\nimage = "ambient-user-override"\n',
            encoding="utf-8",
        )

        result = self.run_script("start", "container-compose")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            configuration.read_text(encoding="utf-8"),
            "# Lane-owned configuration: use the selected binary defaults.\n",
        )

    def test_reuses_the_read_only_lane_configuration(self) -> None:
        first = self.run_script("start", "container-compose")
        second = self.run_script("start", "container-compose")

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)

    def test_rejects_a_symbolic_link_runtime_root(self) -> None:
        actual_root = self.root / "actual-root"
        actual_root.mkdir()
        (actual_root / ".devcontainer-parity-runtime-root").write_text(
            "devcontainer parity runtime root v1\n",
            encoding="utf-8",
        )
        linked_root = self.root / "linked-root"
        linked_root.symlink_to(actual_root, target_is_directory=True)

        result = self.run_script(
            "start",
            "apple-stock",
            {"CONTAINER_APP_ROOT": str(linked_root)},
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("must not be a symbolic link", result.stderr)

    def test_rejects_a_runtime_root_too_long_for_the_provider_socket(self) -> None:
        runtime_root = self.root / ("r" * 110)

        result = self.run_script(
            "start",
            "apple-stock",
            {"CONTAINER_APP_ROOT": str(runtime_root)},
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("too long for the provider Unix socket", result.stderr)

    def test_enhanced_root_identity_is_bound_to_the_runtime_binary(self) -> None:
        result = self.run_script("start", "container-compose")

        self.assertEqual(result.returncode, 0, result.stderr)
        identity = (
            self.root / "runtime-root" / "engine-provider" / "state-root-id"
        ).read_text(encoding="utf-8").strip()
        digest = subprocess.run(
            ["shasum", "-a", "256", str(self.compose)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()[0]
        self.assertEqual(
            identity,
            (
                f"{digest[0:8]}-{digest[8:12]}-{digest[12:16]}-"
                f"{digest[16:20]}-{digest[20:32]}"
            ),
        )

    def test_rejects_a_symbolic_link_provider_identity_directory(self) -> None:
        runtime_root = self.root / "runtime-root"
        runtime_root.mkdir()
        (runtime_root / ".devcontainer-parity-runtime-root").write_text(
            "devcontainer parity runtime root v1\n",
            encoding="utf-8",
        )
        outside = self.root / "outside-provider"
        outside.mkdir()
        (runtime_root / "engine-provider").symlink_to(
            outside,
            target_is_directory=True,
        )

        result = self.run_script("start", "container-compose")

        self.assertEqual(result.returncode, 1)
        self.assertIn("identity path must not be a symbolic link", result.stderr)
        self.assertFalse((outside / "state-root-id").exists())

    def test_enhanced_root_identity_uses_the_packaged_binary_not_wrapper(self) -> None:
        install_root = self.root / "packaged"
        runtime_binary = install_root / "libexec" / "bin" / "compose"
        runtime_binary.parent.mkdir(parents=True)
        runtime_binary.write_bytes(b"immutable packaged runtime\n")
        runtime_binary.chmod(runtime_binary.stat().st_mode | stat.S_IXUSR)

        result = self.run_script(
            "start",
            "container-compose",
            {"CONTAINER_INSTALL_ROOT": str(install_root)},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        identity = (
            self.root / "runtime-root" / "engine-provider" / "state-root-id"
        ).read_text(encoding="utf-8").strip()
        digest = hashlib.sha256(runtime_binary.read_bytes()).hexdigest()
        self.assertEqual(
            identity,
            (
                f"{digest[0:8]}-{digest[8:12]}-{digest[12:16]}-"
                f"{digest[16:20]}-{digest[20:32]}"
            ),
        )

    def test_rejects_unknown_lane_without_runtime_changes(self) -> None:
        result = self.run_script("start", "unknown")

        self.assertEqual(result.returncode, 1)
        self.assertIn("unsupported lane", result.stderr)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
