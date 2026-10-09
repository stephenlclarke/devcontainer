#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# SPDX-License-Identifier: Apache-2.0

"""Run released guest probes through the Engine endpoint owned by run_lane."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import time
from typing import Any

from engine_fixture_routes import OWNED_GUEST_FIXTURES
from parity_lib import ParityError, assert_contract


ACCOUNT_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
DEFAULT_RETAINED = ACCOUNT_HOME / "Library/Application Support/ContainerFamily/retained/workflow"
DEFAULT_SSD = Path("/Volumes/SSD/cf/bazel")
COMPOSE_FIXTURES = frozenset({
    "E09-compose-foreground", "E10-compose-quiet", "E11-compose-redirected",
    "E12-compose-tty-input", "E13-compose-signals", "E14-compose-terminal-size",
})
_ATTACHMENT_DIAGNOSTIC_STAGES = frozenset({
    "connect", "upgrade", "start", "primary-startup", "combined-observer", "duplex",
    "observer-startup-history", "combined-duplex", "validate-output", "complete",
})
_ATTACHMENT_DIAGNOSTIC_STREAM_FIELDS = frozenset({
    "inputAcceptedBytes", "inputHalfClosed", "observerInputHalfClosed", "primaryWireBytes",
    "observerWireBytes", "primaryEOF", "observerEOF", "outputWireBytes", "outputEOF",
})
_NATIVE_DIAGNOSTIC_FIXTURES = frozenset({
    "C03-compose-resources", "D05-features", "E07-init-attachment",
})


def _safe_attachment_diagnostic_trace(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only bounded, payload-free E07 stage evidence in the fixture result."""
    trace: list[dict[str, Any]] = []
    for event in events:
        if (not isinstance(event, dict) or type(event.get("stage")) is not str
                or event["stage"] not in _ATTACHMENT_DIAGNOSTIC_STAGES):
            continue
        duration = event.get("durationNS")
        if type(duration) is not int or not 0 <= duration <= 600_000_000_000:
            continue
        item: dict[str, Any] = {"stage": event["stage"], "durationNS": duration}
        status = event.get("status")
        if type(status) is int and 100 <= status <= 599:
            item["status"] = status
        error = event.get("error")
        if type(error) is str and error in {
                "TimeoutError", "OSError", "ValueError", "RuntimeError", "ConnectionError"}:
            item["errorType"] = error
        stream = event.get("stream")
        if isinstance(stream, dict):
            safe_stream: dict[str, int | bool] = {}
            for key in _ATTACHMENT_DIAGNOSTIC_STREAM_FIELDS:
                value = stream.get(key)
                if type(value) is bool:
                    safe_stream[key] = value
                elif type(value) is int and 0 <= value <= 40 * 1024**2:
                    safe_stream[key] = value
            if safe_stream:
                item["stream"] = safe_stream
        trace.append(item)
        if len(trace) >= 8:
            break
    return trace


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _testing_path(repository: Path) -> Path:
    path = repository / "Tools/testing"
    bazel_path = repository / "Tools/bazel"
    if (not path.is_dir() or path.resolve() != path or
            not bazel_path.is_dir() or bazel_path.resolve() != bazel_path):
        raise ParityError("maintained guest-probe modules are unavailable")
    for selected in (bazel_path, path):
        if str(selected) not in sys.path:
            sys.path.insert(0, str(selected))
    return path


def admit_guest_inputs(repository: Path, lane: str, retained: Path, *, builder: bool = False) -> dict[str, Any]:
    """Read and authenticate every pinned input needed by an owned guest route."""

    if lane not in {"docker", "apple-stock", "container-compose"}:
        raise ParityError("unknown parity lane for guest input admission")
    if not retained.is_absolute() or retained.resolve(strict=True) != retained:
        raise ParityError("guest release store must be canonical internal storage")
    if retained.stat().st_dev != ACCOUNT_HOME.stat().st_dev or retained.stat().st_uid != os.getuid():
        raise ParityError("guest release store must be user-owned internal storage")
    try:
        _testing_path(repository)
        images_lock = json.loads((repository / "Tools/bazel/guest-images.lock.json").read_text())
        if lane == "docker":
            from prepare_guest_images import require_image

            candidates = [image for image in images_lock.get("images", [])
                          if image.get("name") == "alpine-workload"]
            if len(candidates) != 1:
                raise ParityError("Docker guest lane requires one pinned alpine workload image")
            workload = require_image(candidates[0], retained / "guest-images")
            inputs = {"workload": workload}
        else:
            from guest_runtime import admit_guest
            from released_engine import provider_image_references

            kernel_lock = json.loads((repository / "Tools/bazel/guest-kernel.lock.json").read_text())
            release_lock = json.loads((repository / "Tools/bazel/releases.lock.json").read_text())
            provider_images = provider_image_references(release_lock, lane, retained)
            builder_lock = (json.loads((repository / "Tools/bazel/builder-images.lock.json").read_text())
                            if builder else None)
            inputs = admit_guest(kernel_lock, images_lock, lane, retained,
                                 builder_lock=builder_lock, fixture="E07-init-attachment",
                                 provider_image_references=provider_images)
        return inputs
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ParityError(f"pinned guest input admission failed ({type(error).__name__})") from None


def guest_input_identity(inputs: dict[str, Any]) -> dict[str, Any]:
    """Return public-safe digests for the admitted private guest inputs."""

    safe_inputs = {}
    for name, value in inputs.items():
        item = value.get("image", {})
        safe_inputs[name] = {
            "archiveSHA256": value.get("sha256"),
            "manifest": item.get("manifest"),
            "config": item.get("config"),
        }
        if name == "kernel":
            kernel_path = Path(value["files"]["kernel"])
            safe_inputs[name] = {"sha256": sha256(kernel_path), "size": kernel_path.stat().st_size}
    return safe_inputs


def preflight_guest_inputs(repository: Path, lane: str, retained: Path, *, builder: bool = False) -> dict[str, Any]:
    """Return immutable input identities after full read-only admission."""

    return guest_input_identity(admit_guest_inputs(repository, lane, retained, builder=builder))


def _retained_root(runner) -> Path:
    value = runner.environment.get("DEVCONTAINER_PARITY_RETAINED_ROOT")
    root = Path(value) if value else DEFAULT_RETAINED
    if not root.is_absolute() or root.resolve(strict=True) != root:
        raise ParityError("parity retained root must be an explicit canonical path")
    if root.stat().st_dev != ACCOUNT_HOME.stat().st_dev or root.stat().st_uid != os.getuid():
        raise ParityError("parity retained root must be user-owned internal storage")
    return root


def _require_private_directory(path: Path, *, create: bool = False) -> None:
    if create:
        path.mkdir(mode=0o700, parents=False, exist_ok=False)
    info = path.lstat()
    if (path.is_symlink() or not path.is_dir() or path.resolve() != path
            or info.st_uid != os.getuid() or info.st_mode & 0o777 != 0o700):
        raise ParityError("owned guest storage must be canonical, user-owned and private")


def _admitted_package_guest_identity(runner) -> dict[str, Any]:
    """Carry the package admitted for this guest case without relabeling candidates."""
    finalized = getattr(runner, "finalized_identity", None)
    if isinstance(finalized, dict):
        return {"sourceCommit": finalized.get("sourceCommit"),
                "archiveSHA256": finalized.get("archiveSHA256")}
    candidate = getattr(runner, "candidate_identity", None)
    if isinstance(candidate, dict):
        return {"sourceCommit": candidate.get("sourceCommit"),
                "archiveSHA256": candidate.get("assetSHA256"),
                "candidateInvocation": candidate.get("candidateInvocation"),
                "candidateReceiptSHA256": candidate.get("candidateReceiptSHA256")}
    return {"sourceCommit": None, "archiveSHA256": None}


def _active_provider_home(runner, *, fixture_selection: tuple[str, ...] | None = None) -> tuple[Path, dict]:
    """Authenticate the same private HOME used by the active provider API."""

    value = runner.environment.get("HOME")
    root = Path(value) if value else Path()
    if not value or not root.is_absolute() or root.resolve(strict=True) != root:
        raise ParityError("active provider HOME must be an explicit canonical path")
    _require_private_directory(root)
    app_root = root / "container"
    if runner.environment.get("CONTAINER_APP_ROOT") != str(app_root):
        raise ParityError("active provider HOME differs from its container application root")
    _require_private_directory(app_root)

    marker = root / "owner.json"
    info = marker.lstat()
    if (marker.is_symlink() or not marker.is_file() or info.st_uid != os.getuid()
            or info.st_nlink != 1 or info.st_size > 4096 or info.st_mode & 0o777 != 0o600):
        raise ParityError("active provider HOME ownership marker is unsafe")
    owner = json.loads(marker.read_bytes())
    identity = owner.get("identity") if isinstance(owner, dict) else None
    retained_value = runner.environment.get("DEVCONTAINER_PARITY_RETAINED_ROOT")
    retained = Path(retained_value) if retained_value else Path()
    if (not retained_value or not retained.is_absolute() or retained.resolve(strict=True) != retained):
        raise ParityError("active provider HOME has no canonical internal retained root")
    _require_private_directory(retained)
    if retained.stat().st_dev != ACCOUNT_HOME.stat().st_dev:
        raise ParityError("active provider guard must be on internal retained storage")
    guard_value = runner.environment.get("DEVCONTAINER_PARITY_GUARD")
    guard_path = Path(guard_value) if guard_value else Path()
    expected_guard = retained / "runtime-admission.json"
    if (not guard_value or guard_path != expected_guard or guard_path.is_symlink()
            or guard_path.resolve(strict=True) != guard_path):
        raise ParityError("active provider HOME has no canonical campaign guard")
    guard_info = guard_path.lstat()
    if (not guard_path.is_file() or guard_info.st_uid != os.getuid()
            or guard_info.st_nlink != 1 or guard_info.st_size > 4096
            or guard_info.st_mode & 0o777 != 0o600):
        raise ParityError("active provider campaign guard is unsafe")
    guard = json.loads(guard_path.read_bytes())
    guard_identity = guard.get("identity") if isinstance(guard, dict) else None
    scope = guard_identity.get("scope") if isinstance(guard_identity, dict) else None
    component_selection = fixture_selection in (
        ("E06-network-volume",), ("E07-init-attachment",),
        ("E13-compose-signals",), ("E14-compose-terminal-size",), ("E04-image-build",),
    )
    diagnostic_selection = (isinstance(fixture_selection, tuple) and bool(fixture_selection)
                            and len(set(fixture_selection)) == len(fixture_selection)
                            and set(fixture_selection) <= _NATIVE_DIAGNOSTIC_FIXTURES)
    diagnostic_fixtures = sorted(fixture_selection) if diagnostic_selection else None
    candidate = getattr(runner, "candidate_identity", None)
    finalized = getattr(runner, "finalized_identity", None)
    expected_guard_identity = None
    if scope == "finalized-native-parity":
        expected_guard_identity = {"campaign": guard_identity.get("campaign") if isinstance(guard_identity, dict) else None,
                                  "sourceCommit": (finalized or {}).get("sourceCommit"), "scope": scope}
    elif scope == "finalized-native-parity-component" and component_selection:
        expected_guard_identity = {"campaign": guard_identity.get("campaign") if isinstance(guard_identity, dict) else None,
                                  "sourceCommit": (finalized or {}).get("sourceCommit"), "scope": scope}
    elif scope == "finalized-native-parity-diagnostic" and diagnostic_selection:
        expected_guard_identity = {
            "campaign": guard_identity.get("campaign") if isinstance(guard_identity, dict) else None,
            "sourceCommit": (finalized or {}).get("sourceCommit"), "scope": scope,
            "diagnosticFixtures": diagnostic_fixtures,
        }
    elif scope == "unsigned-native-candidate-diagnostic" and diagnostic_selection:
        if (isinstance(candidate, dict)
                and candidate.get("scope") == "local-candidate-integration-only"
                and candidate.get("runtimeProfile") == "stock"
                and re.fullmatch(r"[0-9a-f]{40}", str(candidate.get("sourceCommit", "")))
                and re.fullmatch(r"[0-9a-f]{64}", str(candidate.get("assetSHA256", "")))
                and re.fullmatch(r"[0-9a-f]{64}", str(candidate.get("candidateReceiptSHA256", "")))
                and isinstance(candidate.get("candidateInvocation"), str)
                and candidate.get("candidateInvocation")):
            expected_guard_identity = {
                "campaign": guard_identity.get("campaign") if isinstance(guard_identity, dict) else None,
                "sourceCommit": candidate["sourceCommit"], "scope": scope,
                "candidateInvocation": candidate["candidateInvocation"],
                "candidateReceiptSHA256": candidate["candidateReceiptSHA256"],
                "archiveSHA256": candidate["assetSHA256"],
                "runtimeProfile": "stock", "diagnosticFixtures": diagnostic_fixtures,
            }
    if (not isinstance(guard, dict) or set(guard) != {"identity", "root"}
            or not isinstance(guard_identity, dict) or expected_guard_identity is None
            or guard_identity != expected_guard_identity
            or guard.get("root") != str(runner.output.parent)):
        raise ParityError("active provider campaign guard differs from this evidence root")
    expected = {"campaign": guard_identity["campaign"], "lane": runner.lane,
                "sourceCommit": guard_identity["sourceCommit"]}
    if (not isinstance(owner, dict) or set(owner) != {"identity", "root"} or owner.get("root") != str(root)
            or identity != expected):
        raise ParityError("active provider HOME ownership marker differs from this lane")
    return root, owner


class LaneRuntimeView:
    """Verify the existing LaneRunner endpoint without acquiring or starting one."""

    def __init__(self, runner, journal, socket: Path, compose: Path | None = None,
                 fixture_selection: tuple[str, ...] = ()) -> None:
        self.runner = runner
        self.journal = journal
        self.socket = socket
        self.compose = compose
        self.fixture_selection = fixture_selection
        self.endpoint = runner.environment.get("DOCKER_HOST", "")
        if runner.engine is not None:
            self.engine_pid = runner.engine.pid
            self.engine_sha256 = sha256(Path(runner.package_executable("devcontainer-engine")))
        else:
            self.engine_pid = None
            self.engine_sha256 = None

    def verify(self) -> None:
        runner = self.runner
        if runner.finalized_selection is not None:
            runner.readmit_finalized()
        if runner.lane == "docker":
            if not self.endpoint or runner.environment.get("DOCKER_HOST") != self.endpoint:
                raise ParityError("Docker oracle endpoint changed during guest fixture")
            runner.require_docker_oracle()
        else:
            if (runner.engine is None or runner.engine.pid != self.engine_pid
                    or runner.engine.poll() is not None):
                raise ParityError("owned compatibility Engine is no longer running")
            executable = Path(runner.package_executable("devcontainer-engine"))
            if not executable.is_file() or sha256(executable) != self.engine_sha256:
                raise ParityError("owned compatibility Engine bytes changed")
            if self.socket != runner.socket_root / "docker.sock" or not self.socket.is_socket():
                raise ParityError("owned compatibility Engine socket changed")
            _verify_native_api(runner, self.fixture_selection)


class ApiRuntimeView:
    """Verify the selected private API while admitted guest data is provisioned."""

    def __init__(self, runner, root: Path, owner: dict, journal: Any,
                 fixture_selection: tuple[str, ...]) -> None:
        self.runner, self.root, self.owner, self.journal = runner, root, owner, journal
        self.fixture_selection = fixture_selection

    def verify(self) -> None:
        if self.runner.finalized_selection is not None:
            self.runner.readmit_finalized()
        root, owner = _verify_native_api(self.runner, self.fixture_selection)
        if root != self.root or owner != self.owner:
            raise ParityError("active API HOME identity changed during guest provisioning")


def _verify_native_api(runner, fixture_selection: tuple[str, ...]) -> tuple[Path, dict]:
    """Bind API traffic to its admitted service, executable, private HOME and campaign."""

    root, owner = _active_provider_home(runner, fixture_selection=fixture_selection)
    environment = runner.environment
    container = Path(runner.provider_executable("DEVCONTAINER_CONTAINER_BIN", ""))
    api = container.parent / "container-apiserver"
    expected_sha = environment.get("DEVCONTAINER_API_SERVER_SHA256", "")
    expected_pid = environment.get("DEVCONTAINER_API_SERVICE_PID", "")
    definition_sha = environment.get("DEVCONTAINER_API_DEFINITION_SHA256", "")
    if (not api.is_absolute() or api.resolve(strict=True) != api or api.is_symlink()
            or not api.is_file() or not os.access(api, os.X_OK)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha)
            or sha256(api) != expected_sha
            or not expected_pid.isdecimal() or int(expected_pid) <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", definition_sha)):
        raise ParityError("selected private API executable or identity is not admitted")
    from runtime_services import verify_selected_api

    verify_selected_api(root, api, int(expected_pid), definition_sha)
    return root, owner

class OwnedGuestFixtureRunner:
    """Own guest image preparation and execute routed fixtures on one active lane."""

    def __init__(self, runner, fixtures: list[Any], *, admitted_inputs: dict[str, Any] | None = None,
                 fixture_selection: tuple[str, ...] | None = None, builder_required: bool = False) -> None:
        self.runner = runner
        self.repository = runner.repository
        self.lane = runner.lane
        self.fixtures = fixtures
        self.fixture_selection = (fixture_selection if fixture_selection is not None
                                  else tuple(fixture.identifier for fixture in fixtures))
        self.builder_required = builder_required
        self.retained = _retained_root(runner)
        self.inputs = (admitted_inputs if admitted_inputs is not None
                       else admit_guest_inputs(self.repository, self.lane, self.retained,
                                               builder=builder_required))
        self.socket: Path | None = None
        self.container = "" if self.lane == "docker" else runner.provider_executable(
            "DEVCONTAINER_CONTAINER_BIN", shutil.which("container") or "")
        if self.lane != "docker" and not self.container:
            raise ParityError("owned guest route requires the admitted native container provider")
        self.compose_provider: Path | None = None
        self.docker_cli: Path | None = None
        self.docker_compose: Path | None = None
        self.compose = self._admit_compose() if any(
            fixture.identifier in COMPOSE_FIXTURES for fixture in fixtures) else None
        self.preparation: tuple[Path, Any, LaneRuntimeView | ApiRuntimeView, dict] | None = None
        self.preparation_error: str | None = None
        self._provision_event_sequence = 0
        self.image_preexisting = False
        self.loaded_image_id: str | None = None
        self.builder_preparation_guest = None

    def attach_endpoint(self) -> None:
        """Attach after Engine startup and replace API-only provisioning checks."""

        self.socket = self._select_socket()
        if self.lane != "docker" and self.preparation is not None and self.preparation_error is None:
            root, journal, _runtime, owner = self.preparation
            runtime = LaneRuntimeView(self.runner, journal, self.socket, self.compose,
                                      self.fixture_selection)
            runtime.verify()
            self.preparation = (root, journal, runtime, owner)

    def _select_socket(self) -> Path:
        if self.lane == "docker":
            endpoint = self.runner.environment.get("DOCKER_HOST", "")
            if not endpoint.startswith("unix://"):
                raise ParityError("owned guest probes require the explicitly selected Unix Docker endpoint")
            socket = Path(endpoint.removeprefix("unix://"))
            if socket.is_symlink():
                socket = socket.resolve(strict=True)
        else:
            if self.runner.socket_root is None:
                raise ParityError("owned compatibility Engine socket has not been selected")
            socket = self.runner.socket_root / "docker.sock"
        if not socket.is_absolute() or socket.resolve() != socket:
            raise ParityError("owned guest probes require one canonical Unix socket")
        return socket

    def _admit_compose(self) -> Path:
        if self.lane == "docker":
            value = self.runner.environment.get("DEVCONTAINER_DOCKER_COMPOSE_BIN")
            if not value:
                raise ParityError("Docker guest Compose fixtures require the explicitly pinned Compose executable")
            executable = Path(value)
            expected = self.runner.manifest["referencePins"]["docker"]["composeSHA256"]
            if not executable.is_absolute() or executable.resolve(strict=True) != executable or sha256(executable) != expected:
                raise ParityError("Docker Compose executable differs from the manifest-pinned bytes")
            version = subprocess.run([str(executable), "version", "--short"], env=self.runner.environment,
                                     capture_output=True, text=True, timeout=20, check=False)
            if version.returncode != 0 or version.stdout.strip() != self.runner.manifest["referencePins"]["docker"]["composeVersion"]:
                raise ParityError("Docker Compose executable differs from the manifest-pinned version")
            return executable
        if self.runner.finalized is None:
            raise ParityError("native guest Compose fixtures require the admitted finalized package")
        executable = Path(self.runner.finalized["executables"]["devcontainer-compose"])
        expected = self.runner.finalized["productionBinarySHA256"]["bin/devcontainer-compose"]
        if not executable.is_absolute() or executable.resolve(strict=True) != executable or sha256(executable) != expected:
            raise ParityError("native Compose frontend differs from the signed package inventory")
        if self.lane == "apple-stock":
            self.docker_cli, self.docker_compose = self._admit_stock_compose_tools()
        elif self.lane == "container-compose":
            self.compose_provider = self._admit_external_compose_provider()
        else:
            raise ParityError("native guest Compose lane has no supported frontend")
        return executable

    def _admit_stock_compose_tools(self) -> tuple[Path, Path]:
        """Authenticate Docker and standalone Compose against the checked-in lock."""
        environment = self.runner.environment
        pins = self.runner.manifest["referencePins"]["docker"]

        def executable_from_env(name: str, expected_sha: str) -> Path:
            value = environment.get(name)
            if not value:
                raise ParityError(f"stock Compose requires the explicitly admitted {name} executable")
            path = Path(value)
            if (not path.is_absolute() or path.resolve(strict=True) != path or path.is_symlink()
                    or not path.is_file() or not os.access(path, os.X_OK) or sha256(path) != expected_sha):
                raise ParityError(f"stock Compose {name} differs from its manifest-pinned bytes")
            return path

        docker = executable_from_env("DEVCONTAINER_DOCKER_BIN", pins["cliSHA256"])
        compose = executable_from_env("DEVCONTAINER_DOCKER_COMPOSE_BIN", pins["composeSHA256"])
        docker_version = subprocess.run([str(docker), "--version"], cwd=self.repository, env=environment,
                                        capture_output=True, text=True, timeout=20, check=False)
        if (docker_version.returncode != 0
                or not docker_version.stdout.strip().startswith(f"Docker version {pins['cliVersion']},")):
            raise ParityError("stock Docker CLI differs from its manifest-pinned version")
        compose_version = subprocess.run([str(compose), "version", "--short"], cwd=self.repository,
                                         env=environment, capture_output=True, text=True,
                                         timeout=20, check=False)
        if compose_version.returncode != 0 or compose_version.stdout.strip() != pins["composeVersion"]:
            raise ParityError("stock Docker Compose differs from its manifest-pinned version")
        return docker, compose

    def _admit_external_compose_provider(self) -> Path:
        """Authenticate the separate container-compose provider before runtime use."""
        environment = self.runner.environment
        value = environment.get("DEVCONTAINER_COMPOSE_BIN")
        expected_sha = environment.get("DEVCONTAINER_COMPOSE_PROVIDER_SHA256")
        if (not value or not expected_sha or len(expected_sha) != 64
                or any(character not in "0123456789abcdef" for character in expected_sha)):
            raise ParityError("native Compose requires its explicitly admitted external provider and SHA-256")
        executable = Path(value)
        if (not executable.is_absolute() or executable.resolve(strict=True) != executable
                or executable.is_symlink() or not executable.is_file() or not os.access(executable, os.X_OK)
                or sha256(executable) != expected_sha):
            raise ParityError("external container-compose provider differs from its admitted executable")
        pins = self.runner.manifest["referencePins"]["containerCompose"]
        observed = subprocess.run([str(executable), "version", "--format", "json"],
                                  cwd=self.repository, env=environment, capture_output=True,
                                  text=True, timeout=20, check=False)
        if observed.returncode != 0:
            raise ParityError("external container-compose version command failed")
        try:
            identity = json.loads(observed.stdout)
        except json.JSONDecodeError as error:
            raise ParityError("external container-compose returned invalid version JSON") from error
        if (not isinstance(identity, dict) or identity.get("version") != pins["stableVersion"]
                or identity.get("commit") != pins["stableCommit"]):
            raise ParityError("external container-compose provider differs from its locked release")
        return executable

    def _compose_wrapper_selection(self) -> dict[str, str] | None:
        """Bind native Compose CLI selection to inputs admitted for this lane."""
        if self.lane == "docker":
            return None
        if self.lane not in {"apple-stock", "container-compose"} or self.socket is None:
            raise ParityError("native Compose wrapper has no admitted lane endpoint")
        if self.compose is None or not self.container:
            raise ParityError("native Compose wrapper has no admitted provider executables")
        state_value = self.runner.environment.get("DEVCONTAINER_STATE")
        if not state_value:
            raise ParityError("native Compose wrapper has no selected runtime state")
        state = Path(state_value)
        if (not state.is_absolute() or state.resolve(strict=True) != state or not state.is_file()
                or state.is_symlink()):
            raise ParityError("native Compose wrapper state is not the selected canonical database")
        container = Path(self.container)
        if (not container.is_absolute() or container.resolve(strict=True) != container
                or container.is_symlink() or not container.is_file() or not os.access(container, os.X_OK)):
            raise ParityError("native Compose wrapper container is not the admitted executable")
        if (not self.compose.is_absolute() or self.compose.resolve(strict=True) != self.compose
                or self.compose.is_symlink() or not self.compose.is_file() or not os.access(self.compose, os.X_OK)):
            raise ParityError("native Compose wrapper frontend is not the admitted executable")
        selection = {
            "DEVCONTAINER_BACKEND": "stock" if self.lane == "apple-stock" else "container-compose",
            "DEVCONTAINER_COMPOSE_PROVIDER": "docker" if self.lane == "apple-stock" else "container-compose",
            "DEVCONTAINER_CONTAINER_BIN": str(container),
            "DEVCONTAINER_SOCKET": str(self.socket),
            "DEVCONTAINER_STATE": str(state),
        }
        if self.lane == "apple-stock":
            pins = self.runner.manifest["referencePins"]["docker"]
            compose = self.runner.environment.get("DEVCONTAINER_DOCKER_COMPOSE_BIN")
            if (self.docker_cli is None or self.docker_compose is None
                    or compose != str(self.docker_compose)
                    or self.docker_cli.resolve(strict=True) != self.docker_cli or self.docker_cli.is_symlink()
                    or not self.docker_cli.is_file() or not os.access(self.docker_cli, os.X_OK)
                    or self.docker_compose.resolve(strict=True) != self.docker_compose
                    or self.docker_compose.is_symlink() or not self.docker_compose.is_file()
                    or not os.access(self.docker_compose, os.X_OK)
                    or sha256(self.docker_cli) != pins["cliSHA256"]
                    or sha256(self.docker_compose) != pins["composeSHA256"]):
                raise ParityError("stock Compose tools changed after manifest-pinned admission")
            selection.update({"DEVCONTAINER_DOCKER_BIN": str(self.docker_cli),
                              "DEVCONTAINER_DOCKER_COMPOSE_BIN": str(self.docker_compose)})
        else:
            expected_provider_sha = self.runner.environment.get("DEVCONTAINER_COMPOSE_PROVIDER_SHA256")
            if (self.compose_provider is None or not expected_provider_sha
                    or self.compose_provider.resolve(strict=True) != self.compose_provider
                    or self.compose_provider.is_symlink() or not self.compose_provider.is_file()
                    or not os.access(self.compose_provider, os.X_OK)
                    or sha256(self.compose_provider) != expected_provider_sha):
                raise ParityError("external container-compose provider changed after admission")
            selection.update({"DEVCONTAINER_COMPOSE_BIN": str(self.compose_provider)})
        return selection

    def _case_paths(self, fixture: Any) -> tuple[Path, Path, dict]:
        base = self.runner.output.parent / "owned-guest-runtime" / self.lane
        if not base.exists():
            base.mkdir(mode=0o700, parents=True)
        _require_private_directory(base)
        root = base / fixture.identifier
        _require_private_directory(root, create=True)
        identity = {
            "campaign": self.runner.output.parent.name,
            "lane": self.lane,
            "fixture": fixture.identifier,
            "endpointSHA256": hashlib.sha256(str(self.socket).encode()).hexdigest(),
            **_admitted_package_guest_identity(self.runner),
        }
        owner = {"identity": identity, "root": str(root)}
        owner_bytes = json.dumps(owner, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        marker = root / "owner.json"
        marker.write_bytes(owner_bytes)
        marker.chmod(0o600)
        journal_parent = self._journal_parent()
        _require_private_directory(journal_parent)
        from service_journal import ServiceJournal

        journal_key = hashlib.sha256(owner_bytes).hexdigest()
        journal_path = journal_parent / f"{journal_key}.sqlite"
        journal = ServiceJournal(journal_path, owner, create=True)
        return root, journal, owner

    def _journal_parent(self) -> Path:
        base = self.retained / "private-runtime"
        if not base.exists():
            base.mkdir(mode=0o700)
        _require_private_directory(base)
        parent = base / "parity-guest"
        if not parent.exists():
            parent.mkdir(mode=0o700)
        _require_private_directory(parent)
        return parent

    def prepare(self) -> None:
        """Prepare Docker workload images after selecting the Docker endpoint."""

        if self.lane != "docker":
            raise ParityError("native guest inputs must be provisioned before Engine startup")

        first = next((fixture for fixture in self.fixtures if fixture.identifier in OWNED_GUEST_FIXTURES), None)
        if first is None:
            return
        if self.socket is None:
            raise ParityError("owned guest adapter has not been attached to the selected lane endpoint")
        root, journal, owner = self._case_paths_for_preparation()
        runtime = LaneRuntimeView(self.runner, journal, self.socket, self.compose)
        self.preparation = (root, journal, runtime, owner)
        runtime.verify()
        current_inputs = admit_guest_inputs(self.repository, self.lane, self.retained)
        if guest_input_identity(current_inputs) != guest_input_identity(self.inputs):
            raise ParityError("guest input bytes changed after preflight admission")
        self.inputs = current_inputs
        self._prepare_docker_image(runtime, journal)
        self.preparation = (root, journal, runtime, owner)

    def prepare_native_provider(self) -> None:
        """Install the authenticated kernel and images through the ready private API."""

        if self.lane == "docker":
            raise ParityError("Docker image preparation uses its selected Engine endpoint")
        if self.preparation is not None or self.preparation_error is not None:
            raise ParityError("native guest provisioning may run only once")
        first = next((fixture for fixture in self.fixtures
                      if fixture.identifier in OWNED_GUEST_FIXTURES
                      or fixture.identifier == "E06-network-volume"), None)
        if first is None and not self.builder_required:
            return
        root, journal, owner = self._case_paths_for_preparation()
        runtime = ApiRuntimeView(self.runner, root, owner, journal, self.fixture_selection)
        self.preparation = (root, journal, runtime, owner)
        runtime.verify()
        before = admit_guest_inputs(self.repository, self.lane, self.retained,
                                    builder=self.builder_required)
        if guest_input_identity(before) != guest_input_identity(self.inputs):
            raise ParityError("guest input bytes changed before native provisioning")
        if first is not None or self.builder_required:
            from guest_runtime import ReleasedGuest

            preparation_fixture = first.identifier if first is not None else "E07-init-attachment"
            guest = ReleasedGuest(self.inputs, preparation_fixture, root, owner, runtime, self.container,
                                  None, observe=lambda event: self._record_provision_event(journal, event))
            guest.provision()
        runtime.verify()
        after = admit_guest_inputs(self.repository, self.lane, self.retained,
                                   builder=self.builder_required)
        if guest_input_identity(after) != guest_input_identity(self.inputs):
            raise ParityError("guest input bytes changed during native provisioning")
        self.inputs = after
        self.preparation = (root, journal, runtime, owner)

    def prepare_native_builder(self) -> None:
        """Provision the exact selected native builder outside E04's timer."""
        if not self.builder_required or self.lane == "docker" or self.preparation is None:
            raise ParityError("native E04 builder preparation is not admitted")
        if self.preparation_error is not None or "builder" not in self.inputs:
            raise ParityError("native E04 has no complete admitted builder input")
        root, journal, runtime, owner = self.preparation
        runtime.verify()
        from guest_runtime import ReleasedGuest
        from build_runtime import ReleasedBuilder
        from host_runtime import deadline
        from case_evidence import canonical

        builder_guest = ReleasedGuest(self.inputs, "E04-image-build", root, owner, runtime,
                                      self.container, None)
        builder_guest.builder = ReleasedBuilder(
            self.inputs["builder"], root, journal, builder_guest.command)
        journal.put("e04-builder-readiness-intent.json", canonical({
            "fixture": "E04-image-build", "root": str(root),
            "inputsSHA256": hashlib.sha256(canonical(self.inputs)).hexdigest(),
        }))
        with deadline(300):
            builder_guest.builder.provision()
        runtime.verify()
        self.builder_preparation_guest = builder_guest

    def prepare_native_builder_readiness(self) -> None:
        """Prove BuildKit accepts work, then remove only the nonce-tagged image."""
        if self.builder_preparation_guest is None or self.socket is None:
            raise ParityError("native E04 readiness has no owned builder and Engine endpoint")
        from e04_build_readiness import readiness_fixture, command_arguments
        from engine_probe import request
        from urllib.parse import quote
        from case_evidence import canonical

        directory, context, tag, token = readiness_fixture()
        guest = self.builder_preparation_guest
        runtime = self.preparation[2]
        journal = self.preparation[1]
        path = f"/v1.53/images/{quote(tag, safe='')}/json"
        started = time.monotonic_ns()
        try:
            runtime.verify()
            status, _payload = request(self.socket, "GET", path)
            if status != 404:
                raise ParityError("native E04 readiness image tag already exists")
            journal.put("e04-readiness-build-intent.json", canonical({
                "tag": tag, "nonceSHA256": hashlib.sha256(token.encode()).hexdigest(),
                "dockerfileSHA256": hashlib.sha256((context / "Dockerfile").read_bytes()).hexdigest(),
            }))
            guest.command("guest-e04-readiness-build", command_arguments(tag, token, context), timeout=300)
            status, payload = request(self.socket, "GET", path)
            value = json.loads(payload) if payload else None
            if (status != 200 or not isinstance(value, dict) or not isinstance(value.get("Id"), str)
                    or tag not in value.get("RepoTags", [])):
                raise ParityError("native E04 readiness build did not create its exact image")
            identity = {"tag": tag, "imageID": value["Id"]}
            journal.put("e04-readiness-image-created.json", canonical(identity))
            status, payload = request(self.socket, "GET", path)
            current = json.loads(payload) if payload else None
            if status != 200 or not isinstance(current, dict) or current.get("Id") != identity["imageID"]:
                raise ParityError("native E04 readiness image identity changed before cleanup")
            journal.put("e04-readiness-image-remove-intent.json", canonical(identity))
            guest.command("guest-e04-readiness-remove", ["image", "rm", tag], timeout=120)
            status, _payload = request(self.socket, "GET", path)
            if status != 404:
                raise ParityError("native E04 readiness image remains after exact cleanup")
            journal.put("e04-readiness-image-removed.json", canonical(identity))
            journal.put("e04-readiness-build-duration.json", canonical({
                "durationNS": time.monotonic_ns() - started,
                "imageID": identity["imageID"], "tag": tag,
            }))
            runtime.verify()
        finally:
            directory.cleanup()

    def _record_provision_event(self, journal: Any, event: dict[str, Any]) -> None:
        name = f"provision-event-{self._provision_event_sequence:04d}.json"
        self._provision_event_sequence += 1
        journal.put(name, json.dumps(event, sort_keys=True, separators=(",", ":")).encode())

    def _case_paths_for_preparation(self) -> tuple[Path, Any, dict]:
        _testing_path(self.repository)
        # Provisioning writes the kernel and default selection into the HOME
        # actually served by the selected API. A fixture-scoped SSD directory
        # here would make ReleasedGuest verify an inactive, unrelated store.
        if self.lane == "docker":
            base = self.runner.output.parent / "owned-guest-runtime" / self.lane
            if not base.exists():
                base.mkdir(mode=0o700, parents=True)
            _require_private_directory(base)
            root = base / "preparation"
            _require_private_directory(root, create=True)
            owner = {"identity": {"campaign": self.runner.output.parent.name, "lane": self.lane,
                                  "fixture": "owned-guest-preparation",
                                  **_admitted_package_guest_identity(self.runner)},
                     "root": str(root)}
            owner_bytes = json.dumps(owner, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            (root / "owner.json").write_bytes(owner_bytes)
            (root / "owner.json").chmod(0o600)
        else:
            selection = self.fixture_selection
            root, owner = _active_provider_home(self.runner, fixture_selection=selection)
        owner_bytes = json.dumps(owner, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        journal_parent = self._journal_parent()
        from service_journal import ServiceJournal

        journal = ServiceJournal(journal_parent / (hashlib.sha256(owner_bytes).hexdigest() + ".sqlite"), owner,
                                 create=True)
        return root, journal, owner

    def _prepare_docker_image(self, runtime: LaneRuntimeView, journal: Any) -> None:
        from engine_probe import request

        image = self.inputs["workload"]["image"]
        identifier = image["manifest"]
        status, payload = request(self.socket, "GET", f"/v1.53/images/{identifier}/json")
        existing = json.loads(payload) if payload else {}
        if status == 200:
            if (existing.get("Id") != identifier
                    or existing.get("Descriptor", {}).get("digest") != identifier
                    or existing.get("Os") != "linux" or existing.get("Architecture") != "arm64"):
                raise ParityError("existing Docker workload image differs from its locked OCI identity")
            self.image_preexisting = True
            journal.put("workload-image-existing.json", json.dumps(
                {"id": identifier, "existing": True}, sort_keys=True, separators=(",", ":")).encode())
            self.loaded_image_id = identifier
            return
        if status != 404:
            raise ParityError("cannot inspect locked Docker workload image")
        loaded = subprocess.run([self.runner.docker, "--host", self.runner.environment["DOCKER_HOST"],
                                  "image", "load", "--input", self.inputs["workload"]["path"]],
                                cwd=self.repository, env=self.runner.environment, capture_output=True,
                                text=True, timeout=300, check=False)
        journal.put("workload-image-load.json", json.dumps(
            {"archiveSHA256": self.inputs["workload"]["sha256"], "exitCode": loaded.returncode,
             "stdoutSHA256": hashlib.sha256(loaded.stdout.encode()).hexdigest(),
             "stderrSHA256": hashlib.sha256(loaded.stderr.encode()).hexdigest()},
            sort_keys=True, separators=(",", ":")).encode())
        if loaded.returncode != 0:
            raise ParityError("locked Docker workload image load failed")
        status, payload = request(self.socket, "GET", f"/v1.53/images/{identifier}/json")
        observed = json.loads(payload) if payload else {}
        if (status != 200 or observed.get("Id") != identifier
                or observed.get("Descriptor", {}).get("digest") != identifier
                or observed.get("Os") != "linux" or observed.get("Architecture") != "arm64"):
            raise ParityError("Docker did not admit the exact locked workload image")
        self.loaded_image_id = identifier
        journal.put("workload-image-loaded.json", json.dumps(
            {"id": identifier, "archiveSHA256": self.inputs["workload"]["sha256"]},
            sort_keys=True, separators=(",", ":")).encode())
        runtime.verify()

    def run(self, fixture: Any, raw: Path) -> dict[str, Any]:
        if fixture.identifier not in OWNED_GUEST_FIXTURES:
            raise ParityError(f"{fixture.identifier} has no owned guest route")
        if self.preparation_error is not None:
            return {
                "id": fixture.identifier,
                "status": "failed",
                "durationSeconds": 0.0,
                "observations": {},
                "differences": [],
                "diagnostic": f"owned guest preparation failed: {self.preparation_error}",
            }
        if self.preparation is None or self.socket is None:
            raise ParityError("owned guest preparation did not complete")
        started = time.monotonic()
        observations: dict[str, str] = {}
        differences: list[str] = []
        diagnostic = ""
        status = "failed"
        signal_stream: dict[str, Any] | None = None
        signal_contract: dict[str, Any] | None = None
        root = journal = runtime = owner = None
        guest = None
        events: list[dict] = []
        try:
            root, journal, owner = self._case_paths(fixture)
            runtime = LaneRuntimeView(self.runner, journal, self.socket, self.compose,
                                      getattr(self, "fixture_selection",
                                              tuple(item.identifier for item in self.fixtures)))
            runtime.verify()
            from guest_runtime import GUEST_API_VERSION, ReleasedGuest

            inputs = dict(self.inputs)
            if self.compose is not None:
                key = "compose" if self.lane == "docker" else "composeCandidate"
                executable_key = "docker-compose" if self.lane == "docker" else "compose"
                inputs[key] = {"executables": {executable_key: str(self.compose)}}
            image_id = self.inputs["workload"]["image"].get("manifest") if self.lane == "docker" else None
            guest = ReleasedGuest(inputs, fixture.identifier, root, owner, runtime, self.container,
                                  self.socket, image_id=image_id, observe=events.append,
                                  compose_selection=(self._compose_wrapper_selection()
                                                     if fixture.identifier in COMPOSE_FIXTURES else None))
            observations = guest.operation()
            differences = assert_contract(fixture, observations)
            if differences:
                raise ParityError("; ".join(differences))
            status = "passed"
        except (OSError, ValueError, RuntimeError, ParityError,
                subprocess.SubprocessError, TimeoutError) as error:
            diagnostic = str(error)
        finally:
            if root is not None and journal is not None and owner is not None:
                try:
                    if runtime is not None:
                        runtime.verify()
                    if runtime is not None and guest is not None:
                        cleanup = guest.cleanup()
                        if cleanup.get("status") != "passed" or cleanup.get("remainingOwnedResources"):
                            raise ParityError("owned guest fixture cleanup is incomplete")
                        if fixture.identifier == "E13-compose-signals":
                            try:
                                from compose_foreground_probe import signal_stream_summary

                                records = journal.records()
                                inspection = json.loads(records["compose-foreground-inspection.json"])
                                if not isinstance(inspection, dict):
                                    raise ValueError("E13 inspection evidence is not an object")
                                config = inspection.get("config")
                                if (not isinstance(config, dict) or set(config) != {"Tty", "OpenStdin"}
                                        or config["Tty"] is not True or config["OpenStdin"] is not False):
                                    raise ValueError("E13 inspected guest did not use the TTY signal contract")
                                signal_contract = {"modeVersion": 2, "tty": True, "openStdin": False}
                                signal_stream = signal_stream_summary(records.get("guest-compose-foreground.log"))
                            except (TypeError, ValueError) as error:
                                status = "failed"
                                diagnostic = f"{diagnostic}; E13 stream evidence: {error}".strip("; ")
                            except KeyError as error:
                                status = "failed"
                                diagnostic = f"{diagnostic}; E13 inspection evidence: {error}".strip("; ")
                    journal.put("probe-events.json", json.dumps(events, sort_keys=True,
                                                                  separators=(",", ":")).encode())
                    journal_receipt = journal.receipt()
                    owner_marker = root / "owner.json"
                    expected_owner = json.dumps(owner, sort_keys=True, separators=(",", ":"),
                                                allow_nan=False).encode()
                    if owner_marker.read_bytes() != expected_owner:
                        raise ParityError("owned guest case root identity changed")
                    receipt_path = raw / "owned-guest-journal-receipt.json"
                    receipt_path.write_text(json.dumps(journal_receipt, sort_keys=True, indent=2) + "\n",
                                            encoding="utf-8")
                    shutil.rmtree(root)
                except (OSError, ValueError, RuntimeError, ParityError,
                        subprocess.SubprocessError, TimeoutError) as error:
                    status = "failed"
                    diagnostic = f"{diagnostic}; cleanup: {error}".strip("; ")
                    try:
                        journal.put("probe-events-cleanup-failed.json", json.dumps(
                            events, sort_keys=True, separators=(",", ":")).encode())
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError,
                            TimeoutError) as evidence_error:
                        diagnostic += f"; probe-event evidence: {evidence_error}"
                    difference = f"{fixture.identifier}: owned guest cleanup is uncertain: {error}"
                    if difference not in self.runner.cleanup_differences:
                        self.runner.cleanup_differences.append(difference)
                    if self.runner.lane != "docker":
                        self.runner._preserve_engine_on_uncertain_guest_cleanup = True
            elif status != "passed":
                status = "failed"
        result = {
            "id": fixture.identifier,
            "status": status,
            "durationSeconds": round(time.monotonic() - started, 3),
            "observations": observations,
            "differences": differences,
            "diagnostic": diagnostic,
        }
        if fixture.identifier == "E07-init-attachment" and status == "failed":
            trace = _safe_attachment_diagnostic_trace(events)
            if trace:
                result["diagnosticTrace"] = trace
        if signal_stream is not None:
            result["signalStream"] = signal_stream
        if signal_contract is not None:
            result["signalContract"] = signal_contract
        return result

    def cleanup(self) -> None:
        if self.preparation_error is not None:
            raise ParityError("owned guest preparation is incomplete; preserve the selected runtime for recovery")
        if self.builder_preparation_guest is not None:
            if self.preparation is None:
                raise ParityError("native builder owner disappeared before cleanup")
            runtime = self.preparation[2]
            runtime.verify()
            from host_runtime import deadline

            with deadline(130):
                self.builder_preparation_guest.builder.cleanup()
            runtime.verify()
            _root, journal, _runtime, _owner = self.preparation
            receipt = journal.receipt()
            (self.runner.output / "native-e04-builder-journal-receipt.json").write_text(
                json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            self.builder_preparation_guest = None
        if self.lane != "docker" or self.loaded_image_id is None or self.image_preexisting:
            return
        from engine_probe import request

        runtime = self.preparation[2] if self.preparation else None
        if runtime is not None:
            runtime.verify()
        # Never remove an image while a container refers to it. If another
        # actor adopted it after our load, retain it and fail closed.
        status, payload = request(self.socket, "GET", "/v1.53/containers/json?all=1")
        if status != 200:
            raise ParityError("cannot prove Docker guest image cleanup is safe")
        containers = json.loads(payload)
        if any(item.get("ImageID") == self.loaded_image_id
               or item.get("Image") == self.loaded_image_id for item in containers):
            raise ParityError("locked workload image is now referenced by another container")
        result = subprocess.run([self.runner.docker, "--host", self.runner.environment["DOCKER_HOST"],
                                 "image", "rm", self.loaded_image_id], cwd=self.repository,
                                env=self.runner.environment, capture_output=True, text=True,
                                timeout=120, check=False)
        if result.returncode != 0:
            raise ParityError("exact newly loaded guest image could not be removed")
        status, payload = request(self.socket, "GET", f"/v1.53/images/{self.loaded_image_id}/json")
        if status != 404:
            raise ParityError("newly loaded guest image remains after cleanup")
