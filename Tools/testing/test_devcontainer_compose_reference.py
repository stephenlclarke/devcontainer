"""C01 project ownership and cleanup over a real private Unix socket."""

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from case_evidence import canonical, contract_observations
from devcontainer_compose_reference import (DevcontainerComposeReference, PROJECT_LABEL, SERVICE_LABEL,
                                           NETWORK_LABEL, fixture_inputs)
from devcontainer_reference import WORKSPACE
from guest_runtime import require_guest_resources_stopped
import test_devcontainer_reference as helpers


NETWORK = "d" * 64


class Handler(helpers.Handler):
    def respond(self):
        path = urlsplit(self.path).path.removeprefix("/v1.53")
        server = self.server
        if not path.startswith("/networks"):
            if self.command == "DELETE" and path == "/containers/" + helpers.ID and not server.ignore_delete:
                if server.network:
                    server.network["Containers"].pop(helpers.ID, None)
            return super().respond()
        server.routes.append((self.command, self.path))
        value = server.network
        status, body = 404, {"message": "absent"}
        if path == "/networks":
            status, body = 200, [value] if value else []
        elif value and path.split("/")[-1] in {value["Name"], value["Id"]}:
            status, body = 200, value
            if self.command == "DELETE":
                status, body = 204, None
                if not server.keep_network:
                    server.network = None
        payload = canonical(body) if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    do_GET = do_DELETE = respond


class ComposeTests(unittest.TestCase):
    stop = helpers.ReferenceTests.stop

    def setUp(self):
        with patch.object(helpers, "Handler", Handler):
            helpers.ReferenceTests.setUp(self)
        self.server.network, self.server.keep_network = None, False

    def reopen(self):
        self.inputs["compose"] = {"executables": {"docker-compose": "/prepared/docker-compose"}}
        self.inputs["devcontainerFixture"] = fixture_inputs(Path(__file__).parents[2])
        return DevcontainerComposeReference(self.vm, self.inputs, self.owner)

    def guest(self):
        value = helpers.ReferenceTests.guest(self)
        value["Config"]["Labels"].update({PROJECT_LABEL: self.fixture.project, SERVICE_LABEL: "app"})
        return value

    def command(self, name, arguments, *, timeout, separate_output=False):
        output = helpers.ReferenceTests.command(self, name, arguments, timeout=timeout, separate_output=separate_output)
        if name == "devcontainer-up":
            self.server.network = {"Id": NETWORK, "Name": self.fixture.network_name,
                                   "Labels": {PROJECT_LABEL: self.fixture.project, NETWORK_LABEL: "default"},
                                   "Created": "2026-09-19T07:00:00Z", "Containers": {helpers.ID: {}}}
        if name == "devcontainer-exec":
            return ("compose_env=compose-service\npost_create=compose-post-create\nworkspace=" + WORKSPACE + "\n").encode()
        return output

    def start(self):
        self.fixture.setup()
        return self.fixture.operation()

    def test_original_fixture_and_complete_project_cleanup(self):
        expected = json.loads((Path(__file__).parents[2] / "Tests/Parity/fixtures/C01-compose-service/contract.json").read_text())
        self.assertEqual(self.start(), contract_observations(expected["expected"]))
        self.assertEqual((self.fixture.workspace / "compose.yaml").read_text(), self.inputs["devcontainerFixture"]["compose"])
        for _, arguments, _ in self.commands[1:]:
            self.assertIn("COMPOSE_PROJECT_NAME=" + self.fixture.project, arguments)
            self.assertEqual(arguments[arguments.index("--docker-compose-path") + 1], "/prepared/docker-compose")
        self.fixture.cleanup()
        self.assertIsNone(self.server.guest)
        self.assertIsNone(self.server.network)
        self.reopen().cleanup()

    def test_existing_project_refused_before_commands(self):
        self.server.network = {"Name": self.fixture.network_name}
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.fixture.setup()
        self.assertEqual(self.commands, [])
        self.fixture.cleanup()
        self.assertIsNotNone(self.server.network)

    def test_network_reappearance_after_completed_cleanup_is_not_deleted(self):
        self.start()
        original = copy.deepcopy(self.server.network)
        self.fixture.cleanup()
        original["Containers"] = {}
        self.server.network = original
        count = sum(method == "DELETE" for method, _ in self.server.routes)
        with self.assertRaisesRegex(ValueError, "reappeared"):
            self.reopen().cleanup()
        self.assertIsNotNone(self.server.network)
        self.assertEqual(sum(method == "DELETE" for method, _ in self.server.routes), count)

    def test_replaced_or_foreign_network_never_deleted(self):
        self.start()
        original = copy.deepcopy(self.server.network)
        for field, value in [("Id", "e" * 64), ("Created", "changed"), ("Labels", {}),
                             ("Name", "foreign"), ("Containers", {"foreign": {}})]:
            with self.subTest(field=field):
                self.server.network = {**copy.deepcopy(original), field: value}
                with self.assertRaises(ValueError):
                    self.fixture.cleanup()
                self.assertIsNotNone(self.server.guest)
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))

    def test_service_label_and_extra_project_members_fail_closed(self):
        self.start()
        for key in (PROJECT_LABEL, SERVICE_LABEL):
            value = self.server.guest["Config"]["Labels"][key]
            self.server.guest["Config"]["Labels"][key] = "foreign"
            with self.assertRaises(ValueError):
                self.fixture.cleanup()
            self.server.guest["Config"]["Labels"][key] = value
        self.server.count = 2
        with self.assertRaises(ValueError):
            self.fixture.cleanup()
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))

    def test_unverified_network_removal_cannot_report_project_cleanup(self):
        self.start()
        self.server.keep_network = True
        with self.assertRaisesRegex(ValueError, "removal unverified"):
            self.fixture.cleanup()
        self.assertNotIn("c01-project-removed.json", self.vm.journal.records())
        with self.assertRaisesRegex(ValueError, "C01 project"):
            require_guest_resources_stopped(self.vm.journal.records())

    def test_unfinished_command_cannot_remove_resources(self):
        self.start()
        records = self.vm.journal.records()
        del records["devcontainer-up-exit.json"]
        with patch.object(self.vm.journal, "records", return_value=records):
            with self.assertRaisesRegex(ValueError, "completion is unknown"):
                self.fixture.cleanup()
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))

    def test_missing_network_receipt_or_changed_plan_preserves_resources(self):
        self.start()
        records = self.vm.journal.records()
        del records["c01-network-created.json"]
        with patch.object(self.vm.journal, "records", return_value=records):
            with self.assertRaisesRegex(ValueError, "creation or membership is uncertain"):
                self.fixture.cleanup()
        self.fixture.plan["project"] = "changed"
        with self.assertRaisesRegex(ValueError, "intent changed"):
            self.fixture.cleanup()
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))

    def test_unexpected_network_membership_blocks_success(self):
        self.fixture.setup()
        command = self.vm.command
        def foreign_membership(name, *args, **kwargs):
            result = command(name, *args, **kwargs)
            if name == "devcontainer-up":
                self.server.network["Containers"]["foreign"] = {}
            return result
        with patch.object(self.vm, "command", side_effect=foreign_membership):
            with self.assertRaisesRegex(ValueError, "membership differs"):
                self.fixture.operation()
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))

    def test_completed_failed_probe_preserves_identity_for_cleanup(self):
        self.fixture.setup()
        command = self.vm.command
        def failed_probe(name, *args, **kwargs):
            result = command(name, *args, **kwargs)
            return b"unexpected=output\n" if name == "devcontainer-exec" else result
        with patch.object(self.vm, "command", side_effect=failed_probe):
            with self.assertRaises(ValueError):
                self.fixture.operation()
        self.assertIn("c01-network-created.json", self.vm.journal.records())
        self.fixture.cleanup()
        self.assertIsNone(self.server.guest)
        self.assertIsNone(self.server.network)

    def test_network_malformed_inspection_and_responses_fail_closed(self):
        self.start()
        original = copy.deepcopy(self.server.network)
        for field, value in (("Id", "../escape"), ("Created", ""), ("Containers", [])):
            with self.subTest(field=field):
                self.server.network = {**copy.deepcopy(original), field: value}
                with self.assertRaises(ValueError):
                    self.fixture.cleanup()
        with patch.object(self.fixture, "call", return_value=(500, b'{"message":"unavailable"}')):
            with self.assertRaisesRegex(ValueError, "inspection failed"):
                self.fixture.network(self.fixture.network_name)
            with self.assertRaisesRegex(ValueError, "inventory is invalid"):
                self.fixture.project_inventory("networks")
