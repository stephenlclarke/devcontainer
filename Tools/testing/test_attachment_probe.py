"""Real Unix HTTP init streams and strict, journal-bound E07 observations."""

import json
from pathlib import Path
import queue
import selectors
import socket
from socketserver import ThreadingMixIn, UnixStreamServer
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, Mock, patch
from urllib.parse import parse_qs, urlsplit

from attachment_probe import (AttachmentFixture, BINARY_INPUT, ERROR_OUTPUT, OUTPUT_PREFIX, OUTPUT_SUFFIX,
                              _history_output_limit, observed_duplex, started_output)
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
                server.starts += 1
                server.started.set()
                if server.truncate_history_after_starts == server.starts:
                    server.truncate_history = True
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
        options = parse_qs(parsed.query)
        live = options["stream"] == ["1"]
        observer = live and options["logs"] == ["1"]
        self.close_connection = True
        self.connection.settimeout(3)
        if live and not observer:
            server.started.clear()
            server.attached.set()
        head = (b"HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n"
                b"Content-Type: application/vnd.docker.raw-stream\r\n\r\n")
        if observer:
            chunks = queue.Queue()
            # Transport readiness must not imply that saved-log replay is ready.
            self.connection.sendall(head)
            with server.output_lock:
                history = bytes(server.history) + bytes(server.running_history)
                server.observers.append(chunks)
            self.connection.sendall(history)
            while (chunk := chunks.get(timeout=3)) is not None:
                self.connection.sendall(chunk)
            if server.duplicate_combined:
                self.connection.sendall(frame(OUTPUT_PREFIX))
            return
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
        with server.output_lock:
            server.running_history = bytes(payload)
        self.connection.sendall(payload)
        while chunk := self.rfile.read1(65536):
            value = frame(chunk)
            # The fixture's two binary patterns contain no valid multibyte
            # sequence. Model the fake driver independently of the oracle.
            logged = b"".join(bytes((byte,)) if byte < 128 else b"\xef\xbf\xbd" for byte in chunk)
            payload.extend(frame(logged))
            self.connection.sendall(value)
            with server.output_lock:
                server.running_history = bytes(payload)
                for observer in server.observers:
                    observer.put(value)
        last = frame(OUTPUT_SUFFIX)
        payload.extend(last)
        self.connection.sendall(last)
        with server.output_lock:
            for observer in server.observers:
                observer.put(last)
                observer.put(None)
            server.observers.clear()
            server.running_history = b""
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
        self.server.output_lock = threading.Lock()
        self.server.observers, self.server.running_history = [], b""
        self.server.duplicate_combined = False
        self.server.log_driver = {"Type": "json-file", "Config": {}}
        self.server.first_history, self.server.duplicate_first = b"", False
        self.server.starts, self.server.truncate_history_after_starts = 0, None
        self.events = []
        self.fixture.observe = self.events.append

    def test_real_binary_streams_restart_history_and_exact_cleanup(self):
        contract = Path(__file__).parents[2] / "Tests/Parity/fixtures/E07-init-attachment/contract.json"
        expected = contract_observations(json.loads(contract.read_text())["expected"])
        self.assertEqual(self.fixture.operation(), expected)
        attachments = [event for event in self.events if "/attach?" in event["route"]]
        self.assertEqual(len(attachments), 5)
        self.assertTrue(all(event["durationNS"] > 0 and event["status"] == 101 for event in attachments))
        self.assertTrue(all(event["stage"] == "complete" for event in attachments))
        combined = next(event["stream"] for event in attachments if "stdin=0" in event["route"])
        self.assertEqual(combined["inputAcceptedBytes"], len(BINARY_INPUT))
        self.assertTrue(all(combined[key] for key in ("inputHalfClosed", "observerInputHalfClosed",
                                                      "primaryEOF", "observerEOF")))
        self.assertGreater(combined["primaryWireBytes"], len(BINARY_INPUT))
        self.assertGreater(combined["observerWireBytes"], len(BINARY_INPUT))
        self.assertEqual(sum(path.endswith("/start") for _, path in self.server.routes), 2)
        self.assertIn("attachment", json.loads(self.journal.records()["container-intent.json"]))
        self.assertIn("init-combined-attachment.json", self.journal.records())
        self.assertEqual(self.fixture.cleanup(), {"status": "passed", "remainingOwnedResources": []})
        self.assertEqual(self.reopen().cleanup()["status"], "passed")

    def test_eight_generation_diagnostic_checks_every_restart_and_retains_each_summary(self):
        fixture = AttachmentFixture(self.socket, self.owner, self.server.image, "1.54", self.journal,
                                    generation_count=8, observe=self.events.append)
        contract = Path(__file__).parents[2] / "Tests/Parity/fixtures/E07-init-attachment/contract.json"
        expected = contract_observations(json.loads(contract.read_text())["expected"])
        self.assertEqual(fixture.operation(), expected)
        attachments = [event for event in self.events if "/attach?" in event["route"]]
        self.assertEqual(len(attachments), 17)
        self.assertEqual(self.server.starts, 8)
        self.assertEqual({event["generation"] for event in attachments}, set(range(1, 9)))
        self.assertEqual(sum("logs=1&stream=1&stdin=0" in event["route"] for event in attachments), 1)
        records = self.journal.records()
        self.assertEqual(json.loads(records["init-generations.json"]), {
            "count": 8, "diagnosticOnly": True, "perTransferDeadlineSeconds": 30})
        for generation in range(1, 9):
            record = json.loads(records[f"init-generation-{generation}.json"])
            self.assertEqual(record["generation"], generation)
            self.assertEqual(record["inputBytes"], len(BINARY_INPUT))
            self.assertGreater(record["durationNS"], 0)
            self.assertIn(f"init-history-{generation}.json", records)
        self.assertEqual(fixture.cleanup(), {"status": "passed", "remainingOwnedResources": []})

    def test_eight_generation_diagnostic_stops_at_first_failed_history_check(self):
        self.server.truncate_history_after_starts = 3
        fixture = AttachmentFixture(self.socket, self.owner, self.server.image, "1.54", self.journal,
                                    generation_count=8)
        with self.assertRaisesRegex(ValueError, "Truncated exec stream"):
            fixture.operation()
        self.assertEqual(self.server.starts, 3)
        records = self.journal.records()
        self.assertIn("init-generation-1.json", records)
        self.assertIn("init-generation-2.json", records)
        self.assertNotIn("init-generation-3.json", records)
        self.assertNotIn("init-history-4.json", records)
        self.assertEqual(fixture.cleanup()["status"], "passed")

    def test_attachment_generation_count_is_finite(self):
        for value in (0, 3, 9, True):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "only 2 or 8"):
                AttachmentFixture(self.socket, self.owner, self.server.image, "1.54", self.journal,
                                  generation_count=value)

    def test_history_output_bound_preserves_default_and_is_expected_size_bounded(self):
        history = (b"stdout" * 4, b"stderr" * 3)
        self.assertEqual(_history_output_limit(history, 2), 40 * 1024**2)
        self.assertEqual(_history_output_limit(history, 8), 40 * 1024**2 + sum(map(len, history)))
        with self.assertRaisesRegex(ValueError, "only 2 or 8"):
            _history_output_limit(history, 9)

    def test_expanded_output_bound_is_rejected_for_live_or_unselected_history(self):
        fixture = object.__new__(AttachmentFixture)
        fixture.generation_count = 8
        expanded = 40 * 1024**2 + 1
        with self.assertRaisesRegex(ValueError, "limited to eight-generation history"):
            fixture.transfer(history=False, live=True, incoming=BINARY_INPUT,
                             generation=3, output_limit=expanded)
        with self.assertRaisesRegex(ValueError, "limited to eight-generation history"):
            fixture.transfer(history=True, live=False, generation=None, output_limit=expanded)

    def test_malformed_history_cannot_satisfy_observations(self):
        self.server.truncate_history = True
        with self.assertRaisesRegex(ValueError, "Truncated exec stream"):
            self.fixture.operation()
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_duplicated_saved_prefix_cannot_satisfy_combined_attachment(self):
        self.server.duplicate_combined = True
        with self.assertRaisesRegex(ValueError, "Combined history/live output"):
            self.fixture.operation()
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_startup_markers_accept_split_headers_and_reject_missing_or_changed_bytes(self):
        payload = frame(OUTPUT_PREFIX) + frame(ERROR_OUTPUT, 2)
        connection = Mock()
        connection.recv.side_effect = [bytes((byte,)) for byte in payload]
        self.assertEqual(started_output(connection, b"", time.monotonic() + 3), payload)
        for initial, chunks, message in (
                (b"", [b""], "before both startup"),
                (frame(b"wrong"), [], "markers differ"),
                (b"\x03\0\0\0\0\0\0\0", [], "Malformed"),
                (b"\x01\0\0\0\0\x01\0\0", [], "frame exceeds"),
                (b"x" * 4097, [], "output exceeds")):
            connection.recv.side_effect = chunks
            deadline = time.monotonic() + 3
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                started_output(connection, initial, deadline)

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


class AttachmentIOTests(unittest.TestCase):
    def test_observer_waits_for_saved_markers_after_headers(self):
        fixture = object.__new__(AttachmentFixture)
        fixture.identifier, fixture.version, fixture.socket = "b" * 64, "1.54", Path("unused")
        fixture.inspect = Mock(return_value={"State": {"Status": "running"}})
        fixture.owned = Mock(return_value=fixture.identifier)
        fixture.journal, fixture.observe = Mock(), None
        observer, primary = MagicMock(), Mock()
        observer.__enter__.return_value = observer
        markers = frame(OUTPUT_PREFIX) + frame(ERROR_OUTPUT, 2)
        observer.recv.side_effect = [markers[:5], markers[5:]]

        def transfer(_primary, _initial, _observer, history, _incoming, _end, **kwargs):
            self.assertEqual(history, markers, "input began before saved startup records arrived")
            self.assertEqual(observer.recv.call_count, 2)
            return ((OUTPUT_PREFIX, ERROR_OUTPUT),) * 2

        with patch("attachment_probe.socket.socket", return_value=observer), \
                patch("attachment_probe.upgrade", return_value=(101, b"")), \
                patch("attachment_probe.observed_duplex", side_effect=transfer):
            self.assertEqual(fixture.observe_running(primary, markers, b"binary", time.monotonic() + 3),
                             (OUTPUT_PREFIX, ERROR_OUTPUT))

    def pair(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        return left, right

    def test_output_only_peers_half_close_and_drain_both_sources(self):
        primary, writer = self.pair()
        observer, replay = self.pair()
        for connection in (writer, replay):
            connection.sendall(frame(b"out") + frame(b"err", 2))
            connection.shutdown(socket.SHUT_WR)
        self.assertEqual(observed_duplex(primary, b"", observer, b"", b"", time.monotonic() + 3),
                         ((b"out", b"err"), (b"out", b"err")))
        self.assertEqual((writer.recv(1), replay.recv(1)), (b"", b""))

    def test_startup_timeout_retains_only_wire_progress(self):
        connection, progress = Mock(), {}
        connection.recv.side_effect = [frame(OUTPUT_PREFIX), TimeoutError("no stderr")]
        deadline = time.monotonic() + 3
        with self.assertRaises(TimeoutError):
            started_output(connection, b"", deadline, progress=progress)
        self.assertEqual(progress, {"outputWireBytes": len(frame(OUTPUT_PREFIX)), "outputEOF": False})

    def test_duplex_timeout_distinguishes_input_and_each_output(self):
        primary, writer = self.pair()
        observer, replay = self.pair()
        markers = frame(OUTPUT_PREFIX) + frame(ERROR_OUTPUT, 2)
        progress = {}
        selector = MagicMock()
        selector.__enter__.return_value = selector
        selector.get_map.return_value = {1: object()}
        selector.select.return_value = [(Mock(fileobj=primary), selectors.EVENT_WRITE)]
        writer.settimeout(1)
        # Expire after exactly one write instead of depending on host scheduling.
        deadline = time.monotonic() + 3
        with patch("attachment_probe.selectors.DefaultSelector", return_value=selector), \
                patch("attachment_probe.remaining", side_effect=[3, TimeoutError("deadline")]), \
                self.assertRaises(TimeoutError):
            observed_duplex(primary, markers, observer, markers, b"payload", deadline,
                            progress=progress)
        self.assertEqual(writer.recv(8), b"payload")
        self.assertEqual(progress, {"inputAcceptedBytes": 7, "inputHalfClosed": True,
                                   "observerInputHalfClosed": True, "primaryWireBytes": len(markers),
                                   "observerWireBytes": len(markers), "primaryEOF": False, "observerEOF": False})

    def test_observer_history_failure_retains_stage_without_starting_input(self):
        fixture = object.__new__(AttachmentFixture)
        fixture.identifier, fixture.version, fixture.socket = "b" * 64, "1.54", Path("unused")
        fixture.inspect = Mock(return_value={"State": {"Status": "running"}})
        fixture.owned = Mock(return_value=fixture.identifier)
        events = []
        fixture.journal, fixture.observe = Mock(), events.append
        observer = MagicMock()
        observer.__enter__.return_value = observer
        observer.recv.side_effect = TimeoutError("no history")
        mock_value = Mock()
        deadline = time.monotonic() + 3
        with patch("attachment_probe.socket.socket", return_value=observer), \
                patch("attachment_probe.upgrade", return_value=(101, b"")), \
                patch("attachment_probe.observed_duplex") as transfer, self.assertRaises(TimeoutError):
            fixture.observe_running(mock_value, b"", b"payload", deadline)
        transfer.assert_not_called()
        self.assertEqual(events[0]["stage"], "observer-startup-history")
        self.assertEqual(events[0]["stream"], {"outputWireBytes": 0, "outputEOF": False})
        self.assertEqual(events[0]["error"], "TimeoutError")

    def test_incomplete_input_output_limit_and_deadline_are_failures(self):
        for kind, message in (("input", "before input"), ("limit", "output exceeds"),
                              ("deadline", "whole-connection deadline")):
            with self.subTest(kind=kind):
                primary, writer = self.pair()
                observer, replay = self.pair()
                if kind == "limit":
                    writer.sendall(frame(b"too much"))
                writer.shutdown(socket.SHUT_WR)
                replay.shutdown(socket.SHUT_WR)
                deadline = time.monotonic() + (3 if kind != "deadline" else -1)
                with patch("attachment_probe.MAX_OUTPUT", 1), \
                        self.assertRaisesRegex((ValueError, TimeoutError), message):
                    observed_duplex(primary, b"", observer, b"", b"pending" if kind == "input" else b"",
                                    deadline)

    def test_zero_length_write_fails_instead_of_spinning(self):
        primary, observer = Mock(), Mock()
        primary.send.return_value = 0
        selector = MagicMock()
        selector.__enter__.return_value = selector
        selector.get_map.return_value = {1: object()}
        selector.select.return_value = [(Mock(fileobj=primary), selectors.EVENT_WRITE)]
        deadline = time.monotonic() + 3
        with patch("attachment_probe.selectors.DefaultSelector", return_value=selector), \
                self.assertRaisesRegex(ValueError, "stopped accepting input"):
            observed_duplex(primary, b"", observer, b"", b"pending", deadline)
