"""D01 through the admitted private bundle and an exclusively owned Apple engine."""

from __future__ import annotations

import json
from pathlib import Path
import time

from case_evidence import canonical
from devcontainer_reference import DevcontainerReference, IMAGE
from guest_fixture import OWNER_LABEL
from guest_runtime import diagnostic_snapshot
from host_runtime import OwnedProcess


COMMANDS = ("devcontainer-image-pull", "devcontainer-up", "devcontainer-exec")


class CandidateCommands:
    """Keep CLI process completion and both diagnostic streams recoverable."""

    def __init__(self, root: Path, socket: Path, runtime, container: str):
        self.root, self.socket, self.runtime, self.container = root, socket, runtime, container
        self.journal = runtime.journal
        self.uncertain = False
        self.children = []
        self.pending_logs = {}

    def retain_logs(self):
        for name, paths in list(self.pending_logs.items()):
            if name + "-stopped.json" not in self.journal.records():
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
                    child.stop()
                    self.journal.put(name + "-stopped.json", canonical({"verifiedStopped": True}))
        finally:
            self.retain_logs()
        self.uncertain = False
        self.runtime.verify()
        records = self.journal.records()
        if any(json.loads(records[name + suffix + "-log.json"])["truncated"] for suffix, _ in paths):
            raise ValueError("D01 command output exceeded its bound")
        if code != 0:
            raise RuntimeError("D01 command failed; private diagnostic logs retained")
        return records[name + ".log"]

    def close(self):
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
        self.vm.command("devcontainer-image-pull", [self.vm.container, "image", "pull", "--arch", "arm64", IMAGE], timeout=120)

    def arguments(self, command: str) -> list[str]:
        cli = self.inputs["devcontainerCandidate"]["executables"]["devcontainer"]
        arguments = ["/usr/bin/env", "DOCKER_HOST=unix://" + str(self.socket), cli, command,
                     "--workspace-folder", str(self.workspace), "--id-label", OWNER_LABEL + "=" + self.owner]
        return arguments + (["--log-format", "json"] if command == "up" else [])

    def cleanup(self):
        self.vm.close()
        super().cleanup()
