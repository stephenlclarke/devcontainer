"""Actual Compose CLI foreground, quiet and redirected-terminal contracts."""

import json
import os
from pathlib import Path
import signal
import subprocess
import time
from urllib.parse import quote

from case_evidence import canonical, digest
from exec_probe import remaining
from foreground_probe import ForegroundFixture
from guest_fixture import GuestFixture, OWNER_LABEL
from host_runtime import OwnedProcess


FIXTURE = "E09-compose-foreground"
QUIET_FIXTURE = "E10-compose-quiet"
REDIRECTED_FIXTURE = "E11-compose-redirected"
TTY_INPUT_FIXTURE = "E12-compose-tty-input"
SIGNAL_FIXTURE = "E13-compose-signals"
TERMINAL_SIZE_FIXTURE = "E14-compose-terminal-size"
FIXTURES = {FIXTURE, QUIET_FIXTURE, REDIRECTED_FIXTURE, TTY_INPUT_FIXTURE, SIGNAL_FIXTURE, TERMINAL_SIZE_FIXTURE}
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

    expected_tty = False
    ready_output = STDOUT

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
        if (config.get("Tty") is not self.expected_tty or config.get("OpenStdin") is not True or
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
                         "command": [part.replace("$", "$$") for part in self.intent["command"]],
                         "labels": self.intent["labels"]}}}
        if self.redirected:
            # `run` selects the terminal independently of the service default.
            configuration["services"]["app"]["tty"] = True
        path = self.root / "compose-foreground.json"
        with path.open("xb") as output:
            output.write(canonical(configuration))
        arguments = [self.executable, "--project-name", self.project, "--file", str(path),
                     "run", "--rm", "--no-deps", "--pull", "never", "--name", self.name, "app"]
        if not self.redirected and not self.expected_tty:
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
            if output == self.ready_output:
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
            if not self.ready_output.startswith(output) or self.child.process.poll() is not None:
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


class ComposeSignalFixture(ComposeForegroundFixture):
    """Host signals target only the admitted CLI; the guest must observe both."""

    command = ("sh", "-c", "trap 'printf \"signal:USR1\\n\"' USR1; "
               "trap 'printf \"signal:TERM\\n\"; exit 23' TERM; "
               "printf 'compose-stdout\\n'; printf 'compose-stderr\\n' >&2; "
               "while :; do IFS= read -r ignored; done")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.intent["command"] = list(self.command)
        self.intent["composeSignals"] = ["SIGUSR1", "SIGTERM"]

    def send_signal(self, name, number):
        expected = json.loads(self.journal.records()[PROCESS + "-process.json"])
        if self.child.identity() != expected:
            raise ValueError("Compose signal target incarnation changed")
        self.runtime.verify()
        self.journal.put(PROCESS + "-" + name.lower() + "-intent.json", canonical({"signal": name, "process": expected}))
        # The unreaped direct child cannot have its PID recycled. Do not signal
        # its group: the contract is CLI forwarding, not harness guest control.
        os.kill(self.child.process.pid, number)

    def require_signal_output(self, expected, end):
        while True:
            output = self.snapshot(self.output)
            if output == expected:
                return
            if not expected.startswith(output) or self.child.process.poll() is not None:
                raise ValueError("Compose CLI did not forward the signal to its guest")
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
                self.send_signal("SIGUSR1", signal.SIGUSR1)
                self.require_signal_output(STDOUT + b"signal:USR1\n", end)
                current = self.inspect(self.identifier)
                if (current is None or self.owned(current) != self.identifier or
                        current.get("State", {}).get("Status") != "running"):
                    raise ValueError("Signal forwarding replaced or stopped the guest")
                self.journal.put("compose-signal-continued.json", canonical({"id": self.identifier, "running": True}))
                self.send_signal("SIGTERM", signal.SIGTERM)
                code = self.child.process.wait(timeout=remaining(end))
                self.journal.put(PROCESS + "-exit.json", canonical({"code": code,
                                 "durationNS": time.monotonic_ns() - started}))
                if code != 23:
                    raise ValueError("Compose signal exit differs from the guest trap status")
            finally:
                self.child.process.stdin.close()
        expected = STDOUT + b"signal:USR1\nsignal:TERM\n"
        actual_output, actual_errors = self.snapshot(self.output), self.snapshot(self.errors)
        if (actual_output != expected or actual_errors.count(STDERR) != 1 or
                STDOUT in actual_errors or b"signal:" in actual_errors):
            raise ValueError("Compose signal output differs from exact guest streams")
        ForegroundFixture.require_auto_removed(self)
        self.runtime.verify()
        return {key: "true" for key in ("usr1_forwarded", "guest_continues", "term_forwarded", "exact_exit", "auto_remove")}


class ComposeTerminalInputFixture(ComposeForegroundFixture):
    """Reject piped interactive TTY input after dependencies, before job image work.

    The dependency is deliberately left running by Compose's failed command.
    Retain its identity before checking the error, then remove only that owned
    resource. An unexpected job or uncertain creation remains quarantined.
    """

    missing_image = "sha256:" + "f" * 64
    terminal_error = b"cannot attach stdin to a TTY-enabled container because stdin is not a terminal\n"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.app_name = self.name + "-job"
        self.intent["command"] = ["sleep", "300"]
        self.intent["composeTerminalInput"] = {"appName": self.app_name, "image": self.missing_image}

    def owned(self, value):
        identifier = GuestFixture.owned(self, value)
        config, host = value["Config"], value.get("HostConfig", {})
        labels = config["Labels"]
        if (config.get("Tty") is not False or config.get("OpenStdin") is not False or
                host.get("AutoRemove") is not False or host.get("NetworkMode") != "none" or
                labels.get("com.docker.compose.project") != self.project or
                labels.get("com.docker.compose.service") != "dependency"):
            raise ValueError("Compose terminal dependency configuration changed")
        return identifier

    def require_missing_image(self):
        status, payload = self.call("GET", f"/images/{self.missing_image}/json")
        value = json.loads(payload)
        if status != 404 or not isinstance(value, dict) or not isinstance(value.get("message"), str):
            raise ValueError("Compose terminal job image must remain absent")

    def prepare(self):
        if "container-intent.json" in self.journal.records():
            raise ValueError("Compose terminal input already attempted")
        status, payload = self.call("GET", f"/images/{self.image}/json")
        if status != 200 or json.loads(payload).get("Id") != self.image:
            raise ValueError("Compose terminal input requires a prepared dependency image")
        self.require_missing_image()
        if self.inspect(self.name) is not None or self.inspect(self.app_name) is not None:
            raise ValueError("Compose terminal input requires unused names")
        self.root.mkdir(mode=0o700, exist_ok=True)
        configuration = {"services": {
            "dependency": {"image": self.image, "container_name": self.name, "network_mode": "none",
                           "command": self.intent["command"], "labels": self.intent["labels"]},
            "app": {"image": self.missing_image, "network_mode": "none", "command": ["true"],
                    "depends_on": ["dependency"], "labels": self.intent["labels"]}}}
        path = self.root / "compose-terminal-input.json"
        with path.open("xb") as output:
            output.write(canonical(configuration))
        arguments = [self.executable, "--project-name", self.project, "--file", str(path),
                     "run", "--rm", "--pull", "never", "--name", self.app_name, "--tty", "app"]
        self.journal.put("container-intent.json", canonical(self.intent))
        self.journal.put(PROCESS + "-intent.json", canonical({"arguments": arguments,
                         "configurationSHA256": digest(canonical(configuration))}))
        return arguments

    def operation(self):
        arguments = self.prepare()
        started, end = time.monotonic_ns(), time.monotonic() + 45
        self.runtime.verify()
        with self.output.open("xb") as output, self.errors.open("xb") as errors:
            self.command_attempted = True
            self.child.start(arguments, self.root, output, errors=errors, stdin=subprocess.PIPE,
                             runtime_socket=self.socket, provider_install=self.provider_install)
            self.journal.put(PROCESS + "-process.json", canonical(self.child.identity()))
            self.child.process.stdin.close()
            code = self.child.process.wait(timeout=remaining(end))
        self.journal.put(PROCESS + "-exit.json", canonical({"code": code,
                         "durationNS": time.monotonic_ns() - started}))
        actual = self.inspect(self.name)
        if actual is None:
            raise ValueError("Compose terminal validation ran before dependency startup")
        self.identifier = GuestFixture.owned(self, actual)
        self.journal.put("container-created.json", canonical({"id": self.identifier}))
        self.journal.put("compose-terminal-dependency.json", canonical(actual))
        self.owned(actual)
        if actual.get("State", {}).get("Status") != "running":
            raise ValueError("Compose terminal dependency is not running")
        actual_output, actual_errors = self.snapshot(self.output), self.snapshot(self.errors)
        if code != 1:
            raise ValueError("Compose terminal validation lost the exact exit status")
        error_lines = actual_errors.splitlines(keepends=True)
        if (actual_output or not error_lines or error_lines[-1] != self.terminal_error or
                error_lines.count(self.terminal_error) != 1):
            raise ValueError("Compose terminal validation changed the error or output stream")
        if self.inspect(self.app_name) is not None:
            raise ValueError("Compose terminal validation created a one-off job")
        self.require_missing_image()
        filters = quote(canonical({"label": [OWNER_LABEL + "=" + self.owner]}).decode(), safe="")
        status, payload = self.call("GET", "/containers/json?all=true&filters=" + filters)
        inventory = json.loads(payload)
        if (status != 200 or not isinstance(inventory, list) or len(inventory) != 1 or
                not isinstance(inventory[0], dict) or inventory[0].get("Id") != self.identifier):
            raise ValueError("Compose terminal validation left unexpected owned resources")
        self.journal.put("compose-terminal-inventory.json", canonical(inventory))
        self.runtime.verify()
        return {key: "true" for key in ("dependency_started", "oneoff_absent", "tty_error",
                                        "exact_exit", "image_not_prepared")}
