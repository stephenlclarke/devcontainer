#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Unit tests for the real VS Code parity orchestrator."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest import mock

from parity_lib import Fixture, ParityError
from run_lane import FIXTURE_WORKSPACE_MARKER, FIXTURE_WORKSPACE_MARKER_CONTENT, LaneRunner
from run_vscode import (
    DRIVER_PHASE_TIMEOUT_SECONDS,
    DriverPhaseDeadline,
    FIXTURE_ID,
    VSCodeLane,
    VSCodePins,
    code_command,
    decode_vsix_response,
    download_vsix,
    isolated_vscode_processes,
    no_resources_remain,
    parse_code_version,
    scrub_sensitive_evidence,
    validate_driver_result,
    verify_code_version,
    verify_guest_workspace,
    verify_signing_output,
    verify_vsix_metadata,
    vscode_environment,
    vscode_settings,
)


def pins(**overrides: str) -> VSCodePins:
    """Create concise immutable pins for focused tests."""

    values = {
        "version": "1.2.3",
        "commit": "a" * 40,
        "platform": "darwin-arm64",
        "archive_url": "https://example.invalid/code.zip",
        "archive_sha256": "1" * 64,
        "application_identifier": "com.microsoft.VSCode",
        "signing_team_identifier": "UBF8T346G9",
        "extension_version": "4.5.6",
        "extension_url": "https://example.invalid/extension.vsix",
        "extension_sha256": "",
        "embedded_cli_version": "7.8.9",
        "embedded_cli_commit": "b" * 40,
        "embedded_cli_sha256": "",
    }
    values.update(overrides)
    return VSCodePins(**values)


def vsix_bytes(cli: bytes = b"embedded-cli") -> bytes:
    """Build a minimal in-memory VSIX with the authenticated identity fields."""

    output = io.BytesIO()
    package = {
        "dependencies": {
            "@devcontainers/cli": f"https://github.com/devcontainers/cli.git#{'b' * 40}"
        },
        "name": "remote-containers",
        "publisher": "ms-vscode-remote",
        "version": "4.5.6",
    }
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("extension/package.json", json.dumps(package))
        archive.writestr(
            "extension/dist/spec-node/devContainersSpecCLI.js",
            cli,
        )
    return output.getvalue()


class Response:
    """Context-managed fake URL response."""

    def __init__(self, value: bytes) -> None:
        self.value = value

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, _: int) -> bytes:
        return self.value


class VSCodeParityTests(unittest.TestCase):
    def test_guest_workspace_admission_checks_exact_lifecycle_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            colima = root / "colima"
            colima.write_bytes(b"pinned colima executable")
            colima.chmod(0o700)
            workspace = root / "repository" / ".build" / "parity-workspaces" / "case"
            source = workspace / ".devcontainer"
            source.mkdir(parents=True)
            workspace = workspace.resolve()
            source = workspace / ".devcontainer"
            (source / "devcontainer.json").write_text("{}\n", encoding="utf-8")
            lifecycle = source / "lifecycle.sh"
            lifecycle.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            expected_digests = {
                str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (source / "devcontainer.json", lifecycle)
            }
            calls: list[tuple[list[str], dict[str, object]]] = []

            def guest_run(
                command: list[str], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                calls.append((command, kwargs))
                if "sha256sum" in command:
                    path = command[-1]
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        f"{expected_digests[path]}  {path}\n",
                        "",
                    )
                return subprocess.CompletedProcess(command, 0, "", "")

            verify_guest_workspace(
                colima,
                hashlib.sha256(colima.read_bytes()).hexdigest(),
                workspace,
                {"PATH": "/usr/bin:/bin"},
                run=guest_run,
            )

            self.assertEqual(len(calls), 4)
            self.assertEqual(calls[0][0][-3:], ["test", "-r", str(source / "devcontainer.json")])
            self.assertEqual(calls[1][0][-3:], ["sha256sum", "--", str(source / "devcontainer.json")])
            self.assertEqual(calls[2][0][-3:], ["test", "-r", str(lifecycle)])
            self.assertEqual(calls[3][0][-3:], ["sha256sum", "--", str(lifecycle)])
            self.assertTrue(all(call[1]["timeout"] == 30 for call in calls))
            self.assertTrue(all(call[1]["env"] == {"PATH": "/usr/bin:/bin"} for call in calls))

    def test_guest_workspace_admission_rejects_missing_and_changed_guest_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            colima = root / "colima"
            colima.write_bytes(b"pinned colima executable")
            colima.chmod(0o700)
            workspace = root / "workspace"
            source = workspace / ".devcontainer"
            source.mkdir(parents=True)
            workspace = workspace.resolve()
            source = workspace / ".devcontainer"
            (source / "devcontainer.json").write_text("{}\n", encoding="utf-8")
            lifecycle = source / "lifecycle.sh"
            lifecycle.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable_digest = hashlib.sha256(colima.read_bytes()).hexdigest()

            def missing_lifecycle(
                command: list[str], **_kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                exit_code = int("-r" in command and command[-1] == str(lifecycle))
                if "sha256sum" in command:
                    path = command[-1]
                    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
                    return subprocess.CompletedProcess(command, 0, f"{digest}  {path}\n", "")
                return subprocess.CompletedProcess(command, exit_code, "", "missing guest file")

            self.assertRaisesRegex(
                ParityError,
                "cannot read.*lifecycle",
                verify_guest_workspace,
                colima,
                executable_digest,
                workspace,
                {},
                run=missing_lifecycle,
            )

            def mismatched_digest(
                command: list[str], **_kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                if "sha256sum" in command:
                    path = command[-1]
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        f"{'0' * 64}  {path}\n",
                        "",
                    )
                return subprocess.CompletedProcess(command, 0, "", "")

            self.assertRaisesRegex(
                ParityError,
                "bytes differ.*devcontainer.json",
                verify_guest_workspace,
                colima,
                executable_digest,
                workspace,
                {},
                run=mismatched_digest,
            )

    def test_guest_workspace_admission_rejects_changed_colima_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            colima = root / "colima"
            colima.write_bytes(b"changed colima executable")
            colima.chmod(0o700)
            workspace = root / "workspace"
            workspace.mkdir()
            runner = mock.Mock()

            self.assertRaisesRegex(
                ParityError,
                "Colima executable changed",
                verify_guest_workspace,
                colima,
                "0" * 64,
                workspace,
                {},
                run=runner,
            )
            runner.assert_not_called()

    def test_guest_workspace_rejects_noncanonical_colima_alias_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            colima = root / "colima"
            colima.write_bytes(b"pinned colima executable")
            colima.chmod(0o700)
            alias = root / "colima-alias"
            alias.symlink_to(colima)
            workspace = root / "workspace"
            workspace.mkdir()
            runner = mock.Mock()

            self.assertRaisesRegex(
                ParityError,
                "noncanonical or unusable",
                verify_guest_workspace,
                alias,
                hashlib.sha256(colima.read_bytes()).hexdigest(),
                workspace,
                {},
                run=runner,
            )
            runner.assert_not_called()

    def test_guest_workspace_rejects_non_executable_colima_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            colima = root / "colima"
            colima.write_bytes(b"pinned colima executable")
            colima.chmod(0o600)
            workspace = root / "workspace"
            workspace.mkdir()
            runner = mock.Mock()

            self.assertRaisesRegex(
                ParityError,
                "noncanonical or unusable",
                verify_guest_workspace,
                colima,
                hashlib.sha256(colima.read_bytes()).hexdigest(),
                workspace,
                {},
                run=runner,
            )
            runner.assert_not_called()

    def test_parent_phase_deadline_is_monotonic_and_resets_on_transition(self) -> None:
        deadline = DriverPhaseDeadline()
        deadline.observe("local-open", 0.0)
        deadline.observe("attaching", 1.0)
        deadline.observe("attaching", 1.0 + DRIVER_PHASE_TIMEOUT_SECONDS - 0.1)
        deadline.observe("rebuilding", 40.0)
        deadline.observe("rebuilding", 99.9)

        self.assertRaisesRegex(
            ParityError,
            "phase rebuilding timed out",
            deadline.observe,
            "rebuilding",
            100.0,
        )

    def test_launch_enforces_phase_deadline_after_extension_timer_is_gone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "evidence" / "docker"
            output.mkdir(parents=True)
            driver_state = root / "driver-state.json"
            driver_state.write_text('{"phase":"attaching"}\n', encoding="utf-8")
            lane = VSCodeLane.__new__(VSCodeLane)
            lane.lane = "docker"
            lane.repository = root
            lane.output = output
            lane.runtime = mock.Mock(environment={"PATH": "/usr/bin:/bin"})
            lane.vscode_gui = root / "Code"
            lane.devcontainer_docker_path = mock.Mock(return_value="/pinned/docker")
            process = mock.Mock()
            process.poll.return_value = None
            lane.process = None

            with (
                mock.patch("run_vscode.subprocess.Popen", return_value=process),
                mock.patch("run_vscode.os.killpg"),
                mock.patch("run_vscode.terminate_isolated_vscode"),
                mock.patch("run_vscode.time.monotonic", side_effect=[0.0, 0.0, 0.0, 61.0, 61.0]),
                mock.patch("run_vscode.time.sleep"),
            ):
                self.assertRaisesRegex(
                    ParityError,
                    "phase attaching timed out after 61.0 seconds",
                    lane.launch,
                    root / "workspace",
                    root / "profile" / "data",
                    root / "profile" / "extensions",
                    driver_state,
                    root / "driver-result.json",
                )

            process.wait.assert_called_once_with(timeout=10)

    def test_parent_phase_timeout_flows_through_fixture_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture_root = root / "fixture"
            fixture_root.mkdir()
            lane = VSCodeLane.__new__(VSCodeLane)
            lane.lane = "docker"
            lane.repository = root
            lane.evidence_root = root / "evidence"
            lane.output = lane.evidence_root / lane.lane
            lane.runtime = mock.Mock(
                docker="/usr/bin/docker",
                environment={"PATH": "/usr/bin:/bin"},
            )
            workspace_owner = LaneRunner.__new__(LaneRunner)
            workspace_owner.repository = root
            created: dict[str, Path] = {}

            def create_workspace(fixture: Fixture) -> tuple[Path, Path]:
                workspace_root, workspace = workspace_owner.create_fixture_workspace(
                    fixture
                )
                created["root"] = workspace_root
                created["workspace"] = workspace
                return workspace_root, workspace

            lane.runtime.create_fixture_workspace.side_effect = create_workspace
            lane.runtime.cleanup_fixture_workspace.side_effect = (
                workspace_owner.cleanup_fixture_workspace
            )
            lane.runtime.cleanup_fixture.return_value = ""
            lane.fixture = mock.Mock(
                return_value=Fixture(
                    identifier=FIXTURE_ID,
                    directory=fixture_root,
                    expected={"open": "true"},
                    backends=("docker",),
                    runner="vscode",
                )
            )
            lane.require_reference = mock.Mock(return_value={})
            lane.prepare_runtime = mock.Mock(return_value={})
            lane.require_guest_workspace = mock.Mock()
            lane.compose_path = mock.Mock(return_value="/usr/bin/docker-compose")
            lane.install_extensions = mock.Mock()
            lane.launch = mock.Mock(
                side_effect=ParityError(
                    "VS Code driver phase attaching timed out after 60.0 seconds"
                )
            )
            with (
                mock.patch("run_vscode.discover_compose_project", return_value=""),
                mock.patch(
                    "run_vscode.no_resources_remain",
                    return_value=(True, "clean\n"),
                ) as absence,
                mock.patch("run_vscode.scrub_sensitive_evidence", return_value=([], [])),
                mock.patch("run_vscode.terminate_isolated_vscode"),
                mock.patch("run_vscode.assert_contract", return_value=[]),
                mock.patch("run_vscode.time.monotonic", side_effect=[100.0, 102.0]),
            ):
                status = lane.run()

            self.assertEqual(status, 1)
            lane.runtime.cleanup_fixture.assert_called_once()
            absence.assert_called_once()
            lane.runtime.cleanup_fixture_workspace.assert_not_called()
            self.assertTrue(created["root"].exists())
            result = json.loads((lane.output / "results.json").read_text(encoding="utf-8"))
            self.assertIn("phase attaching timed out", result["fixtures"][0]["diagnostic"])
            self.assertIn("workspace preserved", result["fixtures"][0]["diagnostic"])
            self.assertEqual(result["fixtures"][0]["observations"]["cleanup"], "false")

    def test_discovery_timeout_still_cleans_engine_and_writes_failure_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture_root = root / "fixture"
            fixture_root.mkdir()
            lane = VSCodeLane.__new__(VSCodeLane)
            lane.lane = "docker"
            lane.repository = root
            lane.evidence_root = root / "evidence"
            lane.output = lane.evidence_root / lane.lane
            lane.runtime = mock.Mock(docker="/usr/bin/docker", environment={"PATH": "/usr/bin:/bin"})
            workspace_owner = LaneRunner.__new__(LaneRunner)
            workspace_owner.repository = root
            created: dict[str, Path] = {}

            def create_workspace(fixture: Fixture) -> tuple[Path, Path]:
                workspace_root, workspace = workspace_owner.create_fixture_workspace(fixture)
                created["root"] = workspace_root
                created["workspace"] = workspace
                return workspace_root, workspace

            lane.runtime.create_fixture_workspace.side_effect = create_workspace
            lane.runtime.cleanup_fixture_workspace.side_effect = workspace_owner.cleanup_fixture_workspace
            lane.runtime.cleanup_fixture.return_value = "cleanup attempted\n"
            lane.fixture = mock.Mock(return_value=Fixture(
                identifier=FIXTURE_ID,
                directory=fixture_root,
                expected={"open": "true"},
                backends=("docker",),
                runner="vscode",
            ))
            lane.require_reference = mock.Mock(return_value={})
            lane.prepare_runtime = mock.Mock(return_value={})
            lane.require_guest_workspace = mock.Mock()
            lane.compose_path = mock.Mock(return_value="/usr/bin/docker-compose")
            lane.install_extensions = mock.Mock()
            lane.launch = mock.Mock(side_effect=ParityError("driver failed"))
            with (
                mock.patch(
                    "run_vscode.discover_compose_project",
                    side_effect=subprocess.TimeoutExpired("docker inspect", 30),
                ),
                mock.patch("run_vscode.no_resources_remain", return_value=(False, "uncertain\n")),
                mock.patch("run_vscode.scrub_sensitive_evidence", return_value=([], [])),
                mock.patch("run_vscode.terminate_isolated_vscode"),
                mock.patch("run_vscode.assert_contract", return_value=[]),
                mock.patch("run_vscode.time.monotonic", side_effect=[100.0, 102.0]),
            ):
                status = lane.run()

            self.assertEqual(status, 1)
            lane.runtime.cleanup_fixture.assert_called_once()
            lane.runtime.stop_engine.assert_called_once()
            lane.runtime.cleanup_fixture_workspace.assert_not_called()
            self.assertTrue(created["root"].exists())
            self.assertIn("Compose project discovery failed", (lane.output / "cleanup.log").read_text())
            self.assertTrue((lane.output / "security-scan.json").is_file())

    def test_no_resources_timeout_preserves_workspace_and_writes_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture_root = root / "fixture"
            fixture_root.mkdir()
            lane = VSCodeLane.__new__(VSCodeLane)
            lane.lane = "docker"
            lane.repository = root
            lane.evidence_root = root / "evidence"
            lane.output = lane.evidence_root / lane.lane
            lane.runtime = mock.Mock(docker="/usr/bin/docker", environment={"PATH": "/usr/bin:/bin"})
            workspace_owner = LaneRunner.__new__(LaneRunner)
            workspace_owner.repository = root
            created: dict[str, Path] = {}

            def create_workspace(fixture: Fixture) -> tuple[Path, Path]:
                workspace_root, workspace = workspace_owner.create_fixture_workspace(fixture)
                created["root"] = workspace_root
                created["workspace"] = workspace
                return workspace_root, workspace

            lane.runtime.create_fixture_workspace.side_effect = create_workspace
            lane.runtime.cleanup_fixture_workspace.side_effect = workspace_owner.cleanup_fixture_workspace
            lane.runtime.cleanup_fixture.return_value = "cleanup attempted\n"
            lane.fixture = mock.Mock(return_value=Fixture(
                identifier=FIXTURE_ID,
                directory=fixture_root,
                expected={"open": "true"},
                backends=("docker",),
                runner="vscode",
            ))
            lane.require_reference = mock.Mock(return_value={})
            lane.prepare_runtime = mock.Mock(return_value={})
            lane.require_guest_workspace = mock.Mock()
            lane.compose_path = mock.Mock(return_value="/usr/bin/docker-compose")
            lane.install_extensions = mock.Mock()
            lane.launch = mock.Mock(side_effect=ParityError("driver failed"))
            with (
                mock.patch("run_vscode.discover_compose_project", return_value="known-project"),
                mock.patch(
                    "run_vscode.no_resources_remain",
                    side_effect=subprocess.TimeoutExpired("docker network ls", 30),
                ),
                mock.patch("run_vscode.scrub_sensitive_evidence", return_value=([], [])),
                mock.patch("run_vscode.terminate_isolated_vscode"),
                mock.patch("run_vscode.assert_contract", return_value=[]),
                mock.patch("run_vscode.time.monotonic", side_effect=[100.0, 102.0]),
            ):
                status = lane.run()

            self.assertEqual(status, 1)
            lane.runtime.cleanup_fixture.assert_called_once()
            lane.runtime.stop_engine.assert_called_once()
            lane.runtime.cleanup_fixture_workspace.assert_not_called()
            self.assertTrue(created["root"].exists())
            self.assertIn("resource verification failed", (lane.output / "cleanup.log").read_text())
            self.assertTrue((lane.output / "security-scan.json").is_file())

    def test_empty_compose_project_cannot_prove_network_volume_cleanup(self) -> None:
        with mock.patch("run_vscode.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            clean, evidence = no_resources_remain(
                "/usr/bin/docker", Path("/workspace"), "", {"PATH": "/usr/bin:/bin"}
            )
        self.assertFalse(clean)
        self.assertIn("Compose project identity is unavailable", evidence)
        self.assertEqual(run.call_count, 1)

    def test_gui_termination_failure_preserves_workspace_profile_and_cleanup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture_root = root / "fixture"
            fixture_root.mkdir()
            profile_root = root / "retained-profile"
            profile_root.mkdir()
            lane = VSCodeLane.__new__(VSCodeLane)
            lane.lane = "docker"
            lane.repository = root
            lane.evidence_root = root / "evidence"
            lane.output = lane.evidence_root / lane.lane
            lane.runtime = mock.Mock(
                docker="/usr/bin/docker",
                environment={"PATH": "/usr/bin:/bin"},
                finalized_identity=None,
            )
            workspace_owner = LaneRunner.__new__(LaneRunner)
            workspace_owner.repository = root
            created: dict[str, Path] = {}

            def create_workspace(fixture: Fixture) -> tuple[Path, Path]:
                workspace_root, workspace = workspace_owner.create_fixture_workspace(fixture)
                created["root"] = workspace_root
                created["workspace"] = workspace
                return workspace_root, workspace

            lane.runtime.create_fixture_workspace.side_effect = create_workspace
            lane.runtime.cleanup_fixture_workspace.side_effect = workspace_owner.cleanup_fixture_workspace
            lane.fixture = mock.Mock(return_value=Fixture(
                identifier=FIXTURE_ID,
                directory=fixture_root,
                expected={"open": "true"},
                backends=("docker",),
                runner="vscode",
            ))
            lane.require_reference = mock.Mock(return_value={})
            lane.prepare_runtime = mock.Mock(return_value={})
            lane.require_guest_workspace = mock.Mock()
            lane.compose_path = mock.Mock(return_value="/usr/bin/docker-compose")
            lane.install_extensions = mock.Mock()
            lane.launch = mock.Mock(side_effect=ParityError("driver failed"))

            with (
                mock.patch("run_vscode.tempfile.mkdtemp", return_value=str(profile_root)),
                mock.patch(
                    "run_vscode.terminate_isolated_vscode",
                    side_effect=ParityError("owned GUI process did not stop"),
                ),
                mock.patch("run_vscode.discover_compose_project", return_value="known-project") as discover,
                mock.patch("run_vscode.no_resources_remain", return_value=(True, "clean\n")) as absence,
                mock.patch("run_vscode.scrub_sensitive_evidence", return_value=([], [])),
                mock.patch("run_vscode.assert_contract", return_value=[]),
                mock.patch("run_vscode.time.monotonic", side_effect=[100.0, 102.0]),
            ):
                status = lane.run()

            self.assertEqual(status, 1)
            lane.runtime.cleanup_fixture.assert_not_called()
            absence.assert_not_called()
            discover.assert_not_called()
            lane.runtime.cleanup_fixture_workspace.assert_not_called()
            lane.runtime.stop_engine.assert_called_once()
            self.assertTrue(created["root"].exists())
            self.assertTrue(profile_root.exists())
            cleanup_log = (lane.output / "cleanup.log").read_text(encoding="utf-8")
            self.assertIn("process closure is uncertain", cleanup_log)
            self.assertIn("profile preserved", cleanup_log)
            self.assertTrue((lane.output / "security-scan.json").is_file())
            result = json.loads((lane.output / "results.json").read_text(encoding="utf-8"))
            self.assertEqual(result["fixtures"][0]["observations"]["cleanup"], "false")

    def test_native_compose_setting_selects_admitted_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "devcontainer-compose"
            executable.write_bytes(b"signed adapter")
            lane = VSCodeLane.__new__(VSCodeLane)
            lane.lane = "container-compose"
            lane.repository = Path("/repository")
            lane.runtime = mock.Mock()
            lane.runtime.package_executable.return_value = str(executable)
            self.assertEqual(lane.compose_path(), str(executable))
            lane.runtime.package_executable.assert_called_once_with("devcontainer-compose")

    def test_native_vscode_uses_packaged_docker_surface(self) -> None:
        lane = VSCodeLane.__new__(VSCodeLane)
        lane.lane = "apple-stock"
        lane.runtime = mock.Mock()
        lane.runtime.finalized_selection = {"expected_source_commit": "a" * 40}
        lane.runtime.package_executable.return_value = "/signed/devcontainer-docker"
        self.assertEqual(lane.devcontainer_docker_path(), "/signed/devcontainer-docker")

    def test_code_version_requires_all_three_identity_lines(self) -> None:
        self.assertEqual(
            parse_code_version(f"1.2.3\n{'a' * 40}\narm64\n"),
            ("1.2.3", "a" * 40, "arm64"),
        )
        with self.assertRaisesRegex(ParityError, "2 non-empty lines"):
            parse_code_version("1.2.3\narm64\n")

    def test_code_version_rejects_a_different_commit(self) -> None:
        output = f"1.2.3\n{'c' * 40}\narm64\n"
        reference_pins = pins()
        with self.assertRaisesRegex(ParityError, "identity differs"):
            verify_code_version(output, reference_pins)

    def test_signing_identity_rejects_a_different_team(self) -> None:
        output = "Identifier=com.microsoft.VSCode\nTeamIdentifier=DIFFERENT\n"
        reference_pins = pins()
        with self.assertRaisesRegex(ParityError, "signing identity differs"):
            verify_signing_output(output, reference_pins)

    def test_gzip_marketplace_response_is_expanded(self) -> None:
        value = vsix_bytes()
        self.assertEqual(decode_vsix_response(gzip.compress(value)), value)
        with self.assertRaisesRegex(ParityError, "not a VSIX"):
            decode_vsix_response(b"not-a-zip")

    def test_download_authenticates_expanded_vsix(self) -> None:
        value = vsix_bytes()
        digest = __import__("hashlib").sha256(value).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "extension.vsix"
            result = download_vsix(
                "https://example.invalid/extension.vsix",
                destination,
                digest,
                opener=lambda *_args, **_kwargs: Response(gzip.compress(value)),
            )
            self.assertEqual(result.read_bytes(), value)

    def test_vsix_metadata_binds_extension_and_embedded_cli(self) -> None:
        value = vsix_bytes()
        cli_digest = __import__("hashlib").sha256(b"embedded-cli").hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            extension = Path(temporary) / "extension.vsix"
            extension.write_bytes(value)
            identity = verify_vsix_metadata(
                extension,
                pins(embedded_cli_sha256=cli_digest),
            )
        self.assertEqual(identity["version"], "4.5.6")
        self.assertEqual(identity["embeddedCliCommit"], "b" * 40)

    def test_driver_result_requires_exact_true_observations(self) -> None:
        observations = {
            "attach": True,
            "extension_activation": True,
            "forward_port": True,
            "integrated_command": True,
            "open": True,
            "rebuild": True,
            "reopen": True,
            "vscode_server": True,
        }
        self.assertEqual(
            validate_driver_result(
                {
                    "observations": observations,
                    "status": "ready-for-cleanup",
                }
            )["rebuild"],
            "true",
        )
        observations["rebuild"] = False
        with self.assertRaisesRegex(ParityError, "failed observation"):
            validate_driver_result(
                {
                    "observations": observations,
                    "status": "ready-for-cleanup",
                }
            )

    def test_settings_disable_updates_and_bind_explicit_tools(self) -> None:
        settings = vscode_settings("/tool/docker", "/tool/compose")
        self.assertEqual(settings["dev.containers.dockerPath"], "/tool/docker")
        self.assertEqual(
            settings["dev.containers.dockerComposePath"],
            "/tool/compose",
        )
        self.assertEqual(settings["update.mode"], "none")
        self.assertFalse(settings["security.workspace.trust.enabled"])

    def test_launch_command_uses_isolated_profile_and_extension_root(self) -> None:
        command = code_command(
            "/tool/code",
            Path("/evidence/user"),
            Path("/evidence/extensions"),
            Path("/evidence/workspace"),
            Path("/source/driver"),
        )
        self.assertEqual(command[0], "/tool/code")
        self.assertIn("/evidence/user", command)
        self.assertIn("/evidence/extensions", command)
        self.assertIn("--force-disable-user-env", command)
        self.assertIn("--use-inmemory-secretstorage", command)
        self.assertIn("--extensionDevelopmentPath=/source/driver", command)
        self.assertEqual(command[-1], "/evidence/workspace")

    def test_process_cleanup_selects_only_the_unique_isolated_profile(self) -> None:
        output = "\n".join(
            [
                "  101 /Applications/Visual Studio Code.app/Code "
                "--user-data-dir /tmp/dc-vscode-docker-unique/data",
                "  102 Code Helper --user-data-dir=/tmp/dc-vscode-other/data",
                "invalid command /tmp/dc-vscode-docker-unique/data",
                "  101 duplicate /tmp/dc-vscode-docker-unique/data",
                "  103 shutdownMonitor "
                "/tmp/dc-vscode-docker-unique/data/logs/window1",
            ]
        )
        self.assertEqual(
            isolated_vscode_processes(
                output,
                Path("/tmp/dc-vscode-docker-unique/data"),
            ),
            [101, 103],
        )

    def test_gui_environment_is_fail_closed_and_uses_isolated_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = vscode_environment(
                {
                    "CONTAINER_COMPOSE_CONTAINER": "/stable/bin/container",
                    "DEVCONTAINER_COMPOSE_PROVIDER": "docker",
                    "DEVCONTAINER_CONTAINER_BIN": "/pinned/container",
                    "DEVCONTAINER_DOCKER_BIN": "/pinned/docker",
                    "DEVCONTAINER_DOCKER_COMPOSE_BIN": "/pinned/docker-compose",
                    "DEVCONTAINER_CONFIG": "/private/tmp/config.toml",
                    "DEVCONTAINER_SOCKET": "/private/tmp/devcontainer.sock",
                    "DEVCONTAINER_STATE": "/private/tmp/state.sqlite",
                    "DOCKER_HOST": "unix:///private/tmp/docker.sock",
                    "GITHUB_TOKEN": "must-not-leak",
                    "HOME": "/Users/operator",
                    "PATH": "/usr/bin:/bin",
                    "SONAR_TOKEN": "must-not-leak",
                },
                Path(temporary),
            )
            self.assertEqual(
                environment["DOCKER_HOST"],
                "unix:///private/tmp/docker.sock",
            )
            self.assertEqual(
                environment["CONTAINER_COMPOSE_CONTAINER"],
                "/stable/bin/container",
            )
            self.assertEqual(
                environment["DEVCONTAINER_CONFIG"],
                "/private/tmp/config.toml",
            )
            self.assertEqual(
                environment["DEVCONTAINER_SOCKET"],
                "/private/tmp/devcontainer.sock",
            )
            self.assertEqual(
                environment["DEVCONTAINER_STATE"],
                "/private/tmp/state.sqlite",
            )
            for key, expected in {
                "DEVCONTAINER_COMPOSE_PROVIDER": "docker",
                "DEVCONTAINER_CONTAINER_BIN": "/pinned/container",
                "DEVCONTAINER_DOCKER_BIN": "/pinned/docker",
                "DEVCONTAINER_DOCKER_COMPOSE_BIN": "/pinned/docker-compose",
            }.items():
                self.assertEqual(environment[key], expected)
            self.assertEqual(environment["HOME"], f"{temporary}/home")
            self.assertEqual(environment["LOGNAME"], "devcontainer-runner")
            self.assertEqual(environment["SHELL"], "/bin/zsh")
            self.assertEqual(environment["TMPDIR"], f"{temporary}/tmp")
            self.assertEqual(environment["USER"], "devcontainer-runner")
            self.assertNotIn("GITHUB_TOKEN", environment)
            self.assertNotIn("SONAR_TOKEN", environment)

    def test_sensitive_evidence_is_removed_without_retaining_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            safe = root / "safe.log"
            leaked = root / "leaked.json"
            safe.write_text("status=passed\n", encoding="utf-8")
            leaked.write_text(
                '{"GITHUB_TOKEN":"must-not-survive"}\n',
                encoding="utf-8",
            )

            names, removed = scrub_sensitive_evidence(root)

            self.assertEqual(names, ["GITHUB_TOKEN"])
            self.assertEqual(removed, ["leaked.json"])
            self.assertTrue(safe.is_file())
            self.assertFalse(leaked.exists())

            (root / "security-scan.json").write_text(
                '{"detectedNames":["GITHUB_TOKEN"]}\n',
                encoding="utf-8",
            )
            names, removed = scrub_sensitive_evidence(root)
            self.assertEqual((names, removed), ([], []))

    def test_completed_vscode_fixture_records_junit_timing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture_root = root / "fixture"
            fixture_root.mkdir()
            lane = VSCodeLane.__new__(VSCodeLane)
            lane.lane = "docker"
            lane.repository = root
            lane.evidence_root = root / "evidence"
            lane.output = lane.evidence_root / lane.lane
            lane.runtime = mock.Mock(
                docker="/usr/bin/docker",
                environment={"PATH": "/usr/bin:/bin"},
            )
            workspace_owner = LaneRunner.__new__(LaneRunner)
            workspace_owner.repository = root

            created_workspace: dict[str, Path] = {}

            def create_owned_workspace(fixture: Fixture) -> tuple[Path, Path]:
                workspace_root, workspace = workspace_owner.create_fixture_workspace(
                    fixture
                )
                created_workspace["root"] = workspace_root
                created_workspace["workspace"] = workspace
                return workspace_root, workspace

            def verify_owned_workspace(
                colima_bin: Path,
                colima_sha256: str,
                workspace: Path,
                _environment: dict[str, str],
            ) -> None:
                self.assertEqual(colima_bin, lane.colima_bin)
                self.assertEqual(colima_sha256, lane.colima_sha256)
                marker = workspace.parent / FIXTURE_WORKSPACE_MARKER
                self.assertEqual(
                    marker.read_text(encoding="utf-8"),
                    FIXTURE_WORKSPACE_MARKER_CONTENT,
                )

            lane.runtime.create_fixture_workspace.side_effect = create_owned_workspace
            lane.runtime.cleanup_fixture_workspace.side_effect = (
                workspace_owner.cleanup_fixture_workspace
            )
            lane.runtime.cleanup_fixture.return_value = ""
            lane.colima_bin = Path("/admitted/colima")
            lane.colima_sha256 = "c" * 64
            lane.fixture = mock.Mock(
                return_value=Fixture(
                    identifier=FIXTURE_ID,
                    directory=fixture_root,
                    expected={"open": "true"},
                    backends=("docker",),
                    runner="vscode",
                )
            )
            lane.require_reference = mock.Mock(return_value={})
            lane.prepare_runtime = mock.Mock(return_value={})
            lane.compose_path = mock.Mock(return_value="/usr/bin/docker-compose")
            lane.install_extensions = mock.Mock()

            def complete_driver(
                _workspace: Path,
                _user_data: Path,
                _extensions: Path,
                _driver_state: Path,
                driver_result: Path,
            ) -> None:
                driver_result.write_text("{}\n", encoding="utf-8")

            lane.launch = mock.Mock(side_effect=complete_driver)
            observations = {
                "attach": "true",
                "cleanup": "true",
                "extension_activation": "true",
                "forward_port": "true",
                "integrated_command": "true",
                "open": "true",
                "rebuild": "true",
                "reopen": "true",
                "vscode_server": "true",
            }
            with (
                mock.patch(
                    "run_vscode.validate_driver_result",
                    return_value=observations,
                ),
                mock.patch(
                    "run_vscode.discover_compose_project",
                    return_value="known-project",
                ),
                mock.patch(
                    "run_vscode.verify_guest_workspace",
                    side_effect=verify_owned_workspace,
                ) as guest_check,
                mock.patch(
                    "run_vscode.no_resources_remain",
                    return_value=(True, "clean\n"),
                ),
                mock.patch(
                    "run_vscode.scrub_sensitive_evidence",
                    return_value=([], []),
                ),
                mock.patch("run_vscode.terminate_isolated_vscode"),
                mock.patch("run_vscode.assert_contract", return_value=[]),
                mock.patch(
                    "run_vscode.time.monotonic",
                    side_effect=[100.0, 102.5],
                ),
            ):
                status = lane.run()

            report = ET.parse(lane.output / "junit.xml").getroot()
            case = report.find("testcase")
            self.assertEqual(status, 0)
            self.assertEqual(report.attrib["time"], "2.500")
            self.assertIsNotNone(case)
            self.assertEqual(case.attrib["name"], FIXTURE_ID)
            self.assertEqual(case.attrib["time"], "2.500")
            workspace_root = created_workspace["root"]
            workspace = created_workspace["workspace"]
            self.assertTrue(
                workspace.is_relative_to(root / ".build" / "parity-workspaces")
            )
            self.assertFalse(workspace.exists())
            guest_check.assert_called_once_with(
                lane.colima_bin,
                lane.colima_sha256,
                workspace,
                lane.runtime.environment,
            )
            lane.runtime.cleanup_fixture_workspace.assert_called_once_with(
                workspace_root
            )


if __name__ == "__main__":
    unittest.main()
