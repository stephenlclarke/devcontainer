"""Direct-HTTP lifecycle contracts, framing and readiness regressions."""

import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from guest_fixture import GuestFixture
from lifecycle_probe import COMMAND, lifecycle, ready, stdout_frames
import test_guest_fixture as helpers
from engine_probe import request


def frame(content, channel=1):
    return bytes([channel, 0, 0, 0]) + len(content).to_bytes(4, "big") + content


class Handler(helpers.Handler):
    def dispatch(self):
        server = self.server
        path = urlsplit(self.path).path
        verb = path.rsplit("/", 1)[-1]
        if self.command == "GET" and verb == "logs":
            server.routes.append((self.command, self.path))
            return server.log_status, frame(f"ready-{server.generation}\n".encode())
        if self.command == "POST" and verb in {"restart", "kill", "wait"}:
            server.routes.append((self.command, self.path))
            if verb == server.fail_operation:
                return 500, {"message": "failed"}
            if verb == "restart":
                server.generation += 1
                if server.replace_id:
                    server.guest["Id"] = "d" * 64
            if verb == "kill":
                server.guest["State"] = {"Status": "exited", "ExitCode": server.exit_code}
            return (200, server.wait_result) if verb == "wait" else (204, b"")
        if self.command == "DELETE" and server.guest is None and server.repeat_status != 404:
            server.routes.append((self.command, self.path))
            return server.repeat_status, {"message": "not an absence result"}
        return super().dispatch()


class LifecycleTests(unittest.TestCase):
    # Reuse the socket/journal fixture without inheriting or rerunning its tests.
    reopen = helpers.GuestFixtureTests.reopen
    stop = helpers.GuestFixtureTests.stop

    def setUp(self):
        with patch.object(helpers, "Handler", Handler):
            helpers.GuestFixtureTests.setUp(self)
        self.events = []
        self.fixture = GuestFixture(self.socket, self.owner, self.server.image, "1.54", self.journal,
                                    command=COMMAND, observe=self.events.append)
        self.server.generation, self.server.exit_code = 1, 7
        self.server.wait_result = {"StatusCode": 7, "Error": None}
        self.server.fail_operation, self.server.replace_id = None, False
        self.server.repeat_status, self.server.log_status = 404, 200

    def test_all_original_observations_through_real_socket_and_owned_cleanup(self):
        contract = Path(__file__).parents[2] / "Tests/Parity/fixtures/E02-container-lifecycle/contract.json"
        expected = {key: str(value).lower() for key, value in json.loads(contract.read_text())["expected"].items()}
        self.assertEqual(lifecycle(self.fixture), expected)
        self.assertEqual(self.fixture.cleanup()["status"], "passed")
        mutations = [(method, path) for method, path in self.server.routes if method != "GET"]
        self.assertEqual([method for method, _ in mutations], ["POST"] * 5 + ["DELETE", "DELETE"])
        self.assertNotIn("force=true", mutations[-2][1])
        self.assertTrue(all(event["durationNS"] >= 0 for event in self.events))

    def test_wrong_wait_and_inspect_exit_codes_are_preserved(self):
        self.server.exit_code = 137
        self.server.wait_result = {"StatusCode": 42}
        observed = lifecycle(self.fixture)
        self.assertEqual(observed["exit_status"], "137")
        self.assertEqual(observed["wait_status"], "42")

    def test_restart_transport_deadline_covers_server_grace_and_startup(self):
        with patch("guest_fixture.request", wraps=request) as transport:
            lifecycle(self.fixture)
        restarts = [call for call in transport.call_args_list if "/restart?t=10" in call.args[2]]
        self.assertEqual(len(restarts), 1)
        self.assertEqual(restarts[0].kwargs["timeout"], 20)
        self.fixture.cleanup()

    def test_arbitrary_error_is_not_idempotent_removal(self):
        self.server.repeat_status = 500
        self.assertEqual(lifecycle(self.fixture)["idempotent_cleanup"], "false")

    def test_mutable_wrong_command_is_rejected_before_creation(self):
        ordinary = self.reopen()
        with self.assertRaisesRegex(ValueError, "signal-aware"):
            lifecycle(ordinary)
        self.assertEqual(self.server.routes, [])

    def test_restart_identity_change_does_not_signal_the_replacement(self):
        self.server.replace_id = True
        with self.assertRaisesRegex(ValueError, "identity"):
            lifecycle(self.fixture)
        self.assertFalse(any("/kill" in path for _, path in self.server.routes))

    def test_restart_and_signal_errors_are_not_retried(self):
        for operation in ("restart", "kill"):
            # Each attempted case needs its own journal and name. Reuse no outcome.
            with self.subTest(operation=operation):
                self.server.fail_operation = operation
                journal = helpers.ServiceJournal(self.root / (operation + ".sqlite"), {"operation": operation}, create=True)
                fixture = GuestFixture(self.socket, self.owner, self.server.image, "1.54", journal, command=COMMAND)
                with self.assertRaises(ValueError):
                    lifecycle(fixture)
                fixture.cleanup()
                self.server.generation = 1

    def test_wait_error_or_missing_status_is_not_a_valid_exit_code(self):
        for index, result in enumerate(({"StatusCode": True}, {"StatusCode": 7, "Error": {"Message": "failed"}}, {})):
            self.server.wait_result = result
            journal = helpers.ServiceJournal(self.root / f"wait-{index}.sqlite", {"index": index}, create=True)
            fixture = GuestFixture(self.socket, self.owner, self.server.image, "1.54", journal, command=COMMAND)
            with self.subTest(result=result), self.assertRaisesRegex(ValueError, "wait failed"):
                lifecycle(fixture)
            fixture.cleanup()
            self.server.generation = 1

    def test_missing_inspected_exit_code_fails(self):
        self.server.exit_code = None
        with self.assertRaisesRegex(ValueError, "exit code"):
            lifecycle(self.fixture)
        self.fixture.cleanup()

    def test_stale_start_log_does_not_prove_restart_readiness(self):
        self.fixture.setup()
        with patch("lifecycle_probe.time.monotonic", side_effect=[0, 0, 6]), \
                patch("lifecycle_probe.time.sleep") as sleep, self.assertRaises(TimeoutError):
            ready(self.fixture, 2)
        sleep.assert_called_once()
        self.fixture.cleanup()

    def test_invalid_log_status_fails_instead_of_polling(self):
        self.fixture.setup()
        self.server.log_status = 403
        with self.assertRaisesRegex(ValueError, "readiness"):
            ready(self.fixture, 1)
        self.fixture.cleanup()

    def test_stdout_and_stderr_frames_do_not_merge(self):
        self.assertEqual(stdout_frames(frame(b"ready-", 1) + frame(b"noise", 2) + frame(b"2\n", 1)), b"ready-2\n")
        self.assertEqual(stdout_frames(b""), b"")
        for payload in (b"raw log", frame(b"x")[:-1], frame(b"x", 0), b"\1\1\0\0\0\0\0\0"):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                stdout_frames(payload)

    def test_missing_guest_id_prevents_start_and_ordinary_remove(self):
        for operation in (self.fixture.start, self.fixture.remove):
            with self.assertRaisesRegex(ValueError, "verified created ID"):
                operation()
        self.assertEqual(self.server.routes, [])

    def test_failed_ordinary_removal_does_not_report_success(self):
        self.fixture.setup()
        self.server.ignore_delete = True
        with self.assertRaisesRegex(ValueError, "removal failed"):
            self.fixture.remove()

    def test_request_errors_are_timed_without_error_message_leakage(self):
        with patch("guest_fixture.request", side_effect=TimeoutError("secret")), self.assertRaises(TimeoutError):
            self.fixture.inspect(self.fixture.name)
        self.assertEqual(self.events[0]["error"], "TimeoutError")
        self.assertNotIn("secret", json.dumps(self.events))


if __name__ == "__main__":
    unittest.main()
