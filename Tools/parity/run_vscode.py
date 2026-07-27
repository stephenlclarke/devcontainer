#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Drive a pinned VS Code Dev Containers session against one runtime lane."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from parity_lib import (
    LANES,
    ParityError,
    assert_contract,
    atomic_json,
    implemented_fixtures,
    load_manifest,
)
from run_lane import LaneRunner

FIXTURE_ID = "V01-vscode-end-to-end"
DEFAULT_TIMEOUT_SECONDS = 1800
SENSITIVE_ENVIRONMENT_NAME = re.compile(
    r"\b[A-Z][A-Z0-9_]*(?:ACCESS_KEY|API_KEY|APP_PASSWORD|"
    r"PASSWORD|PRIVATE_KEY|SECRET|TOKEN)[A-Z0-9_]*\b"
)


@dataclass(frozen=True)
class VSCodePins:
    """Immutable identities for the real VS Code reference client."""

    version: str
    commit: str
    platform: str
    archive_url: str
    archive_sha256: str
    application_identifier: str
    signing_team_identifier: str
    extension_version: str
    extension_url: str
    extension_sha256: str
    embedded_cli_version: str
    embedded_cli_commit: str
    embedded_cli_sha256: str

    @classmethod
    def from_manifest(cls, manifest: Mapping[str, Any]) -> VSCodePins:
        """Parse the checked-in reference pin without accepting missing fields."""

        value = manifest["referencePins"]["vscode"]
        extension = value["devContainersExtension"]
        return cls(
            version=str(value["version"]),
            commit=str(value["commit"]),
            platform=str(value["platform"]),
            archive_url=str(value["archiveURL"]),
            archive_sha256=str(value["archiveSHA256"]),
            application_identifier=str(value["applicationIdentifier"]),
            signing_team_identifier=str(value["signingTeamIdentifier"]),
            extension_version=str(extension["version"]),
            extension_url=str(extension["vsixURL"]),
            extension_sha256=str(extension["vsixSHA256"]),
            embedded_cli_version=str(extension["embeddedCliVersion"]),
            embedded_cli_commit=str(extension["embeddedCliCommit"]),
            embedded_cli_sha256=str(extension["embeddedCliSHA256"]),
        )


def sha256_bytes(value: bytes) -> str:
    """Return the lowercase SHA-256 digest for reference artifacts."""

    return hashlib.sha256(value).hexdigest()


def parse_code_version(output: str) -> tuple[str, str, str]:
    """Parse ``code --version`` without ignoring distribution identity."""

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) != 3:
        raise ParityError(f"code --version returned {len(lines)} non-empty lines")
    return lines[0], lines[1], lines[2]


def verify_code_version(output: str, pins: VSCodePins) -> dict[str, str]:
    """Require the exact VS Code version, commit, and architecture."""

    version, commit, architecture = parse_code_version(output)
    expected_architecture = "arm64" if pins.platform.endswith("arm64") else "x64"
    actual = {
        "version": version,
        "commit": commit,
        "architecture": architecture,
    }
    expected = {
        "version": pins.version,
        "commit": pins.commit,
        "architecture": expected_architecture,
    }
    if actual != expected:
        raise ParityError(
            "VS Code reference identity differs: "
            f"expected {expected!r}, found {actual!r}"
        )
    return actual


def verify_signing_output(output: str, pins: VSCodePins) -> dict[str, str]:
    """Require Microsoft's expected macOS application and team identifiers."""

    values: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"Identifier", "TeamIdentifier"}:
            values[key] = value.strip()
    expected = {
        "Identifier": pins.application_identifier,
        "TeamIdentifier": pins.signing_team_identifier,
    }
    if values != expected:
        raise ParityError(
            "VS Code signing identity differs: "
            f"expected {expected!r}, found {values!r}"
        )
    return values


def decode_vsix_response(value: bytes) -> bytes:
    """Expand Marketplace transport compression and require a ZIP payload."""

    if value.startswith(b"\x1f\x8b"):
        value = gzip.decompress(value)
    if not value.startswith(b"PK"):
        raise ParityError("Dev Containers Marketplace response is not a VSIX ZIP")
    return value


def download_vsix(
    url: str,
    destination: Path,
    expected_sha256: str,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> Path:
    """Download and authenticate the pinned extension distribution."""

    if destination.is_file():
        current = destination.read_bytes()
        if sha256_bytes(current) == expected_sha256:
            return destination

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "devcontainer-parity/1",
        },
    )
    with opener(request, timeout=120) as response:
        compressed = response.read(64 * 1024 * 1024 + 1)
    if len(compressed) > 64 * 1024 * 1024:
        raise ParityError("Dev Containers VSIX exceeds the 64 MiB safety limit")
    value = decode_vsix_response(compressed)
    actual = sha256_bytes(value)
    if actual != expected_sha256:
        raise ParityError(
            "Dev Containers VSIX digest differs: "
            f"expected {expected_sha256}, found {actual}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".vsix.tmp")
    temporary.write_bytes(value)
    temporary.replace(destination)
    return destination


def verify_vsix_metadata(path: Path, pins: VSCodePins) -> dict[str, str]:
    """Verify extension and embedded reference-CLI identities inside the VSIX."""

    with zipfile.ZipFile(path) as archive:
        package = json.loads(archive.read("extension/package.json"))
        embedded = archive.read(
            "extension/dist/spec-node/devContainersSpecCLI.js"
        )
    cli_source = package.get("dependencies", {}).get(
        "@devcontainers/cli",
        package.get("devDependencies", {}).get("@devcontainers/cli", ""),
    )
    identity = {
        "publisher": str(package.get("publisher", "")),
        "name": str(package.get("name", "")),
        "version": str(package.get("version", "")),
        "embeddedCliVersion": pins.embedded_cli_version,
        "embeddedCliCommit": str(cli_source).rsplit("#", 1)[-1],
        "embeddedCliSHA256": sha256_bytes(embedded),
    }
    expected = {
        "publisher": "ms-vscode-remote",
        "name": "remote-containers",
        "version": pins.extension_version,
        "embeddedCliVersion": pins.embedded_cli_version,
        "embeddedCliCommit": pins.embedded_cli_commit,
        "embeddedCliSHA256": pins.embedded_cli_sha256,
    }
    if identity != expected:
        raise ParityError(
            "Dev Containers VSIX identity differs: "
            f"expected {expected!r}, found {identity!r}"
        )
    return identity


def vscode_settings(
    docker: str,
    compose: str,
) -> dict[str, Any]:
    """Build a deterministic, non-interactive isolated VS Code profile."""

    return {
        "dev.containers.cacheVolume": False,
        "dev.containers.dockerComposePath": compose,
        "dev.containers.dockerPath": docker,
        "dev.containers.experimentalAppleContainerSupport": False,
        "dev.containers.logLevel": "trace",
        "dev.containers.optimisticallyLaunchDocker": False,
        "extensions.autoCheckUpdates": False,
        "extensions.autoUpdate": False,
        "security.workspace.trust.enabled": False,
        "telemetry.telemetryLevel": "off",
        "update.mode": "none",
        "window.confirmBeforeClose": "never",
        "window.restoreWindows": "none",
        "workbench.startupEditor": "none",
    }


VSCODE_ENVIRONMENT_KEYS = frozenset(
    {
        "DEVELOPER_DIR",
        "DEVCONTAINER_CONFIG",
        "DEVCONTAINER_SOCKET",
        "DEVCONTAINER_STATE",
        "DOCKER_CERT_PATH",
        "DOCKER_CONFIG",
        "DOCKER_CONTEXT",
        "DOCKER_HOST",
        "DOCKER_TLS_VERIFY",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "NO_COLOR",
        "PATH",
        "SDKROOT",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TERM",
        "TOOLCHAINS",
    }
)


def vscode_environment(
    source: Mapping[str, str],
    profile_root: Path,
) -> dict[str, str]:
    """Create an isolated GUI environment that cannot inherit credentials."""

    home = profile_root / "home"
    temporary = profile_root / "tmp"
    home.mkdir(parents=True, exist_ok=True)
    temporary.mkdir(parents=True, exist_ok=True)
    environment = {
        key: value
        for key, value in source.items()
        if key in VSCODE_ENVIRONMENT_KEYS
    }
    environment["HOME"] = str(home)
    environment["LOGNAME"] = "devcontainer-runner"
    environment["SHELL"] = "/bin/zsh"
    environment["TMPDIR"] = str(temporary)
    environment["USER"] = "devcontainer-runner"
    return environment


def scrub_sensitive_evidence(root: Path) -> tuple[list[str], list[str]]:
    """Remove text evidence containing credential-shaped environment names."""

    detected: set[str] = set()
    removed: list[str] = []
    for path in sorted(root.rglob("*")):
        if (
            not path.is_file()
            or path.name == "security-scan.json"
            or path.suffix not in {".json", ".log"}
        ):
            continue
        value = path.read_text(encoding="utf-8", errors="replace")
        matches = set(SENSITIVE_ENVIRONMENT_NAME.findall(value))
        if not matches:
            continue
        detected.update(matches)
        removed.append(str(path.relative_to(root)))
        path.unlink()
    return sorted(detected), removed


def code_command(
    code: str,
    user_data: Path,
    extensions: Path,
    workspace: Path,
    driver: Path,
) -> list[str]:
    """Return the isolated VS Code launch command used by the live gate."""

    return [
        code,
        "--user-data-dir",
        str(user_data),
        "--extensions-dir",
        str(extensions),
        "--disable-telemetry",
        "--disable-updates",
        "--disable-workspace-trust",
        "--force-disable-user-env",
        "--locale",
        "en-US",
        "--skip-add-to-recently-opened",
        "--use-inmemory-secretstorage",
        f"--extensionDevelopmentPath={driver}",
        "--new-window",
        str(workspace),
    ]


def validate_driver_result(value: Mapping[str, Any]) -> dict[str, str]:
    """Accept only evidence emitted after every interactive transition."""

    if value.get("status") != "ready-for-cleanup":
        raise ParityError(
            f"VS Code driver did not reach cleanup: {value.get('diagnostic', value)}"
        )
    observations = value.get("observations")
    if not isinstance(observations, dict):
        raise ParityError("VS Code driver result has no observations")
    required = {
        "attach",
        "extension_activation",
        "forward_port",
        "integrated_command",
        "open",
        "rebuild",
        "reopen",
        "vscode_server",
    }
    if set(observations) != required:
        raise ParityError(
            "VS Code driver observation keys differ: "
            f"expected {sorted(required)}, found {sorted(observations)}"
        )
    if any(observations[key] is not True for key in required):
        raise ParityError(f"VS Code driver reported a failed observation: {observations}")
    return {key: "true" for key in sorted(required)}


def terminate_isolated_vscode(user_data: Path) -> None:
    """Terminate only VS Code processes carrying this run's unique profile path."""

    result = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    marker = str(user_data)
    for line in result.stdout.splitlines():
        stripped = line.strip()
        process_id, separator, command = stripped.partition(" ")
        if not separator or marker not in command:
            continue
        try:
            pid = int(process_id)
        except ValueError:
            continue
        if pid != os.getpid():
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def discover_compose_project(
    docker: str,
    workspace: Path,
    environment: Mapping[str, str],
) -> str:
    """Read the Compose project label before cleanup removes the container."""

    label = f"devcontainer.local_folder={workspace}"
    discover = subprocess.run(
        [docker, "ps", "-aq", "--filter", f"label={label}"],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=30,
    )
    identifiers = [line for line in discover.stdout.splitlines() if line]
    if discover.returncode != 0 or not identifiers:
        return ""
    inspect = subprocess.run(
        [
            docker,
            "inspect",
            "--format",
            '{{ index .Config.Labels "com.docker.compose.project" }}',
            identifiers[0],
        ],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=30,
    )
    return inspect.stdout.strip() if inspect.returncode == 0 else ""


def no_resources_remain(
    docker: str,
    workspace: Path,
    project: str,
    environment: Mapping[str, str],
) -> tuple[bool, str]:
    """Prove that the fixture left no containers, networks, or volumes."""

    commands: list[tuple[str, Sequence[str]]] = [
        (
            "containers",
            [
                docker,
                "ps",
                "-aq",
                "--filter",
                f"label=devcontainer.local_folder={workspace}",
            ],
        )
    ]
    if project:
        for resource in ("network", "volume"):
            commands.append(
                (
                    f"{resource}s",
                    [
                        docker,
                        resource,
                        "ls",
                        "--quiet",
                        "--filter",
                        f"label=com.docker.compose.project={project}",
                    ],
                )
            )
    logs: list[str] = []
    passed = True
    for name, command in commands:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            env=environment,
            text=True,
            timeout=30,
        )
        logs.append(
            f"$ {' '.join(command)}\n"
            f"exit={result.returncode}\n{result.stdout}{result.stderr}"
        )
        passed = passed and result.returncode == 0 and not result.stdout.strip()
    return passed, "\n".join(logs)


class VSCodeLane:
    """Own one isolated real-VS-Code parity execution and its evidence."""

    def __init__(self, lane: str, repository: Path, evidence_root: Path) -> None:
        self.lane = lane
        self.repository = repository
        self.evidence_root = evidence_root
        self.output = evidence_root / lane
        self.manifest = load_manifest(
            repository / "Tests" / "Parity" / "manifest.json"
        )
        self.pins = VSCodePins.from_manifest(self.manifest)
        self.code = os.environ.get("DEVCONTAINER_VSCODE_BIN") or shutil.which(
            "code"
        )
        self.vscode_app = Path(
            os.environ.get(
                "DEVCONTAINER_VSCODE_APP",
                "/Applications/Visual Studio Code.app",
            )
        )
        self.vscode_gui = self.vscode_app / "Contents/MacOS/Code"
        self.runtime = LaneRunner(lane, repository, evidence_root / "runtime")
        self.runtime.output = self.output / "runtime"
        self.runtime.runtime_root = self.output / "runtime-state"
        self.process: subprocess.Popen[bytes] | None = None

    def fixture(self) -> Any:
        """Return the checked-in V01 fixture contract."""

        fixtures = implemented_fixtures(self.repository, self.manifest)
        try:
            return next(
                fixture
                for fixture in fixtures
                if fixture.identifier == FIXTURE_ID
                and fixture.runner == "vscode"
                and self.lane in fixture.backends
            )
        except StopIteration as error:
            raise ParityError(f"{FIXTURE_ID} is not implemented for {self.lane}") from error

    def require_reference(self) -> dict[str, Any]:
        """Verify the installed VS Code application and pinned extension asset."""

        if not self.code:
            raise ParityError("VS Code command-line launcher is required")
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise ParityError("VS Code live parity requires an arm64 Mac")
        version = subprocess.run(
            [self.code, "--version"],
            capture_output=True,
            check=False,
            env=self.runtime.environment,
            text=True,
            timeout=30,
        )
        if version.returncode != 0:
            raise ParityError(f"code --version exited {version.returncode}")
        actual_version = verify_code_version(version.stdout, self.pins)
        if not self.vscode_app.is_dir():
            raise ParityError(f"VS Code application is missing: {self.vscode_app}")
        if not self.vscode_gui.is_file():
            raise ParityError(f"VS Code GUI executable is missing: {self.vscode_gui}")
        verify = subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(self.vscode_app)],
            capture_output=True,
            check=False,
            env=self.runtime.environment,
            text=True,
            timeout=60,
        )
        if verify.returncode != 0:
            raise ParityError(f"VS Code signature verification failed: {verify.stderr}")
        details = subprocess.run(
            ["codesign", "-dv", "--verbose=4", str(self.vscode_app)],
            capture_output=True,
            check=False,
            env=self.runtime.environment,
            text=True,
            timeout=30,
        )
        if details.returncode != 0:
            raise ParityError(f"cannot inspect VS Code signature: {details.stderr}")
        signing = verify_signing_output(details.stderr, self.pins)

        vsix = download_vsix(
            self.pins.extension_url,
            self.evidence_root
            / "reference"
            / f"remote-containers-{self.pins.extension_version}.vsix",
            self.pins.extension_sha256,
        )
        extension = verify_vsix_metadata(vsix, self.pins)
        return {
            "archive": {
                "sha256": self.pins.archive_sha256,
                "url": self.pins.archive_url,
            },
            "extension": extension
            | {
                "sha256": self.pins.extension_sha256,
                "url": self.pins.extension_url,
            },
            "installed": actual_version | signing,
        }

    def prepare_runtime(self) -> dict[str, Any]:
        """Start or validate the selected Docker-compatible runtime endpoint."""

        self.runtime.output.mkdir(parents=True, exist_ok=True)
        if self.lane == "docker":
            self.runtime.configure_docker_oracle()
        else:
            self.runtime.start_engine()
        return self.runtime.fingerprint()

    def compose_path(self) -> str:
        """Select the same separately installed Compose adapter as CLI parity."""

        if self.lane == "docker":
            compose = shutil.which("docker-compose")
            if not compose:
                raise ParityError("docker-compose executable is required for VS Code parity")
            return compose
        wrapper = self.repository / ".build" / "debug" / "devcontainer-compose"
        if not wrapper.is_file():
            raise ParityError(f"Compose adapter is missing: {wrapper}")
        return str(wrapper)

    def install_extensions(
        self,
        user_data: Path,
        extensions: Path,
        reference: Mapping[str, Any],
    ) -> None:
        """Install and verify the authenticated Dev Containers VSIX."""

        vsix = (
            self.evidence_root
            / "reference"
            / f"remote-containers-{self.pins.extension_version}.vsix"
        )
        install = subprocess.run(
            [
                self.code,
                "--user-data-dir",
                str(user_data),
                "--extensions-dir",
                str(extensions),
                "--disable-telemetry",
                "--force-disable-user-env",
                "--install-extension",
                str(vsix),
                "--force",
            ],
            capture_output=True,
            check=False,
            env=vscode_environment(
                self.runtime.environment,
                user_data.parent,
            ),
            text=True,
            timeout=180,
        )
        (self.output / "extension-install.log").write_text(
            install.stdout + install.stderr,
            encoding="utf-8",
        )
        if install.returncode != 0:
            raise ParityError(f"cannot install pinned Dev Containers VSIX: {install.stderr}")
        extension_directory = (
            extensions
            / f"ms-vscode-remote.remote-containers-{self.pins.extension_version}"
        )
        embedded = extension_directory / "dist/spec-node/devContainersSpecCLI.js"
        if not embedded.is_file():
            raise ParityError("installed Dev Containers extension has no embedded CLI")
        if sha256_bytes(embedded.read_bytes()) != self.pins.embedded_cli_sha256:
            raise ParityError("installed Dev Containers embedded CLI digest differs")
        embedded_version = subprocess.run(
            ["node", str(embedded), "--version"],
            capture_output=True,
            check=False,
            env=vscode_environment(
                self.runtime.environment,
                user_data.parent,
            ),
            text=True,
            timeout=30,
        )
        if (
            embedded_version.returncode != 0
            or embedded_version.stdout.strip() != self.pins.embedded_cli_version
        ):
            raise ParityError("installed Dev Containers embedded CLI version differs")

        reference_extension = reference["extension"]
        if reference_extension["embeddedCliSHA256"] != self.pins.embedded_cli_sha256:
            raise ParityError("reference evidence changed during extension installation")

    def launch(
        self,
        workspace: Path,
        user_data: Path,
        extensions: Path,
        driver_state: Path,
        driver_result: Path,
    ) -> None:
        """Launch VS Code and wait until the driver closes its isolated window."""

        environment = vscode_environment(
            self.runtime.environment,
            user_data.parent,
        )
        if self.lane == "apple-compose":
            environment["DEVCONTAINER_COMPOSE_PROVIDER"] = "container-compose"
            environment["DEVCONTAINER_COMPOSE_BIN"] = os.environ.get(
                "DEVCONTAINER_COMPOSE_BIN",
                shutil.which("container-compose") or "container-compose",
            )
        environment.update(
            {
                "DEVCONTAINER_VSCODE_DRIVER_BACKEND": self.lane,
                "DEVCONTAINER_VSCODE_DRIVER_DOCKER": self.runtime.docker,
                "DEVCONTAINER_VSCODE_DRIVER_PORT": "8123",
                "DEVCONTAINER_VSCODE_DRIVER_RESULT": str(driver_result),
                "DEVCONTAINER_VSCODE_DRIVER_STATE": str(driver_state),
                "DEVCONTAINER_VSCODE_DRIVER_TIMEOUT_MS": str(
                    (DEFAULT_TIMEOUT_SECONDS - 60) * 1000
                ),
                "DEVCONTAINER_VSCODE_DRIVER_WORKSPACE": str(workspace),
            }
        )
        stdout = (self.output / "code.stdout.log").open("wb")
        stderr = (self.output / "code.stderr.log").open("wb")
        try:
            self.process = subprocess.Popen(
                code_command(
                    str(self.vscode_gui),
                    user_data,
                    extensions,
                    workspace,
                    self.repository / "Tools/parity/vscode-driver-extension",
                ),
                cwd=self.repository,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            deadline = time.monotonic() + DEFAULT_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                if driver_result.is_file():
                    return
                if self.process.poll() is not None:
                    stderr.flush()
                    detail = (self.output / "code.stderr.log").read_text(
                        encoding="utf-8",
                        errors="replace",
                    )
                    raise ParityError(
                        "VS Code exited before the parity driver completed "
                        f"(status {self.process.returncode}): {detail[-2000:]}"
                    )
                time.sleep(0.25)
            raise ParityError("VS Code parity driver timed out")
        finally:
            stdout.close()
            stderr.close()
            terminate_isolated_vscode(user_data)

    def run(self) -> int:
        """Execute V01, clean resources, and emit normal three-lane evidence."""

        if self.lane not in LANES:
            raise ParityError(f"unknown lane {self.lane!r}")
        if self.output.exists():
            shutil.rmtree(self.output)
        self.output.mkdir(parents=True)
        started = time.monotonic()
        fixture = self.fixture()
        workspace = self.output / "workspace"
        shutil.copytree(fixture.directory, workspace)
        runtime_fixture = replace(fixture, directory=workspace)
        driver_state = self.output / "driver-state.json"
        driver_result = self.output / "driver-result.json"
        atomic_json(
            driver_state,
            {
                "diagnostic": "",
                "events": [],
                "observations": {
                    "attach": False,
                    "extension_activation": False,
                    "forward_port": False,
                    "integrated_command": False,
                    "open": False,
                    "rebuild": False,
                    "reopen": False,
                    "vscode_server": False,
                },
                "phase": "local-open",
                "schemaVersion": 1,
            },
        )
        profile_root = Path(
            tempfile.mkdtemp(prefix=f"dc-vscode-{self.lane}-", dir="/tmp")
        )
        user_data = profile_root / "data"
        extensions = profile_root / "extensions"

        status = "failed"
        observations: dict[str, str] = {}
        differences: list[str] = []
        diagnostic = ""
        cleanup_log = ""
        project = ""
        runtime_ready = False
        security_passed = True
        try:
            reference = self.require_reference()
            atomic_json(self.output / "reference.json", reference)
            fingerprint = self.prepare_runtime()
            runtime_ready = True
            atomic_json(self.output / "fingerprint.json", fingerprint)
            compose = self.compose_path()
            settings = vscode_settings(self.runtime.docker, compose)
            atomic_json(user_data / "User/settings.json", settings)
            atomic_json(self.output / "vscode-settings.json", settings)
            self.install_extensions(user_data, extensions, reference)
            self.launch(
                workspace,
                user_data,
                extensions,
                driver_state,
                driver_result,
            )
            if not driver_result.is_file():
                raise ParityError("VS Code driver did not emit a result")
            observations = validate_driver_result(
                json.loads(driver_result.read_text(encoding="utf-8"))
            )
            project = discover_compose_project(
                self.runtime.docker,
                workspace,
                self.runtime.environment,
            )
        except (
            OSError,
            ParityError,
            subprocess.SubprocessError,
            ValueError,
            zipfile.BadZipFile,
        ) as error:
            diagnostic = str(error)
        finally:
            terminate_isolated_vscode(user_data)
            if runtime_ready:
                if not project:
                    project = discover_compose_project(
                        self.runtime.docker,
                        workspace,
                        self.runtime.environment,
                    )
                cleanup_log = self.runtime.cleanup_fixture(runtime_fixture)
                clean, proof = no_resources_remain(
                    self.runtime.docker,
                    workspace,
                    project,
                    self.runtime.environment,
                )
                cleanup_log += proof
                observations["cleanup"] = "true" if clean else "false"
                if cleanup_log.startswith("ERROR:") or not clean:
                    diagnostic = (
                        f"{diagnostic}; runtime cleanup did not complete"
                    ).strip("; ")
            (self.output / "cleanup.log").write_text(
                cleanup_log or "runtime did not start\n",
                encoding="utf-8",
            )
            self.runtime.stop_engine()
            profile_logs = user_data / "logs"
            if profile_logs.is_dir():
                shutil.copytree(
                    profile_logs,
                    self.output / "vscode-logs",
                    dirs_exist_ok=True,
                )
            shutil.rmtree(profile_root, ignore_errors=True)
            sensitive_names, removed_files = scrub_sensitive_evidence(
                self.output
            )
            security_passed = not sensitive_names
            atomic_json(
                self.output / "security-scan.json",
                {
                    "detectedNames": sensitive_names,
                    "removedFiles": removed_files,
                    "status": "passed" if security_passed else "failed",
                },
            )
            if not security_passed:
                diagnostic = (
                    f"{diagnostic}; sensitive environment names were detected "
                    "and their evidence files were removed"
                ).strip("; ")

        differences = assert_contract(runtime_fixture, observations)
        if differences:
            diagnostic = f"{diagnostic}; {'; '.join(differences)}".strip("; ")
        elif security_passed:
            status = "passed"
        result = {
            "diagnostic": diagnostic,
            "differences": differences,
            "durationSeconds": round(time.monotonic() - started, 3),
            "id": FIXTURE_ID,
            "observations": observations,
            "status": status,
        }
        payload = {
            "backend": self.lane,
            "fixtures": [result],
            "schemaVersion": 1,
            "status": status,
        }
        atomic_json(self.output / "results.json", payload)
        atomic_json(
            self.output / "live-result.json",
            {
                "attach": observations.get("attach") == "true",
                "cleanup": observations.get("cleanup") == "true",
                "extensionActivation": observations.get("extension_activation")
                == "true",
                "forwardPort": observations.get("forward_port") == "true",
                "integratedCommand": observations.get("integrated_command")
                == "true",
                "open": observations.get("open") == "true",
                "rebuild": observations.get("rebuild") == "true",
                "reopen": observations.get("reopen") == "true",
                "status": status,
                "vscodeServer": observations.get("vscode_server") == "true",
            },
        )
        print(
            f"{self.lane} VS Code parity {status}: "
            f"{self.output / 'results.json'}"
        )
        return 0 if status == "passed" else 1


def parse_args() -> argparse.Namespace:
    """Parse one explicit runtime lane and evidence root."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lane", choices=LANES)
    parser.add_argument("evidence", type=Path)
    return parser.parse_args()


def main() -> int:
    """Run the requested lane with concise operator-facing failures."""

    args = parse_args()
    repository = Path(__file__).resolve().parents[2]
    try:
        return VSCodeLane(args.lane, repository, args.evidence.resolve()).run()
    except (OSError, ParityError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
