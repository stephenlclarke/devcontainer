"""D01 through the admitted private bundle and an exclusively owned Apple engine."""

from __future__ import annotations

import json
from pathlib import Path
import time

from case_evidence import canonical
from devcontainer_reference import DevcontainerReference
from devcontainer_build_reference import DevcontainerBuildReference
from devcontainer_users_reference import DevcontainerUsersReference
from devcontainer_lifecycle_reference import DevcontainerLifecycleReference
from devcontainer_features_reference import DevcontainerFeaturesReference
from guest_fixture import OWNER_LABEL
from guest_runtime import diagnostic_snapshot
from host_runtime import OwnedProcess


COMMANDS = ("devcontainer-image-pull", "devcontainer-up", "devcontainer-exec", "devcontainer-frozen-lock")


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
                    if name == "devcontainer-up" and name + "-exit.json" in self.journal.records():
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
