"""Real piped child IO and Unix inspection, with a simulated Compose backend."""

import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from case_evidence import canonical, contract_observations
from compose_foreground_probe import (COMMAND, FIXTURE, QUIET_FIXTURE, REDIRECTED_FIXTURE,
                                      PROCESS, STDERR, STDOUT, ComposeForegroundFixture)
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
        with self.assertRaisesRegex(ValueError, "lost the guest exit"):
            self.run_cli(COMMAND[2].replace("exit 17", "exit 0"))
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_stdout_corruption_and_stderr_omission_fail(self):
        with self.assertRaisesRegex(ValueError, "changed or merged"):
            self.run_cli(COMMAND[2].replace("printf 'compose-stderr\\n' >&2", ":"))
        self.fixture.cleanup()

    def test_guest_bytes_on_wrong_stream_fail(self):
        with self.assertRaisesRegex(ValueError, "changed or merged"):
            self.run_cli(COMMAND[2].replace("exit 17", "printf 'compose-stdout\\n' >&2; exit 17"))
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
        with self.assertRaisesRegex(ValueError, "explicit reconciliation"):
            require_guest_commands_stopped({PROCESS + "-intent.json": canonical({})})

    def test_reopened_fixture_cannot_claim_an_existing_child_stopped(self):
        self.fixture.prepare()
        with self.fixture.output.open("xb") as output:
            self.fixture.child.start(["/bin/sleep", "30"], self.root, output)
        self.journal.put(PROCESS + "-process.json", canonical(self.fixture.child.identity()))
        self.server.guest = self.guest()
        with self.assertRaisesRegex(ValueError, "incarnation reconciliation"):
            self.reopen().cleanup()
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
        with self.assertRaisesRegex(ValueError, "canonical"):
            OwnedProcess().start(["/owned/compose"], self.root, Mock(), runtime_socket=Path("relative"))


if __name__ == "__main__":
    unittest.main()
