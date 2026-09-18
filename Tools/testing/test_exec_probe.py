"""Real Unix transport, duplex backpressure and E03 ownership regressions."""

import json
from pathlib import Path
import socket
import sys
import time
import unittest
from unittest.mock import Mock, patch

from case_evidence import canonical
from exec_probe import BINARY_INPUT, attached, duplex, exec_streams, execute, streams, upgrade
import test_guest_fixture as helpers


def frame(data, channel=1):
    return bytes((channel, 0, 0, 0)) + len(data).to_bytes(4, "big") + data


class Handler(helpers.Handler):
    protocol_version = "HTTP/1.1"

    def dispatch(self):
        server = self.server
        if self.path.endswith("/exec"):
            config = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            identifier = server.created_id or f"{len(server.execs) + 1:064x}"
            server.execs[identifier] = {"config": config, "completed": False}
            server.routes.append((self.command, self.path))
            return server.create_status, {"Id": server.created_id or identifier}
        if "/exec/" in self.path and self.path.endswith("/json"):
            identifier = self.path.split("/")[-2]
            entry = server.execs[identifier]
            value = {"ID": identifier, "ContainerID": server.guest["Id"], "Running": False,
                     "ExitCode": 7 if "stderr-value" in " ".join(entry["config"]["Cmd"]) else 0}
            value.update(server.inspect_override)
            if entry["completed"]:
                value.update(server.completed_override)
                if server.pending_exit:
                    server.pending_exit -= 1
                    value["Running"] = True
            return 200, value
        return super().dispatch()

    def respond(self):
        if "/exec/" not in self.path or not self.path.endswith("/start"):
            return super().respond()
        server = self.server
        server.routes.append((self.command, self.path))
        entry = server.execs[self.path.split("/")[-2]]
        start = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        config = entry["config"]
        server.starts.append(start)
        self.close_connection = True
        self.connection.settimeout(2)
        self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        headers = (b"HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n"
                   b"Content-Type: application/vnd.docker.raw-stream\r\n\r\n")
        # Fragment headers; the client must preserve bytes after their terminator.
        for part in (headers[:9], headers[9:47], headers[47:]):
            self.connection.sendall(part)
        command = config["Cmd"]
        if command == ["cat"]:
            while chunk := self.rfile.read1(4096):
                self.connection.sendall(frame(chunk))
        elif config["Tty"]:
            self.connection.sendall(b"tty-value")
        elif config.get("Env"):
            self.connection.sendall(frame(b"present|/tmp|0:0"))
        else:
            self.connection.sendall(frame(server.stdout) + frame(b"stderr-value", 2))
        entry["completed"] = True

    do_GET = do_POST = do_DELETE = respond


class ExecTests(unittest.TestCase):
    reopen = helpers.GuestFixtureTests.reopen
    stop = helpers.GuestFixtureTests.stop

    def setUp(self):
        self.server_errors = []
        # LIFO cleanup joins the server before checking its collected errors.
        self.addCleanup(self.assertEqual, self.server_errors, [])
        with patch.object(helpers, "Handler", Handler):
            helpers.GuestFixtureTests.setUp(self)
        self.server.handle_error = lambda *_: self.server_errors.append(sys.exc_info()[1])
        self.server.execs, self.server.starts = {}, []
        self.server.inspect_override, self.server.completed_override = {}, {}
        self.server.create_status, self.server.created_id = 201, None
        self.server.pending_exit = 0
        self.server.stdout = b"stdout-value"
        self.events = []
        self.fixture.observe = self.events.append
        self.fixture.setup()

    def test_all_six_original_observations_and_duplex_backpressure(self):
        contract = Path(__file__).parents[2] / "Tests/Parity/fixtures/E03-exec-streams/contract.json"
        expected = {key: str(value).lower() for key, value in json.loads(contract.read_text())["expected"].items()}
        self.assertEqual(exec_streams(self.fixture), expected)
        self.assertEqual(len(BINARY_INPUT), 4 * 1024**2)
        configs = [entry["config"] for entry in self.server.execs.values()]
        self.assertEqual((configs[0]["Env"], configs[0]["User"], configs[0]["WorkingDir"]),
                         (["PARITY_VALUE=present"], "0:0", "/tmp"))
        self.assertTrue(configs[2]["AttachStdin"])
        self.assertTrue(configs[3]["Tty"])
        self.assertEqual(self.server.starts, [{"Detach": False, "Tty": value} for value in (False, False, False, True)])
        self.assertTrue(all(entry["completed"] for entry in self.server.execs.values()))
        self.assertEqual(self.fixture.cleanup()["status"], "passed")
        for name in ("environment", "streams", "binary", "tty"):
            self.assertIn("exec-" + name + "-completed.json", self.journal.records())
        starts = [event for event in self.events if "/exec/" in event["route"] and event["route"].endswith("/start")]
        self.assertEqual(len(starts), 4)
        self.assertTrue(all(set(event) == {"method", "route", "status", "durationNS"} for event in starts))

    def test_wrong_output_and_exit_are_observed_not_normalized(self):
        self.server.stdout = b"wrong"
        self.server.completed_override = {"ExitCode": 9}
        observed = exec_streams(self.fixture)
        for name in ("stdout", "exact_exit", "environment", "binary_duplex", "tty"):
            self.assertEqual(observed[name], "false")
        self.assertEqual(observed["stderr"], "true")

    def test_changed_guest_is_rejected_before_exec_creation(self):
        self.server.guest["Config"]["Labels"] = {}
        with self.assertRaises(ValueError):
            exec_streams(self.fixture)
        self.assertEqual(self.server.execs, {})

    def test_guest_must_be_running_before_exec_creation(self):
        self.server.guest["State"]["Status"] = "exited"
        with self.assertRaisesRegex(ValueError, "running guest"):
            exec_streams(self.fixture)
        self.assertEqual(self.server.execs, {})

    def test_write_ahead_journal_failure_prevents_mutation(self):
        with patch.object(self.journal, "put", side_effect=OSError("disk full")), self.assertRaises(OSError):
            exec_streams(self.fixture)
        self.assertEqual(self.server.execs, {})

    def test_failed_creation_is_not_retried_and_parent_cleanup_still_works(self):
        self.server.create_status = 500
        with self.assertRaisesRegex(ValueError, "valid identity"):
            exec_streams(self.fixture)
        with self.assertRaisesRegex(ValueError, "already attempted"):
            exec_streams(self.fixture)
        self.assertEqual(len(self.server.execs), 1)
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_invalid_exec_id_is_not_used_in_a_route(self):
        self.server.created_id = "../other"
        with self.assertRaisesRegex(ValueError, "valid identity"):
            exec_streams(self.fixture)
        self.assertEqual(self.server.starts, [])

    def test_changed_exec_parent_and_running_exec_are_not_started(self):
        self.server.inspect_override = {"ContainerID": "d" * 64}
        with self.assertRaisesRegex(ValueError, "owned guest"):
            execute(self.fixture, "wrong-parent", ["true"])
        self.server.inspect_override = {"Running": True}
        with self.assertRaisesRegex(ValueError, "already running"):
            execute(self.fixture, "already-running", ["true"])
        self.assertEqual(self.server.starts, [])

    def test_released_uuid_exec_and_native_parent_alias_keep_exact_ownership(self):
        self.server.created_id = "2bcb3a6b-152c-40ea-a55f-4a364211d6ae"
        self.server.inspect_override = {"ContainerID": self.fixture.name}
        self.assertEqual(execute(self.fixture, "uuid", ["true"])[2], 0)
        self.assertEqual(len(self.server.starts), 1)
        self.assertIn(("GET", f"/v1.54/containers/{self.fixture.name}/json"), self.server.routes)

    def test_parent_path_injection_is_rejected_before_followup_request(self):
        self.server.inspect_override = {"ContainerID": "../other"}
        with self.assertRaisesRegex(ValueError, "parent identifier"):
            execute(self.fixture, "unsafe-parent", ["true"])
        self.assertFalse(any(".." in path for _, path in self.server.routes))

    def test_open_exec_and_invalid_exit_code_never_count_as_completed(self):
        for name, overrides in (("live", {"Running": True}), ("bool", {"ExitCode": False}),
                                ("missing", {"ExitCode": None}), ("foreign", {"ID": "d" * 64})):
            self.server.completed_override = overrides
            with self.subTest(name=name), patch("exec_probe.EXIT_WAIT", 0.02), self.assertRaises((ValueError, TimeoutError)):
                execute(self.fixture, name, ["true"])
            self.assertNotIn("exec-" + name + "-completed.json", self.journal.records())

    def test_stream_eof_can_precede_exit_publication_without_restarting_exec(self):
        self.server.pending_exit = 1
        self.assertEqual(execute(self.fixture, "settling", ["true"])[2], 0)
        self.assertEqual(len(self.server.starts), 1)
        self.assertEqual(len(self.server.execs), 1)

    def test_lost_stream_response_keeps_intent_and_prevents_repeat(self):
        with patch("exec_probe.attached", side_effect=TimeoutError), self.assertRaises(TimeoutError):
            execute(self.fixture, "uncertain", ["true"])
        with self.assertRaisesRegex(ValueError, "already attempted"):
            execute(self.fixture, "uncertain", ["true"])
        self.assertIn("exec-uncertain-created.json", self.journal.records())
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_socket_failure_is_timed_without_logging_data(self):
        self.fixture.socket = self.root / "absent"
        with self.assertRaises(OSError):
            attached(self.fixture, "e" * 64, tty=False, incoming=b"private", timeout=1)
        event = self.events[-1]
        self.assertEqual(set(event), {"method", "route", "error", "durationNS"})
        self.assertNotIn("private", str(event))


class ExecTransportTests(unittest.TestCase):
    def test_stream_framing_preserves_interleaved_binary_channels(self):
        self.assertEqual(streams(frame(b"a\0") + frame(b"error", 2) + frame(b"b")), (b"a\0b", b"error"))
        for payload in (b"x", frame(b"x")[:-1], bytes((3, 0, 0, 0)) + b"\0" * 4,
                        bytes((1, 1, 0, 0)) + b"\0" * 4):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                streams(payload)

    def test_upgrade_preserves_initial_bytes_and_supports_legacy_raw_200(self):
        for status in (b"101 UPGRADED", b"200 OK"):
            connection = Mock()
            connection.recv.return_value = (b"HTTP/1.1 " + status + b"\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n"
                                            b"Content-Type: application/vnd.docker.raw-stream\r\n\r\ninitial")
            self.assertEqual(upgrade(connection, "/exec/id/start", b"{}", time.monotonic() + 1),
                             (int(status[:3]), b"initial"))

    def test_invalid_upgrade_headers_never_become_exec_output(self):
        base = b"HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\nUpgrade: tcp\r\nContent-Type: application/vnd.docker.raw-stream"
        bad = [base.replace(b"101", b"500"), base.replace(b"tcp", b"websocket"),
               base.replace(b"application/vnd.docker.raw-stream", b"text/html"),
               base + b"\r\nContent-Length: 0", base + b"\r\nTransfer-Encoding: chunked",
               base + b"\r\nUpgrade: tcp", base + b"\r\nbad-header"]
        for payload in bad:
            connection = Mock()
            connection.recv.return_value = payload + b"\r\n\r\n"
            deadline = time.monotonic() + 1
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                upgrade(connection, "/exec/id/start", b"{}", deadline)

    def test_unterminated_and_oversized_headers_fail_boundedly(self):
        for payload in (b"", b"x" * 4096):
            connection = Mock()
            connection.recv.return_value = payload
            deadline = time.monotonic() + 1
            with self.subTest(payload=bool(payload)), self.assertRaises(ValueError):
                upgrade(connection, "/exec/id/start", b"{}", deadline)

    def pair(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        return left, right

    def test_whole_connection_timeout_and_write_half_close(self):
        left, right = self.pair()
        deadline = time.monotonic() + 0.02
        with self.assertRaises(TimeoutError):
            duplex(left, b"", b"", deadline)
        self.assertEqual(right.recv(1), b"")

    def test_excess_output_and_early_eof_do_not_pass(self):
        left, right = self.pair()
        right.sendall(b"x" * 128)
        deadline = time.monotonic() + 1
        with patch("exec_probe.MAX_OUTPUT", 64), self.assertRaisesRegex(ValueError, "exceeds"):
            duplex(left, b"", b"", deadline)
        left, right = self.pair()
        right.shutdown(socket.SHUT_WR)
        deadline = time.monotonic() + 1
        with self.assertRaisesRegex(ValueError, "before input"):
            duplex(left, b"", BINARY_INPUT, deadline)
