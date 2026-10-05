"""Real piped child IO and Unix inspection, with a simulated Compose backend."""

import json
import hashlib
import os
import pty
import select
from pathlib import Path
import signal
import sqlite3
import subprocess
import time
import unittest
from unittest.mock import Mock, patch

from case_evidence import canonical, contract_observations
from compose_foreground_probe import (COMMAND, FIXTURE, QUIET_FIXTURE, REDIRECTED_FIXTURE,
                                      TTY_INPUT_FIXTURE, SIGNAL_FIXTURE, PROCESS, STDERR, STDOUT,
                                      PROJECT_DOWN_PROCESS, USR1_OUTPUT, TERM_OUTPUT, ComposeForegroundFixture,
                                      ComposeTerminalInputFixture, ComposeSignalFixture,
                                      signal_stream_summary)
from foreground_probe import ForegroundFixture
from guest_runtime import guest_diagnostic_plan, require_guest_cleanup, require_guest_commands_stopped
from host_runtime import OwnedProcess
import test_guest_fixture as helpers


class ComposeForegroundTests(unittest.TestCase):
    stop = helpers.GuestFixtureTests.stop

    def reopen(self):
        return ComposeForegroundFixture(self.socket, self.owner, self.server.image, "1.54", self.journal,
                                        root=self.root, executable="/owned/compose", runtime=Mock())

    def setUp(self):
        helpers.GuestFixtureTests.setUp(self)
        self.addCleanup(self.fixture.child.stop)

    def guest(self):
        return {"Id": "b" * 64, "Name": "/" + self.fixture.name, "Image": self.server.image,
                "Config": {"Image": self.server.image, "Cmd": list(COMMAND), "Tty": False, "OpenStdin": True,
                           "Labels": {**self.fixture.intent["labels"], "com.docker.compose.project": self.fixture.project,
                                      "com.docker.compose.service": "app"}},
                "HostConfig": {"AutoRemove": True, "NetworkMode": "none"}, "State": {"Status": "running"}}

    def ready_with(self, inspections, *, outputs=None, process=None, end=None):
        process = process or Mock()
        process.poll.return_value = None
        with patch.object(self.fixture, "snapshot", side_effect=outputs or [STDOUT] * len(inspections)), \
                patch.object(self.fixture, "inspect", side_effect=inspections) as inspect, \
                patch.object(self.fixture.child, "process", process):
            self.fixture.ready(end or time.monotonic() + 1)
        self.inspection_calls = list(inspect.call_args_list)
        return [call.args[0] for call in inspect.call_args_list]

    def test_ready_waits_for_same_owned_guest_to_be_running(self):
        created, running = self.guest(), self.guest()
        created["State"] = {"Status": "created"}
        inspected = self.ready_with([created, running])
        self.assertEqual(inspected, [self.fixture.name, "b" * 64])
        self.assertTrue(all(0 < call.kwargs["total_timeout"] <= 1 for call in self.inspection_calls))
        self.assertEqual(self.fixture.identifier, "b" * 64)
        self.assertEqual(json.loads(self.journal.records()["container-created.json"]), {"id": "b" * 64})
        self.assertIn("compose-foreground-inspection.json", self.journal.records())

    def test_ready_rejects_replacement_and_disappearance_after_observed_creation(self):
        created = self.guest()
        created["State"] = {"Status": "created"}
        replacement = self.guest()
        replacement["Id"] = "c" * 64
        for later, message in ((replacement, "identity changed"), (None, "no running guest")):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.ready_with([created, later])
            self.assertEqual(json.loads(self.journal.records()["container-created.json"]), {"id": "b" * 64})
            self.assertNotIn("compose-foreground-inspection.json", self.journal.records())

    def test_ready_does_not_journal_unowned_creation(self):
        foreign = self.guest()
        foreign["State"] = {"Status": "created"}
        foreign["Config"]["Labels"]["com.docker.compose.project"] = "other-project"
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            self.ready_with([foreign])
        self.assertNotIn("container-created.json", self.journal.records())

    def test_wrong_config_image_records_only_owned_observation_and_proves_auto_removal(self):
        self.fixture.prepare()
        self.fixture.command_attempted = True
        wrong = self.guest()
        wrong["Config"]["Image"] = "docker.io/library/alpine:3.22.5"
        with self.assertRaisesRegex(ValueError, "configImage"):
            self.ready_with([wrong])
        records = self.journal.records()
        self.assertEqual(json.loads(records["container-observed-owned.json"])["id"], "b" * 64)
        self.assertEqual(json.loads(records["compose-foreground-failed-inspect.json"]), wrong)
        self.assertNotIn("container-created.json", records)
        self.assertNotIn("compose-foreground-inspection.json", records)
        self.server.guest = None  # The stopped one-off was auto-removed.
        self.assertEqual(self.fixture.cleanup(), {"status": "passed", "remainingOwnedResources": []})
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))
        self.assertIn("container-removed.json", self.journal.records())

    def test_observed_wrong_config_live_guest_cannot_authorize_delete(self):
        self.fixture.prepare()
        self.fixture.command_attempted = True
        wrong = self.guest()
        wrong["Config"]["Image"] = "docker.io/library/alpine:3.22.5"
        with self.assertRaisesRegex(ValueError, "configImage"):
            self.ready_with([wrong])
        self.server.guest = wrong
        with self.assertRaisesRegex(ValueError, "configImage"):
            self.fixture.cleanup()
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))
        self.assertNotIn("container-removed.json", self.journal.records())

    def test_unowned_observation_cannot_prove_auto_removal(self):
        self.fixture.prepare()
        self.fixture.command_attempted = True
        foreign = self.guest()
        foreign["Config"]["Labels"]["devcontainer.parity.case"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "not the exact owned guest"):
            self.ready_with([foreign])
        self.assertNotIn("container-observed-owned.json", self.journal.records())
        with self.assertRaisesRegex(ValueError, "Uncertain creation"):
            self.fixture.cleanup()
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))

    def test_ready_rejects_cli_exit_and_unexpected_stdout_while_created(self):
        created = self.guest()
        created["State"] = {"Status": "created"}
        process = Mock()
        process.poll.side_effect = [None, 23]
        with self.assertRaisesRegex(ValueError, "CLI exited"):
            self.ready_with([created, created], process=process)
        with self.assertRaisesRegex(ValueError, "unexpected foreground"):
            self.ready_with([created], outputs=[STDOUT, STDOUT + b"unexpected\n"])

    def test_ready_requires_running_before_original_deadline(self):
        created = self.guest()
        created["State"] = {"Status": "created"}
        with patch("compose_foreground_probe.remaining", side_effect=[1, 1, 1, 1, TimeoutError("deadline")]), \
                patch("compose_foreground_probe.time.sleep"):
            with self.assertRaisesRegex(TimeoutError, "deadline"):
                self.ready_with([created, created])
        self.assertEqual(json.loads(self.journal.records()["container-created.json"]), {"id": "b" * 64})
        self.assertNotIn("compose-foreground-inspection.json", self.journal.records())

        running = self.guest()
        with patch("compose_foreground_probe.remaining", side_effect=[1, 1, 0.25, TimeoutError("deadline")]):
            with self.assertRaisesRegex(TimeoutError, "deadline"):
                self.ready_with([running])
        self.assertNotIn("compose-foreground-inspection.json", self.journal.records())

    def test_inspect_forwards_its_total_http_budget(self):
        with patch.object(self.fixture, "call", return_value=(200, canonical(self.guest()))) as call:
            self.fixture.inspect(self.fixture.name, total_timeout=0.25)
        call.assert_called_once_with("GET", f"/containers/{self.fixture.name}/json", total_timeout=0.25)
        self.assertNotIn("compose-foreground-inspection.json", self.journal.records())

    def test_final_inspection_journal_cannot_outlive_deadline_or_cli(self):
        running = self.guest()
        with patch("compose_foreground_probe.remaining",
                   side_effect=[1, 1, 1, 1, TimeoutError("deadline")]):
            with self.assertRaisesRegex(TimeoutError, "deadline"):
                self.ready_with([running])
        self.assertIn("compose-foreground-inspection.json", self.journal.records())
        self.assertIsNone(self.fixture.identifier)

        process = Mock()
        process.poll.side_effect = [None, 23]
        running = self.guest()
        with self.assertRaisesRegex(ValueError, "CLI exited"):
            self.ready_with([running], process=process)
        self.assertIsNone(self.fixture.identifier)

    def test_created_identity_remains_recoverable_after_readiness_failure(self):
        self.fixture.prepare()
        self.fixture.command_attempted = True
        created = self.guest()
        created["State"] = {"Status": "created"}
        self.server.guest = created
        with patch("compose_foreground_probe.remaining", side_effect=[1, 1, 1, 1, TimeoutError("deadline")]), \
                patch("compose_foreground_probe.time.sleep"):
            with self.assertRaisesRegex(TimeoutError, "deadline"):
                self.ready_with([created, created])
        self.assertEqual(json.loads(self.journal.records()["container-created.json"]), {"id": "b" * 64})
        self.assertEqual(self.fixture.cleanup()["remainingOwnedResources"], [])
        self.assertIsNone(self.server.guest)

    def run_cli(self, script=COMMAND[2]):
        original = self.fixture.child.start
        original_auto_remove = ForegroundFixture.require_auto_removed

        def start(arguments, root, output, **kwargs):
            self.cli_arguments = arguments
            self.server.guest = self.guest()
            original(["/bin/sh", "-c", script], root, output, **kwargs)

        def auto_remove(fixture):
            self.assertEqual(fixture.child.process.returncode, 17)
            self.server.guest = None
            original_auto_remove(fixture)

        with patch.object(self.fixture.child, "start", side_effect=start), \
                patch.object(ForegroundFixture, "require_auto_removed", auto_remove):
            return self.fixture.operation()

    def configure_native_stock_wrapper(self):
        provider_root = self.root / "provider"
        binary_dir = provider_root / "bin"
        binary_dir.mkdir(mode=0o700, parents=True)
        container = binary_dir / "container"
        wrapper = self.root / "devcontainer-compose-wrapper"
        docker = self.root / "docker"
        docker_compose = self.root / "docker-compose"
        for executable in (container, wrapper, docker, docker_compose):
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
        state = self.root / "selected runtime state.sqlite"
        with sqlite3.connect(state) as database:
            database.execute("CREATE TABLE projects (compose_project TEXT NOT NULL)")
            database.execute("INSERT INTO projects VALUES (?)", (self.fixture.project,))
        state.chmod(0o600)
        self.fixture.provider_install = provider_root
        self.fixture.executable = str(wrapper)
        self.fixture.wrapper_selection = {
            "DEVCONTAINER_BACKEND": "stock",
            "DEVCONTAINER_COMPOSE_PROVIDER": "docker",
            "DEVCONTAINER_CONTAINER_BIN": str(container),
            "DEVCONTAINER_DOCKER_BIN": str(docker),
            "DEVCONTAINER_DOCKER_COMPOSE_BIN": str(docker_compose),
            "DEVCONTAINER_STATE": str(state),
            "DEVCONTAINER_SOCKET": str(self.socket),
        }
        return state, wrapper, docker, docker_compose

    class FakeOwnedDownProcess:
        def __init__(self, state, *, exit_code=0, wait_error=None):
            self.state = state
            self.exit_code = exit_code
            self.wait_error = wait_error
            self.process = None
            self.arguments = None
            self.environment = None
            self.stopped = False

        def start(self, arguments, _root, output, *, errors, wrapper_environment, **_kwargs):
            self.arguments = arguments
            self.environment = dict(wrapper_environment)
            output.write(b"down stdout\n")
            errors.write(b"down stderr\n")
            self.process = Mock()
            self.process.args = arguments
            self.process.pid = 12345
            self.process.wait.side_effect = self.wait

        def wait(self, *, timeout):
            if self.wait_error is not None:
                raise self.wait_error
            if self.exit_code == 0:
                with sqlite3.connect(self.state) as database:
                    database.execute("DELETE FROM projects")
            return self.exit_code

        def stop(self):
            self.stopped = True

    def test_native_wrapper_project_claim_is_released_after_guest_cleanup(self):
        state, wrapper, docker, docker_compose = self.configure_native_stock_wrapper()
        self.run_cli()
        child = self.FakeOwnedDownProcess(state)
        with patch("compose_foreground_probe.OwnedProcess", return_value=child):
            cleanup = self.fixture.cleanup()

        expected = [str(wrapper), "--project-name", self.fixture.project, "--file",
                    str(self.root / "compose-foreground.json"), "down"]
        self.assertEqual(child.arguments, expected)
        self.assertEqual(child.environment["DEVCONTAINER_DOCKER_BIN"], str(docker))
        self.assertEqual(child.environment["DEVCONTAINER_DOCKER_COMPOSE_BIN"], str(docker_compose))
        self.assertEqual(child.environment["DEVCONTAINER_CONFIG"], str(self.root / "devcontainer-config.toml"))
        self.assertTrue(child.stopped)
        self.assertEqual(cleanup["status"], "passed")
        records = self.journal.records()
        receipt = json.loads(records[PROJECT_DOWN_PROCESS + "-receipt.json"])
        self.assertEqual(receipt["status"], "passed")
        self.assertTrue(receipt["projectClaimAbsent"])
        self.assertTrue(receipt["logsRetained"])
        self.assertEqual(require_guest_commands_stopped(records), [PROCESS, PROJECT_DOWN_PROCESS])
        require_guest_cleanup(records)

    def test_native_project_cleanup_refuses_to_run_when_guest_cleanup_is_uncertain(self):
        state, *_ = self.configure_native_stock_wrapper()
        self.run_cli()
        with patch("compose_foreground_probe.GuestFixture.cleanup", side_effect=ValueError("uncertain guest")), \
                patch("compose_foreground_probe.OwnedProcess") as launch:
            with self.assertRaisesRegex(ValueError, "uncertain guest"):
                self.fixture.cleanup()
        launch.assert_not_called()
        records = self.journal.records()
        self.assertNotIn(PROJECT_DOWN_PROCESS + "-intent.json", records)
        self.assertFalse(self.fixture._project_claim_is_absent(state))

    def test_nonzero_project_down_keeps_claim_and_uncertain_receipt(self):
        state, *_ = self.configure_native_stock_wrapper()
        self.run_cli()
        child = self.FakeOwnedDownProcess(state, exit_code=7)
        with patch("compose_foreground_probe.OwnedProcess", return_value=child):
            with self.assertRaisesRegex(RuntimeError, "project cleanup is uncertain"):
                self.fixture.cleanup()
        self.assertTrue(child.stopped)
        records = self.journal.records()
        receipt = json.loads(records[PROJECT_DOWN_PROCESS + "-receipt.json"])
        self.assertEqual(receipt["status"], "uncertain")
        self.assertEqual(receipt["exitCode"], 7)
        self.assertFalse(receipt["projectClaimAbsent"])
        self.assertIn(PROJECT_DOWN_PROCESS + ".log", records)
        self.assertFalse(self.fixture._project_claim_is_absent(state))

    def test_timed_out_project_down_stops_owned_child_and_retains_claim(self):
        state, *_ = self.configure_native_stock_wrapper()
        self.run_cli()
        child = self.FakeOwnedDownProcess(
            state, wait_error=subprocess.TimeoutExpired("compose down", 45))
        with patch("compose_foreground_probe.OwnedProcess", return_value=child):
            with self.assertRaisesRegex(RuntimeError, "project cleanup is uncertain"):
                self.fixture.cleanup()
        self.assertTrue(child.stopped)
        receipt = json.loads(self.journal.records()[PROJECT_DOWN_PROCESS + "-receipt.json"])
        self.assertEqual(receipt["status"], "uncertain")
        self.assertTrue(receipt["processStopped"])
        self.assertFalse(receipt["projectClaimAbsent"])
        self.assertFalse(self.fixture._project_claim_is_absent(state))

    def test_native_terminal_input_records_its_own_config_and_selection(self):
        state, wrapper, docker, docker_compose = self.configure_native_stock_wrapper()
        terminal_root = self.root / "terminal"
        terminal = ComposeTerminalInputFixture(
            self.socket, self.owner, self.server.image, "1.54", self.journal,
            root=terminal_root, executable=str(wrapper), runtime=Mock(),
            provider_install=self.fixture.provider_install,
            wrapper_selection=self.fixture.wrapper_selection,
        )
        terminal.require_missing_image = lambda: None
        arguments = terminal.prepare()
        config_path = terminal_root / "compose-terminal-input.json"
        self.assertEqual(arguments[:5], [str(wrapper), "--project-name", terminal.project,
                                         "--file", str(config_path)])
        config = json.loads(config_path.read_bytes())
        self.assertEqual(set(config["services"]), {"dependency", "app"})
        self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(terminal.wrapper_environment["DEVCONTAINER_CONFIG"],
                         str(terminal_root / "devcontainer-config.toml"))
        self.assertEqual(terminal.wrapper_environment["DEVCONTAINER_BACKEND"], "stock")
        self.assertEqual(terminal.wrapper_environment["DEVCONTAINER_COMPOSE_PROVIDER"], "docker")
        self.assertEqual(terminal.wrapper_environment["DEVCONTAINER_DOCKER_BIN"], str(docker))
        self.assertEqual(terminal.wrapper_environment["DEVCONTAINER_DOCKER_COMPOSE_BIN"],
                         str(docker_compose))
        inputs, recorded_state, _ = terminal._project_down_inputs(self.journal.records())
        self.assertEqual(inputs, [str(wrapper), "--project-name", terminal.project,
                                  "--file", str(config_path), "down"])
        self.assertEqual(recorded_state, state)

    def test_real_child_stdin_output_exit_and_cleanup(self):
        observed = self.run_cli()
        contract = json.loads((Path(__file__).parents[2] / "Tests/Parity/fixtures" / FIXTURE / "contract.json").read_text())
        self.assertEqual(observed, contract_observations(contract["expected"]))
        self.assertEqual(self.cli_arguments[0], "/owned/compose")
        self.assertIn("--rm", self.cli_arguments)
        self.assertNotIn("--quiet", self.cli_arguments)
        self.assertEqual(self.cli_arguments[-1], "app")
        self.assertEqual(self.fixture.cleanup()["remainingOwnedResources"], [])
        self.assertEqual(require_guest_commands_stopped(self.journal.records()), [PROCESS])
        self.assertEqual(json.loads(self.journal.records()[PROCESS + "-exit.json"])["code"], 17)
        self.assertIn(STDERR, self.journal.records()[PROCESS + "-stderr.log"])
        self.assertNotIn(PROJECT_DOWN_PROCESS + "-intent.json", self.journal.records())

    def test_guest_variable_is_escaped_only_in_compose_source(self):
        self.fixture.prepare()
        config = json.loads((self.root / "compose-foreground.json").read_text())
        self.assertIn("$$line", config["services"]["app"]["command"][2])
        self.assertIn('"$line"', self.fixture.intent["command"][2])
        with self.assertRaisesRegex(ValueError, "already attempted"):
            self.fixture.prepare()

    def test_quiet_argument_is_executed_and_journalled_without_losing_guest_io(self):
        self.fixture.quiet = True
        observed = self.run_cli()
        contract = json.loads((Path(__file__).parents[2] / "Tests/Parity/fixtures" / QUIET_FIXTURE / "contract.json").read_text())
        self.assertEqual(observed, contract_observations(contract["expected"]))
        self.assertEqual(self.cli_arguments[-2:], ["--quiet", "app"])
        self.assertEqual(json.loads(self.journal.records()[PROCESS + "-intent.json"])["arguments"], self.cli_arguments)
        self.assertEqual(self.fixture.cleanup()["remainingOwnedResources"], [])

    def test_redirected_cli_must_override_service_tty_without_explicit_flag(self):
        self.fixture.redirected = True
        observed = self.run_cli()
        contract = json.loads((Path(__file__).parents[2] / "Tests/Parity/fixtures" / REDIRECTED_FIXTURE / "contract.json").read_text())
        self.assertEqual(observed, contract_observations(contract["expected"]))
        config = json.loads((self.root / "compose-foreground.json").read_text())
        self.assertIs(config["services"]["app"]["tty"], True)
        self.assertNotIn("-T", self.cli_arguments)
        self.assertNotIn("--no-tty", self.cli_arguments)
        self.assertNotIn("--interactive", self.cli_arguments)
        self.assertEqual(json.loads(self.journal.records()[PROCESS + "-intent.json"])["arguments"], self.cli_arguments)
        self.assertEqual(self.fixture.cleanup()["remainingOwnedResources"], [])

    def test_wrong_exit_is_not_replaced_with_success(self):
        changed_command = COMMAND[2].replace("exit 17", "exit 0")
        with self.assertRaisesRegex(ValueError, "lost the guest exit"):
            self.run_cli(changed_command)
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_stdout_corruption_and_stderr_omission_fail(self):
        changed_command = COMMAND[2].replace("printf 'compose-stderr\\n' >&2", ":")
        with self.assertRaisesRegex(ValueError, "changed or merged"):
            self.run_cli(changed_command)
        self.fixture.cleanup()

    def test_guest_bytes_on_wrong_stream_fail(self):
        changed_command = COMMAND[2].replace("exit 17", "printf 'compose-stdout\\n' >&2; exit 17")
        with self.assertRaisesRegex(ValueError, "changed or merged"):
            self.run_cli(changed_command)
        self.fixture.cleanup()

    def test_unexpected_early_stdout_fails_and_exact_owned_guest_is_recovered(self):
        with self.assertRaisesRegex(ValueError, "unexpected foreground"):
            self.run_cli("printf 'unexpected\\n'; sleep 30")
        self.assertEqual(self.fixture.cleanup()["status"], "passed")
        self.assertIsNone(self.server.guest)

    def test_foreign_project_and_changed_settings_refuse_mutation(self):
        for field, value in (("Tty", True), ("OpenStdin", False)):
            guest = self.guest()
            guest["Config"][field] = value
            with self.assertRaisesRegex(ValueError, "configuration"):
                self.fixture.owned(guest)
        for mutation in ({"AutoRemove": False}, {"NetworkMode": "bridge"}):
            guest = self.guest()
            guest["HostConfig"].update(mutation)
            with self.assertRaisesRegex(ValueError, "configuration"):
                self.fixture.owned(guest)
        guest = self.guest()
        guest["Config"]["Labels"]["com.docker.compose.project"] = "foreign"
        with self.assertRaisesRegex(ValueError, "configuration"):
            self.fixture.owned(guest)

    def test_missing_image_never_starts_or_records_creation(self):
        self.server.prepared = False
        with self.assertRaisesRegex(ValueError, "prepared image"):
            self.fixture.operation()
        self.assertIsNone(self.fixture.child.process)
        self.assertNotIn("container-intent.json", self.journal.records())
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_missing_process_closure_quarantines_recovery(self):
        record_json = {PROCESS + "-intent.json": canonical({})}
        with self.assertRaisesRegex(ValueError, "explicit reconciliation"):
            require_guest_commands_stopped(record_json)

    def test_reopened_fixture_cannot_claim_an_existing_child_stopped(self):
        self.fixture.prepare()
        with self.fixture.output.open("xb") as output:
            self.fixture.child.start(["/bin/sleep", "30"], self.root, output)
        self.journal.put(PROCESS + "-process.json", canonical(self.fixture.child.identity()))
        self.server.guest = self.guest()
        fixture = self.reopen()
        with self.assertRaisesRegex(ValueError, "incarnation reconciliation"):
            fixture.cleanup()
        self.assertIsNone(self.fixture.child.process.poll())
        self.assertNotIn(PROCESS + "-stopped.json", self.journal.records())
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))

    def test_partial_diagnostic_recovery_preserves_both_streams(self):
        self.run_cli()
        self.fixture.cleanup()
        records = self.journal.records()
        for name in (PROCESS + "-log.json", PROCESS + "-stderr.log", PROCESS + "-stderr-log.json"):
            del records[name]
        with self.assertRaisesRegex(ValueError, "diagnostics must be retained"):
            require_guest_cleanup(records)
        plan = guest_diagnostic_plan(self.root, records)
        self.assertEqual(plan[PROCESS + "-stderr.log"], STDERR)
        require_guest_cleanup({**records, **plan})
        self.assertEqual(guest_diagnostic_plan(self.root, {**records, **plan}), {})

    def test_socket_environment_is_explicit_and_stdin_default_stays_closed(self):
        process = OwnedProcess()
        with patch("host_runtime.subprocess.Popen") as launch:
            process.start(["/owned/compose"], self.root, Mock(), runtime_socket=self.socket,
                          provider_install=self.root)
        environment = launch.call_args.kwargs["env"]
        self.assertEqual(environment["DOCKER_HOST"], "unix://" + str(self.socket))
        self.assertEqual(environment["CONTAINER_COMPOSE_ENGINE_SOCKET"], str(self.socket))
        self.assertEqual(environment["CONTAINER_COMPOSE_CONTAINER"], str(self.root / "bin/container"))
        self.assertEqual(environment["CONTAINER_BIN"], str(self.root / "bin/container"))
        self.assertNotIn("DEVCONTAINER_CONFIG", environment)
        self.assertNotIn("DEVCONTAINER_COMPOSE_PROVIDER", environment)
        process = OwnedProcess()
        mock_value = Mock()
        socket_path = Path("relative")
        with self.assertRaisesRegex(ValueError, "canonical"):
            process.start(["/owned/compose"], self.root, mock_value, runtime_socket=socket_path)

    def test_native_cli_gets_empty_private_config_and_only_admitted_lane_paths(self):
        provider_root = self.root / "provider"
        binary_dir = provider_root / "bin"
        binary_dir.mkdir(parents=True, mode=0o700)
        container = binary_dir / "container"
        wrapper = self.root / "devcontainer-compose-wrapper"
        provider = self.root / "container-compose-provider"
        for executable in (container, wrapper, provider):
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
        state = self.root / "state.sqlite"
        state.touch(mode=0o600)
        self.fixture.provider_install = provider_root
        self.fixture.executable = str(wrapper)
        self.fixture.wrapper_selection = {
            "DEVCONTAINER_BACKEND": "container-compose",
            "DEVCONTAINER_COMPOSE_PROVIDER": "container-compose",
            "DEVCONTAINER_COMPOSE_BIN": str(provider),
            "DEVCONTAINER_CONTAINER_BIN": str(container),
            "DEVCONTAINER_STATE": str(state),
            "DEVCONTAINER_SOCKET": str(self.socket),
        }
        arguments = self.fixture.prepare()
        config = self.root / "devcontainer-config.toml"
        self.assertEqual(config.read_bytes(), b"")
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(arguments[0], str(wrapper))
        self.assertNotEqual(arguments[0], self.fixture.wrapper_selection["DEVCONTAINER_COMPOSE_BIN"])

        process = OwnedProcess()
        with patch.dict("os.environ", {
            "DEVCONTAINER_BACKEND": "stock",
            "DEVCONTAINER_CONFIG": "/operator/config.toml",
        }), patch("host_runtime.subprocess.Popen") as spawn:
            process.start(arguments, self.root, Mock(), stdin=subprocess.PIPE,
                          runtime_socket=self.socket, provider_install=provider_root,
                          wrapper_environment=self.fixture.wrapper_environment)
        environment = spawn.call_args.kwargs["env"]
        for key, value in self.fixture.wrapper_environment.items():
            self.assertEqual(environment[key], value)
        self.assertNotEqual(environment["DEVCONTAINER_CONFIG"], "/operator/config.toml")
        self.assertEqual(environment["DEVCONTAINER_BACKEND"], "container-compose")

    def test_stock_native_cli_selects_docker_compose_and_pinned_docker_cli(self):
        provider_root = self.root / "provider"
        binary_dir = provider_root / "bin"
        binary_dir.mkdir(parents=True, mode=0o700)
        container = binary_dir / "container"
        wrapper = self.root / "devcontainer-compose-wrapper"
        docker = self.root / "docker"
        docker_compose = self.root / "docker-compose"
        for executable in (container, wrapper, docker, docker_compose):
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
        state = self.root / "state.sqlite"
        state.touch(mode=0o600)
        self.fixture.provider_install = provider_root
        self.fixture.executable = str(wrapper)
        self.fixture.wrapper_selection = {
            "DEVCONTAINER_BACKEND": "stock",
            "DEVCONTAINER_COMPOSE_PROVIDER": "docker",
            "DEVCONTAINER_CONTAINER_BIN": str(container),
            "DEVCONTAINER_DOCKER_BIN": str(docker),
            "DEVCONTAINER_DOCKER_COMPOSE_BIN": str(docker_compose),
            "DEVCONTAINER_STATE": str(state),
            "DEVCONTAINER_SOCKET": str(self.socket),
        }
        arguments = self.fixture.prepare()
        config = self.root / "devcontainer-config.toml"
        self.assertEqual(config.read_bytes(), b"")
        self.assertEqual(arguments[0], str(wrapper))
        self.assertNotIn("DEVCONTAINER_COMPOSE_BIN", self.fixture.wrapper_environment)
        self.assertEqual(self.fixture.wrapper_environment["DEVCONTAINER_COMPOSE_PROVIDER"], "docker")

        process = OwnedProcess()
        with patch("host_runtime.subprocess.Popen") as spawn:
            process.start(arguments, self.root, Mock(), stdin=subprocess.PIPE,
                          runtime_socket=self.socket, provider_install=provider_root,
                          wrapper_environment=self.fixture.wrapper_environment)
        environment = spawn.call_args.kwargs["env"]
        self.assertEqual(environment["DEVCONTAINER_DOCKER_BIN"], str(docker))
        self.assertEqual(environment["DEVCONTAINER_DOCKER_COMPOSE_BIN"], str(docker_compose))
        self.assertEqual(environment["DEVCONTAINER_BACKEND"], "stock")


class ComposeSignalTests(unittest.TestCase):
    stop = helpers.GuestFixtureTests.stop
    setUp = ComposeForegroundTests.setUp

    def reopen(self):
        return ComposeSignalFixture(self.socket, self.owner, self.server.image, "1.54", self.journal,
                                    root=self.root, executable="/owned/compose", runtime=Mock())

    def guest(self):
        value = ComposeForegroundTests.guest(self)
        value["Config"]["Cmd"] = list(self.fixture.command)
        value["Config"]["Tty"] = True
        value["Config"]["OpenStdin"] = False
        return value

    def test_q_inspect_split_is_owned_only_for_exact_signal_argv(self):
        value = self.guest()
        value["Config"]["Entrypoint"] = ["sh"]
        value["Config"]["Cmd"] = list(self.fixture.command[1:])
        self.assertEqual(self.fixture.owned(value), value["Id"])
        value["Config"]["Cmd"] = ["-c", "other script"]
        with self.assertRaises(ValueError):
            self.fixture.owned(value)

    def run_cli(self, script=None):
        original = self.fixture.child.start
        original_auto_remove = ForegroundFixture.require_auto_removed

        def start(arguments, root, output, **kwargs):
            self.server.guest = self.guest()
            command = self.fixture.command[2] if script is None else script
            command = command.replace("stty -onlcr -echo <&1 || exit 24; ", "", 1)
            original(["/bin/sh", "-c", command], root, output, **kwargs)

        def auto_remove(fixture):
            self.assertEqual(fixture.child.process.returncode, 23)
            self.server.guest = None
            original_auto_remove(fixture)

        with patch.object(self.fixture.child, "start", side_effect=start), \
                patch.object(ForegroundFixture, "require_auto_removed", auto_remove):
            return self.fixture.operation()

    def test_real_signals_output_exit_and_owned_cleanup(self):
        observed = self.run_cli()
        contract = json.loads((Path(__file__).parents[2] / "Tests/Parity/fixtures" / SIGNAL_FIXTURE / "contract.json").read_text())
        self.assertEqual(observed, contract_observations(contract["expected"]))
        self.assertEqual(self.fixture.cleanup()["remainingOwnedResources"], [])
        records = self.journal.records()
        self.assertEqual(json.loads(records[PROCESS + "-exit.json"])["code"], 23)
        for name in ("SIGUSR1", "SIGTERM"):
            self.assertEqual(json.loads(records[PROCESS + "-" + name.lower() + "-intent.json"])["signal"], name)
        self.assertEqual(require_guest_commands_stopped(records), [PROCESS])
        self.assertEqual(records[PROCESS + ".log"], STDOUT + b"signal:USR1\nsignal:TERM\n")
        self.assertEqual(signal_stream_summary(records[PROCESS + ".log"]), {
            "stdoutSHA256": hashlib.sha256(STDOUT + USR1_OUTPUT + TERM_OUTPUT).hexdigest(),
            "signals": ["SIGUSR1", "SIGTERM"],
            "counts": {"SIGUSR1": 1, "SIGTERM": 1},
        })

    def test_duplicate_usr1_bytes_fail_exact_tty_contract(self):
        script = self.fixture.command[2].replace(
            "trap 'printf \"signal:USR1\\n\"' USR1",
            "trap 'printf \"signal:USR1\\n\"; printf \"signal:USR1\\n\"' USR1",
        )
        with self.assertRaisesRegex(ValueError, "exact guest streams"):
            self.run_cli(script)
        output = self.fixture.snapshot(self.fixture.output)
        self.assertEqual(output, STDOUT + USR1_OUTPUT * 2 + TERM_OUTPUT)
        with self.assertRaises(ValueError):
            signal_stream_summary(output)
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_signal_stream_rejects_missing_extra_or_reordered_bytes(self):
        for output in (
            STDOUT + TERM_OUTPUT,
            STDOUT + USR1_OUTPUT,
            STDOUT + TERM_OUTPUT + USR1_OUTPUT,
            STDOUT + USR1_OUTPUT * 2 + TERM_OUTPUT,
            STDOUT + USR1_OUTPUT + TERM_OUTPUT + TERM_OUTPUT,
            STDOUT + USR1_OUTPUT + b"noise\n" + TERM_OUTPUT,
        ):
            with self.subTest(output=output), self.assertRaises(ValueError):
                signal_stream_summary(output)

    def test_configuration_contains_exact_traps_not_original_stdin_fixture(self):
        arguments = self.fixture.prepare()
        configuration = json.loads((self.root / "compose-foreground.json").read_text())
        self.assertEqual(configuration["services"]["app"]["command"], list(self.fixture.command))
        self.assertTrue(configuration["services"]["app"]["tty"])
        self.assertEqual(arguments[-3:-1], ["--no-tty=false", "--interactive=false"])
        self.assertNotIn("-T", arguments)
        self.assertEqual(self.guest()["Config"]["OpenStdin"], False)

    def test_real_pty_has_exact_lf_and_single_signal_traps(self):
        master, slave = pty.openpty()
        process = None
        output = bytearray()

        def until(expected):
            end = time.monotonic() + 5
            while expected not in output:
                timeout = end - time.monotonic()
                if timeout <= 0:
                    self.fail("owned PTY guest did not emit its signal marker")
                if select.select([master], [], [], timeout)[0]:
                    output.extend(os.read(master, 4096))

        try:
            process = subprocess.Popen(list(self.fixture.command), stdin=subprocess.DEVNULL, stdout=slave,
                                       stderr=slave, start_new_session=True)
            os.close(slave)
            slave = -1
            until(STDOUT)
            os.kill(process.pid, signal.SIGUSR1)
            until(USR1_OUTPUT)
            os.kill(process.pid, signal.SIGTERM)
            until(TERM_OUTPUT)
            self.assertEqual(process.wait(timeout=5), 23)
            self.assertEqual(bytes(output), STDOUT + USR1_OUTPUT + TERM_OUTPUT)
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            if slave >= 0:
                os.close(slave)
            os.close(master)

    def test_wrong_trap_exit_is_not_success(self):
        changed_command = self.fixture.command[2].replace("exit 23", "exit 0")
        with self.assertRaisesRegex(ValueError, "guest trap status"):
            self.run_cli(changed_command)
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_wrong_usr1_bytes_fail_without_second_signal(self):
        changed_command = self.fixture.command[2].replace("signal:USR1", "wrong:USR1")
        with self.assertRaisesRegex(ValueError, "did not forward"):
            self.run_cli(changed_command)
        self.assertNotIn(PROCESS + "-sigterm-intent.json", self.journal.records())
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_wrong_term_bytes_fail_after_real_exit(self):
        changed_command = self.fixture.command[2].replace("signal:TERM", "wrong:TERM")
        with self.assertRaisesRegex(ValueError, "exact guest streams"):
            self.run_cli(changed_command)
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_signal_target_is_revalidated_before_any_signal(self):
        self.journal.put(PROCESS + "-process.json", canonical({"pid": 17}))
        with patch.object(self.fixture.child, "identity", return_value={"pid": 18}), \
                patch("compose_foreground_probe.os.kill") as kill:
            with self.assertRaisesRegex(ValueError, "incarnation changed"):
                self.fixture.send_signal("SIGTERM", signal.SIGTERM)
        kill.assert_not_called()
        self.assertNotIn(PROCESS + "-sigterm-intent.json", self.journal.records())

    def test_changed_guest_cannot_receive_followup_signal(self):
        original = self.fixture.require_signal_output

        def change_guest(end):
            original(end)
            self.server.guest["State"]["Status"] = "exited"

        with patch.object(self.fixture, "require_signal_output", side_effect=change_guest):
            with self.assertRaisesRegex(ValueError, "replaced or stopped"):
                self.run_cli()
        self.assertNotIn(PROCESS + "-sigterm-intent.json", self.journal.records())
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_missing_signal_is_bounded_and_cleanup_remains_owned(self):
        original = self.fixture.require_signal_output

        def expire_signal_wait(end):
            with patch("compose_foreground_probe.remaining", side_effect=TimeoutError("signal deadline")):
                original(end)

        with patch.object(self.fixture, "require_signal_output", side_effect=expire_signal_wait):
            changed_command = self.fixture.command[2].replace('printf "signal:USR1\\n"', ":")
            with self.assertRaisesRegex(TimeoutError, "signal deadline"):
                self.run_cli(changed_command)
        self.assertIn(PROCESS + "-sigusr1-intent.json", self.journal.records())
        self.assertNotIn(PROCESS + "-sigterm-intent.json", self.journal.records())
        self.assertEqual(self.fixture.cleanup()["status"], "passed")


class ComposeTerminalInputTests(unittest.TestCase):
    stop = helpers.GuestFixtureTests.stop
    diagnostic = "cannot attach stdin to a TTY-enabled container because stdin is not a terminal"

    def reopen(self):
        return ComposeTerminalInputFixture(self.socket, self.owner, self.server.image, "1.54", self.journal,
                                           root=self.root, executable="/owned/compose", runtime=Mock())

    def setUp(self):
        helpers.GuestFixtureTests.setUp(self)
        self.addCleanup(self.fixture.child.stop)
        original = self.fixture.call

        def call(method, route, *args, **kwargs):
            if route == f"/images/{self.fixture.missing_image}/json":
                return 404, canonical({"message": "missing image"})
            return original(method, route, *args, **kwargs)

        self.fixture.call = Mock(side_effect=call)

    def guest(self):
        return {"Id": "b" * 64, "Name": "/" + self.fixture.name, "Image": self.server.image,
                "Config": {"Image": self.server.image, "Cmd": ["sleep", "300"], "Tty": False, "OpenStdin": False,
                           "Labels": {**self.fixture.intent["labels"], "com.docker.compose.project": self.fixture.project,
                                      "com.docker.compose.service": "dependency"}},
                "HostConfig": {"AutoRemove": False, "NetworkMode": "none"}, "State": {"Status": "running"}}

    def run_cli(self, script=None, *, dependency=True):
        if script is None:
            script = f"printf '{self.diagnostic}\\n' >&2; exit 1"
        original = self.fixture.child.start

        def start(arguments, root, output, **kwargs):
            self.cli_arguments = arguments
            records = self.journal.records()
            self.assertIn("container-intent.json", records)
            self.assertEqual(json.loads(records[PROCESS + "-intent.json"])["arguments"], arguments)
            self.assertIs(kwargs["stdin"], subprocess.PIPE)
            self.server.guest = self.guest() if dependency else None
            # Hold the fake CLI until the fixture journals its process identity
            # and closes stdin. No timing sleeps or mocked process identities.
            original(["/bin/sh", "-c", "read -r ignored; " + script], root, output, **kwargs)

        with patch.object(self.fixture.child, "start", side_effect=start):
            return self.fixture.operation()

    def test_dependency_precedes_exact_terminal_error_without_job_image_or_creation(self):
        observed = self.run_cli()
        contract = json.loads((Path(__file__).parents[2] / "Tests/Parity/fixtures" / TTY_INPUT_FIXTURE / "contract.json").read_text())
        self.assertEqual(observed, contract_observations(contract["expected"]))
        self.assertEqual(self.cli_arguments[-2:], ["--tty", "app"])
        self.assertNotIn("--no-deps", self.cli_arguments)
        configuration = json.loads((self.root / "compose-terminal-input.json").read_text())
        self.assertEqual(configuration["services"]["app"]["depends_on"], ["dependency"])
        self.assertEqual(configuration["services"]["app"]["image"], self.fixture.missing_image)
        self.assertEqual(configuration["services"]["dependency"]["command"], ["sleep", "300"])
        self.assertEqual(json.loads(self.journal.records()["container-created.json"]), {"id": "b" * 64})
        self.assertEqual(self.fixture.cleanup()["remainingOwnedResources"], [])
        self.assertEqual(self.reopen().cleanup()["status"], "passed")
        self.assertEqual(require_guest_commands_stopped(self.journal.records()), [PROCESS])
        self.assertEqual(self.journal.records()[PROCESS + "-stderr.log"], self.fixture.terminal_error)

    def test_wrong_exit_retains_dependency_identity_before_failure_and_cleanup(self):
        with self.assertRaisesRegex(ValueError, "exact exit"):
            self.run_cli("exit 2")
        self.assertIn("container-created.json", self.journal.records())
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_wrong_error_and_stdout_are_not_normalized(self):
        with self.assertRaisesRegex(ValueError, "error or output stream"):
            self.run_cli(f"printf '{self.diagnostic}\\n'; exit 1")
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_unobserved_dependency_creation_quarantines(self):
        with self.assertRaisesRegex(ValueError, "before dependency startup"):
            self.run_cli(dependency=False)
        with self.assertRaisesRegex(ValueError, "Uncertain creation"):
            self.fixture.cleanup()
        self.assertNotIn("container-removed.json", self.journal.records())

    def test_prefixed_terminal_diagnostic_cannot_pass_as_an_exact_line(self):
        with self.assertRaisesRegex(ValueError, "error or output stream"):
            self.run_cli(f"printf 'unrelated failure: {self.diagnostic}\\n' >&2; exit 1")
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_progress_lines_do_not_replace_the_exact_final_diagnostic(self):
        self.run_cli(f"printf 'dependency Started\\n{self.diagnostic}\\n' >&2; exit 1")
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_duplicate_diagnostic_is_not_normalized(self):
        with self.assertRaisesRegex(ValueError, "error or output stream"):
            self.run_cli(f"printf '{self.diagnostic}\\n{self.diagnostic}\\n' >&2; exit 1")
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_stopped_dependency_is_not_a_startup_pass(self):
        original = self.guest
        with patch.object(self, "guest", side_effect=lambda: {**original(), "State": {"Status": "exited"}}):
            with self.assertRaisesRegex(ValueError, "not running"):
                self.run_cli()
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_nonterminal_dependency_settings_and_foreign_service_are_rejected(self):
        for section, key, value in (("Config", "Tty", True), ("Config", "OpenStdin", True),
                                    ("HostConfig", "AutoRemove", True), ("HostConfig", "NetworkMode", "bridge")):
            guest = self.guest()
            guest[section][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "configuration"):
                self.fixture.owned(guest)
        guest = self.guest()
        guest["Config"]["Labels"]["com.docker.compose.service"] = "app"
        with self.assertRaisesRegex(ValueError, "configuration"):
            self.fixture.owned(guest)

    def test_existing_job_and_prepared_job_image_fail_before_any_launch(self):
        with patch.object(self.fixture, "inspect", return_value=self.guest()):
            with self.assertRaisesRegex(ValueError, "unused names"):
                self.fixture.operation()
        with patch.object(self.fixture, "call", return_value=(200, canonical({"Id": self.server.image}))):
            with self.assertRaisesRegex(ValueError, "image must remain absent"):
                self.fixture.operation()
        self.assertIsNone(self.fixture.child.process)
        self.assertNotIn("container-intent.json", self.journal.records())

    def test_extra_owned_resource_is_not_hidden(self):
        original = self.fixture.call

        def call(method, route, *args, **kwargs):
            if route.startswith("/containers/json"):
                return 200, canonical([{"Id": "b" * 64}, {"Id": "e" * 64}])
            return original(method, route, *args, **kwargs)

        with patch.object(self.fixture, "call", side_effect=call):
            with self.assertRaisesRegex(ValueError, "unexpected owned resources"):
                self.run_cli()
            with self.assertRaisesRegex(ValueError, "residue"):
                self.fixture.cleanup()
        self.assertNotIn("container-removed.json", self.journal.records())

    def test_timeout_stops_only_owned_child_and_removes_dependency(self):
        with patch("compose_foreground_probe.remaining", return_value=0.01):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.run_cli("exec /bin/sleep 30")
        self.assertIsNone(self.fixture.child.process.poll())
        self.assertEqual(self.fixture.cleanup()["status"], "passed")
        self.assertIsNotNone(self.fixture.child.process.poll())
        self.assertIn(PROCESS + "-process.json", self.journal.records())


if __name__ == "__main__":
    unittest.main()
