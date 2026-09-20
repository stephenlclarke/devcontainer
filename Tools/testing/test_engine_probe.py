"""Real Unix-socket integration tests of the Docker-independent E01 probe."""

from http.server import BaseHTTPRequestHandler
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
from pathlib import Path
import socket
from socketserver import ThreadingUnixStreamServer
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from engine_probe import engine_negotiation, request


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/v1.24/"):
            self.reply(400, b'{"message":"API version too old"}')
        elif self.path == "/version":
            self.reply(200, self.server.version)
        elif self.path in {"/_ping", "/v1.44/_ping"}:
            self.reply(self.server.ping_status, b"OK")
        elif self.path == "/oversized":
            self.reply(200, b"x" * 65537)
        elif self.path == "/truncated-build":
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.send_header("X-Container-Create-Preflight", "rejected")
            self.end_headers()
            # A complete JSON record is still an incomplete HTTP response.
            self.wfile.write(b'{"stream":"Step 1 complete"}\n')
            self.close_connection = True
        elif self.path in {"/drip-length", "/drip-close", "/drip-headers"}:
            try:
                if self.path == "/drip-headers":
                    for byte in b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n":
                        self.wfile.write(bytes([byte]))
                        time.sleep(0.02)
                else:
                    self.send_response(200)
                    if self.path == "/drip-length":
                        self.send_header("Content-Length", "100")
                    self.end_headers()
                    for _ in range(100):
                        self.wfile.write(b"x")
                        time.sleep(0.02)
            except (BrokenPipeError, ConnectionResetError):
                pass  # The absolute request deadline deliberately closes the peer.
        else:
            self.reply(404, self.server.error_body)

    def do_HEAD(self):
        self.reply(200, b"")

    def do_POST(self):
        if self.path.startswith("/v1.24/"):
            self.reply(400, b'{"message":"API version too old"}')
            return
        self.server.received_body = self.rfile.read(int(self.headers["Content-Length"]))
        self.reply(400, self.server.error_body)

    def reply(self, code, body):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        for name, value in getattr(self.server, "extra_headers", []):
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        # HTTP traffic is asserted below, not printed into unrelated build logs.
        pass


class EngineProbeTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.scratch.cleanup)
        self.socket = Path(self.scratch.name) / "s"
        self.server = ThreadingUnixStreamServer(str(self.socket), Handler)
        self.server.version = b'{"MinAPIVersion":"1.44"}'
        self.server.ping_status = 200
        self.server.error_body = b'{"message":"invalid request"}'
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.assertFalse(self.thread.is_alive())

    def test_all_existing_negotiation_observations_over_real_unix_http(self):
        contract = Path(__file__).parents[2] / "Tests/Parity/fixtures/E01-engine-negotiation/contract.json"
        expected = json.loads(contract.read_text())["expected"]
        expected = {key: str(value).lower() if isinstance(value, bool) else str(value) for key, value in expected.items()}
        self.assertEqual(engine_negotiation(self.socket), expected)
        self.assertEqual(self.server.received_body, b"{")

    def test_bad_ping_is_observed_not_normalized_to_success(self):
        self.server.ping_status = 503
        observed = engine_negotiation(self.socket)
        self.assertEqual(observed["ping"], "false")
        self.assertEqual(observed["api_prefix"], "false")

    def test_trace_records_routes_status_and_duration_without_payloads(self):
        events = []
        engine_negotiation(self.socket, observe=events.append)
        self.assertEqual(len(events), 6)
        self.assertEqual(events[1]["route"], "/version")
        self.assertEqual(events[-1]["status"], 400)
        for event in events:
            self.assertEqual(set(event), {"method", "route", "status", "durationNS"})
            self.assertGreaterEqual(event["durationNS"], 0)

    def test_timeout_trace_identifies_route_without_exception_text(self):
        events = []
        with patch("engine_probe.request", side_effect=[(200, b"OK"), TimeoutError("sensitive detail")]), \
                self.assertRaises(TimeoutError):
            engine_negotiation(self.socket, observe=events.append)
        self.assertEqual(events[-1]["route"], "/version")
        self.assertEqual(events[-1]["error"], "TimeoutError")
        self.assertNotIn("sensitive", json.dumps(events))

    def test_matching_error_status_without_error_envelope_does_not_pass(self):
        self.server.error_body = b'{"other":"not a Docker error"}'
        observed = engine_negotiation(self.socket)
        self.assertEqual(observed["error_envelope"], "false")
        self.assertEqual(observed["malformed_request"], "false")

    def test_bad_version_fails_before_mutating_requests(self):
        self.server.version = b'{"MinAPIVersion":"../unsafe"}'
        with self.assertRaisesRegex(ValueError, "minimum API"):
            engine_negotiation(self.socket)
        self.assertFalse(hasattr(self.server, "received_body"))

    def test_large_reply_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "64 KiB"):
            request(self.socket, "GET", "/oversized")

    def test_truncated_content_length_is_not_a_completed_build_response(self):
        with self.assertRaises(http.client.IncompleteRead):
            request(self.socket, "GET", "/truncated-build")

    def test_completed_response_exposes_headers_without_collapsing_duplicates(self):
        self.server.extra_headers = [("X-Container-Create-Preflight", "rejected"),
                                     ("x-container-create-preflight", "other")]
        headers = []
        self.assertEqual(request(self.socket, "POST", "/containers/create", b"{}", response_headers=headers),
                         (400, self.server.error_body))
        self.assertEqual([(key, value) for key, value in headers if key.lower() == "x-container-create-preflight"],
                         self.server.extra_headers)

    def test_incomplete_or_oversized_response_never_exposes_completion_headers(self):
        for route, error in (("/truncated-build", http.client.IncompleteRead), ("/oversized", ValueError)):
            with self.subTest(route=route):
                headers = []
                with self.assertRaises(error):
                    request(self.socket, "GET", route, response_headers=headers)
                self.assertEqual(headers, [])

    def test_exact_response_bound_accepts_complete_body(self):
        self.assertEqual(request(self.socket, "GET", "/_ping", max_bytes=2), (200, b"OK"))

    def test_missing_endpoint_does_not_start_or_download_anything(self):
        missing = self.socket.with_name("missing")
        with self.assertRaises((FileNotFoundError, ConnectionRefusedError)):
            engine_negotiation(missing)

    def test_nonresponsive_endpoint_times_out(self):
        idle = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(idle.close)
        path = self.socket.with_name("idle")
        idle.bind(str(path))
        idle.listen(1)
        with self.assertRaises(TimeoutError):
            request(path, "GET", "/_ping", timeout=0.02)

    def test_absolute_deadline_closes_four_trickling_workers_before_join(self):
        for route in ("/drip-length", "/drip-close", "/drip-headers"):
            def invoke(_index):
                with self.assertRaises(TimeoutError):
                    request(self.socket, "GET", route, timeout=1, total_timeout=0.08)
            started = time.monotonic()
            with self.subTest(route=route), ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(invoke, range(4)))
            self.assertLess(time.monotonic() - started, 1)

    def test_completed_request_cancels_deadline_and_preserves_body(self):
        self.assertEqual(request(self.socket, "GET", "/_ping", total_timeout=1), (200, b"OK"))

    def test_invalid_total_deadlines_fail_without_connection(self):
        for value in (0, -1, True, 301, float('nan'), '1'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "deadline"):
                request(self.socket, "GET", "/_ping", total_timeout=value)

    def test_timer_before_socket_creation_cannot_start_late_request(self):
        from engine_probe import UnixHTTPConnection
        connection = UnixHTTPConnection(self.socket, 1)
        connection.expired.set()
        with self.assertRaises(TimeoutError):
            connection.connect()
        connection.close()

    def test_expiry_between_flag_check_and_connect_invalidates_socket(self):
        original_connect = socket.socket.connect
        callbacks = []

        def timer(_seconds, callback):
            callbacks.append(callback)
            return Mock()

        def raced_connect(sock, address):
            callbacks[0]()
            return original_connect(sock, address)

        with patch('engine_probe.threading.Timer', side_effect=timer), \
                patch('engine_probe.socket.socket.connect', raced_connect), self.assertRaises(TimeoutError):
            request(self.socket, 'GET', '/drip-close', total_timeout=1)


if __name__ == "__main__":
    unittest.main()
