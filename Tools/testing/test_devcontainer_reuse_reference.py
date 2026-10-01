"""D07 generations and volume recovery over real Unix HTTP, without a VM."""

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from case_evidence import canonical, contract_observations
from devcontainer_candidate import DevcontainerReuseCandidate
from devcontainer_reuse_reference import DevcontainerReuseReference, VOLUME, fixture_inputs
from guest_fixture import OWNER_LABEL
from guest_runtime import require_guest_resources_stopped
import test_devcontainer_reference as helpers


class Handler(helpers.Handler):
    def respond(self):
        server = self.server
        path = urlsplit(self.path).path.removeprefix("/v1.53")
        server.routes.append((self.command, self.path))
        status, body = 404, {"message": "absent"}
        if path == "/volumes/create":
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            server.volume = dict(body, CreatedAt="2026-09-19T00:00:00Z", Mountpoint="/owned/volume")
            status, body = 201, server.volume
        elif path == "/volumes":
            status, body = 200, {"Volumes": [server.volume] if server.volume else [], "Warnings": []}
        elif path == "/volumes/" + VOLUME and server.volume:
            status, body = 200, server.volume
            if self.command == "DELETE":
                if server.fail_volume_delete:
                    status, body = 409, {"message": "in use"}
                else:
                    server.volume = None
                    status, body = 204, None
        elif path == "/containers/json":
            status, body = 200, [{"Id": item["Id"]} for item in server.guests.values()
                                 if item["Config"]["Labels"].get(OWNER_LABEL) == server.owner]
        elif path.startswith("/containers/"):
            identifier = path.split("/")[2]
            if identifier in server.guests:
                status, body = 200, server.guests[identifier]
                if self.command == "DELETE":
                    if not server.ignore_delete:
                        del server.guests[identifier]
                    status, body = 204, None
        payload = canonical(body) if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    do_GET = do_POST = do_DELETE = respond


class ReuseTests(unittest.TestCase):
    stop = helpers.ReferenceTests.stop
    first, replacement = "a" * 64, "d" * 64
    adapter = DevcontainerReuseReference

    def setUp(self):
        with patch.object(helpers, "Handler", Handler):
            helpers.ReferenceTests.setUp(self)
        self.server.volume = None
        self.server.guests = {}
        self.server.owner = self.fixture.owner
        self.server.fail_volume_delete = False
        self.reuse_id, self.rebuild_id = self.first, self.replacement
        self.values = {"devcontainer-exec": "1", "devcontainer-reuse-exec": "1", "devcontainer-rebuild-exec": "2"}
        self.keep_old = False

    def reopen(self):
        self.inputs["devcontainerFixture"] = fixture_inputs(Path(__file__).parents[2])
        if self.adapter is DevcontainerReuseCandidate:
            self.vm.container = "/prepared/container"
            self.vm.close, self.vm.prepare_cleanup = Mock(), Mock()
            self.inputs["devcontainerCandidate"] = {"executables": {"devcontainer": "/prepared/devcontainer"}}
        return self.adapter(self.vm, self.inputs, self.owner)

    def guest(self, identifier):
        value = helpers.ReferenceTests.guest(self)
        value["Id"] = identifier
        value["Mounts"].append({"Type": "volume", "Name": VOLUME, "Destination": "/cache"})
        return value

    def command(self, name, arguments, *, timeout, separate_output=False):
        self.commands.append((name, arguments, timeout))
        self.vm.journal.put(name + "-intent.json", canonical({"arguments": arguments, "timeout": timeout}))
        self.vm.journal.put(name + "-exit.json", canonical({"code": 0}))
        if name in ("devcontainer-up", "devcontainer-reuse", "devcontainer-rebuild"):
            identifier = {"devcontainer-up": self.first, "devcontainer-reuse": self.reuse_id,
                          "devcontainer-rebuild": self.rebuild_id}[name]
            if name == "devcontainer-rebuild":
                self.assertIn("--remove-existing-container", arguments)
            if not self.keep_old or name != "devcontainer-rebuild":
                self.server.guests.clear()
            self.server.guests[identifier] = self.guest(identifier)
            return canonical({"outcome": "success", "containerId": identifier})
        if name in self.values:
            value = self.values[name]
            return f"create_count={value}\nstart_count={value}\n".encode()
        return b"pulled"

    def start(self, *, retain=False):
        self.fixture.setup()
        if retain:
            with patch.object(self.fixture, "remove_owned"):
                return self.fixture.operation()
        return self.fixture.operation()

    def test_original_contract_complete_and_idempotent_cleanup(self):
        contract = Path(__file__).parents[2] / "Tests/Parity/fixtures/D07-reuse-cleanup/contract.json"
        self.assertEqual(self.start(), contract_observations(json.loads(contract.read_text())["expected"]))
        self.assertEqual(len(self.commands), 7)
        self.assertEqual(self.server.guests, {})
        self.assertIsNone(self.server.volume)
        self.assertEqual([method for method, _ in self.server.routes].count("DELETE"), 2)
        self.reopen().cleanup()
        self.assertEqual((self.fixture.workspace / ".devcontainer/devcontainer.json").read_text(),
                         self.inputs["devcontainerFixture"]["configuration"])
        if self.adapter is DevcontainerReuseCandidate:
            self.vm.close.assert_called_once()
            for name, arguments, _ in self.commands[1:]:
                self.assertNotIn("--docker-path", arguments)

    def test_foreign_volume_is_not_adopted_or_removed(self):
        self.server.volume = {"Name": VOLUME, "Labels": {OWNER_LABEL: "foreign"}}
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.fixture.setup()
        self.fixture.cleanup()
        self.assertIsNotNone(self.server.volume)
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))

    def test_hook_errors_fail_before_later_generation(self):
        self.values["devcontainer-exec"] = "7"
        with self.assertRaisesRegex(ValueError, "initial hooks"):
            self.start()
        self.assertNotIn("devcontainer-reuse-intent.json", self.vm.journal.records())
        self.fixture.cleanup()

    def test_reuse_identity_change_is_not_normalized(self):
        self.reuse_id = self.replacement
        with self.assertRaisesRegex(ValueError, "reuse changed"):
            self.start()
        self.fixture.cleanup()

    def test_reuse_hook_repeat_is_failure(self):
        self.values["devcontainer-reuse-exec"] = "2"
        with self.assertRaisesRegex(ValueError, "reran hooks"):
            self.start()
        self.fixture.cleanup()

    def test_rebuild_requires_new_identity(self):
        self.rebuild_id = self.first
        with self.assertRaisesRegex(ValueError, "replace"):
            self.start()
        self.fixture.cleanup()

    def test_rebuild_requires_persisted_volume_data(self):
        self.values["devcontainer-rebuild-exec"] = "1"
        with self.assertRaisesRegex(ValueError, "preserve volume"):
            self.start()
        self.fixture.cleanup()

    def test_surviving_old_generation_is_not_hidden(self):
        self.keep_old = True
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.start()
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.fixture.cleanup()
        self.assertEqual(len(self.server.guests), 2)

    def test_changed_volume_and_mount_prevent_deletion(self):
        self.start(retain=True)
        volume = copy.deepcopy(self.server.volume)
        self.server.volume["CreatedAt"] = "replaced"
        with self.assertRaisesRegex(ValueError, "volume identity"):
            self.fixture.cleanup()
        self.server.volume = volume
        self.server.guests[self.replacement]["Mounts"].pop()
        with self.assertRaisesRegex(ValueError, "volume mount"):
            self.fixture.cleanup()
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))

    def test_missing_command_exit_quarantines_before_requests(self):
        self.start(retain=True)
        records = self.vm.journal.records()
        del records["devcontainer-rebuild-exit.json"]
        self.server.routes.clear()
        with patch.object(self.vm.journal, "records", return_value=records), self.assertRaisesRegex(ValueError, "completion"):
            self.fixture.cleanup()
        self.assertEqual(self.server.routes, [])

    def test_replacement_after_known_result_is_protected(self):
        self.start(retain=True)
        self.server.guests = {"e" * 64: self.guest("e" * 64)}
        with self.assertRaisesRegex(ValueError, "replaced"):
            self.fixture.cleanup()
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))

    def test_volume_cleanup_failure_remains_unsealed_then_recovers(self):
        self.server.fail_volume_delete = True
        with self.assertRaisesRegex(ValueError, "deletion"):
            self.start()
        self.assertEqual(self.server.guests, {})
        self.assertNotIn("devcontainer-removed.json", self.vm.journal.records())
        with self.assertRaisesRegex(ValueError, "D07 volume"):
            require_guest_resources_stopped(self.vm.journal.records())
        self.server.fail_volume_delete = False
        self.reopen().cleanup()
        self.assertIsNone(self.server.volume)

    def test_container_cleanup_failure_does_not_remove_volume(self):
        self.server.ignore_delete = True
        with self.assertRaisesRegex(ValueError, "removal"):
            self.start()
        self.assertIsNotNone(self.server.volume)
        self.server.ignore_delete = False
        self.reopen().cleanup()

    def test_no_submission_cleanup_and_changed_plan(self):
        self.fixture.cleanup()
        self.fixture.setup()
        self.fixture.plan["owner"] = "foreign"
        with self.assertRaisesRegex(ValueError, "ownership"):
            self.fixture.cleanup()

    def test_completed_rebuild_missing_result_reconciles_owned_inventory(self):
        self.start(retain=True)
        records = self.vm.journal.records()
        del records["devcontainer-rebuild-result.json"]
        with patch.object(self.vm.journal, "records", return_value=records):
            self.assertEqual(self.reopen().recovery_plan(), self.replacement)

    def test_missing_rebuild_result_cannot_use_old_generation_as_absence_proof(self):
        self.start(retain=True)
        records = self.vm.journal.records()
        del records["devcontainer-rebuild-result.json"]
        replacement = self.server.guests[self.replacement]
        for guests in ({}, {self.first: self.guest(self.first)},
                       {self.replacement: {**replacement, "Config": {**replacement["Config"],
                                             "Labels": {OWNER_LABEL: "changed"}}}}):
            with self.subTest(guests=guests):
                self.server.guests = guests
                self.server.routes.clear()
                with patch.object(self.vm.journal, "records", return_value=records), \
                        self.assertRaisesRegex(ValueError, "rebuild outcome"):
                    self.reopen().cleanup()
                self.assertIsNotNone(self.server.volume)
                self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))

    def test_observed_replacement_delete_receipt_can_finish_missing_result_cleanup(self):
        self.start(retain=True)
        self.vm.journal.put("d07-delete.json", canonical({"id": self.replacement}))
        self.server.guests.clear()
        records = self.vm.journal.records()
        del records["devcontainer-rebuild-result.json"]
        with patch.object(self.vm.journal, "records", return_value=records):
            self.assertIsNone(self.reopen().recovery_plan())

    def test_old_identity_with_changed_owner_is_never_ignored(self):
        self.start(retain=True)
        old = self.guest(self.first)
        old["Config"]["Labels"][OWNER_LABEL] = "foreign"
        self.server.guests[self.first] = old
        with self.assertRaisesRegex(ValueError, "earlier container"):
            self.fixture.cleanup()
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))

    def test_unobserved_creation_outcome_is_not_absence_proof(self):
        self.fixture.setup()
        self.command("devcontainer-up", [], timeout=120)
        self.server.guests.clear()
        with self.assertRaisesRegex(ValueError, "no observed"):
            self.reopen().cleanup()

    def test_container_reappearing_after_removal_is_not_deleted(self):
        self.start()
        self.server.guests[self.replacement] = self.guest(self.replacement)
        # Restore the original volume identity to isolate the container receipt guard.
        records = self.vm.journal.records()
        self.server.volume = json.loads(records["d07-volume-created.json"])
        self.server.routes.clear()
        with self.assertRaisesRegex(ValueError, "reappeared"):
            self.reopen().cleanup()
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))


class ReuseCandidateTests(ReuseTests):
    adapter = DevcontainerReuseCandidate
    first = "ABCDEF01-1234-5678-abcd-123456789012"
    replacement = "ABCDEF02-1234-5678-abcd-123456789012"


if __name__ == "__main__":
    unittest.main()
