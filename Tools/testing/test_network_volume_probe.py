"""E06 journal/ownership tests through real Unix HTTP; no host runtime."""

import json
from pathlib import Path
from unittest.mock import patch
import unittest
from urllib.parse import parse_qs, urlsplit

from case_evidence import canonical
from guest_fixture import OWNER_LABEL
from network_volume_probe import NetworkVolumeFixture, ScopedJournal
import test_guest_fixture as helpers


class Handler(helpers.Handler):
    def dispatch(self):
        server = self.server
        parsed = urlsplit(self.path)
        path = parsed.path.removeprefix("/v1.54")
        parts = path.strip("/").split("/")
        if parts[0] in {"networks", "volumes"}:
            server.routes.append((self.command, self.path))
            resources = server.resources[parts[0]]
            if parts[-1] == "create":
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                value = dict(body, Created="2026-09-17T00:00:00Z", CreatedAt="2026-09-17T00:00:00Z",
                             Mountpoint="/private/volume", Id="d" * 64, Containers={})
                if parts[0] == "networks":
                    value["Driver"] = server.network_driver
                resources[body["Name"]] = value
                return 201, ({"Id": value["Id"]} if parts[0] == "networks" else value)
            if len(parts) == 1:
                entries = list(resources.values())
                if server.residue:
                    entries.append({"Name": "leftover"})
                return 200, entries if parts[0] == "networks" else {"Volumes": entries, "Warnings": []}
            value = next((item for item in resources.values() if parts[1] in {item["Name"], item["Id"]}), None)
            if value is None:
                return 404, {"message": "missing resource"}
            if self.command == "DELETE":
                if server.fail_delete:
                    return 409, {"message": "still in use"}
                del resources[value["Name"]]
                return 204, b""
            return 200, value
        if path == "/containers/create":
            server.routes.append((self.command, self.path))
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            name = parse_qs(parsed.query)["name"][0]
            identifier = f"{len(server.guests) + 1:064x}"
            value = {"Id": identifier, "Name": "/" + name, "Config": body, "Image": server.image,
                     "State": {"Status": "created"}, "Mounts": [dict(mount, Destination=mount["Target"])
                        for mount in body["HostConfig"].get("Mounts", [])]}
            server.guests[identifier] = value
            return 201, {"Id": identifier}
        if path == "/containers/json":
            labels = json.loads(parse_qs(parsed.query)["filters"][0])["label"]
            owner = labels[0].split("=", 1)[1]
            return 200, [item for item in server.guests.values() if item["Config"]["Labels"].get(OWNER_LABEL) == owner]
        if parts[0] == "containers":
            server.guest = next((item for item in server.guests.values()
                                 if parts[1] in {item["Id"], item["Name"][1:]}), None)
            if self.command == "DELETE" and server.guest is not None:
                del server.guests[server.guest["Id"]]
        return super().dispatch()


class NetworkVolumeTests(unittest.TestCase):
    reopen = helpers.GuestFixtureTests.reopen
    stop = helpers.GuestFixtureTests.stop

    def setUp(self):
        with patch.object(helpers, "Handler", Handler):
            helpers.GuestFixtureTests.setUp(self)
        self.server.resources = {"networks": {}, "volumes": {}}
        self.server.guests = {}
        self.server.residue, self.server.fail_delete = False, False
        self.server.network_driver = "bridge"
        self.fixture = NetworkVolumeFixture(self.socket, self.owner, self.server.image, "1.54", self.journal, self.root)

    def exec_result(self, guest, name, command):
        self.commands.append((guest.name, name, command))
        return {"dns": (b"ping result", b"", 0), "write": (b"", b"", 0),
                "persisted": (b"volume-data", b"", 0), "original": (b"read-only\n", b"", 0),
                "readonly": (b"", b"read-only file system", 1), "tmpfs": (b"scratch", b"", 0)}[name]

    def operation(self):
        self.commands = []
        with patch("network_volume_probe.execute", side_effect=self.exec_result):
            return self.fixture.operation()

    def test_complete_observations_preserve_three_guests_mounts_and_alias(self):
        contract = Path(__file__).parents[2] / "Tests/Parity/fixtures/E06-network-volume/contract.json"
        expected = {key: str(value).lower() for key, value in json.loads(contract.read_text())["expected"].items()}
        self.assertEqual(self.operation(), expected)
        self.assertEqual(len(self.server.guests), 3)
        app, peer, reader = self.fixture.guests
        configs = [self.server.guests[item.identifier]["Config"] for item in (app, peer, reader)]
        self.assertEqual(configs[0]["NetworkingConfig"]["EndpointsConfig"][self.fixture.network.name]["Aliases"], ["app"])
        self.assertEqual([item["Type"] for item in configs[0]["HostConfig"]["Mounts"]], ["volume", "bind", "tmpfs"])
        self.assertTrue(configs[0]["HostConfig"]["Mounts"][1]["ReadOnly"])
        self.assertEqual(configs[2]["HostConfig"]["NetworkMode"], "none")
        self.assertEqual(self.commands[0][0], peer.name)
        self.assertEqual(self.commands[2][0], reader.name)
        self.assertEqual((self.root / "readonly-input/input.txt").read_bytes(), b"read-only\n")
        self.assertEqual(self.fixture.cleanup()["status"], "passed")
        self.assertEqual(self.server.guests, {})
        self.assertEqual(self.server.resources, {"networks": {}, "volumes": {}})
        mutations = [(method, path) for method, path in self.server.routes if method == "DELETE"]
        self.assertEqual(len(mutations), 5)
        self.assertTrue(mutations[-2][1].endswith("d" * 64))
        self.assertNotIn("force", mutations[-1][1])
        self.assertIn("network-volume-removed.json", self.journal.records())

    def test_changed_observations_are_not_normalized(self):
        self.commands = []
        original = self.exec_result
        def bad(guest, name, command):
            result = original(guest, name, command)
            return (b"wrong", result[1], 0 if name == "readonly" else 9)
        with patch("network_volume_probe.execute", side_effect=bad):
            observed = self.fixture.operation()
        for key in ("bind_read_only", "network_dns", "tmpfs", "volume_persistence"):
            self.assertEqual(observed[key], "false")
        self.fixture.cleanup()

    def test_partial_setup_cleanup_removes_only_attempted_resources(self):
        with patch.object(self.fixture.volume, "create", side_effect=RuntimeError("interrupted")), self.assertRaises(RuntimeError):
            self.fixture.operation()
        self.assertEqual(self.fixture.cleanup()["status"], "passed")
        self.assertEqual(self.server.resources["networks"], {})
        self.assertFalse(any(path.startswith("/v1.54/volumes") for _, path in self.server.routes))

    def test_existing_network_is_not_adopted_or_removed(self):
        self.server.resources["networks"][self.fixture.network.name] = {"Name": self.fixture.network.name, "Id": "d" * 64}
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.fixture.operation()
        self.fixture.cleanup()
        self.assertEqual(len(self.server.resources["networks"]), 1)
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))

    def test_intent_failure_prevents_mutations_and_bind_creation(self):
        with patch.object(self.journal, "put", side_effect=OSError), self.assertRaises(OSError):
            self.fixture.operation()
        self.assertEqual(self.server.routes, [])
        self.assertFalse((self.root / "readonly-input").exists())

    def test_reopened_journal_cleans_owned_resources_without_recreating_them(self):
        self.operation()
        reopened = NetworkVolumeFixture(self.socket, self.owner, self.server.image, "1.54", self.journal, self.root)
        with self.assertRaisesRegex(ValueError, "already attempted"):
            reopened.operation()
        self.assertEqual(reopened.cleanup()["status"], "passed")
        self.assertEqual(sum(path == "/v1.54/networks/create" for _, path in self.server.routes), 1)

    def test_foreign_guest_blocks_provider_deletion(self):
        self.operation()
        self.server.guests[self.fixture.guests[-1].identifier]["Config"]["Labels"] = {}
        with self.assertRaisesRegex(ValueError, "ownership"):
            self.fixture.cleanup()
        self.assertTrue(self.server.resources["networks"])
        self.assertTrue(self.server.resources["volumes"])
        self.assertNotIn("network-volume-removed.json", self.journal.records())

    def test_replaced_network_or_volume_is_never_deleted(self):
        for resource, collection, field in ((self.fixture.network, "networks", "Created"),
                                            (self.fixture.volume, "volumes", "CreatedAt")):
            with self.subTest(kind=collection):
                resource.create()
                self.server.resources[collection][resource.name][field] = "different generation"
                with self.assertRaisesRegex(ValueError, "replaced"):
                    resource.cleanup()
                self.assertTrue(self.server.resources[collection])

    def test_native_driver_metadata_is_preserved_and_still_owns_cleanup(self):
        self.server.network_driver = "container-network-vmnet"
        resource = self.fixture.network
        actual = resource.create()
        self.assertEqual(actual["Driver"], "container-network-vmnet")
        self.assertEqual(json.loads(self.journal.records()[resource.prefix + "-intent.json"])["Driver"], "bridge")
        self.assertEqual(json.loads(self.journal.records()[resource.prefix + "-created.json"])["Driver"], "container-network-vmnet")
        resource.cleanup()
        self.assertEqual(self.server.resources["networks"], {})

    def test_lost_create_response_reconciles_only_observed_owned_resource(self):
        resource = self.fixture.network
        call = resource.client.call
        def lose(method, route, body=None):
            result = call(method, route, body)
            if method == "POST":
                raise TimeoutError("lost response")
            return result
        with patch.object(resource.client, "call", side_effect=lose), self.assertRaises(TimeoutError):
            resource.create()
        resource.cleanup()
        self.assertEqual(self.server.resources["networks"], {})

    def test_unobserved_create_stays_uncertain(self):
        resource = self.fixture.volume
        self.journal.put(resource.prefix + "-intent.json", canonical(resource.intent))
        with self.assertRaisesRegex(ValueError, "uncertain"):
            resource.cleanup()

    def test_delete_failure_and_remaining_network_connections_preserve_resources(self):
        resource = self.fixture.network
        resource.create()
        self.server.fail_delete = True
        with self.assertRaisesRegex(ValueError, "unverified"):
            resource.cleanup()
        self.server.fail_delete = False
        self.server.resources["networks"][resource.name]["Containers"] = {"foreign": {}}
        with self.assertRaisesRegex(ValueError, "attached"):
            resource.cleanup()

    def test_lost_delete_response_resumes_absence_checks(self):
        resource = self.fixture.volume
        resource.create()
        call = resource.client.call
        def lose(method, route, body=None):
            result = call(method, route, body)
            if method == "DELETE":
                raise TimeoutError("lost deletion")
            return result
        with patch.object(resource.client, "call", side_effect=lose), self.assertRaises(TimeoutError):
            resource.cleanup()
        resource.cleanup()
        self.assertEqual(sum(method == "DELETE" for method, _ in self.server.routes), 1)

    def test_labeled_residue_blocks_completion(self):
        self.fixture.volume.create()
        self.server.residue = True
        with self.assertRaisesRegex(ValueError, "residue"):
            self.fixture.volume.cleanup()

    def test_deleted_network_name_cannot_hide_a_renamed_same_id(self):
        resource = self.fixture.network
        resource.create()
        actual = self.server.resources["networks"].pop(resource.name)
        actual["Name"] = "renamed"
        self.server.resources["networks"]["renamed"] = actual
        with self.assertRaisesRegex(ValueError, "renamed"):
            resource.cleanup()
        self.assertFalse(any(method == "DELETE" for method, _ in self.server.routes))

    def test_reappearing_resource_does_not_inherit_previous_cleanup_authority(self):
        resource = self.fixture.volume
        identity = resource.create()
        resource.cleanup()
        self.server.resources["volumes"][resource.name] = dict(identity, Id="d" * 64)
        with self.assertRaisesRegex(ValueError, "reappeared"):
            resource.cleanup()
        self.assertEqual(sum(method == "DELETE" for method, _ in self.server.routes), 1)

    def test_empty_invalid_volume_listing_cannot_prove_absence(self):
        resource = self.fixture.volume
        resource.create()
        call = resource.client.call
        def invalid_listing(method, route, body=None):
            return (200, b"{}") if route.startswith("/volumes?filters=") else call(method, route, body)
        with patch.object(resource.client, "call", side_effect=invalid_listing), self.assertRaisesRegex(ValueError, "residue"):
            resource.cleanup()
        self.assertNotIn("e06-volume-removed.json", self.journal.records())

    def test_wrong_tmpfs_type_cannot_pass_from_ordinary_filesystem_writes(self):
        self.commands = []
        original = self.exec_result
        def replace_mount(guest, name, command):
            result = original(guest, name, command)
            if name == "tmpfs":
                self.server.guests[guest.identifier]["Mounts"] = []
            return result
        with patch("network_volume_probe.execute", side_effect=replace_mount):
            self.assertEqual(self.fixture.operation()["tmpfs"], "false")
        self.fixture.cleanup()

    def test_resource_journal_identity_and_snapshot_fields_are_required(self):
        resource = self.fixture.network
        identity = resource.create()
        with self.assertRaisesRegex(ValueError, "already attempted"):
            resource.create()
        for key in ("Name", "Labels", "Created", "Id"):
            invalid = dict(identity)
            invalid.pop(key)
            with self.subTest(key=key), self.assertRaises(ValueError):
                resource.identity(invalid)
        self.journal.put(resource.prefix + "-delete.json", canonical(dict(identity, Created="changed")))
        with self.assertRaisesRegex(ValueError, "disagree"):
            resource.cleanup()

    def test_scope_and_fixture_identity_cannot_be_substituted(self):
        with self.assertRaises(ValueError):
            ScopedJournal(self.journal, "other")
        self.operation()
        other = NetworkVolumeFixture(self.socket, "f" * 64, self.server.image, "1.54", self.journal, self.root)
        with self.assertRaisesRegex(ValueError, "another fixture"):
            other.cleanup()
