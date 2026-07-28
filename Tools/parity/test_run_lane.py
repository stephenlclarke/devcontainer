#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Security-focused tests for the live parity lane environment."""

from __future__ import annotations

import sqlite3
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from run_lane import (
    LaneRunner,
    create_socket_root,
    run_checked,
    safe_environment,
)


class SafeEnvironmentTests(unittest.TestCase):
    def test_environment_uses_an_explicit_non_secret_allowlist(self) -> None:
        environment = safe_environment(
            {
                "BASH_ENV": "/tmp/host-shell-hook",
                "CONTAINER_COMPOSE_BUILD_INFO": "/tmp/build-info.json",
                "CONTAINER_COMPOSE_CONTAINER": "/tmp/container",
                "DEVCONTAINER_DOCKER_ORACLE_HOST": "unix:///tmp/docker.sock",
                "DOCKER_CONTEXT": "fixture",
                "GITHUB_TOKEN": "must-not-leak",
                "HOME": "/Users/operator",
                "LD_PRELOAD": "/tmp/injected.dylib",
                "PATH": "/usr/bin:/bin",
                "SONAR_TOKEN": "must-not-leak",
            }
        )

        self.assertEqual(
            environment,
            {
                "CONTAINER_COMPOSE_BUILD_INFO": "/tmp/build-info.json",
                "CONTAINER_COMPOSE_CONTAINER": "/tmp/container",
                "DEVCONTAINER_DOCKER_ORACLE_HOST": "unix:///tmp/docker.sock",
                "DOCKER_CONTEXT": "fixture",
                "HOME": "/Users/operator",
                "PATH": "/usr/bin:/bin",
            },
        )


class BoundedCommandTests(unittest.TestCase):
    def test_clean_swift_build_can_select_the_live_gate_timeout(self) -> None:
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch(
            "run_lane.subprocess.run",
            return_value=completed,
        ) as run:
            result = run_checked(
                ["swift", "build"],
                cwd=Path("/repository"),
                environment={"PATH": "/usr/bin:/bin"},
                timeout_seconds=1800,
            )

        self.assertIs(result, completed)
        self.assertEqual(run.call_args.kwargs["timeout"], 1800)

    def test_compatibility_socket_stays_within_darwin_limit(self) -> None:
        with mock.patch(
            "run_lane.tempfile.mkdtemp",
            return_value="/tmp/dc-sock-fixture",
        ) as make_directory:
            root = create_socket_root()

        self.assertEqual(root, Path("/tmp/dc-sock-fixture"))
        self.assertLess(len(str(root / "docker.sock").encode()), 104)
        make_directory.assert_called_once_with(prefix="dc-sock-", dir="/tmp")


class CleanupFixtureTests(unittest.TestCase):
    def test_failed_compose_down_is_reported_and_all_project_containers_removed(
        self,
    ) -> None:
        runner = LaneRunner.__new__(LaneRunner)
        runner.repository = Path("/repository")
        runner.environment = {"PATH": "/usr/bin:/bin"}
        runner.docker = "/usr/bin/docker"
        runner.lane = "apple-stock"
        fixture = SimpleNamespace(directory=Path("/fixtures/C02"))
        completed = [
            mock.Mock(returncode=0, stdout="primary\n", stderr=""),
            mock.Mock(returncode=0, stdout="parity-project\n", stderr=""),
            mock.Mock(
                returncode=17,
                stdout="",
                stderr="network still has active endpoints\n",
            ),
            mock.Mock(returncode=0, stdout="primary\ndependency\n", stderr=""),
            mock.Mock(
                returncode=0,
                stdout="primary\ndependency\n",
                stderr="",
            ),
        ]

        with (
            mock.patch.object(Path, "is_file", return_value=True),
            mock.patch.object(
                Path,
                "read_text",
                return_value='{"dockerComposeFile":"../compose.yaml"}',
            ),
            mock.patch("run_lane.subprocess.run", side_effect=completed) as run,
        ):
            output = runner.cleanup_fixture(fixture)

        self.assertIn("ERROR: compose down exited 17", output)
        self.assertIn("network still has active endpoints", output)
        self.assertEqual(
            run.call_args_list[-1].args[0],
            ["/usr/bin/docker", "rm", "-f", "primary", "dependency"],
        )


class BuilderCleanupTests(unittest.TestCase):
    def test_builder_cleanup_reports_and_removes_exact_leaked_container(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            runner = LaneRunner.__new__(LaneRunner)
            runner.repository = Path("/repository")
            runner.environment = {"PATH": "/usr/bin:/bin"}
            runner.docker = "/usr/bin/docker"
            runner.builder_name = "devcontainer-parity-fixture"
            runner.builder_container_ids = {"builder-container-id"}
            runner.cleanup_differences = []
            runner.output = Path(temporary)
            completed = [
                mock.Mock(returncode=0, stdout="", stderr=""),
                mock.Mock(
                    returncode=0,
                    stdout="builder-container-id\n",
                    stderr="",
                ),
            ]

            with (
                mock.patch(
                    "run_lane.subprocess.run",
                    side_effect=completed,
                ) as run,
                mock.patch(
                    "run_lane.time.monotonic",
                    side_effect=[0.0, 16.0],
                ),
            ):
                runner.stop_builder()

        self.assertEqual(
            run.call_args_list[-1].args[0],
            ["/usr/bin/docker", "rm", "-f", "builder-container-id"],
        )
        self.assertEqual(
            runner.cleanup_differences,
            [
                "isolated buildx builder leaked container(s): "
                "builder-container-id"
            ],
        )
        self.assertIsNone(runner.builder_name)
        self.assertEqual(runner.builder_container_ids, set())

    def test_runtime_state_cleanup_reports_durable_leaks(self) -> None:
        with TemporaryDirectory() as temporary:
            runner = LaneRunner.__new__(LaneRunner)
            runner.lane = "container-compose"
            runner.runtime_root = Path(temporary)
            runner.cleanup_differences = []
            state = runner.runtime_root / "state.sqlite"
            with sqlite3.connect(state) as database:
                database.execute("CREATE TABLE projects (key TEXT)")
                database.execute(
                    "CREATE TABLE runtime_containers (runtime_id TEXT)"
                )
                database.execute("INSERT INTO projects VALUES ('fixture')")
                database.execute(
                    "INSERT INTO runtime_containers VALUES ('fixture')"
                )

            runner.check_runtime_state_cleanup()

        self.assertEqual(
            runner.cleanup_differences,
            [
                "lane state leaked 1 project claim(s) and "
                "1 runtime container record(s)"
            ],
        )


if __name__ == "__main__":
    unittest.main()
