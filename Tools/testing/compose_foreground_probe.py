"""Actual Compose CLI foreground, quiet and redirected-terminal contracts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import stat
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
PROJECT_DOWN_PROCESS = "guest-compose-project-down"
PROJECT_DOWN_TIMEOUT = 45
STDOUT = b"compose-stdout\n"
STDERR = b"compose-stderr\n"
USR1_OUTPUT = b"signal:USR1\n"
TERM_OUTPUT = b"signal:TERM\n"
SIGNAL_QUIET_WINDOW = 0.2
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
                 wrapper_selection=None, quiet=False, redirected=False, **kwargs):
        super().__init__(*args, command=COMMAND, **kwargs)
        self.root, self.executable, self.runtime = root, executable, runtime
        self.provider_install = provider_install
        self.wrapper_selection = wrapper_selection
        self.wrapper_environment = None
        self.quiet = quiet
        self.redirected = redirected
        self.child = OwnedProcess()
        self.command_attempted = False
        self.compose_config_path: Path | None = None
        self.compose_run_arguments: list[str] | None = None
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

    def observe_owned(self, value):
        """Record only identity sufficient to prove later auto-removal, not readiness."""
        config = value.get("Config") if isinstance(value, dict) else None
        labels = config.get("Labels") if isinstance(config, dict) else None
        identifier = value.get("Id") if isinstance(value, dict) else None
        if (not isinstance(identifier, str) or re.fullmatch(self.id_pattern, identifier) is None
                or value.get("Name") != "/" + self.name
                or not isinstance(labels, dict) or labels.get(OWNER_LABEL) != self.owner
                or value.get("Image") != self.image):
            raise ValueError("Compose foreground observation is not the exact owned guest")
        receipt = canonical({"id": identifier, "name": self.name, "owner": self.owner,
                             "image": self.image, "intentSHA256": digest(canonical(self.intent))})
        previous = self.journal.records().get("container-observed-owned.json")
        if previous is None:
            self.journal.put("container-observed-owned.json", receipt)
        elif previous != receipt:
            raise ValueError("Compose foreground observed guest identity changed")
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
        self._prepare_wrapper_environment()
        path = self.root / "compose-foreground.json"
        with path.open("xb") as output:
            output.write(canonical(configuration))
        path.chmod(0o600)
        arguments = [self.executable, "--project-name", self.project, "--file", str(path),
                     "run", "--rm", "--no-deps", "--pull", "never", "--name", self.name, "app"]
        if not self.redirected and not self.expected_tty:
            arguments.insert(-1, "-T")
        if self.quiet:
            arguments.insert(-1, "--quiet")
        self._record_compose_intent(arguments, path, configuration)
        return arguments

    def _prepare_wrapper_environment(self):
        """Create the private empty config and preserve the admitted native selection."""
        if self.wrapper_selection is None:
            return
        selected_provider = (self.wrapper_selection.get("DEVCONTAINER_COMPOSE_BIN")
                             or self.wrapper_selection.get("DEVCONTAINER_DOCKER_COMPOSE_BIN"))
        if not selected_provider or selected_provider == self.executable:
            raise ValueError("Native Compose wrapper and external provider must remain distinct")
        config_path = self.root / "devcontainer-config.toml"
        descriptor = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        config_path.chmod(0o600)
        self.wrapper_environment = {
            **self.wrapper_selection,
            "DEVCONTAINER_CONFIG": str(config_path),
        }

    def _record_compose_intent(self, arguments, path, configuration):
        """Bind teardown to the exact owner-scoped command and config executed."""
        self.compose_config_path = path
        self.compose_run_arguments = list(arguments)
        intent = {"arguments": arguments, "configurationSHA256": digest(canonical(configuration))}
        if self.wrapper_selection is not None:
            wrapper = self._require_private_wrapper()
            intent.update({"project": self.project,
                           "wrapperSHA256": digest(wrapper.read_bytes()),
                           "selectionSHA256": digest(canonical(self.wrapper_environment))})
        self.journal.put("container-intent.json", canonical(self.intent))
        self.journal.put(PROCESS + "-intent.json", canonical(intent))

    def snapshot(self, path):
        from guest_runtime import diagnostic_snapshot
        data, metadata = diagnostic_snapshot(path)
        if json.loads(metadata)["truncated"]:
            raise ValueError("Compose foreground output exceeded its diagnostic bound")
        return data

    def ready(self, end):
        observed_id = None
        while True:
            remaining(end)
            output = self.snapshot(self.output)
            remaining(end)
            if output == self.ready_output:
                actual = self.inspect(observed_id or self.name, total_timeout=remaining(end))
                if actual is None:
                    raise ValueError("Compose foreground output has no running guest")
                if observed_id is None:
                    self.observe_owned(actual)
                try:
                    identifier = self.owned(actual)
                except ValueError:
                    # Full inspect is private evidence only. Never project it
                    # into the public fixture result or a readiness receipt.
                    raw = canonical(actual)
                    if len(raw) <= 128 * 1024:
                        self.journal.put("compose-foreground-failed-inspect.json", raw)
                    else:
                        self.journal.put("compose-foreground-failed-inspect-metadata.json", canonical({
                            "size": len(raw), "sha256": digest(raw), "truncated": True}))
                    raise
                if observed_id is None:
                    observed_id = identifier
                    # Creation is an ownership fact even while the native
                    # start request is still committing its running state.
                    self.journal.put("container-created.json", canonical({"id": identifier}))
                elif identifier != observed_id:
                    raise ValueError("Compose foreground guest identity changed during startup")
                remaining(end)
                if self.child.process.poll() is not None:
                    raise ValueError("Compose CLI exited before its guest was running")
                status = actual.get("State", {}).get("Status")
                if status == "created":
                    time.sleep(min(remaining(end), 0.01))
                    continue
                if status != "running":
                    raise ValueError("Compose foreground guest left its created state before running")
                self.journal.put("compose-foreground-inspection.json", canonical({
                    "identity": {key: actual.get(key) for key in ("Id", "Name", "Image")},
                    "config": {key: actual.get("Config", {}).get(key) for key in ("Tty", "OpenStdin")},
                    "host": {key: actual.get("HostConfig", {}).get(key) for key in ("AutoRemove", "NetworkMode")}}))
                remaining(end)
                if self.child.process.poll() is not None:
                    raise ValueError("Compose CLI exited before its guest was running")
                self.identifier = identifier
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
                             runtime_socket=self.socket, provider_install=self.provider_install,
                             wrapper_environment=self.wrapper_environment)
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
        cleanup = super().cleanup(observed_stopped=PROCESS + "-stopped.json")
        if self.wrapper_selection is not None and self.command_attempted:
            self._release_compose_project()
        return cleanup

    def _require_private_wrapper(self) -> Path:
        """Require the unchanged canonical signed wrapper before each owned call."""
        wrapper = Path(self.executable)
        if (not wrapper.is_absolute() or wrapper.resolve(strict=True) != wrapper
                or wrapper.is_symlink() or not wrapper.is_file() or not os.access(wrapper, os.X_OK)):
            raise ValueError("Signed Compose wrapper is not a canonical executable")
        return wrapper

    def _project_down_inputs(self, records):
        """Revalidate the exact private config and selection before project teardown."""
        if self.wrapper_environment is None or self.runtime is None:
            raise ValueError("Native Compose project cleanup has no admitted wrapper environment")
        config_path = self.compose_config_path
        if config_path is None or self.compose_run_arguments is None:
            raise ValueError("Compose project cleanup has no captured fixture command")
        config_info = config_path.lstat()
        root_info = self.root.lstat()
        if (self.root.is_symlink() or self.root.resolve(strict=True) != self.root
                or not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.getuid()
                or root_info.st_mode & 0o777 != 0o700
                or config_path.is_symlink() or not stat.S_ISREG(config_info.st_mode)
                or config_info.st_uid != os.getuid() or config_info.st_nlink != 1
                or config_info.st_mode & 0o777 != 0o600 or config_info.st_size > 64 * 1024):
            raise ValueError("Compose project cleanup configuration is not a private owned file")
        descriptor = os.open(config_path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as configuration_file:
            opened_info = os.fstat(configuration_file.fileno())
            if (opened_info.st_dev, opened_info.st_ino) != (config_info.st_dev, config_info.st_ino):
                raise ValueError("Compose project cleanup configuration changed while opening")
            config_bytes = configuration_file.read(64 * 1024 + 1)
        if len(config_bytes) > 64 * 1024:
            raise ValueError("Compose project cleanup configuration exceeds its bound")
        config = json.loads(config_bytes)
        services = config.get("services") if isinstance(config, dict) else None
        app = services.get("app") if isinstance(services, dict) else None
        if (self.wrapper_environment.get("DEVCONTAINER_CONFIG") != str(
                    self.root / "devcontainer-config.toml")
                or not isinstance(services, dict)
                or set(services) not in ({"app"}, {"dependency", "app"})):
            raise ValueError("Compose project cleanup configuration is outside the owned fixture scope")
        for service in services.values():
            if (not isinstance(service, dict) or service.get("network_mode") != "none"
                    or "volumes" in service or service.get("labels") != self.intent["labels"]):
                raise ValueError("Compose project cleanup service is not owner-scoped and network-free")
        if set(services) == {"dependency", "app"}:
            dependency = services["dependency"]
            app = services["app"]
            if (not hasattr(self, "app_name") or dependency.get("image") != self.image
                    or dependency.get("container_name") != self.name
                    or dependency.get("command") != self.intent["command"]
                    or app.get("image") != self.missing_image or app.get("command") != ["true"]
                    or app.get("depends_on") != ["dependency"]):
                raise ValueError("Compose terminal cleanup differs from its admitted dependency fixture")
        elif not isinstance(app, dict) or app.get("image") != self.image:
            raise ValueError("Compose project cleanup image differs from its admitted fixture")
        original = json.loads(records[PROCESS + "-intent.json"])
        wrapper = self._require_private_wrapper()
        if (not isinstance(original, dict)
                or original.get("arguments") != self.compose_run_arguments
                or original.get("configurationSHA256") != digest(config_bytes)
                or original.get("project") != self.project
                or original.get("wrapperSHA256") != digest(wrapper.read_bytes())
                or original.get("selectionSHA256") != digest(canonical(self.wrapper_environment))):
            raise ValueError("Compose project cleanup inputs differ from the executed fixture")
        state = Path(self.wrapper_environment["DEVCONTAINER_STATE"])
        if not state.is_absolute() or state.resolve(strict=True) != state or state.is_symlink():
            raise ValueError("Compose project cleanup state is not the selected canonical database")
        self.runtime.verify()
        arguments = [str(wrapper), "--project-name", self.project, "--file", str(config_path), "down"]
        intent = {
            "arguments": arguments,
            "project": self.project,
            "wrapperSHA256": original["wrapperSHA256"],
            "configurationSHA256": original["configurationSHA256"],
            "selectionSHA256": original["selectionSHA256"],
            "timeoutSeconds": PROJECT_DOWN_TIMEOUT,
        }
        return arguments, state, intent

    def _project_claim_is_absent(self, state):
        """Check only this case's project claim in the already selected database."""
        with sqlite3.connect(state.as_uri() + "?mode=ro", uri=True) as database:
            count = database.execute(
                "SELECT COUNT(*) FROM projects WHERE compose_project = ?", (self.project,)
            ).fetchone()[0]
        return count == 0

    def _retain_project_down_logs(self, root):
        """Retain bounded stdout and stderr only after the child group is gone."""
        from guest_runtime import diagnostic_snapshot

        for suffix, path in (("", root / (PROJECT_DOWN_PROCESS + ".log")),
                             ("-stderr", root / (PROJECT_DOWN_PROCESS + "-stderr.log"))):
            if not path.exists():
                raise ValueError("Compose project cleanup output was not retained")
            payload, metadata = diagnostic_snapshot(path)
            self.journal.put(PROJECT_DOWN_PROCESS + suffix + ".log", payload)
            self.journal.put(PROJECT_DOWN_PROCESS + suffix + "-log.json", metadata)
            if json.loads(metadata)["truncated"]:
                raise ValueError("Compose project cleanup output exceeded its diagnostic bound")

    def _release_compose_project(self):
        """Release only this one-off Compose project's durable wrapper claim."""
        records = self.journal.records()
        if PROJECT_DOWN_PROCESS + "-intent.json" in records:
            raise ValueError("Compose project cleanup already attempted; reconcile its receipt")
        arguments, state, intent = self._project_down_inputs(records)
        self.journal.put(PROJECT_DOWN_PROCESS + "-intent.json", canonical(intent))
        child = OwnedProcess()
        stdout_path = self.root / (PROJECT_DOWN_PROCESS + ".log")
        stderr_path = self.root / (PROJECT_DOWN_PROCESS + "-stderr.log")
        started = time.monotonic_ns()
        exit_code = None
        stopped = False
        failure = None
        interrupted = None
        try:
            with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
                child.start(arguments, self.root, stdout, errors=stderr,
                            runtime_socket=self.socket, provider_install=self.provider_install,
                            wrapper_environment=self.wrapper_environment)
                self.journal.put(PROJECT_DOWN_PROCESS + "-process.json", canonical({
                    "pid": child.process.pid, "arguments": list(child.process.args)}))
                exit_code = child.process.wait(timeout=PROJECT_DOWN_TIMEOUT)
                self.journal.put(PROJECT_DOWN_PROCESS + "-exit.json", canonical({
                    "code": exit_code, "durationNS": time.monotonic_ns() - started}))
                if exit_code != 0:
                    failure = RuntimeError("signed Compose project cleanup exited unsuccessfully")
        except KeyboardInterrupt as error:
            interrupted = error
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, TimeoutError) as error:
            failure = error
        finally:
            try:
                child.stop()
                stopped = True
                self.journal.put(PROJECT_DOWN_PROCESS + "-stopped.json",
                                 canonical({"verifiedStopped": True}))
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, TimeoutError) as error:
                failure = failure or error

            logs_retained = False
            if stopped:
                try:
                    self._retain_project_down_logs(self.root)
                    logs_retained = True
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, TimeoutError) as error:
                    failure = failure or error

            claim_absent = False
            try:
                self.runtime.verify()
                claim_absent = self._project_claim_is_absent(state)
                if not claim_absent:
                    failure = failure or RuntimeError("signed Compose project cleanup left its project claim")
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError,
                    TimeoutError, sqlite3.Error, TypeError) as error:
                failure = failure or error

            receipt = {
                "schemaVersion": 1,
                "status": "passed" if failure is None and exit_code == 0 and stopped and claim_absent
                          else "uncertain",
                "project": self.project,
                "intentSHA256": digest(canonical(intent)),
                "exitCode": exit_code,
                "processStopped": stopped,
                "projectClaimAbsent": claim_absent,
                "logsRetained": logs_retained,
            }
            try:
                self.journal.put(PROJECT_DOWN_PROCESS + "-receipt.json", canonical(receipt))
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, TimeoutError) as error:
                failure = failure or error

        if interrupted is not None:
            raise interrupted
        if failure is not None or exit_code != 0 or not stopped or not claim_absent:
            raise RuntimeError(f"signed Compose project cleanup is uncertain: {failure or 'incomplete receipt'}")


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

    def require_signal_output(self, end):
        previous = None
        stable_since = None
        while True:
            output = self.snapshot(self.output)
            state = usr1_prefix_state(output)
            if state is None:
                raise ValueError("Compose CLI did not forward the signal to its guest")
            count, partial = state
            now = time.monotonic()
            if output != previous:
                previous = output
                stable_since = now
            if count > 0 and not partial and now - stable_since >= SIGNAL_QUIET_WINDOW:
                return
            if self.child.process.poll() is not None:
                raise ValueError("Compose CLI did not forward the signal to its guest")
            time.sleep(min(remaining(end), 0.01))

    def operation(self):
        arguments = self.prepare()
        started, end = time.monotonic_ns(), time.monotonic() + 45
        self.runtime.verify()
        with self.output.open("xb") as output, self.errors.open("xb") as errors:
            self.command_attempted = True
            self.child.start(arguments, self.root, output, errors=errors, stdin=subprocess.PIPE,
                             runtime_socket=self.socket, provider_install=self.provider_install,
                             wrapper_environment=self.wrapper_environment)
            self.journal.put(PROCESS + "-process.json", canonical(self.child.identity()))
            try:
                self.ready(end)
                self.send_signal("SIGUSR1", signal.SIGUSR1)
                self.require_signal_output(end)
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
        actual_output, actual_errors = self.snapshot(self.output), self.snapshot(self.errors)
        try:
            signal_stream_summary(actual_output)
        except ValueError:
            raise ValueError("Compose signal output differs from exact guest streams") from None
        if (actual_errors.count(STDERR) != 1 or STDOUT in actual_errors or b"signal:" in actual_errors):
            raise ValueError("Compose signal output differs from exact guest streams")
        ForegroundFixture.require_auto_removed(self)
        self.runtime.verify()
        return {key: "true" for key in ("usr1_forwarded", "guest_continues", "term_forwarded", "exact_exit", "auto_remove")}


def usr1_prefix_state(output: bytes) -> tuple[int, bool] | None:
    """Return complete USR1 lines and whether the last line is still arriving."""
    if not isinstance(output, bytes):
        return None
    if len(output) <= len(STDOUT):
        return (0, False) if STDOUT.startswith(output) else None
    if not output.startswith(STDOUT):
        return None
    tail = output[len(STDOUT):]
    count, _ = divmod(len(tail), len(USR1_OUTPUT))
    remainder = tail[count * len(USR1_OUTPUT):]
    if tail[:count * len(USR1_OUTPUT)] != USR1_OUTPUT * count or not USR1_OUTPUT.startswith(remainder):
        return None
    return count, bool(remainder)


def signal_stream_summary(stdout: bytes) -> dict:
    """Describe exact guest stdout only when it has the E13 signal grammar."""
    if not isinstance(stdout, bytes) or not stdout.startswith(STDOUT):
        raise ValueError("Compose signal stream has an invalid stdout prefix")
    tail = stdout[len(STDOUT):]
    if not tail.endswith(TERM_OUTPUT):
        raise ValueError("Compose signal stream must end with one TERM trap")
    usr1_bytes = tail[:-len(TERM_OUTPUT)]
    if (not usr1_bytes or len(usr1_bytes) % len(USR1_OUTPUT) != 0 or
            usr1_bytes != USR1_OUTPUT * (len(usr1_bytes) // len(USR1_OUTPUT))):
        raise ValueError("Compose signal stream must contain only ordered USR1 traps before TERM")
    usr1_count = len(usr1_bytes) // len(USR1_OUTPUT)
    signals = ["SIGUSR1"] * usr1_count + ["SIGTERM"]
    return {
        "stdoutSHA256": hashlib.sha256(stdout).hexdigest(),
        "signals": signals,
        "counts": {"SIGUSR1": usr1_count, "SIGTERM": 1},
    }


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
        self._prepare_wrapper_environment()
        configuration = {"services": {
            "dependency": {"image": self.image, "container_name": self.name, "network_mode": "none",
                           "command": self.intent["command"], "labels": self.intent["labels"]},
            "app": {"image": self.missing_image, "network_mode": "none", "command": ["true"],
                    "depends_on": ["dependency"], "labels": self.intent["labels"]}}}
        path = self.root / "compose-terminal-input.json"
        with path.open("xb") as output:
            output.write(canonical(configuration))
        path.chmod(0o600)
        arguments = [self.executable, "--project-name", self.project, "--file", str(path),
                     "run", "--rm", "--pull", "never", "--name", self.app_name, "--tty", "app"]
        self._record_compose_intent(arguments, path, configuration)
        return arguments

    def operation(self):
        arguments = self.prepare()
        started, end = time.monotonic_ns(), time.monotonic() + 45
        self.runtime.verify()
        with self.output.open("xb") as output, self.errors.open("xb") as errors:
            self.command_attempted = True
            self.child.start(arguments, self.root, output, errors=errors, stdin=subprocess.PIPE,
                             runtime_socket=self.socket, provider_install=self.provider_install,
                             wrapper_environment=self.wrapper_environment)
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
