"""D01 through the admitted private bundle and an exclusively owned Apple engine."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time

from case_evidence import canonical
from devcontainer_reference import DevcontainerReference
from devcontainer_build_reference import DevcontainerBuildReference
from devcontainer_users_reference import DevcontainerUsersReference
from devcontainer_lifecycle_reference import DevcontainerLifecycleReference
from devcontainer_features_reference import DevcontainerFeaturesReference
from devcontainer_ports_reference import DevcontainerPortsReference
from devcontainer_reuse_reference import DevcontainerReuseReference, COMMANDS as REUSE_COMMANDS, UP_COMMANDS
from devcontainer_compose_reference import DevcontainerComposeReference
from devcontainer_dependencies_reference import DevcontainerDependenciesReference, DATABASE_IMAGE
from guest_fixture import OWNER_LABEL
from guest_runtime import diagnostic_snapshot
from host_runtime import OwnedProcess


COMMANDS = ("devcontainer-image-pull", "devcontainer-dependency-pull", "devcontainer-up", "devcontainer-exec", "devcontainer-frozen-lock", *REUSE_COMMANDS)


class CandidateCommands:
    """Keep CLI process completion and both diagnostic streams recoverable."""

    def __init__(self, root: Path, socket: Path, runtime, container: str):
        self.root, self.socket, self.runtime, self.container = root, socket, runtime, container
        self.journal = runtime.journal
        self.uncertain = False
        self.children = []
        self.pending_logs = {}
        self.attachments = []

    def retain_logs(self):
        for name, paths in list(self.pending_logs.items()):
            if name + "-stopped.json" not in self.journal.records():
                if any(name == pending for pending, _ in self.attachments):
                    continue  # Foreground run helpers live until the guest is removed.
                raise ValueError("D01 command shutdown is unverified")
            for suffix, path in paths:
                data, metadata = diagnostic_snapshot(path)
                self.journal.put(name + suffix + ".log", data)
                self.journal.put(name + suffix + "-log.json", metadata)
            del self.pending_logs[name]

    def command(self, name: str, arguments: list[str], *, timeout=60, separate_output=False):
        if name not in COMMANDS or self.uncertain or name + "-intent.json" in self.journal.records():
            raise ValueError("Uncertain or repeated D01 command")
        self.runtime.verify()
        self.journal.put(name + "-intent.json", canonical({"arguments": arguments, "timeout": timeout,
                                                         "separateOutput": True}))
        child = OwnedProcess()
        self.children.append(child)
        self.uncertain = True
        started = time.monotonic_ns()
        paths = [("", self.root / (name + ".log")), ("-stderr", self.root / (name + "-stderr.log"))]
        try:
            with paths[0][1].open("xb") as output, paths[1][1].open("xb") as errors:
                self.pending_logs[name] = paths
                try:
                    child.start(arguments, self.root, output, errors=errors,
                                provider_install=Path(self.container).parent.parent)
                    self.journal.put(name + "-process.json", canonical({"pid": child.process.pid}))
                    code = child.process.wait(timeout=timeout)
                    self.journal.put(name + "-exit.json", canonical({"code": code,
                                     "durationNS": time.monotonic_ns() - started}))
                finally:
                    if name in UP_COMMANDS and name + "-exit.json" in self.journal.records():
                        # The upstream CLI intentionally leaves its foreground
                        # attachment behind. Do not signal a reaped leader's group;
                        # guest deletion closes the stream before final group proof.
                        self.attachments.append((name, child))
                    else:
                        child.stop()
                        self.journal.put(name + "-stopped.json", canonical({"verifiedStopped": True}))
        finally:
            self.retain_logs()
        self.uncertain = False
        self.runtime.verify()
        snapshots = [diagnostic_snapshot(path) for _, path in paths]
        if any(json.loads(metadata)["truncated"] for _, metadata in snapshots):
            raise ValueError("D01 command output exceeded its bound")
        if code != 0:
            raise RuntimeError("D01 command failed; private diagnostic logs retained")
        return snapshots[0][0]

    def prepare_cleanup(self):
        attached = {child for _, child in self.attachments}
        for child in self.children:
            if child not in attached:
                child.stop()
        self.retain_logs()
        records = self.journal.records()
        deferred = {name for name, _ in self.attachments}
        self.uncertain = any(name + "-intent.json" in records and
                             (name + "-exit.json" not in records or
                              (name + "-stopped.json" not in records and name not in deferred))
                             for name in COMMANDS)

    def close(self):
        self.prepare_cleanup()
        for name, child in self.attachments:
            end = time.monotonic() + 5
            while True:
                try:
                    child.stop()
                    break
                except RuntimeError:
                    if time.monotonic() >= end:
                        raise
                    time.sleep(0.02)
            self.journal.put(name + "-stopped.json", canonical({"verifiedStopped": True}))
        self.attachments.clear()
        for child in self.children:
            child.stop()
        self.retain_logs()
        records = self.journal.records()
        self.uncertain = any(name + "-intent.json" in records and
                             (name + "-exit.json" not in records or name + "-stopped.json" not in records)
                             for name in COMMANDS)


class DevcontainerCandidate(DevcontainerReference):
    """Preserve reference observations; explicitly admit native UUID identities."""

    id_pattern = r"(?:[0-9a-f]{64}|[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12})"

    def __init__(self, commands: CandidateCommands, inputs: dict, owner: dict, *, observe=None):
        super().__init__(commands, {**inputs, "devcontainers": inputs["devcontainerCandidate"]}, owner, observe=observe)

    def prepare(self):
        (self.vm.root / "workspace").mkdir(mode=0o700, exist_ok=True)
        super().prepare()

    def prepare_image(self):
        # Preserve the checked-in index digest, not a mutable tag or leaf-only
        # alias. The native provider pulls this exact image into its private store.
        self.vm.command("devcontainer-image-pull", [self.vm.container, "image", "pull", "--arch", "arm64", self.reference_image], timeout=120)

    def arguments(self, command: str) -> list[str]:
        cli = self.inputs["devcontainerCandidate"]["executables"]["devcontainer"]
        arguments = ["/usr/bin/env", "DOCKER_HOST=unix://" + str(self.socket), cli, command,
                     "--workspace-folder", str(self.workspace), "--id-label", OWNER_LABEL + "=" + self.owner]
        return arguments + (["--log-format", "json"] if command == "up" else [])

    def cleanup(self):
        self.vm.prepare_cleanup()
        # ReleasedGuest owns the whole cleanup deadline, including attachment
        # closure. The reference wrapper would install a second alarm.
        self.remove_owned()
        self.vm.close()


class DevcontainerBuildCandidate(DevcontainerCandidate, DevcontainerBuildReference):
    """D02 keeps reference image/observation checks and native process ownership."""

    def __init__(self, commands, inputs, owner, *, before_build, observe=None):
        super().__init__(commands, inputs, owner, observe=observe)
        self.before_build = before_build

    def arguments(self, command):
        arguments = super().arguments(command)
        return arguments + (["--buildkit", "never"] if command == "up" else [])

    def execute(self):
        # Recheck the admitted builder and host DNS immediately before the CLI
        # can submit its build, rather than trusting setup-time ownership alone.
        self.before_build()
        return super().execute()


class DevcontainerUsersCandidate(DevcontainerBuildCandidate, DevcontainerUsersReference):
    """D03 uses native ownership/build lifetime and unchanged non-root observations."""


class DevcontainerLifecycleCandidate(DevcontainerCandidate, DevcontainerLifecycleReference):
    """D04 uses the same hook assertions with native image/process ownership."""


class DevcontainerFeaturesCandidate(DevcontainerBuildCandidate, DevcontainerFeaturesReference):
    """D05 uses the same locked Features and negative proof on the native engine."""

    negative_stderr_suffix = "-stderr.log"

    def arguments(self, command):
        # Candidate's facade bypasses Reference.arguments in this MRO.
        arguments = super().arguments(command)
        return arguments + (["--frozen-lockfile"] if command == "up" else [])


class DevcontainerReuseCandidate(DevcontainerCandidate, DevcontainerReuseReference):
    """Preserve D07 generations and volume ownership with native command lifetimes."""


class DevcontainerPortsCandidate(DevcontainerCandidate, DevcontainerPortsReference):
    """Preserve D06 observations and owned collision cleanup on native IDs."""

    def arguments(self, command):
        arguments = super().arguments(command)
        return arguments + (["--include-configuration"] if command == "up" else [])


class DevcontainerComposeCandidate(DevcontainerCandidate, DevcontainerComposeReference):
    """Keep original C01 observations and ownership without any Docker client."""

    def compose_executable(self):
        return self.inputs["composeCandidate"]["executables"]["compose"]

    def arguments(self, command):
        arguments = super().arguments(command)
        backend = "stock" if self.inputs["composeCandidate"]["runtimeProfile"] == "stock" else "container-compose"
        # HOME alone does not isolate Foundation's user-directory resolution.
        # Select every facade-owned state path and the admitted native frontend.
        values = {"COMPOSE_PROJECT_NAME": self.project, "DEVCONTAINER_COMPOSE_PROVIDER": "container-compose",
                  "DEVCONTAINER_COMPOSE_BIN": self.compose_executable(), "DEVCONTAINER_BACKEND": backend,
                  "DEVCONTAINER_SOCKET": str(self.socket), "DEVCONTAINER_CONTAINER_BIN": self.vm.container,
                  "DEVCONTAINER_CONFIG": str(self.vm.root / "config.toml"),
                  "DEVCONTAINER_STATE": str(self.vm.root / "state.sqlite")}
        arguments[2:2] = [key + "=" + value for key, value in values.items()]
        return arguments


class DevcontainerDependenciesCandidate(DevcontainerComposeCandidate, DevcontainerDependenciesReference):
    """C02 uses the same three-service contract with native command ownership."""

    def prepare_image(self):
        super().prepare_image()
        self.vm.command("devcontainer-dependency-pull", [self.vm.container, "image", "pull", "--arch", "arm64",
                                                       DATABASE_IMAGE], timeout=120)

    def execute(self):
        result = super().execute()
        # Inspection projects observed addresses, not the adopted attachment
        # specification. Retain only the network fields needed to distinguish
        # adoption/filtering failures, never the full environment-bearing spec.
        database = self.vm.root / "state.sqlite"
        try:
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
                rows = connection.execute(
                    "SELECT runtime_id, docker_id, json_extract(specification_json, '$.networks'), created_at, started_at "
                    "FROM runtime_containers WHERE docker_id=?", (self.identifier,)).fetchall()
            self.journal.put("c02-app-adopted-networks.json", canonical(rows))
        except sqlite3.Error as error:
            self.journal.put("c02-app-adopted-networks.json", canonical({"error": type(error).__name__}))
        return result
