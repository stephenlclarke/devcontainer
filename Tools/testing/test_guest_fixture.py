"""Exercise guest ownership and archive integration through a real Unix socket."""

import copy
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
from socketserver import UnixStreamServer
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from case_evidence import canonical
from guest_fixture import GuestFixture, OWNER_LABEL
from service_journal import ServiceJournal


class Handler(BaseHTTPRequestHandler):
    def dispatch(self):
        server = self.server
        path = urlsplit(self.path).path.removeprefix("/v1.54")
        server.routes.append((self.command, self.path))
        size = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(size)
        if self.command == "GET" and path.startswith("/images/"):
            return (200, {"Id": server.image}) if server.prepared else (404, {"message": "missing image"})
        if self.command == "GET" and path == "/containers/json":
            return 200, ([{"Id": server.guest["Id"]}] if server.guest else [])
        if self.command == "POST" and path == "/containers/create":
            config = json.loads(body)
            server.guest = {"Id": "b" * 64, "Name": "/" + server.name, "Config": config,
                            "Image": server.image, "State": {"Status": "created"}}
            return 201, {"Id": server.guest["Id"]}
        parts = path.split("/")
        if len(parts) < 3 or not server.guest or parts[2] not in {server.guest["Id"], server.guest["Name"][1:]}:
            return 404, {"message": "missing container"}
        if self.command == "DELETE":
            if not server.ignore_delete:
                server.guest = None
            return 204, b""
        if parts[-1] == "start":
            if server.fail_start:
                return 500, {"message": "start failure"}
            server.guest["State"]["Status"] = "running"
            return 204, b""
        if parts[-1] == "archive":
            if self.command == "PUT":
                server.archive = body
                return 200, b""
            return 200, server.archive
        return 200, server.guest

    def respond(self):
        status, value = self.dispatch()
        body = value if isinstance(value, bytes) else canonical(value)
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_DELETE = respond

    def log_message(self, *_args):
        # Request assertions below replace console logging of response content.
        pass


class GuestFixtureTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.socket = self.root / "s"
        self.server = UnixStreamServer(str(self.socket), Handler)
        self.server.image = "sha256:" + "c" * 64
        self.server.guest, self.server.routes = None, []
        self.server.prepared, self.server.fail_start, self.server.ignore_delete = True, False, False
        self.owner = "a" * 64
        self.journal = ServiceJournal(self.root / "guest.sqlite", {"case": self.owner}, create=True)
        self.fixture = self.reopen()
        self.server.name = self.fixture.name
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.addCleanup(self.stop)

    def reopen(self):
        return GuestFixture(self.socket, self.owner, self.server.image, "1.54", self.journal)

    def stop(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.assertFalse(self.thread.is_alive())

    def test_create_start_archive_remove_and_repeated_cleanup(self):
        self.fixture.setup()
        observed = self.fixture.archive()
        self.assertEqual(observed, {"content": "true", "large_file": "true", "long_path": "true",
                                    "mode": "0o750", "symlink": "true"})
        self.assertEqual(self.fixture.cleanup(), {"status": "passed", "remainingOwnedResources": []})
        self.assertEqual(self.reopen().cleanup()["status"], "passed")
        self.assertEqual(sum(method == "DELETE" for method, _ in self.server.routes), 1)
        records = self.journal.records()
        self.assertEqual(json.loads(records["container-created.json"]), {"id": "b" * 64})
        self.assertIn("container-removed.json", records)
        self.assertIsNone(self.server.guest)

    def test_invalid_input_does_not_touch_socket_or_journal(self):
        for owner, image, version in (("bad", self.server.image, "1.54"),
                                      (self.owner, "alpine:latest", "1.54"),
                                      (self.owner, self.server.image, "../escape")):
            with self.subTest(image=image), self.assertRaises(ValueError):
                GuestFixture(self.socket, owner, image, version, self.journal)
        self.assertEqual(self.server.routes, [])

    def test_aliased_socket_and_cross_endpoint_recovery_are_rejected(self):
        link = self.root / "link"
        link.symlink_to(self.socket)
        with self.assertRaises(ValueError):
            GuestFixture(link, self.owner, self.server.image, "1.54", self.journal)
        self.fixture.setup()
        for socket, version in ((self.socket.with_name("other"), "1.54"), (self.socket, "1.53")):
            other = GuestFixture(socket, self.owner, self.server.image, version, self.journal)
            with self.subTest(socket=socket), self.assertRaisesRegex(ValueError, "another fixture"):
                other.cleanup()

    def test_network_configuration_is_snapshotted_and_invalid_options_rejected(self):
        mount = {"Type": "tmpfs", "Target": "/scratch"}
        guest = GuestFixture(self.socket, self.owner, self.server.image, "1.54", self.journal,
                             network="fixture-net", mounts=(mount,), aliases=("app",))
        mount["Target"] = "/changed-after-admission"
        self.assertEqual(guest.intent["networkMounts"]["mounts"][0]["Target"], "/scratch")
        for options in ({"network": "../unsafe"}, {"mounts": ({"Type": "tmpfs"}, "bad")},
                        {"aliases": ("../other",)}, {"mounts": []}, {"aliases": []}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "configuration"):
                GuestFixture(self.socket, self.owner, self.server.image, "1.54", self.journal, **options)
        self.assertEqual(self.server.routes, [])

    def test_unprepared_image_does_not_pull_or_register_creation(self):
        self.server.prepared = False
        with self.assertRaisesRegex(ValueError, "no implicit pull"):
            self.fixture.setup()
        self.assertNotIn("container-intent.json", self.journal.records())
        self.assertEqual(self.fixture.cleanup()["status"], "passed")
        self.assertEqual(len(self.server.routes), 1)

    def test_existing_name_is_not_adopted_or_deleted(self):
        self.fixture.setup()
        original = copy.deepcopy(self.server.guest)
        different = ServiceJournal(self.root / "other.sqlite", {"case": "other"}, create=True)
        fixture = GuestFixture(self.socket, self.owner, self.server.image, "1.54", different)
        with self.assertRaisesRegex(ValueError, "already exists"):
            fixture.setup()
        fixture.cleanup()
        self.assertEqual(self.server.guest, original)
        self.assertNotIn("container-intent.json", different.records())

    def test_journal_failure_prevents_creation(self):
        with patch.object(self.journal, "put", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.fixture.setup()
        self.assertFalse(any(method == "POST" for method, _ in self.server.routes))

    def test_lost_create_response_reconciles_observed_exact_owned_container(self):
        original = self.fixture.call
        def lose_response(method, route, body=None):
            result = original(method, route, body)
            if route.startswith("/containers/create"):
                raise TimeoutError()
            return result
        with patch.object(self.fixture, "call", side_effect=lose_response), self.assertRaises(TimeoutError):
            self.fixture.setup()
        self.assertNotIn("container-created.json", self.journal.records())
        with self.assertRaisesRegex(ValueError, "reconcile"):
            self.reopen().setup()
        self.assertEqual(self.reopen().cleanup()["status"], "passed")
        self.assertIsNone(self.server.guest)

    def test_unobserved_pending_create_is_not_mistaken_for_successful_cleanup(self):
        self.journal.put("container-intent.json", canonical(self.fixture.intent))
        with self.assertRaisesRegex(ValueError, "Uncertain creation"):
            self.fixture.cleanup()
        self.assertNotIn("container-removed.json", self.journal.records())

    def test_lost_create_and_delete_responses_recover_from_journalled_delete_id(self):
        self.journal.put("container-intent.json", canonical(self.fixture.intent))
        self.journal.put("container-delete-intent.json", canonical({"id": "b" * 64}))
        self.assertEqual(self.fixture.cleanup()["status"], "passed")
        self.assertTrue(any("/" + "b" * 64 + "/json" in route for _, route in self.server.routes))

    def test_failed_start_still_removes_owned_resource(self):
        self.server.fail_start = True
        with self.assertRaisesRegex(ValueError, "start failed"):
            self.fixture.setup()
        self.assertEqual(self.fixture.cleanup()["status"], "passed")

    def test_changed_labels_image_name_or_id_never_authorise_deletion(self):
        self.fixture.setup()
        original = copy.deepcopy(self.server.guest)
        for kind in ("labels", "image", "config-image", "id", "name", "missing-labels"):
            changed = copy.deepcopy(original)
            if kind == "labels":
                changed["Config"]["Labels"][OWNER_LABEL] = "foreign"
            elif kind == "image":
                changed["Image"] = "sha256:" + "d" * 64
            elif kind == "config-image":
                changed["Config"]["Image"] = "alpine:latest"
            elif kind == "id":
                changed["Id"] = "d" * 64
            elif kind == "name":
                changed["Name"] = "/renamed"
            else:
                changed["Config"]["Labels"] = None
            self.server.guest = changed
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.fixture.cleanup()
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))

    def test_unknown_removal_outcome_resumes_by_id_without_repeating_delete(self):
        self.fixture.setup()
        original = self.fixture.call
        def lose_response(method, route, body=None):
            result = original(method, route, body)
            if method == "DELETE":
                raise TimeoutError()
            return result
        with patch.object(self.fixture, "call", side_effect=lose_response), self.assertRaises(TimeoutError):
            self.fixture.cleanup()
        self.assertEqual(self.reopen().cleanup()["status"], "passed")
        self.assertEqual(sum(method == "DELETE" for method, _ in self.server.routes), 1)

    def test_delete_success_with_remaining_resource_is_failure(self):
        self.fixture.setup()
        self.server.ignore_delete = True
        with self.assertRaisesRegex(ValueError, "remains after deletion"):
            self.fixture.cleanup()
        self.assertNotIn("container-removed.json", self.journal.records())

    def test_archive_requires_stable_running_owned_guest(self):
        with self.assertRaisesRegex(ValueError, "successfully started"):
            self.fixture.archive()
        self.fixture.setup()
        self.server.guest["State"]["Status"] = "exited"
        with self.assertRaisesRegex(ValueError, "identity/state"):
            self.fixture.archive()

    def test_foreign_journal_and_disagreeing_resource_ids_are_rejected(self):
        self.fixture.setup()
        other = GuestFixture(self.socket, "d" * 64, self.server.image, "1.54", self.journal)
        with self.assertRaisesRegex(ValueError, "another fixture"):
            other.cleanup()
        self.journal.put("container-delete-intent.json", canonical({"id": "d" * 64}))
        with self.assertRaisesRegex(ValueError, "identities disagree"):
            self.fixture.cleanup()

    def test_reappearing_resource_and_labeled_residue_block_cleanup(self):
        self.fixture.setup()
        original = copy.deepcopy(self.server.guest)
        self.fixture.cleanup()
        self.server.guest = original
        with self.assertRaisesRegex(ValueError, "reappeared"):
            self.fixture.cleanup()
        self.server.guest["Id"], self.server.guest["Name"] = "d" * 64, "/hidden"
        with self.assertRaisesRegex(ValueError, "residue"):
            self.fixture.cleanup()


if __name__ == "__main__":
    unittest.main()
