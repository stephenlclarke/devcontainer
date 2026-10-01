"""Real Unix terminal transport, acknowledged waits and exact auto-removal."""

import json
from pathlib import Path
import select
import socket
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from case_evidence import canonical, contract_observations
from foreground_probe import ForegroundFixture, MARKERS, receive_exact, receive_incarnation, require_eof
from test_attachment_probe import Server
import test_guest_fixture as helpers


class Handler(helpers.Handler):
    protocol_version = "HTTP/1.1"

    def dispatch(self):
        path = urlsplit(self.path).path
        server = self.server
        if self.command == "DELETE":
            server.exited.set()
        if path.endswith("/start"):
            if not server.waiting.is_set() or not server.attached.is_set():
                return 409, {"message": "start preceded subscriptions"}
            value = super().dispatch()
            server.started.set()
            return value
        if path.endswith("/resize"):
            query = parse_qs(urlsplit(self.path).query)
            server.routes.append((self.command, self.path))
            server.size = server.size_override or query["h"][0] + " " + query["w"][0]
            return server.resize_status, b""
        value = super().dispatch()
        if path.endswith("/create"):
            server.guest["HostConfig"] = server.guest["Config"]["HostConfig"]
        return value

    def respond(self):
        path = urlsplit(self.path).path
        self.close_connection = True
        self.connection.settimeout(3)
        server = self.server
        if path.endswith("/wait"):
            server.routes.append((self.command, self.path))
            if server.wait_status != 200:
                self.send_response(server.wait_status)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            server.waiting.set()
            self.send_response(200)
            self.send_header("Connection", "close")
            self.end_headers()
            end = time.monotonic() + 3
            while not server.exited.wait(0.01):
                if select.select([self.connection], [], [], 0)[0] and self.connection.recv(1, socket.MSG_PEEK) == b"":
                    return
                if time.monotonic() >= end:
                    raise TimeoutError("owned foreground waiter never completed")
            try:
                self.wfile.write(canonical({"StatusCode": server.exit_code}))
            except (BrokenPipeError, ConnectionResetError):
                # Failed probes close their wait before owned cleanup exits init.
                return
            return
        if not path.endswith("/attach"):
            return super().respond()
        server.routes.append((self.command, self.path))
        server.attached.set()
        initial = server.guest["State"]["Status"] == "created"
        self.connection.sendall(b"HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n"
                                b"Content-Type: application/vnd.docker.raw-stream\r\n\r\n")
        if initial:
            if not server.started.wait(3):
                raise TimeoutError("start never followed terminal subscription")
            self.connection.sendall(getattr(server, "initial_output", MARKERS))
        pending = bytearray()
        while chunk := self.rfile.read1(4096):
            pending.extend(chunk)
            if pending == b"\x10\x11":
                if server.detach_stops:
                    server.guest["State"]["Status"] = "exited"
                if server.detach_restarts:
                    server.incarnation = b"00000000-0000-4000-8000-000000000002\n"
                return
            if b"\n" not in pending:
                continue
            line, _, rest = pending.partition(b"\n")
            pending = bytearray(rest)
            if line == b"size":
                self.connection.sendall(server.size.encode() + b"\n")
            elif line == b"identity":
                self.connection.sendall(server.incarnation)
            elif line == b"quit":
                server.guest = None
                server.exited.set()
                return
            else:
                self.connection.sendall(b"seen:" + line + b"\n")

    do_GET = do_POST = do_DELETE = respond


class ForegroundTests(unittest.TestCase):
    stop = helpers.GuestFixtureTests.stop

    def reopen(self):
        return ForegroundFixture(self.socket, self.owner, self.server.image, "1.54", self.journal)

    def setUp(self):
        self.errors = []
        self.addCleanup(self.assertEqual, self.errors, [])
        with patch.object(helpers, "Handler", Handler), patch.object(helpers, "UnixStreamServer", Server):
            helpers.GuestFixtureTests.setUp(self)
        self.server.handle_error = lambda *_: self.errors.append(sys.exc_info()[1])
        self.server.waiting, self.server.attached = threading.Event(), threading.Event()
        self.server.started, self.server.exited = threading.Event(), threading.Event()
        self.server.size, self.server.exit_code = "0 0", 17
        self.server.size_override, self.server.detach_stops = None, False
        self.server.detach_restarts = False
        self.server.incarnation = b"00000000-0000-4000-8000-000000000001\n"
        self.server.wait_status, self.server.resize_status = 200, 200
        self.events = []
        self.fixture.observe = self.events.append

    def test_real_terminal_detach_reconnect_wait_and_auto_removal(self):
        contract = Path(__file__).parents[2] / "Tests/Parity/fixtures/E08-foreground-terminal/contract.json"
        expected = contract_observations(json.loads(contract.read_text())["expected"])
        self.assertEqual(self.fixture.operation(), expected)
        self.assertEqual(sum("/attach?" in event["route"] for event in self.events), 2)
        self.assertEqual(sum("condition=next-exit" in event["route"] for event in self.events), 1)
        self.assertTrue(all(event["durationNS"] > 0 for event in self.events))
        self.assertEqual(json.loads(self.journal.records()["foreground-exit.json"]), {"StatusCode": 17})
        self.assertIn("foreground-terminal.json", self.journal.records())
        self.assertEqual(self.fixture.cleanup(), {"status": "passed", "remainingOwnedResources": []})
        self.assertEqual(self.reopen().cleanup()["status"], "passed")
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))

    def test_wrong_exit_does_not_pass_after_auto_removal(self):
        self.server.exit_code = 0
        with self.assertRaisesRegex(ValueError, "real exit status"):
            self.fixture.operation()
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_changed_terminal_policy_prevents_destructive_cleanup(self):
        self.fixture.create()
        self.server.guest["HostConfig"]["AutoRemove"] = False
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            self.fixture.cleanup()
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))

    def test_unacknowledged_wait_never_starts_init(self):
        self.server.wait_status = 503
        with self.assertRaisesRegex(ValueError, "not acknowledged"):
            self.fixture.operation()
        self.assertFalse(any(path.endswith("/start") for _, path in self.server.routes))
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_resize_requires_changed_guest_dimensions(self):
        self.server.size_override = "0 0"
        with self.assertRaisesRegex(ValueError, "output differs"):
            self.fixture.operation()
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_resize_failure_is_not_accepted(self):
        self.server.resize_status = 500
        with self.assertRaisesRegex(ValueError, "resize failed"):
            self.fixture.operation()
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_detach_must_leave_init_running(self):
        self.server.detach_stops = True
        with self.assertRaisesRegex(ValueError, "Detachment stopped"):
            self.fixture.operation()
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_detach_cannot_restart_init_inside_the_same_container(self):
        self.server.detach_restarts = True
        with self.assertRaisesRegex(ValueError, "output differs"):
            self.fixture.operation()
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_auto_removal_requires_absence_without_accepting_identity_drift(self):
        self.fixture.create()
        self.server.guest["State"]["Status"] = "exited"
        with patch("foreground_probe.remaining", side_effect=TimeoutError("expired")), \
                self.assertRaisesRegex(TimeoutError, "expired"):
            self.fixture.require_auto_removed()
        self.server.guest["State"]["Status"] = "running"
        with self.assertRaisesRegex(ValueError, "identity or exit state"):
            self.fixture.require_auto_removed()
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_terminal_bytes_and_eof_are_not_normalized(self):
        connection = Mock()
        connection.recv.side_effect = [b"ty", b"\n"]
        receive_exact(connection, b"t", b"tty\n", time.monotonic() + 3)
        for initial, incoming, message in ((b"tty\r\n", [], "differs"), (b"", [b""], "before expected")):
            connection.recv.side_effect = incoming
            with self.assertRaisesRegex(ValueError, message):
                receive_exact(connection, initial, b"tty\n", time.monotonic() + 3)
        connection.recv.side_effect = [b"unexpected"]
        with self.assertRaisesRegex(ValueError, "unexpected trailing"):
            require_eof(connection, time.monotonic() + 3)

    def test_exit_receipt_requires_bounded_complete_json(self):
        for payload, length, message in ((b"x" * 4097, 0, "exceeds"),
                                         (b'{"StatusCode":17}', 1, "truncated"),
                                         (b'{"StatusCode":true}', 0, "real exit"),
                                         (b'{"StatusCode":17,"Error":{"Message":"failed"}}', 0, "real exit")):
            response = Mock(length=length)
            response.read1.side_effect = [payload, b""]
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.fixture.require_exit(Mock(), response, time.monotonic() + 3)

    def test_incarnation_requires_a_complete_bounded_uuid(self):
        for output in (b"", b"x" * 38, b"not-a-uuid\n"):
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, "process incarnation"):
                receive_incarnation(Mock(recv=Mock(return_value=output)), time.monotonic() + 3)
