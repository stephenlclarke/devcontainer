"""D06 metadata, published HTTP, collision phase and resource ownership."""

import copy
import json
import os
from pathlib import Path
from socketserver import BaseRequestHandler, TCPServer, UnixStreamServer
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from case_evidence import canonical
from devcontainer_candidate import DevcontainerPortsCandidate
from devcontainer_ports_reference import (BINDINGS, FORWARD, IMAGE, DevcontainerPortsReference,
                                         PortCollision, fixture_inputs, host_http)
import devcontainer_ports_reference as ports
from guest_runtime import require_guest_cleanup
from service_journal import ServiceJournal
import test_devcontainer_reference as reference_tests
import test_guest_fixture as guest_tests
import test_devcontainer_candidate as candidate_tests


class PortsReferenceTests(reference_tests.ReferenceTests):
    def reopen(self):
        self.inputs["devcontainerFixture"] = fixture_inputs(Path(__file__).parents[2])
        return DevcontainerPortsReference(self.vm, self.inputs, self.owner)

    def setUp(self):
        super().setUp()
        for name in ("require_free_port", "host_http", "PortCollision"):
            mock = self.enter_patch("devcontainer_ports_reference." + name)
            setattr(self, name, mock)

    def enter_patch(self, name):
        manager = patch(name)
        value = manager.start()
        self.addCleanup(manager.stop)
        return value

    def guest(self):
        value = super().guest()
        value["Config"]["Image"] = IMAGE
        value["HostConfig"] = {"PortBindings": copy.deepcopy(BINDINGS)}
        return value

    def command(self, name, arguments, **kwargs):
        result = super().command(name, arguments, **kwargs)
        if name == "devcontainer-up":
            return canonical({**json.loads(result), "configuration": FORWARD})
        return b"forward_metadata=true\ninside_connectivity=true\n" if name == "devcontainer-exec" else result

    def test_full_contract_uses_exact_pins_isolated_paths_and_verified_cleanup(self):
        self.assertEqual(self.start(), {key: "true" for key in
                                       ("forward_metadata", "inside_connectivity", "host_connectivity", "collision_rejected")})
        self.assertEqual(self.commands[0][1][-1], IMAGE)
        self.assertEqual(self.commands[1][1].count("--include-configuration"), 1)
        self.assertNotIn("--include-configuration", self.commands[2][1])
        self.require_free_port.assert_called_once_with()
        self.assertEqual(self.host_http.call_count, 2)
        self.PortCollision.return_value.reject.assert_called_once_with()
        self.fixture.cleanup()
        self.PortCollision.return_value.cleanup.assert_called_once_with()
        self.assertIsNone(self.server.guest)

    def test_fake_probe_metadata_cannot_replace_actual_cli_metadata(self):
        for value in (None, {}, {**FORWARD, "forwardPorts": []}, {**FORWARD, "portsAttributes": {}}):
            with self.subTest(config=value), self.assertRaisesRegex(ValueError, "forward metadata"):
                self.fixture.up_identity(canonical({"outcome": "success", "containerId": reference_tests.ID,
                                                    "configuration": value}))

    def test_foreign_host_listener_blocks_before_workspace_or_cli_mutation(self):
        self.require_free_port.side_effect = OSError("address in use")
        with self.assertRaises(OSError):
            self.fixture.setup()
        self.assertFalse(self.fixture.workspace.exists())
        self.assertEqual(self.commands, [])

    def test_changed_binding_prevents_mutation(self):
        self.start()
        self.server.guest["HostConfig"]["PortBindings"] = {}
        with self.assertRaisesRegex(ValueError, "port binding"):
            self.fixture.cleanup()
        self.assertIsNotNone(self.server.guest)

    def test_collision_cleanup_precedes_primary_cleanup_and_failure_stops_it(self):
        self.start()
        self.PortCollision.return_value.cleanup.side_effect = ValueError("collision ownership")
        with self.assertRaisesRegex(ValueError, "collision ownership"):
            self.fixture.cleanup()
        self.assertIsNotNone(self.server.guest)
        self.assertNotIn("devcontainer-removed.json", self.vm.journal.records())

class PortsCandidateTests(PortsReferenceTests):
    test_slow_cleanup_response_obeys_whole_phase_deadline = candidate_tests.CandidateTests.test_slow_cleanup_response_obeys_whole_phase_deadline
    test_native_uuid_is_preserved_not_normalized = candidate_tests.CandidateTests.test_native_uuid_is_preserved_not_normalized

    def reopen(self):
        super().reopen()
        self.vm.container = "/prepared/container"
        self.vm.close, self.vm.prepare_cleanup = Mock(), Mock()
        self.inputs["devcontainerCandidate"] = {"executables": {"devcontainer": "/prepared/devcontainer"}}
        return DevcontainerPortsCandidate(self.vm, self.inputs, self.owner)


class HTTPHandler(BaseRequestHandler):
    def handle(self):
        self.request.settimeout(1)
        self.server.request = self.request.recv(4096)
        try:
            for data, delay in self.server.chunks:
                self.request.sendall(data)
                time.sleep(delay)
        except (BrokenPipeError, ConnectionResetError):
            pass  # The tested deadline deliberately closes this owned socket.


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = TCPServer(("127.0.0.1", 0), HTTPHandler)
        self.server.chunks = []
        manager = patch.object(ports, "HOST_PORT", self.server.server_address[1])
        manager.start()
        self.addCleanup(manager.stop)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.server.shutdown)

    def response(self, data):
        self.server.chunks = [(data, 0)]
        host_http(timeout=0.5)

    def test_exact_response_succeeds_without_ambient_proxy(self):
        with patch.dict(os.environ, {"HTTP_PROXY": "http://invalid.invalid:1"}):
            self.response(b"HTTP/1.0 200 OK\r\nContent-Length: 18\r\n\r\ndevcontainer-port\n")

    def test_wrong_status_body_and_incomplete_or_ambiguous_framing_fail(self):
        for headers, body in (
                (b"200 OK\r\nContent-Length: 19", b"devcontainer-port\n"),
                (b"302 Found\r\nContent-Length: 18", b"devcontainer-port\n"),
                (b"200 OK\r\nContent-Length: 18", b"not-the-right-body"),
                (b"200 OK", b"devcontainer-port\n"),
                (b"200 OK\r\nContent-Length: 18\r\nContent-Length: 18", b"devcontainer-port\n"),
                (b"200 OK\r\nContent-Length: 18\r\nTransfer-Encoding: chunked", b"devcontainer-port\n"),
                (b"200 OK\r\nContent-Length: 18", b"devcontainer-port\nextra")):
            with self.subTest(headers=headers, body=body), self.assertRaisesRegex(ValueError, "HTTP content"):
                self.response(b"HTTP/1.0 " + headers + b"\r\n\r\n" + body)

    def test_dripping_header_and_body_cannot_reset_total_deadline(self):
        for prefix in (b"HTTP/1.0 200 OK\r\nX-Slow: ", b"HTTP/1.0 200 OK\r\nContent-Length: 18\r\n\r\n"):
            self.server.chunks = [(prefix, 0)] + [(b"x", 0.04)] * 20
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                host_http(timeout=0.15)
            self.assertLess(time.monotonic() - started, 0.5)

    def test_oversized_header_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "byte bound"):
            self.response(b"HTTP/1.0 200 OK\r\nX-Large: " + b"x" * 8192)

    def test_unrelated_listener_blocks_preflight_without_stopping_it(self):
        with self.assertRaises(OSError):
            ports.require_free_port()
        self.test_exact_response_succeeds_without_ambient_proxy()


class CollisionHandler(guest_tests.Handler):
    def dispatch(self):
        status, result = super().dispatch()
        if self.server.guest is not None:
            self.server.guest["HostConfig"] = self.server.guest["Config"]["HostConfig"]
            self.server.guest["State"]["Running"] = False
        if self.path.endswith("/start"):
            return self.server.collision_status, {"message": self.server.collision_message}
        return status, result


class CollisionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        journal = ServiceJournal(self.root / "journal.sqlite", {"case": "collision"}, create=True)
        client = SimpleNamespace(socket=self.root / "s", owner="a" * 64, version="1.54", journal=journal,
                                 observe=None, id_pattern=r"[0-9a-f]{64}")
        self.image = "sha256:" + "c" * 64
        self.fixture = PortCollision(client, self.image)
        self.journal = journal
        self.server = UnixStreamServer(str(client.socket), CollisionHandler)
        self.server.guest, self.server.routes = None, []
        self.server.image, self.server.name = self.image, self.fixture.name
        self.server.prepared, self.server.ignore_delete, self.server.fail_start = True, False, True
        self.server.collision_status = 500
        self.server.collision_message = "Bind for 127.0.0.1:49277 failed: port is already allocated"
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.server.shutdown)

    def test_real_socket_collision_is_owned_stopped_and_cleaned(self):
        self.fixture.reject()
        self.assertEqual(self.server.guest["HostConfig"]["PortBindings"], BINDINGS)
        with self.assertRaisesRegex(ValueError, "collision resource"):
            require_guest_cleanup(self.journal.records())
        self.fixture.cleanup()
        require_guest_cleanup(self.journal.records())
        self.assertIsNone(self.server.guest)
        self.assertIn("d06-collision-start-result.json", self.journal.records())

    def test_unrelated_error_is_not_a_port_collision(self):
        self.server.collision_message = "kernel unavailable"
        with self.assertRaisesRegex(ValueError, "port-allocation"):
            self.fixture.reject()
        self.fixture.cleanup()

    def test_wrong_status_is_not_a_port_collision(self):
        self.server.collision_status = 409
        with self.assertRaisesRegex(ValueError, "port-allocation"):
            self.fixture.reject()
        self.fixture.cleanup()

    def test_changed_collision_binding_is_never_deleted(self):
        self.fixture.reject()
        self.server.guest["HostConfig"]["PortBindings"] = {}
        with self.assertRaisesRegex(ValueError, "port ownership"):
            self.fixture.cleanup()
        self.assertIsNotNone(self.server.guest)


if __name__ == "__main__":
    unittest.main()
