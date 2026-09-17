"""Real Unix-socket integration tests of the Docker-independent E01 probe."""

from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import socket
from socketserver import UnixStreamServer
import tempfile
import threading
import unittest
from unittest.mock import patch

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
        self.server = UnixStreamServer(str(self.socket), Handler)
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


if __name__ == "__main__":
    unittest.main()
