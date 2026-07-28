#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Run official Dev Containers CLI fixtures against one runtime lane."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from parity_lib import (
    LANES,
    ParityError,
    assert_contract,
    atomic_json,
    implemented_fixtures,
    load_manifest,
    parse_observations,
)


class LaneRunner:
    """Owns lane processes, commands, evidence, and deterministic cleanup."""

    def __init__(self, lane: str, repository: Path, evidence_root: Path) -> None:
        self.lane = lane
        self.repository = repository
        self.output = evidence_root / lane
        self.runtime_root = evidence_root / "runtime" / lane
        self.manifest = load_manifest(
            repository / "Tests" / "Parity" / "manifest.json"
        )
        self.cli_version = self.manifest["referencePins"]["devcontainersCli"][
            "version"
        ]
        self.docker = os.environ.get("DEVCONTAINER_DOCKER_BIN") or shutil.which(
            "docker"
        )
        self.node_package_runner = shutil.which("npx")
        self.engine: subprocess.Popen[bytes] | None = None
        self.engine_log: Any | None = None
        self.builder_name: str | None = None
        self.builder_container_ids: set[str] = set()
        self.cleanup_differences: list[str] = []
        self.socket_root: Path | None = None
        self.environment = safe_environment(os.environ)

    def run(self) -> int:
        if self.lane not in LANES:
            raise ParityError(f"unknown lane {self.lane!r}")
        if not self.docker:
            raise ParityError("docker CLI is required")
        if not self.node_package_runner:
            raise ParityError("npx is required for the pinned @devcontainers/cli")

        if self.output.exists():
            shutil.rmtree(self.output)
        self.output.mkdir(parents=True)
        fixtures = implemented_fixtures(self.repository, self.manifest)
        fixtures = [
            fixture
            for fixture in fixtures
            if self.lane in fixture.backends and fixture.runner != "vscode"
        ]
        selected = {
            value.strip()
            for value in os.environ.get(
                "DEVCONTAINER_PARITY_FIXTURES",
                "",
            ).split(",")
            if value.strip()
        }
        if selected:
            available = {fixture.identifier for fixture in fixtures}
            unknown = selected - available
            if unknown:
                raise ParityError(
                    "unknown or unavailable parity fixture(s): "
                    + ", ".join(sorted(unknown))
                )
            fixtures = [
                fixture
                for fixture in fixtures
                if fixture.identifier in selected
            ]
        if self.lane != "docker":
            self.start_engine()
        else:
            self.configure_docker_oracle()

        results: list[dict[str, Any]] = []
        try:
            self.prepare_builder()
            atomic_json(self.output / "fingerprint.json", self.fingerprint())
            for fixture in fixtures:
                results.append(self.run_fixture(fixture))
        finally:
            try:
                self.stop_builder()
                self.check_runtime_state_cleanup()
            finally:
                self.stop_engine()

        success = (
            all(result["status"] == "passed" for result in results)
            and not self.cleanup_differences
        )
        payload = {
            "schemaVersion": 1,
            "backend": self.lane,
            "status": "passed" if success else "failed",
            "fixtures": results,
            "cleanupDifferences": self.cleanup_differences,
        }
        atomic_json(self.output / "results.json", payload)
        write_junit(
            self.output / "junit.xml",
            self.lane,
            results,
            self.cleanup_differences,
        )
        return 0 if success else 1

    def start_engine(self) -> None:
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise ParityError("Apple lanes require an arm64 Mac")
        engine = self.repository / ".build" / "debug" / "devcontainer-engine"
        if not engine.is_file():
            run_checked(
                ["swift", "build", "--disable-automatic-resolution"],
                cwd=self.repository,
                environment=self.environment,
                timeout_seconds=1800,
            )
        if self.runtime_root.exists():
            shutil.rmtree(self.runtime_root)
        self.runtime_root.mkdir(parents=True)
        self.socket_root = Path(
            tempfile.mkdtemp(prefix=f"devcontainer-{self.lane}-socket-")
        )
        socket_path = self.socket_root / "docker.sock"
        state_path = self.runtime_root / "state.sqlite"
        container = os.environ.get("DEVCONTAINER_CONTAINER_BIN") or shutil.which(
            "container"
        )
        if not container:
            raise ParityError("Apple container CLI is required")
        self.engine_log = (self.output / "engine.log").open("wb")
        self.engine = subprocess.Popen(
            [
                str(engine),
                "--socket",
                str(socket_path),
                "--state",
                str(state_path),
                "--container",
                container,
            ],
            cwd=self.repository,
            env=self.environment,
            stdin=subprocess.DEVNULL,
            stdout=self.engine_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.environment["DOCKER_HOST"] = f"unix://{socket_path}"
        self.environment["DEVCONTAINER_SOCKET"] = str(socket_path)
        self.environment["DEVCONTAINER_STATE"] = str(state_path)
        self.environment["DEVCONTAINER_CONFIG"] = str(
            self.runtime_root / "missing-config.toml"
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.engine.poll() is not None:
                returncode = self.engine.returncode
                self.stop_engine()
                raise ParityError(
                    f"compatibility engine exited with {returncode}"
                )
            if socket_path.exists():
                result = subprocess.run(
                    [self.docker, "version", "--format", "{{.Server.Version}}"],
                    env=self.environment,
                    capture_output=True,
                    check=False,
                    timeout=2,
                )
                if result.returncode == 0:
                    return
            time.sleep(0.1)
        self.stop_engine()
        raise ParityError(f"compatibility engine did not become ready at {socket_path}")

    def stop_engine(self) -> None:
        if self.engine is not None and self.engine.poll() is None:
            os.killpg(self.engine.pid, signal.SIGTERM)
            try:
                self.engine.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.engine.pid, signal.SIGKILL)
                self.engine.wait(timeout=5)
        if self.engine_log is not None:
            self.engine_log.close()
        if self.socket_root is not None:
            shutil.rmtree(self.socket_root, ignore_errors=True)
            self.socket_root = None

    def prepare_builder(self) -> None:
        before = self.docker_container_inventory()
        self.builder_name = f"devcontainer-parity-{self.lane}-{os.getpid()}"
        result = subprocess.run(
            [
                self.docker,
                "buildx",
                "create",
                "--name",
                self.builder_name,
                "--driver",
                "docker-container",
            ],
            cwd=self.repository,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if result.returncode != 0:
            raise ParityError(
                f"cannot create isolated buildx builder: {result.stderr.strip()}"
            )
        self.environment["BUILDX_BUILDER"] = self.builder_name
        bootstrap = subprocess.run(
            [self.docker, "buildx", "inspect", "--bootstrap", self.builder_name],
            cwd=self.repository,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        (self.output / "buildx-bootstrap.log").write_text(
            bootstrap.stdout + bootstrap.stderr,
            encoding="utf-8",
        )
        if bootstrap.returncode != 0:
            raise ParityError(
                f"isolated buildx builder did not become ready: {bootstrap.stderr.strip()}"
            )
        self.builder_container_ids = self.docker_container_inventory() - before
        if not self.builder_container_ids:
            raise ParityError(
                "isolated buildx builder did not expose a dedicated container"
            )

    def stop_builder(self) -> None:
        if self.builder_name is None or not self.docker:
            return
        builder_name = self.builder_name
        result = subprocess.run(
            [self.docker, "buildx", "rm", "--force", self.builder_name],
            cwd=self.repository,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        (self.output / "buildx-remove.log").write_text(
            result.stdout + result.stderr,
            encoding="utf-8",
        )
        if result.returncode != 0:
            self.cleanup_differences.append(
                f"buildx rm for {builder_name} exited {result.returncode}: "
                f"{result.stderr.strip()}"
            )

        remaining = set(self.builder_container_ids)
        deadline = time.monotonic() + 15
        while remaining and time.monotonic() < deadline:
            try:
                remaining &= self.docker_container_inventory()
            except ParityError as error:
                self.cleanup_differences.append(str(error))
                break
            if remaining:
                time.sleep(0.2)
        if remaining:
            identifiers = sorted(remaining)
            cleanup = subprocess.run(
                [self.docker, "rm", "-f", *identifiers],
                cwd=self.repository,
                env=self.environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            diagnostic = (
                "isolated buildx builder leaked container(s): "
                + ", ".join(identifiers)
            )
            if cleanup.returncode != 0:
                diagnostic += (
                    f"; exact cleanup exited {cleanup.returncode}: "
                    f"{cleanup.stderr.strip()}"
                )
            self.cleanup_differences.append(diagnostic)
        self.builder_name = None
        self.builder_container_ids = set()

    def docker_container_inventory(self) -> set[str]:
        """Return the exact container IDs visible through the selected lane."""

        result = subprocess.run(
            [self.docker, "ps", "-aq"],
            cwd=self.repository,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            raise ParityError(
                "cannot inventory parity containers: "
                f"{result.stderr.strip()}"
            )
        return {
            value.strip()
            for value in result.stdout.splitlines()
            if value.strip()
        }

    def check_runtime_state_cleanup(self) -> None:
        """Require Apple lanes to leave no durable project/container ownership."""

        if self.lane == "docker":
            return
        state = self.runtime_root / "state.sqlite"
        if not state.is_file():
            self.cleanup_differences.append(
                f"lane state database is missing: {state}"
            )
            return
        try:
            with sqlite3.connect(f"file:{state}?mode=ro", uri=True) as database:
                projects = int(
                    database.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
                )
                containers = int(
                    database.execute(
                        "SELECT COUNT(*) FROM runtime_containers"
                    ).fetchone()[0]
                )
        except sqlite3.Error as error:
            self.cleanup_differences.append(
                f"cannot inspect lane state cleanup: {error}"
            )
            return
        if projects or containers:
            self.cleanup_differences.append(
                "lane state leaked "
                f"{projects} project claim(s) and "
                f"{containers} runtime container record(s)"
            )

    def require_docker_oracle(self) -> None:
        result = run_checked(
            [self.docker, "info", "--format", "{{json .}}"],
            environment=self.environment,
        )
        try:
            info = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ParityError("Docker oracle returned invalid info JSON") from error
        operating_system = str(info.get("OperatingSystem", "")).lower()
        if "apple container" in operating_system or "devcontainer" in operating_system:
            raise ParityError("docker oracle lane points at the Apple compatibility service")

    def configure_docker_oracle(self) -> None:
        """Resolve the selected context to one explicit, non-ambient endpoint."""

        endpoint = os.environ.get(
            "DEVCONTAINER_DOCKER_ORACLE_HOST",
            os.environ.get("DOCKER_HOST", ""),
        )
        explicit_endpoint = bool(endpoint)
        if not endpoint:
            context = run_checked(
                [
                    self.docker,
                    "context",
                    "inspect",
                    "--format",
                    "{{.Endpoints.docker.Host}}",
                ],
                environment=self.environment,
            )
            endpoint = context.stdout.strip()
        if not endpoint:
            raise ParityError("Docker oracle context has no endpoint")
        self.environment["DOCKER_HOST"] = endpoint
        if explicit_endpoint:
            self.environment.pop("DOCKER_CONTEXT", None)
        elif self.environment.get("DOCKER_CONTEXT"):
            self.environment.setdefault(
                "DOCKER_CONFIG",
                str(Path(self.environment["HOME"]) / ".docker"),
            )
        self.require_docker_oracle()

    def fingerprint(self) -> dict[str, Any]:
        commands: dict[str, Sequence[str]] = {
            "docker": [self.docker, "version", "--format", "{{json .}}"],
            "devcontainers": [
                self.node_package_runner,
                "--yes",
                f"@devcontainers/cli@{self.cli_version}",
                "--version",
            ],
        }
        if self.lane != "docker":
            container = os.environ.get("DEVCONTAINER_CONTAINER_BIN") or "container"
            commands["container"] = [
                container,
                "system",
                "version",
                "--format",
                "json",
            ]
        if self.lane == "container-compose":
            commands["containerCompose"] = [
                os.environ.get("DEVCONTAINER_COMPOSE_BIN", "container-compose"),
                "version",
                "--format",
                "json",
            ]
        fingerprints: dict[str, Any] = {
            "backend": self.lane,
            "machine": platform.machine(),
            "platform": platform.platform(),
            "commands": {},
        }
        for name, command in commands.items():
            result = subprocess.run(
                command,
                cwd=self.repository,
                env=self.environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            fingerprints["commands"][name] = {
                "argv": list(command),
                "exitCode": result.returncode,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
            }
            if result.returncode != 0:
                raise ParityError(f"{name} fingerprint command failed")
        if self.lane == "apple-stock":
            container_output = fingerprints["commands"]["container"]["stdout"]
            try:
                versions = json.loads(container_output)
                versions = versions if isinstance(versions, list) else [versions]
                distribution = next(
                    (
                        value.get("distribution")
                        for value in versions
                        if isinstance(value, dict) and value.get("distribution")
                    ),
                    "apple",
                )
            except json.JSONDecodeError as error:
                raise ParityError("container version returned invalid JSON") from error
            fingerprints["containerDistribution"] = distribution
            if (
                distribution != "apple"
                and os.environ.get("DEVCONTAINER_ALLOW_CUSTOM_STOCK") != "1"
            ):
                raise ParityError(
                    "apple-stock lane requires Apple's stock distribution; "
                    f"found {distribution!r}"
                )
        return fingerprints

    def run_fixture(self, fixture: Any) -> dict[str, Any]:
        raw = self.output / "raw" / fixture.identifier
        raw.mkdir(parents=True)
        workspace = self.output / "workspaces" / fixture.identifier
        if workspace.exists():
            shutil.rmtree(workspace)
        shutil.copytree(fixture.directory, workspace)
        runtime_fixture = replace(fixture, directory=workspace)
        if runtime_fixture.runner == "engine":
            return self.run_engine_fixture(runtime_fixture, raw)
        started = time.monotonic()
        status = "failed"
        observations: dict[str, str] = {}
        differences: list[str] = []
        diagnostic = ""
        try:
            up = self.devcontainer(
                [
                    "up",
                    "--workspace-folder",
                    str(runtime_fixture.directory),
                    "--docker-path",
                    self.docker,
                    "--remove-existing-container",
                    "--log-level",
                    "info",
                    "--log-format",
                    "json",
                ],
                timeout=1800,
            )
            (raw / "up.stdout").write_text(up.stdout, encoding="utf-8")
            (raw / "up.stderr").write_text(up.stderr, encoding="utf-8")
            if up.returncode != 0:
                raise ParityError(f"devcontainer up exited {up.returncode}")
            probe = self.devcontainer(
                [
                    "exec",
                    "--workspace-folder",
                    str(runtime_fixture.directory),
                    "/bin/sh",
                    "./probe.sh",
                ],
                timeout=120,
            )
            (raw / "probe.stdout").write_text(probe.stdout, encoding="utf-8")
            (raw / "probe.stderr").write_text(probe.stderr, encoding="utf-8")
            if probe.returncode != 0:
                raise ParityError(f"devcontainer exec exited {probe.returncode}")
            observations = parse_observations(probe.stdout)
            observations.update(
                self.additional_fixture_observations(
                    runtime_fixture,
                    raw,
                    up,
                )
            )
            differences = assert_contract(runtime_fixture, observations)
            if differences:
                raise ParityError("; ".join(differences))
            status = "passed"
        except (OSError, ParityError, subprocess.TimeoutExpired) as error:
            diagnostic = str(error)
        finally:
            cleanup = self.cleanup_fixture(runtime_fixture)
            (raw / "cleanup.log").write_text(cleanup, encoding="utf-8")
            if cleanup.startswith("ERROR:"):
                status = "failed"
                diagnostic = f"{diagnostic}; {cleanup}".strip("; ")
        return {
            "id": fixture.identifier,
            "status": status,
            "durationSeconds": round(time.monotonic() - started, 3),
            "observations": observations,
            "differences": differences,
            "diagnostic": diagnostic,
        }

    def additional_fixture_observations(
        self,
        fixture: Any,
        raw: Path,
        up: subprocess.CompletedProcess[str],
    ) -> dict[str, str]:
        """Exercise semantics that cannot be observed from inside the container."""

        if fixture.identifier == "D05-features":
            return self.validate_feature_lock(fixture, raw)
        if fixture.identifier == "D06-ports":
            return self.validate_ports(raw)
        if fixture.identifier == "D07-reuse-cleanup":
            return self.validate_reuse_cleanup(fixture, raw, up)
        if fixture.identifier == "C04-compose-lifecycle":
            return self.validate_compose_lifecycle(fixture, raw, up)
        return {}

    def validate_feature_lock(self, fixture: Any, raw: Path) -> dict[str, str]:
        lockfile = fixture.directory / ".devcontainer" / "devcontainer-lock.json"
        frozen_rejected = False
        if lockfile.is_file():
            missing = lockfile.with_suffix(".json.parity-missing")
            lockfile.replace(missing)
            try:
                frozen = self.devcontainer(
                    [
                        "up",
                        "--workspace-folder",
                        str(fixture.directory),
                        "--docker-path",
                        self.docker,
                        "--remove-existing-container",
                        "--frozen-lockfile",
                        "--log-level",
                        "info",
                        "--log-format",
                        "json",
                    ],
                    timeout=1800,
                )
                (raw / "frozen.stdout").write_text(
                    frozen.stdout,
                    encoding="utf-8",
                )
                (raw / "frozen.stderr").write_text(
                    frozen.stderr,
                    encoding="utf-8",
                )
                frozen_rejected = frozen.returncode != 0
            finally:
                missing.replace(lockfile)
        return {"frozen_lock": "true" if frozen_rejected else "false"}

    def validate_ports(self, raw: Path) -> dict[str, str]:
        body = ""
        for _ in range(50):
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:49277/",
                    timeout=2,
                ) as response:
                    body = response.read().decode(errors="replace").strip()
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.1)

        collision_name = f"dcparity-port-collision-{os.getpid()}"
        collision = subprocess.run(
            [
                self.docker,
                "run",
                "--name",
                collision_name,
                "--label",
                "devcontainer.parity=D06-collision",
                "--publish",
                "127.0.0.1:49277:8123",
                "--detach",
                "alpine:3.22",
                "sleep",
                "30",
            ],
            cwd=self.repository,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        (raw / "collision.stdout").write_text(
            collision.stdout,
            encoding="utf-8",
        )
        (raw / "collision.stderr").write_text(
            collision.stderr,
            encoding="utf-8",
        )
        subprocess.run(
            [self.docker, "rm", "--force", collision_name],
            cwd=self.repository,
            env=self.environment,
            capture_output=True,
            check=False,
            timeout=60,
        )
        return {
            "collision_rejected": "true" if collision.returncode != 0 else "false",
            "host_connectivity": "true" if body == "devcontainer-port" else "false",
        }

    def validate_reuse_cleanup(
        self,
        fixture: Any,
        raw: Path,
        up: subprocess.CompletedProcess[str],
    ) -> dict[str, str]:
        first = self.container_id_from_up(up.stdout)
        reused = self.devcontainer(
            [
                "up",
                "--workspace-folder",
                str(fixture.directory),
                "--docker-path",
                self.docker,
                "--log-level",
                "info",
                "--log-format",
                "json",
            ],
            timeout=1800,
        )
        (raw / "reuse.stdout").write_text(reused.stdout, encoding="utf-8")
        (raw / "reuse.stderr").write_text(reused.stderr, encoding="utf-8")
        second = self.container_id_from_up(reused.stdout) if reused.returncode == 0 else ""
        reused_counters = subprocess.run(
            [
                self.docker,
                "exec",
                second or first,
                "sh",
                "-c",
                "printf '%s|%s' \"$(cat /cache/create-count)\" \"$(cat /cache/start-count)\"",
            ],
            cwd=self.repository,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        hooks_idempotent = (
            reused_counters.returncode == 0
            and reused_counters.stdout.strip() == "1|1"
        )
        rebuild = self.devcontainer(
            [
                "up",
                "--workspace-folder",
                str(fixture.directory),
                "--docker-path",
                self.docker,
                "--remove-existing-container",
                "--log-level",
                "info",
                "--log-format",
                "json",
            ],
            timeout=1800,
        )
        (raw / "rebuild.stdout").write_text(rebuild.stdout, encoding="utf-8")
        (raw / "rebuild.stderr").write_text(rebuild.stderr, encoding="utf-8")
        third = (
            self.container_id_from_up(rebuild.stdout)
            if rebuild.returncode == 0
            else ""
        )
        rebuilt_counters = subprocess.run(
            [
                self.docker,
                "exec",
                third or second or first,
                "sh",
                "-c",
                "printf '%s|%s' \"$(cat /cache/create-count)\" \"$(cat /cache/start-count)\"",
            ],
            cwd=self.repository,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        create_count, _, start_count = rebuilt_counters.stdout.strip().partition("|")
        rebuilt = (
            rebuild.returncode == 0
            and bool(second)
            and bool(third)
            and second != third
            and rebuilt_counters.returncode == 0
            and create_count == "2"
            and start_count == "2"
        )
        final = third or second or first
        remove = subprocess.run(
            [self.docker, "rm", "--force", final],
            cwd=self.repository,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        volume_remove = subprocess.run(
            [self.docker, "volume", "rm", "--force", "dcparity-d07-reuse"],
            cwd=self.repository,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        remaining_container = subprocess.run(
            [self.docker, "inspect", final],
            cwd=self.repository,
            env=self.environment,
            capture_output=True,
            check=False,
            timeout=30,
        )
        remaining_volume = subprocess.run(
            [self.docker, "volume", "inspect", "dcparity-d07-reuse"],
            cwd=self.repository,
            env=self.environment,
            capture_output=True,
            check=False,
            timeout=30,
        )
        cleanup = (
            remove.returncode == 0
            and volume_remove.returncode == 0
            and remaining_container.returncode != 0
            and remaining_volume.returncode != 0
        )
        return {
            "cleanup": "true" if cleanup else "false",
            "create_count": create_count,
            "hooks_idempotent_on_reuse": "true" if hooks_idempotent else "false",
            "rebuilt": "true" if rebuilt else "false",
            "reused": "true" if first and first == second else "false",
            "start_count": start_count,
        }

    def validate_compose_lifecycle(
        self,
        fixture: Any,
        raw: Path,
        up: subprocess.CompletedProcess[str],
    ) -> dict[str, str]:
        first = self.container_id_from_up(up.stdout)
        inspect = subprocess.run(
            [self.docker, "inspect", first],
            cwd=self.repository,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        value = json.loads(inspect.stdout)[0] if inspect.returncode == 0 else {}
        first_created = value.get("Created", "")
        labels = value.get("Config", {}).get("Labels", {})
        project = labels.get("com.docker.compose.project", "")
        primary_label = labels.get("com.docker.compose.service") == "app"
        compose = self.compose_command(
            project=project,
            file=fixture.directory / "compose.yaml",
        )
        environment = self.compose_environment()
        restart = subprocess.run(
            [*compose, "restart", "app"],
            cwd=fixture.directory,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        (raw / "compose-restart.log").write_text(
            restart.stdout + restart.stderr,
            encoding="utf-8",
        )
        signal_value = subprocess.run(
            [self.docker, "exec", first, "cat", "/state/signal"],
            cwd=self.repository,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        recreate = subprocess.run(
            [*compose, "up", "--detach", "--force-recreate", "app"],
            cwd=fixture.directory,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        (raw / "compose-recreate.log").write_text(
            recreate.stdout + recreate.stderr,
            encoding="utf-8",
        )
        discovery = subprocess.run(
            [
                self.docker,
                "ps",
                "--quiet",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--filter",
                "label=com.docker.compose.service=app",
            ],
            cwd=self.repository,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        discovery_all = subprocess.run(
            [self.docker, "ps", "--all", "--no-trunc"],
            cwd=self.repository,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        (raw / "compose-discovery-docker.log").write_text(
            "filtered stdout:\n"
            + discovery.stdout
            + "\nfiltered stderr:\n"
            + discovery.stderr
            + "\nall stdout:\n"
            + discovery_all.stdout
            + "\nall stderr:\n"
            + discovery_all.stderr,
            encoding="utf-8",
        )
        if self.lane != "docker":
            container_inventory = subprocess.run(
                [
                    os.environ.get(
                        "DEVCONTAINER_CONTAINER_BIN",
                        shutil.which("container") or "container",
                    ),
                    "list",
                    "--all",
                    "--format",
                    "json",
                ],
                cwd=self.repository,
                env=self.environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            (raw / "compose-discovery-container.log").write_text(
                container_inventory.stdout + container_inventory.stderr,
                encoding="utf-8",
            )
        second = discovery.stdout.strip()
        second_inspect = subprocess.run(
            [self.docker, "inspect", second],
            cwd=self.repository,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        second_value = (
            json.loads(second_inspect.stdout)[0]
            if second_inspect.returncode == 0
            else {}
        )
        second_created = second_value.get("Created", "")
        (raw / "compose-discovery.log").write_text(
            json.dumps(
                {
                    "first": first,
                    "firstCreated": first_created,
                    "second": second,
                    "secondCreated": second_created,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        down = subprocess.run(
            [*compose, "down", "--volumes", "--remove-orphans"],
            cwd=fixture.directory,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        (raw / "compose-down.log").write_text(
            down.stdout + down.stderr,
            encoding="utf-8",
        )
        remaining = subprocess.run(
            [
                self.docker,
                "ps",
                "--all",
                "--quiet",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ],
            cwd=self.repository,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        return {
            "primary_label": "true" if primary_label else "false",
            "recreated": (
                "true"
                if (
                    recreate.returncode == 0
                    and second
                    and (
                        second != first
                        or (
                            first_created
                            and second_created
                            and first_created != second_created
                        )
                    )
                )
                else "false"
            ),
            "restart_signal": signal_value.stdout.strip(),
            "restarted": "true" if restart.returncode == 0 else "false",
            "shutdown": (
                "true"
                if down.returncode == 0 and not remaining.stdout.strip()
                else "false"
            ),
        }

    def container_id_from_up(self, output: str) -> str:
        try:
            value = json.loads(output)
        except json.JSONDecodeError as error:
            raise ParityError("devcontainer up returned invalid JSON") from error
        identifier = value.get("containerId") if isinstance(value, dict) else None
        if not isinstance(identifier, str) or not identifier:
            raise ParityError("devcontainer up did not return a containerId")
        return identifier

    def compose_environment(self) -> dict[str, str]:
        environment = dict(self.environment)
        if self.lane == "container-compose":
            environment["DEVCONTAINER_COMPOSE_PROVIDER"] = "container-compose"
            environment["DEVCONTAINER_COMPOSE_BIN"] = os.environ.get(
                "DEVCONTAINER_COMPOSE_BIN",
                shutil.which("container-compose") or "container-compose",
            )
        return environment

    def compose_command(self, project: str, file: Path) -> list[str]:
        if not project:
            raise ParityError("Compose container did not expose a project label")
        if self.lane == "docker":
            command = [self.docker, "compose"]
        else:
            command = [
                str(self.repository / ".build" / "debug" / "devcontainer-compose")
            ]
        return [
            *command,
            "--project-name",
            project,
            "--file",
            str(file),
        ]

    def run_engine_fixture(self, fixture: Any, raw: Path) -> dict[str, Any]:
        started = time.monotonic()
        status = "failed"
        observations: dict[str, str] = {}
        differences: list[str] = []
        diagnostic = ""
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(self.repository / "Tools" / "parity" / "run_engine_fixture.py"),
                    fixture.identifier,
                ],
                cwd=self.repository,
                env=self.environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=1800,
            )
            (raw / "probe.stdout").write_text(result.stdout, encoding="utf-8")
            (raw / "probe.stderr").write_text(result.stderr, encoding="utf-8")
            if result.returncode != 0:
                raise ParityError(f"engine probe exited {result.returncode}")
            observations = parse_observations(result.stdout)
            differences = assert_contract(fixture, observations)
            if differences:
                raise ParityError("; ".join(differences))
            status = "passed"
        except (OSError, ParityError, subprocess.TimeoutExpired) as error:
            diagnostic = str(error)
        return {
            "id": fixture.identifier,
            "status": status,
            "durationSeconds": round(time.monotonic() - started, 3),
            "observations": observations,
            "differences": differences,
            "diagnostic": diagnostic,
        }

    def devcontainer(
        self,
        arguments: Sequence[str],
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        environment = dict(self.environment)
        if self.lane == "container-compose":
            environment["DEVCONTAINER_COMPOSE_PROVIDER"] = "container-compose"
            environment["DEVCONTAINER_COMPOSE_BIN"] = os.environ.get(
                "DEVCONTAINER_COMPOSE_BIN",
                shutil.which("container-compose") or "container-compose",
            )
        command_arguments = list(arguments)
        if self.lane != "docker" and command_arguments:
            compose_wrapper = (
                self.repository / ".build" / "debug" / "devcontainer-compose"
            )
            command_arguments += ["--docker-compose-path", str(compose_wrapper)]
        return subprocess.run(
            [
                self.node_package_runner,
                "--yes",
                f"@devcontainers/cli@{self.cli_version}",
                *command_arguments,
            ],
            cwd=self.repository,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

    def cleanup_fixture(self, fixture: Any) -> str:
        cleanup_output = ""
        cleanup_error = ""
        label = f"devcontainer.local_folder={fixture.directory}"
        result = subprocess.run(
            [self.docker, "ps", "-aq", "--filter", f"label={label}"],
            cwd=self.repository,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            return f"ERROR: container discovery exited {result.returncode}: {result.stderr}"
        identifiers = [line for line in result.stdout.splitlines() if line]
        compose_config = fixture.directory / "compose.yaml"
        devcontainer_config = fixture.directory / ".devcontainer" / "devcontainer.json"
        is_compose = False
        if devcontainer_config.is_file():
            try:
                is_compose = "dockerComposeFile" in json.loads(
                    devcontainer_config.read_text(encoding="utf-8")
                )
            except json.JSONDecodeError:
                pass
        if is_compose:
            project = fixture.directory.name.lower()
            if identifiers:
                inspect = subprocess.run(
                    [
                        self.docker,
                        "inspect",
                        "--format",
                        '{{ index .Config.Labels "com.docker.compose.project" }}',
                        identifiers[0],
                    ],
                    cwd=self.repository,
                    env=self.environment,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                if inspect.returncode == 0 and inspect.stdout.strip():
                    project = inspect.stdout.strip()
            if self.lane == "docker":
                compose_command = [self.docker, "compose"]
            else:
                compose_command = [
                    str(
                        self.repository
                        / ".build"
                        / "debug"
                        / "devcontainer-compose"
                    )
                ]
            down = subprocess.run(
                [
                    *compose_command,
                    "--project-name",
                    project,
                    "-f",
                    str(compose_config),
                    "down",
                    "--volumes",
                    "--remove-orphans",
                ],
                cwd=fixture.directory,
                env=self.compose_environment(),
                capture_output=True,
                text=True,
                check=False,
                timeout=180,
            )
            if down.returncode == 0:
                cleanup_output += down.stdout + down.stderr
            else:
                cleanup_output += down.stdout + down.stderr
                cleanup_error = (
                    f"compose down exited {down.returncode}: "
                    f"{down.stderr.strip() or down.stdout.strip() or 'no diagnostic output'}"
                )
            rediscovered = subprocess.run(
                [
                    self.docker,
                    "ps",
                    "-aq",
                    "--filter",
                    f"label=com.docker.compose.project={project}",
                ],
                cwd=self.repository,
                env=self.environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if rediscovered.returncode == 0:
                identifiers = [
                    line for line in rediscovered.stdout.splitlines() if line
                ]
                if not identifiers and not cleanup_error:
                    return cleanup_output
            else:
                return (
                    "ERROR: compose cleanup discovery exited "
                    f"{rediscovered.returncode}: {rediscovered.stderr}"
                )
        if identifiers:
            remove = subprocess.run(
                [self.docker, "rm", "-f", *identifiers],
                cwd=self.repository,
                env=self.environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            if remove.returncode != 0:
                return f"ERROR: cleanup exited {remove.returncode}: {remove.stderr}"
            cleanup_output += remove.stdout
        else:
            cleanup_output += "no fixture containers remained\n"
        if cleanup_error:
            return f"ERROR: {cleanup_error}\n{cleanup_output}"
        return cleanup_output


SAFE_ENVIRONMENT_KEYS = frozenset(
    {
        "CONTAINER_COMPOSE_BUILD_INFO",
        "CONTAINER_COMPOSE_CONTAINER",
        "CONTAINER_HOST",
        "CONTAINER_INSTALLATION_ROOT",
        "CONTAINER_REGISTRY_CONFIG",
        "CONTAINER_RUNTIME_CONFIG",
        "DEVELOPER_DIR",
        "DEVCONTAINER_ALLOW_CUSTOM_STOCK",
        "DEVCONTAINER_COMPOSE_BIN",
        "DEVCONTAINER_CONTAINER_BIN",
        "DEVCONTAINER_DOCKER_BIN",
        "DEVCONTAINER_DOCKER_ORACLE_HOST",
        "DEVCONTAINER_PARITY_FIXTURES",
        "DEVCONTAINER_TRACE_PROCESS",
        "DOCKER_CERT_PATH",
        "DOCKER_CONFIG",
        "DOCKER_CONTEXT",
        "DOCKER_DEFAULT_PLATFORM",
        "DOCKER_HOST",
        "DOCKER_TLS_VERIFY",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "NO_COLOR",
        "PATH",
        "SDKROOT",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TERM",
        "TMPDIR",
        "TOOLCHAINS",
    }
)


def safe_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Build a fail-closed environment without credentials or shell hooks."""

    return {
        key: value
        for key, value in source.items()
        if key in SAFE_ENVIRONMENT_KEYS
    }


def run_checked(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    environment: dict[str, str],
    timeout_seconds: int = 60,
) -> subprocess.CompletedProcess[str]:
    """Run a bounded command and surface its diagnostic on failure."""

    result = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_seconds,
    )
    if result.returncode != 0:
        raise ParityError(
            f"{' '.join(command)} exited {result.returncode}: {result.stderr.strip()}"
        )
    return result


def write_junit(
    path: Path,
    lane: str,
    results: list[dict[str, Any]],
    cleanup_differences: Sequence[str] = (),
) -> None:
    """Write a portable JUnit report for CI readers."""

    failures = sum(result["status"] != "passed" for result in results)
    failures += bool(cleanup_differences)
    suite = ET.Element(
        "testsuite",
        {
            "name": f"parity-{lane}",
            "tests": str(len(results) + bool(cleanup_differences)),
            "failures": str(failures),
            "time": f"{sum(result['durationSeconds'] for result in results):.3f}",
        },
    )
    for result in results:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": f"parity.{lane}",
                "name": result["id"],
                "time": f"{result['durationSeconds']:.3f}",
            },
        )
        if result["status"] != "passed":
            failure = ET.SubElement(case, "failure", {"message": result["diagnostic"]})
            failure.text = "\n".join(result["differences"])
    if cleanup_differences:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": f"parity.{lane}",
                "name": "runtime-cleanup",
                "time": "0.000",
            },
        )
        failure = ET.SubElement(
            case,
            "failure",
            {"message": "runtime resources leaked after parity"},
        )
        failure.text = "\n".join(cleanup_differences)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lane", choices=LANES)
    parser.add_argument("evidence", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repository = Path(__file__).resolve().parents[2]
    evidence = args.evidence.resolve()
    try:
        return LaneRunner(args.lane, repository, evidence).run()
    except (OSError, ParityError, subprocess.TimeoutExpired) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
