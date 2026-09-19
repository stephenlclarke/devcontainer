"""Real Unix HTTP init streams and strict, journal-bound E07 observations."""

import json
from pathlib import Path
from socketserver import ThreadingMixIn, UnixStreamServer
import sys
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from attachment_probe import AttachmentFixture, BINARY_INPUT, ERROR_OUTPUT, OUTPUT_PREFIX, OUTPUT_SUFFIX
from case_evidence import contract_observations
from test_exec_probe import frame
import test_guest_fixture as helpers


class Server(ThreadingMixIn, UnixStreamServer):
    # server_close joins every request worker; no orphan daemon threads.
    daemon_threads = False


class Handler(helpers.Handler):
    protocol_version = "HTTP/1.1"

    def dispatch(self):
        server = self.server
        path = urlsplit(self.path).path
        if path.endswith("/create"):
            result = super().dispatch()
            server.guest["HostConfig"] = {"LogConfig": server.log_driver}
            return result
        if path.endswith("/start"):
            if server.attached.is_set():
                status, value = super().dispatch()
                server.started.set()
                return status, value
            return 409, {"message": "start preceded attachment"}
        if path.endswith("/wait"):
            return 200, {"StatusCode": server.exit_code}
        return super().dispatch()

    def respond(self):
        parsed = urlsplit(self.path)
        if not parsed.path.endswith("/attach"):
            self.close_connection = True
            return super().respond()
        server = self.server
        server.routes.append((self.command, self.path))
        live = parse_qs(parsed.query)["stream"] == ["1"]
        self.close_connection = True
        self.connection.settimeout(3)
        if live:
            server.started.clear()
            server.attached.set()
        head = (b"HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n"
                b"Content-Type: application/vnd.docker.raw-stream\r\n\r\n")
        self.connection.sendall(head)
        if not live:
            history = server.history
            if server.duplicate_first and server.first_history and len(history) > len(server.first_history):
                history = server.first_history * 2
            self.connection.sendall(history[:-1] if server.truncate_history else history)
            return
        if not server.started.wait(3):
            raise TimeoutError("start never followed attachment")
        if server.fail_start:
            return
        payload = bytearray(frame(OUTPUT_PREFIX) + frame(ERROR_OUTPUT, 2))
        self.connection.sendall(payload)
        while chunk := self.rfile.read1(65536):
            value = frame(chunk)
            # The fixture's two binary patterns contain no valid multibyte
            # sequence. Model the fake driver independently of the oracle.
            logged = b"".join(bytes((byte,)) if byte < 128 else b"\xef\xbf\xbd" for byte in chunk)
            payload.extend(frame(logged))
            self.connection.sendall(value)
        last = frame(OUTPUT_SUFFIX)
        payload.extend(last)
        self.connection.sendall(last)
        server.history += payload
        if not server.first_history:
            server.first_history = bytes(payload)
        server.guest["State"] = {"Status": "exited", "ExitCode": server.exit_code}
        server.attached.clear()

    do_GET = do_POST = do_DELETE = respond


class AttachmentTests(unittest.TestCase):
    stop = helpers.GuestFixtureTests.stop

    def reopen(self):
        return AttachmentFixture(self.socket, self.owner, self.server.image, "1.54", self.journal)

    def setUp(self):
        self.errors = []
        self.addCleanup(self.assertEqual, self.errors, [])
        with patch.object(helpers, "Handler", Handler), patch.object(helpers, "UnixStreamServer", Server):
            helpers.GuestFixtureTests.setUp(self)
        self.server.handle_error = lambda *_: self.errors.append(sys.exc_info()[1])
        self.server.started, self.server.attached = threading.Event(), threading.Event()
        self.server.history, self.server.exit_code, self.server.truncate_history = b"", 17, False
        self.server.log_driver = {"Type": "json-file", "Config": {}}
        self.server.first_history, self.server.duplicate_first = b"", False
        self.events = []
        self.fixture.observe = self.events.append

    def test_real_binary_streams_restart_history_and_exact_cleanup(self):
        contract = Path(__file__).parents[2] / "Tests/Parity/fixtures/E07-init-attachment/contract.json"
        expected = contract_observations(json.loads(contract.read_text())["expected"])
        self.assertEqual(self.fixture.operation(), expected)
        attachments = [event for event in self.events if "/attach?" in event["route"]]
        self.assertEqual(len(attachments), 4)
        self.assertTrue(all(event["durationNS"] > 0 and event["status"] == 101 for event in attachments))
        self.assertEqual(sum(path.endswith("/start") for _, path in self.server.routes), 2)
        self.assertIn("attachment", json.loads(self.journal.records()["container-intent.json"]))
        self.assertEqual(self.fixture.cleanup(), {"status": "passed", "remainingOwnedResources": []})
        self.assertEqual(self.reopen().cleanup()["status"], "passed")

    def test_malformed_history_cannot_satisfy_observations(self):
        self.server.truncate_history = True
        with self.assertRaisesRegex(ValueError, "Truncated exec stream"):
            self.fixture.operation()
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_replayed_first_generation_cannot_replace_second_history(self):
        self.server.duplicate_first = True
        with self.assertRaisesRegex(ValueError, "history lost, duplicated or relabelled"):
            self.fixture.operation()
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_nonzero_wrong_exit_is_not_normalized(self):
        self.server.exit_code = 18
        with self.assertRaisesRegex(ValueError, "real exit status"):
            self.fixture.operation()
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_changed_stdin_policy_blocks_start_and_cleanup(self):
        self.fixture.create()
        self.server.guest["Config"]["StdinOnce"] = False
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            self.fixture.transfer(history=False, live=True, incoming=BINARY_INPUT)
        self.assertFalse(any(path.endswith("/start") for _, path in self.server.routes))
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            self.fixture.cleanup()

    def test_failed_start_records_failure_and_preserves_owned_cleanup(self):
        self.fixture.create()
        self.server.fail_start = True
        with self.assertRaisesRegex(ValueError, "Attached init start failed"):
            self.fixture.transfer(history=False, live=True)
        self.assertEqual(self.events[-1]["error"], "ValueError")
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_unmatched_logging_driver_is_not_silently_accepted(self):
        self.server.log_driver = {"Type": "local", "Config": {}}
        with self.assertRaisesRegex(ValueError, "pinned json-file driver"):
            self.fixture.operation()
        self.assertFalse(any(path.endswith("/start") for _, path in self.server.routes))
        self.assertEqual(self.fixture.cleanup()["status"], "passed")
