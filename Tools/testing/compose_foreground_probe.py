"""Actual Compose CLI foreground, quiet and redirected-terminal contracts."""

import json
from pathlib import Path
import subprocess
import time

from case_evidence import canonical, digest
from exec_probe import remaining
from foreground_probe import ForegroundFixture
from guest_fixture import GuestFixture
from host_runtime import OwnedProcess


FIXTURE = "E09-compose-foreground"
QUIET_FIXTURE = "E10-compose-quiet"
REDIRECTED_FIXTURE = "E11-compose-redirected"
FIXTURES = {FIXTURE, QUIET_FIXTURE, REDIRECTED_FIXTURE}
PROCESS = "guest-compose-foreground"
STDOUT = b"compose-stdout\n"
STDERR = b"compose-stderr\n"
COMMAND = ("sh", "-c", "printf 'compose-stdout\\n'; printf 'compose-stderr\\n' >&2; "
           "IFS= read -r line || exit 19; printf 'seen:%s\\n' \"$line\"; exit 17")


class ComposeForegroundFixture(GuestFixture):
    """A network-free one-off job with piped stdin and independent output files.

    The guest waits for a token until its immutable ID has been recorded. On
    failure the owned CLI is stopped before resource reconciliation. An
    unobserved create is quarantined, never guessed successful from absence.
    """

    def __init__(self, *args, root: Path, executable: str, runtime, provider_install=None,
                 quiet=False, redirected=False, **kwargs):
        super().__init__(*args, command=COMMAND, **kwargs)
        self.root, self.executable, self.runtime = root, executable, runtime
        self.provider_install = provider_install
        self.quiet = quiet
        self.redirected = redirected
        self.child = OwnedProcess()
        self.command_attempted = False
        self.output = root / (PROCESS + ".log")
        self.errors = root / (PROCESS + "-stderr.log")
        self.project = "cf-e09-" + self.owner[:32]
        self.intent["composeForeground"] = {"project": self.project, "executable": executable}

    def owned(self, value):
        identifier = super().owned(value)
        config, host = value["Config"], value.get("HostConfig", {})
        labels = config["Labels"]
        if (config.get("Tty") is not False or config.get("OpenStdin") is not True or
                host.get("AutoRemove") is not True or host.get("NetworkMode") != "none" or
                labels.get("com.docker.compose.project") != self.project or
                labels.get("com.docker.compose.service") != "app"):
            raise ValueError("Compose foreground configuration changed")
        return identifier

    def prepare(self):
        if "container-intent.json" in self.journal.records():
            raise ValueError("Compose foreground already attempted")
        status, payload = self.call("GET", f"/images/{self.image}/json")
        if status != 200 or json.loads(payload).get("Id") != self.image or self.inspect(self.name) is not None:
            raise ValueError("Compose foreground requires a prepared image and unused name")
        self.root.mkdir(mode=0o700, exist_ok=True)
        configuration = {"services": {"app": {"image": self.image, "network_mode": "none",
                         "command": [part.replace("$", "$$") for part in COMMAND],
                         "labels": self.intent["labels"]}}}
        if self.redirected:
            # `run` selects the terminal independently of the service default.
            configuration["services"]["app"]["tty"] = True
        path = self.root / "compose-foreground.json"
        with path.open("xb") as output:
            output.write(canonical(configuration))
        arguments = [self.executable, "--project-name", self.project, "--file", str(path),
                     "run", "--rm", "--no-deps", "--pull", "never", "--name", self.name, "app"]
        if not self.redirected:
            arguments.insert(-1, "-T")
        if self.quiet:
            arguments.insert(-1, "--quiet")
        self.journal.put("container-intent.json", canonical(self.intent))
        self.journal.put(PROCESS + "-intent.json", canonical({"arguments": arguments,
                         "configurationSHA256": digest(canonical(configuration))}))
        return arguments

    def snapshot(self, path):
        from guest_runtime import diagnostic_snapshot
        data, metadata = diagnostic_snapshot(path)
        if json.loads(metadata)["truncated"]:
            raise ValueError("Compose foreground output exceeded its diagnostic bound")
        return data

    def ready(self, end):
        while True:
            output = self.snapshot(self.output)
            if output == STDOUT:
                actual = self.inspect(self.name)
                if actual is None or actual.get("State", {}).get("Status") != "running":
                    raise ValueError("Compose foreground output has no running guest")
                self.journal.put("compose-foreground-inspection.json", canonical({
                    "identity": {key: actual.get(key) for key in ("Id", "Name", "Image")},
                    "config": {key: actual.get("Config", {}).get(key) for key in ("Tty", "OpenStdin")},
                    "host": {key: actual.get("HostConfig", {}).get(key) for key in ("AutoRemove", "NetworkMode")}}))
                self.identifier = self.owned(actual)
                self.journal.put("container-created.json", canonical({"id": self.identifier}))
                return
            if not STDOUT.startswith(output) or self.child.process.poll() is not None:
                raise ValueError("Compose CLI exited or emitted unexpected foreground stdout")
            time.sleep(min(remaining(end), 0.01))

    def operation(self):
        arguments = self.prepare()
        started, end = time.monotonic_ns(), time.monotonic() + 45
        self.runtime.verify()
        with self.output.open("xb") as output, self.errors.open("xb") as errors:
            self.command_attempted = True
            self.child.start(arguments, self.root, output, errors=errors, stdin=subprocess.PIPE,
                             runtime_socket=self.socket, provider_install=self.provider_install)
            self.journal.put(PROCESS + "-process.json", canonical(self.child.identity()))
            try:
                self.ready(end)
                token = ("compose-input-" + self.owner[:16]).encode()
                self.child.process.stdin.write(token + b"\n")
                self.child.process.stdin.close()
                code = self.child.process.wait(timeout=remaining(end))
                self.journal.put(PROCESS + "-exit.json", canonical({"code": code,
                                 "durationNS": time.monotonic_ns() - started}))
                if code != 17:
                    raise ValueError("Compose CLI lost the guest exit status")
            finally:
                if self.child.process.stdin is not None:
                    self.child.process.stdin.close()
        actual_output, actual_errors = self.snapshot(self.output), self.snapshot(self.errors)
        # Compose may emit its own progress diagnostics on stderr. Guest bytes
        # must still be exact on stdout and occur only once on their own stream.
        if (actual_output != STDOUT + b"seen:" + token + b"\n" or actual_errors.count(STDERR) != 1 or
                STDOUT in actual_errors or b"seen:" + token in actual_errors):
            raise ValueError("Compose CLI changed or merged foreground streams")
        ForegroundFixture.require_auto_removed(self)
        self.runtime.verify()
        return {key: "true" for key in ("compose_cli", "stdin_roundtrip", "separate_streams", "exact_exit", "auto_remove")}

    def cleanup(self):
        records = self.journal.records()
        if (PROCESS + "-intent.json" in records and not self.command_attempted and
                records.get(PROCESS + "-stopped.json") != canonical({"verifiedStopped": True})):
            raise ValueError("Reopened Compose command needs recorded incarnation reconciliation")
        self.child.stop()
        if self.child.process is not None and self.child.process.stdin is not None:
            self.child.process.stdin.close()
        if PROCESS + "-intent.json" in records:
            self.journal.put(PROCESS + "-stopped.json", canonical({"verifiedStopped": True}))
            for suffix, path in (("", self.output), ("-stderr", self.errors)):
                if path.exists():
                    from guest_runtime import diagnostic_snapshot
                    payload, metadata = diagnostic_snapshot(path)
                    self.journal.put(PROCESS + suffix + ".log", payload)
                    self.journal.put(PROCESS + suffix + "-log.json", metadata)
        return super().cleanup()
