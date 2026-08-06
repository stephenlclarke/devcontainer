#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Security-focused tests for the live parity lane environment."""

from __future__ import annotations

import signal
import sqlite3
import subprocess
import unittest
import urllib.error
from contextlib import closing
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from parity_lib import Fixture, ParityError
from run_lane import (
    LaneRunner,
    create_socket_root,
    install_cancellation_handlers,
    resolver_nameservers,
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
                "RUNNER_TRACKING_ID": "github_fixture",
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
                "RUNNER_TRACKING_ID": "github_fixture",
            },
        )


class RuntimePathTests(unittest.TestCase):
    def test_provider_explicit_binaries_are_first_on_child_path(self) -> None:
        environment = {
            "DEVCONTAINER_CONTAINER_BIN": "/stable/container/bin/container",
            "DEVCONTAINER_COMPOSE_BIN": "/stable/compose/bin/container-compose",
            "HOME": "/Users/operator",
            "PATH": "/current/bin:/usr/bin:/stable/container/bin",
        }
        with (
            mock.patch.dict("run_lane.os.environ", environment, clear=True),
            mock.patch(
                "run_lane.load_manifest",
                return_value={
                    "referencePins": {
                        "devcontainersCli": {
                            "version": "0.88.0",
                        },
                    },
                },
            ),
            mock.patch(
                "run_lane.shutil.which",
                side_effect=["/current/bin/docker", "/current/bin/npx"],
            ),
        ):
            runner = LaneRunner(
                "container-compose",
                Path("/repository"),
                Path("/evidence"),
            )

        self.assertEqual(
            runner.environment["PATH"],
            (
                "/stable/container/bin:/stable/compose/bin:"
                "/current/bin:/usr/bin"
            ),
        )
        self.assertEqual(
            runner.environment["CONTAINER_COMPOSE_CONTAINER"],
            "/stable/container/bin/container",
        )


class CancellationHandlerTests(unittest.TestCase):
    def test_workflow_termination_becomes_a_catchable_cleanup_error(self) -> None:
        with mock.patch("run_lane.signal.signal") as register:
            install_cancellation_handlers()

        self.assertEqual(
            [call.args[0] for call in register.call_args_list],
            [signal.SIGINT, signal.SIGTERM],
        )
        cancel = register.call_args_list[1].args[1]
        with self.assertRaisesRegex(
            ParityError,
            "CLI parity interrupted by SIGTERM",
        ):
            cancel(signal.SIGTERM, None)
        cancel(signal.SIGTERM, None)


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


class FingerprintTests(unittest.TestCase):
    def test_direct_cli_reference_is_retained_with_runtime_evidence(self) -> None:
        runner = LaneRunner.__new__(LaneRunner)
        runner.lane = "docker"
        runner.docker = "/usr/bin/docker"
        runner.node_package_runner = "/usr/bin/npx"
        runner.cli_version = "0.88.0"
        runner.cli_reference = {
            "version": "0.88.0",
            "source": "https://github.com/devcontainers/cli",
            "commit": "a" * 40,
            "npmIntegrity": "sha512-" + "b" * 86 + "==",
        }
        runner.repository = Path("/repository")
        runner.environment = {"PATH": "/usr/bin:/bin"}
        completed = [
            mock.Mock(returncode=0, stdout='{"Client":{}}', stderr=""),
            mock.Mock(returncode=0, stdout="0.88.0\n", stderr=""),
        ]

        with (
            mock.patch("run_lane.platform.machine", return_value="arm64"),
            mock.patch(
                "run_lane.platform.platform",
                return_value="macOS-26-arm64",
            ),
            mock.patch(
                "run_lane.subprocess.run",
                side_effect=completed,
            ) as run,
        ):
            fingerprint = runner.fingerprint()

        self.assertEqual(
            fingerprint["devcontainersReference"],
            runner.cli_reference,
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["/usr/bin/npx", "--yes", "@devcontainers/cli@0.88.0", "--version"],
        )


class FixtureProbeTests(unittest.TestCase):
    def test_fixture_probe_uses_the_resolved_remote_workspace(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "probe.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            repository = root / "repository"
            repository.mkdir()
            runner = LaneRunner.__new__(LaneRunner)
            runner.output = root / "evidence"
            runner.repository = repository
            runner.devcontainer_docker = "/usr/bin/docker"
            fixture = Fixture(
                directory=source,
                identifier="fixture",
                expected={"ready": "true"},
                backends=("docker",),
                runner="devcontainer",
            )
            calls: list[list[str]] = []
            up = mock.Mock(
                returncode=0,
                stdout=(
                    '{"containerId":"fixture",'
                    '"remoteWorkspaceFolder":"/workspaces/fixture"}'
                ),
                stderr="",
            )
            probe = mock.Mock(returncode=0, stdout="ready=true\n", stderr="")

            def devcontainer(arguments: list[str], timeout: int) -> mock.Mock:
                self.assertIn(timeout, {120, 1800})
                calls.append(arguments)
                return up if len(calls) == 1 else probe

            runner.devcontainer = devcontainer
            runner.additional_fixture_observations = mock.Mock(return_value={})
            runner.cleanup_fixture = mock.Mock(return_value="")
            with mock.patch("run_lane.assert_contract", return_value=[]):
                result = runner.run_fixture(fixture)

        self.assertEqual(result["status"], "passed")
        self.assertEqual(
            calls[1][-6:],
            [
                "--",
                "/bin/sh",
                '-c',
                'cd "$1" && exec /bin/sh ./probe.sh',
                "probe",
                "/workspaces/fixture",
            ],
        )

    def test_remote_workspace_requires_an_absolute_path(self) -> None:
        runner = LaneRunner.__new__(LaneRunner)

        with self.assertRaisesRegex(
            ParityError,
            "absolute remoteWorkspaceFolder",
        ):
            runner.remote_workspace_from_up('{"remoteWorkspaceFolder":"relative"}')

    def test_fixture_reports_a_workspace_cleanup_failure(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = LaneRunner.__new__(LaneRunner)
            runner.output = root / "evidence"
            runner.repository = root / "repository"
            runner.repository.mkdir()
            runner.devcontainer_docker = "/usr/bin/docker"
            workspace_root = root / "workspace-root"
            workspace = workspace_root / "fixture"
            workspace.mkdir(parents=True)
            fixture = Fixture(
                directory=root / "source",
                identifier="fixture",
                expected={"ready": "true"},
                backends=("docker",),
                runner="devcontainer",
            )
            up = mock.Mock(
                returncode=0,
                stdout=(
                    '{"containerId":"fixture",'
                    '"remoteWorkspaceFolder":"/workspaces/fixture"}'
                ),
                stderr="",
            )
            probe = mock.Mock(returncode=0, stdout="ready=true\n", stderr="")
            runner.create_fixture_workspace = mock.Mock(
                return_value=(workspace_root, workspace)
            )
            runner.devcontainer = mock.Mock(side_effect=[up, probe])
            runner.additional_fixture_observations = mock.Mock(return_value={})
            runner.cleanup_fixture = mock.Mock(return_value="")
            runner.cleanup_fixture_workspace = mock.Mock(
                return_value="ERROR: fixture workspace cleanup failed"
            )

            with mock.patch("run_lane.assert_contract", return_value=[]):
                result = runner.run_fixture(fixture)

        self.assertEqual(result["status"], "failed")
        self.assertIn("workspace cleanup failed", result["diagnostic"])

    def test_engine_fixture_skips_the_bind_mount_workspace_copy(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = LaneRunner.__new__(LaneRunner)
            runner.output = root / "evidence"
            fixture = Fixture(
                directory=root / "source",
                identifier="engine-fixture",
                expected={},
                backends=("docker",),
                runner="engine",
            )
            expected = {"id": "engine-fixture", "status": "passed"}
            runner.run_engine_fixture = mock.Mock(return_value=expected)

            result = runner.run_fixture(fixture)

        self.assertIs(result, expected)
        runner.run_engine_fixture.assert_called_once()


class FixtureWorkspaceTests(unittest.TestCase):
    def test_fixture_workspace_is_copied_under_the_repository_build_root(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            source = root / "source"
            source.mkdir()
            (source / "probe.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            runner = LaneRunner.__new__(LaneRunner)
            runner.repository = repository
            fixture = Fixture(
                directory=source,
                identifier="fixture",
                expected={},
                backends=("docker",),
                runner="devcontainer",
            )

            workspace_root, workspace = runner.create_fixture_workspace(fixture)

            self.assertEqual(workspace, workspace_root / "fixture")
            self.assertTrue((workspace / "probe.sh").is_file())
            self.assertEqual(
                (workspace_root / ".devcontainer-parity-workspace-root").read_text(
                    encoding="utf-8"
                ),
                "devcontainer parity workspace root v1\n",
            )
            self.assertEqual(
                workspace_root.parent,
                repository / ".build" / "parity-workspaces",
            )
            self.assertEqual(runner.cleanup_fixture_workspace(workspace_root), "")
            self.assertFalse(workspace_root.exists())

    def test_workspace_cleanup_preserves_a_root_without_its_marker(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            source = root / "source"
            source.mkdir()
            runner = LaneRunner.__new__(LaneRunner)
            runner.repository = repository
            fixture = Fixture(
                directory=source,
                identifier="fixture",
                expected={},
                backends=("docker",),
                runner="devcontainer",
            )
            workspace_root, _ = runner.create_fixture_workspace(fixture)
            (workspace_root / ".devcontainer-parity-workspace-root").unlink()

            cleanup = runner.cleanup_fixture_workspace(workspace_root)

            self.assertIn("marker is unsafe", cleanup)
            self.assertTrue(workspace_root.is_dir())

    def test_workspace_cleanup_rejects_a_missing_root(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            runner = LaneRunner.__new__(LaneRunner)
            runner.repository = repository

            cleanup = runner.cleanup_fixture_workspace(
                repository / ".build" / "parity-workspaces" / "missing"
            )

            self.assertIn("unsafe fixture workspace root", cleanup)

    def test_workspace_copy_failure_removes_its_owned_root(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            source = root / "source"
            source.mkdir()
            runner = LaneRunner.__new__(LaneRunner)
            runner.repository = repository
            fixture = Fixture(
                directory=source,
                identifier="fixture",
                expected={},
                backends=("docker",),
                runner="devcontainer",
            )

            with mock.patch(
                "run_lane.shutil.copytree",
                side_effect=OSError("copy failed"),
            ):
                with self.assertRaisesRegex(OSError, "copy failed"):
                    runner.create_fixture_workspace(fixture)

            self.assertFalse((repository / ".build" / "parity-workspaces").exists())

    def test_workspace_cleanup_rejects_escaped_and_modified_roots(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            source = root / "source"
            source.mkdir()
            runner = LaneRunner.__new__(LaneRunner)
            runner.repository = repository
            fixture = Fixture(
                directory=source,
                identifier="fixture",
                expected={},
                backends=("docker",),
                runner="devcontainer",
            )
            escaped = root / "escaped"
            escaped.mkdir()
            (escaped / ".devcontainer-parity-workspace-root").write_text(
                "devcontainer parity workspace root v1\n",
                encoding="utf-8",
            )
            workspace_root, _ = runner.create_fixture_workspace(fixture)
            (workspace_root / ".devcontainer-parity-workspace-root").write_text(
                "changed\n",
                encoding="utf-8",
            )

            escaped_cleanup = runner.cleanup_fixture_workspace(escaped)
            modified_cleanup = runner.cleanup_fixture_workspace(workspace_root)

            self.assertIn("escaped its parent", escaped_cleanup)
            self.assertIn("did not match", modified_cleanup)
            self.assertTrue(escaped.is_dir())
            self.assertTrue(workspace_root.is_dir())

    def test_workspace_cleanup_leaves_a_shared_parent_and_reports_removal_errors(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            source = root / "source"
            source.mkdir()
            runner = LaneRunner.__new__(LaneRunner)
            runner.repository = repository
            fixture = Fixture(
                directory=source,
                identifier="fixture",
                expected={},
                backends=("docker",),
                runner="devcontainer",
            )
            first_root, _ = runner.create_fixture_workspace(fixture)
            second_root, _ = runner.create_fixture_workspace(fixture)

            self.assertEqual(runner.cleanup_fixture_workspace(first_root), "")
            self.assertTrue(second_root.parent.is_dir())

            with mock.patch(
                "run_lane.shutil.rmtree",
                side_effect=OSError("permission denied"),
            ):
                failed_cleanup = runner.cleanup_fixture_workspace(second_root)

            self.assertIn("cleanup failed", failed_cleanup)
            self.assertTrue(second_root.is_dir())


class PortEvidenceTests(unittest.TestCase):
    def test_failed_host_connections_are_preserved_as_evidence(self) -> None:
        with TemporaryDirectory() as temporary:
            runner = LaneRunner.__new__(LaneRunner)
            runner.repository = Path("/repository")
            runner.environment = {"PATH": "/usr/bin:/bin"}
            runner.docker = "/usr/bin/docker"
            completed = [
                mock.Mock(returncode=17, stdout="", stderr="collision"),
                mock.Mock(returncode=0, stdout="", stderr=""),
            ]

            with (
                mock.patch(
                    "run_lane.urllib.request.urlopen",
                    side_effect=urllib.error.URLError("No route to host"),
                ),
                mock.patch("run_lane.time.sleep"),
                mock.patch(
                    "run_lane.subprocess.run",
                    side_effect=completed,
                ),
            ):
                observations = runner.validate_ports(Path(temporary))

            evidence = (
                Path(temporary) / "host-connectivity.log"
            ).read_text(encoding="utf-8")

        self.assertEqual(
            observations,
            {
                "collision_rejected": "true",
                "host_connectivity": "false",
            },
        )
        self.assertIn("attempt 1: URLError: <urlopen error No route", evidence)
        self.assertIn("attempt 50: URLError: <urlopen error No route", evidence)


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
    def test_stock_client_reports_buildx_unavailable(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            docker = root / "docker"
            docker.write_text(
                "#!/bin/sh\nprintf 'forwarded:%s\\n' \"$*\"\n",
                encoding="utf-8",
            )
            docker.chmod(0o700)
            runner = LaneRunner.__new__(LaneRunner)
            runner.lane = "apple-stock"
            runner.docker = str(docker)
            runner.devcontainer_docker = runner.docker
            runner.socket_root = root
            runner.environment = {}

            runner.configure_devcontainer_client()
            buildx = subprocess.run(
                [runner.docker, "buildx", "version"],
                capture_output=True,
                check=False,
                text=True,
            )
            forwarded = subprocess.run(
                [runner.docker, "version"],
                capture_output=True,
                check=False,
                text=True,
            )
            legacy_build = subprocess.run(
                [
                    runner.docker,
                    "build",
                    "--progress",
                    "plain",
                    "--load",
                    ".",
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            wrapper_source = Path(runner.docker).read_text(encoding="utf-8")

        self.assertNotEqual(buildx.returncode, 0)
        self.assertIn("unknown command", buildx.stderr)
        self.assertEqual(forwarded.returncode, 0)
        self.assertEqual(forwarded.stdout.strip(), "forwarded:version")
        self.assertEqual(legacy_build.returncode, 0)
        self.assertEqual(legacy_build.stdout.strip(), "forwarded:build .")
        self.assertTrue(wrapper_source.startswith("#!/bin/sh\n"))
        self.assertTrue(wrapper_source.endswith('exec "$docker" "$@"\n'))
        self.assertEqual(runner.environment["DOCKER_BUILDKIT"], "0")
        self.assertEqual(
            runner.environment["DEVCONTAINER_DOCKER_BIN"],
            runner.devcontainer_docker,
        )
        self.assertEqual(runner.docker, runner.devcontainer_docker)

    def test_resolver_nameservers_rejects_invalid_and_duplicate_entries(
        self,
    ) -> None:
        self.assertEqual(
            resolver_nameservers(
                """
                nameserver 192.0.2.53
                nameserver 2001:db8::53
                nameserver fe80::1%en0
                nameserver 192.0.2.53
                nameserver invalid.example
                search example.test
                """
            ),
            ["192.0.2.53", "2001:db8::53", "fe80::1%en0"],
        )

    def test_provider_builder_disables_restart_and_uses_host_dns(self) -> None:
        with TemporaryDirectory() as temporary:
            runner = LaneRunner.__new__(LaneRunner)
            runner.lane = "container-compose"
            runner.repository = Path("/repository")
            runner.environment = {"PATH": "/usr/bin:/bin"}
            runner.docker = "/usr/bin/docker"
            runner.output = Path(temporary)
            completed = [
                mock.Mock(returncode=0, stdout="", stderr=""),
                mock.Mock(returncode=0, stdout="", stderr=""),
            ]

            with (
                mock.patch.object(
                    runner,
                    "docker_container_inventory",
                    side_effect=[set(), {"builder-container-id"}],
                ),
                mock.patch(
                    "run_lane.subprocess.run",
                    side_effect=completed,
                ) as run,
                mock.patch(
                    "run_lane.Path.read_text",
                    return_value="nameserver 192.0.2.53\n",
                ),
                mock.patch("run_lane.os.getpid", return_value=123),
            ):
                runner.prepare_builder()

        self.assertEqual(
            run.call_args_list[0].args[0],
            [
                "/usr/bin/docker",
                "buildx",
                "create",
                "--name",
                "devcontainer-parity-container-compose-123",
                "--driver",
                "docker-container",
                "--driver-opt",
                "restart-policy=no",
                "--buildkitd-config",
                str(Path(temporary) / "buildkitd.toml"),
            ],
        )
        self.assertEqual(
            runner.environment["BUILDX_BUILDER"],
            "devcontainer-parity-container-compose-123",
        )
        self.assertEqual(
            runner.builder_container_ids,
            {"builder-container-id"},
        )

    def test_docker_builder_uses_daemon_integrated_buildkit(self) -> None:
        with TemporaryDirectory() as temporary:
            runner = LaneRunner.__new__(LaneRunner)
            runner.lane = "docker"
            runner.repository = Path("/repository")
            runner.environment = {"PATH": "/usr/bin:/bin"}
            runner.docker = "/usr/bin/docker"
            runner.output = Path(temporary)
            runner.builder_name = None

            with mock.patch(
                "run_lane.subprocess.run",
                return_value=mock.Mock(returncode=0, stdout="ready", stderr=""),
            ) as run:
                runner.prepare_builder()

        self.assertEqual(
            run.call_args.args[0],
            [
                "/usr/bin/docker",
                "buildx",
                "inspect",
                "--bootstrap",
                "default",
            ],
        )
        self.assertEqual(runner.environment["BUILDX_BUILDER"], "default")
        self.assertIsNone(runner.builder_name)

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
            with closing(sqlite3.connect(state)) as database, database:
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
