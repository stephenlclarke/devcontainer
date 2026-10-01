"""D01 ownership and CLI contract tests; real Unix HTTP, no Docker or Node."""

from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
from socketserver import UnixStreamServer
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

from case_evidence import canonical
from devcontainer_reference import DevcontainerReference, IMAGE, WORKSPACE, created_id, fixture_inputs, observations
from guest_fixture import OWNER_LABEL
from service_journal import ServiceJournal
from host_runtime import deadline


ID = "a" * 64
PROBE = b"environment=image-config\nworkspace=/workspaces/devcontainer-parity\npost_create=post-create\nuid=0\n"


class Handler(BaseHTTPRequestHandler):
    def respond(self):
        server = self.server
        route = urlsplit(self.path).path.removeprefix("/v1.53")
        server.routes.append((self.command, self.path))
        guest = server.guest
        if route == "/containers/json":
            status, body = 200, ([{"Id": guest["Id"]}] * server.count if guest else [])
        elif route == "/containers/" + ID + "/json":
            status, body = (200, guest) if guest else (404, {"message": "absent"})
        elif route == "/containers/" + ID and self.command == "DELETE":
            if not server.ignore_delete:
                server.guest = None
            status, body = 204, None
        elif route.startswith("/images/") and route.endswith("/json"):
            body = getattr(server, "images", {}).get(unquote(route[len("/images/"):-len("/json")]))
            status, body = (200, body) if body is not None else (404, {"message": "absent"})
        else:
            status, body = 404, {"message": "absent"}
        payload = canonical(body) if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            try:
                if server.drip:
                    for byte in payload:
                        self.wfile.write(bytes([byte]))
                        time.sleep(0.02)
                else:
                    self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass  # The phase deadline intentionally abandons the slow peer.

    do_GET = do_DELETE = respond

    def log_message(self, *_args):
        pass  # Exact request assertions replace console output.


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "workspace").mkdir()
        self.server = UnixStreamServer(str(self.root / "s"), Handler)
        self.server.guest, self.server.routes = None, []
        self.server.ignore_delete, self.server.count = False, 1
        self.server.drip = False
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.addCleanup(self.stop)
        self.owner = {"identity": "test case", "root": str(self.root)}
        journal = ServiceJournal(self.root / "journal.sqlite", self.owner, create=True)
        self.vm = SimpleNamespace(root=self.root, socket=self.root / "s", journal=journal,
                                  command=self.command, uncertain=False)
        self.inputs = {"tools": {"docker": "/prepared/docker"},
                       "workload": {"image": {"manifest": "sha256:" + "b" * 64, "config": "sha256:" + "c" * 64}},
                       "devcontainers": {"node": "/prepared/node", "cli": "/prepared/devcontainer.js"},
                       "devcontainerFixture": fixture_inputs(Path(__file__).parents[2])}
        self.fixture = self.reopen()
        self.commands = []

    def stop(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def reopen(self):
        return DevcontainerReference(self.vm, self.inputs, self.owner)

    def guest(self):
        return {"Id": ID, "Config": {"Image": IMAGE, "Labels": {OWNER_LABEL: self.fixture.owner}},
                "Image": self.inputs["workload"]["image"]["manifest"],
                "Mounts": [{"Type": "bind", "Source": str(self.fixture.workspace), "Destination": WORKSPACE}]}

    def command(self, name, arguments, *, timeout, separate_output=False):
        self.commands.append((name, arguments, timeout))
        if name in {"devcontainer-up", "devcontainer-exec"} and arguments:
            self.assertTrue(separate_output)
        self.vm.journal.put(name + "-intent.json", canonical({"arguments": arguments, "timeout": timeout}))
        self.vm.journal.put(name + "-exit.json", canonical({"code": 0}))
        if name == "devcontainer-up":
            self.server.guest = self.guest()
            return canonical({"outcome": "success", "containerId": ID})
        return PROBE if name == "devcontainer-exec" else b"image loaded"

    def start(self):
        self.fixture.setup()
        return self.fixture.operation()

    def test_full_contract_uses_exact_pins_isolated_paths_and_verified_cleanup(self):
        self.assertEqual(self.start(), {"environment": "image-config", "workspace": WORKSPACE,
                                       "post_create": "post-create", "uid": "0"})
        self.assertEqual([entry[0] for entry in self.commands],
                         ["devcontainer-image-pull", "devcontainer-up", "devcontainer-exec"])
        self.assertEqual([entry[2] for entry in self.commands], [120, 120, 60])
        self.assertEqual(self.commands[0][1][-1], IMAGE)
        self.assertIn("--log-format", self.commands[1][1])
        self.assertNotIn("--log-format", self.commands[2][1])
        self.assertEqual(self.commands[2][1][-3:], ["--", "/bin/sh", WORKSPACE + "/probe.sh"])
        for _, arguments, _ in self.commands[1:]:
            self.assertEqual(arguments[:4], ["/usr/bin/env", "DOCKER_HOST=unix://" + str(self.vm.socket),
                                             "/prepared/node", "/prepared/devcontainer.js"])
            self.assertIn(OWNER_LABEL + "=" + self.fixture.owner, arguments)
        self.assertEqual((self.fixture.workspace / "probe.sh").read_text(), self.inputs["devcontainerFixture"]["probe"])
        self.assertEqual(self.fixture.recovery_plan(), ID)
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))
        self.fixture.cleanup()
        self.assertIsNone(self.server.guest)
        self.assertIn("devcontainer-removed.json", self.vm.journal.records())
        self.assertTrue(any(method == "DELETE" and ID in route for method, route in self.server.routes))
        self.reopen().cleanup()

    def test_uncertain_process_or_missing_exit_prevents_any_cleanup_request(self):
        self.start()
        self.server.routes.clear()
        self.vm.uncertain = True
        with self.assertRaisesRegex(ValueError, "uncertain"):
            self.fixture.cleanup()
        self.vm.uncertain = False
        self.vm.journal.put("devcontainer-other-intent.json", b"{}")
        # A fresh journal with just up intent represents interruption after spawn.
        records = self.vm.journal.records()
        del records["devcontainer-up-exit.json"]
        original = self.vm.journal.records
        self.vm.journal.records = lambda: records
        self.addCleanup(setattr, self.vm.journal, "records", original)
        with self.assertRaisesRegex(ValueError, "completion"):
            self.fixture.cleanup()
        self.assertEqual(self.server.routes, [])

    def test_replaced_resource_and_ambiguous_inventory_are_never_deleted(self):
        self.start()
        original = self.guest()
        alterations = [dict(original, Image="sha256:" + "d" * 64), dict(original, Mounts=[]),
                       dict(original, Config={"Image": IMAGE, "Labels": {OWNER_LABEL: "foreign"}}),
                       dict(original, Config={"Image": "alpine:latest", "Labels": {OWNER_LABEL: self.fixture.owner}})]
        for changed in alterations:
            with self.subTest(changed=changed):
                self.server.guest = changed
                with self.assertRaises(ValueError):
                    self.fixture.cleanup()
        self.server.guest, self.server.count = original, 2
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.fixture.cleanup()
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))

    def test_live_resource_after_delete_keeps_cleanup_unsealed(self):
        self.start()
        self.server.ignore_delete = True
        with self.assertRaisesRegex(ValueError, "removal"):
            self.fixture.cleanup()
        self.assertNotIn("devcontainer-removed.json", self.vm.journal.records())
        self.server.ignore_delete = False
        self.reopen().cleanup()

    def test_slow_cleanup_response_obeys_whole_phase_deadline(self):
        self.start()
        self.server.drip = True
        def short_bound(seconds):
            self.assertEqual(seconds, 45)
            return deadline(0.08)
        started = time.monotonic()
        with patch("devcontainer_reference.deadline", side_effect=short_bound), self.assertRaises(TimeoutError):
            self.fixture.cleanup()
        self.assertLess(time.monotonic() - started, 1)
        self.assertIsNotNone(self.server.guest)
        self.assertNotIn("devcontainer-removed.json", self.vm.journal.records())

    def test_completed_up_can_reconcile_observed_resource_without_result_receipt(self):
        self.fixture.setup()
        self.command("devcontainer-up", [], timeout=120)
        self.reopen().cleanup()
        self.assertIsNone(self.server.guest)

    def test_unobserved_up_outcome_remains_quarantined_even_when_inventory_empty(self):
        self.fixture.setup()
        self.command("devcontainer-up", [], timeout=120)
        self.server.guest = None
        with self.assertRaisesRegex(ValueError, "no observed"):
            self.reopen().cleanup()

    def test_known_absent_guest_finishes_recovery_without_second_delete(self):
        self.start()
        self.server.guest = None
        self.reopen().cleanup()
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))

    def test_setup_is_not_retried_and_config_pin_drift_is_refused(self):
        self.fixture.setup()
        with self.assertRaisesRegex(ValueError, "already"):
            self.reopen().setup()
        fake = self.root / "repo/Tests/Parity/fixtures/D01-image-config"
        (fake / ".devcontainer").mkdir(parents=True)
        (fake / ".devcontainer/devcontainer.json").write_text('{"image":"alpine:latest"}')
        (fake / "probe.sh").write_text("probe")
        with self.assertRaisesRegex(ValueError, "pin changed"):
            fixture_inputs(self.root / "repo")

    def test_cleanup_without_submission_and_changed_plan(self):
        self.fixture.cleanup()
        self.fixture.setup()
        self.fixture.cleanup()
        self.fixture.plan["owner"] = "foreign"
        with self.assertRaisesRegex(ValueError, "ownership"):
            self.fixture.cleanup()
        with self.assertRaisesRegex(ValueError, "plan identity"):
            self.fixture.operation()

    def test_created_id_requires_one_successful_full_id(self):
        good = canonical({"outcome": "success", "containerId": ID})
        self.assertEqual(created_id(good), ID)
        for payload in (b"", b"[]", good + b"\n" + good, b'{"outcome":"error"}', b'{"outcome":"success","containerId":"abc"}',
                        b'{"unexpected":"record"}', b"not-json", b"{broken"):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                created_id(payload)

    def test_probe_never_normalizes_values_or_accepts_missing_duplicate_extra_records(self):
        self.assertEqual(observations(PROBE.replace(b"uid=0", b"uid=1000"))["uid"], "1000")
        for payload in (b"", b'{"type":"start","text":"exec"}\n' + PROBE,
                        PROBE + b"uid=0\n", PROBE + b"other=value\n", PROBE + b"unexpected\n",
                        PROBE.replace(b"uid=0\n", b"")):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                observations(payload)


if __name__ == "__main__":
    unittest.main()
