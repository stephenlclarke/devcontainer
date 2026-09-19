"""C02 real-socket ownership, failure and restart-recovery component tests."""

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from case_evidence import canonical, contract_observations
from devcontainer_dependencies_reference import (DevcontainerDependenciesReference, DATABASE_IMAGE, FIXTURE,
                                                SERVICES, fixture_inputs)
from devcontainer_compose_reference import PROJECT_LABEL, SERVICE_LABEL, NETWORK_LABEL
from devcontainer_reference import IMAGE, WORKSPACE
from guest_fixture import OWNER_LABEL
from guest_runtime import require_guest_resources_stopped
import test_devcontainer_reference as helpers


IDS = dict(zip(SERVICES, ("a" * 64, "b" * 64, "c" * 64)))
NETWORK = "d" * 64


class Handler(helpers.Handler):
    def respond(self):
        server = self.server
        split = urlsplit(self.path)
        path = split.path.removeprefix("/v1.53")
        server.routes.append((self.command, self.path))
        status, body = 404, {"message": "absent"}
        if path == "/_container-family/recovery":
            status, body = 200, {"epoch": server.epoch, "protocol": "1"}
            if self.command == "POST":
                value = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                if server.pending_creation or value.get("epoch") != server.epoch:
                    status, body = 409, {"message": "uncertain creation"}
                else:
                    body.update({"owner": value["owner"], "state": "quiescent"})
        elif path == "/containers/json":
            filters = json.loads(parse_qs(split.query).get("filters", ["{}"])[0])
            labels = dict(value.split("=", 1) for value in filters.get("label", []))
            body = [{"Id": item["Id"]} for item in server.services.values()
                    if all(item["Config"]["Labels"].get(key) == value for key, value in labels.items())]
            status = 200
        elif path.startswith("/containers/"):
            identifier = path.split("/")[2]
            item = server.services.get(identifier)
            if item is not None:
                status, body = 200, item
                if path.endswith("/archive"):
                    status, body = server.archive_status, b"retained hosts archive"
                if self.command == "DELETE":
                    status, body = 204, None
                    if not server.ignore_delete:
                        del server.services[identifier]
                        if server.network:
                            server.network["Containers"].pop(identifier, None)
        elif path == "/networks":
            status, body = 200, [server.network] if server.network else []
        elif server.network and path in {"/networks/" + NETWORK, "/networks/" + server.network["Name"]}:
            status, body = 200, server.network
            if self.command == "DELETE":
                status, body = 204, None
                if not server.keep_network:
                    server.network = None
        payload = body if isinstance(body, bytes) else canonical(body) if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    do_GET = do_DELETE = do_POST = respond


class DependenciesTests(unittest.TestCase):
    stop = helpers.ReferenceTests.stop

    def setUp(self):
        with patch.object(helpers, "Handler", Handler):
            helpers.ReferenceTests.setUp(self)
        self.server.services, self.server.network, self.server.keep_network = {}, None, False
        self.server.archive_status = 200
        self.up_exit = 0
        self.server.epoch = "11111111-1111-1111-1111-111111111111"
        self.server.pending_creation = False

    def reopen(self):
        self.inputs["compose"] = {"executables": {"docker-compose": "/prepared/docker-compose"}}
        self.inputs["dependencyWorkload"] = {"image": {"manifest": "sha256:" + "e" * 64, "config": "sha256:" + "f" * 64}}
        self.inputs["devcontainerFixture"] = fixture_inputs(Path(__file__).parents[2])
        return DevcontainerDependenciesReference(self.vm, self.inputs, self.owner)

    def service(self, role):
        labels = {PROJECT_LABEL: self.fixture.project, SERVICE_LABEL: role,
                  "com.docker.compose.project.working_dir": str(self.fixture.workspace)}
        mounts = []
        if role == "app":
            labels[OWNER_LABEL] = self.fixture.owner
            mounts = [{"Type": "bind", "Source": str(self.fixture.workspace), "Destination": WORKSPACE}]
        return {"Id": IDS[role], "Name": "/" + self.fixture.project + "-" + role + "-1",
                "Created": "2026-09-19T11:30:00Z", "Mounts": mounts,
                "Image": self.inputs["dependencyWorkload" if role == "database" else "workload"]["image"]["manifest"],
                "Config": {"Labels": labels, "Image": DATABASE_IMAGE if role == "database" else IMAGE,
                           "Cmd": ["sleep", "infinity"], "Entrypoint": None},
                "State": {"Status": "running", "Health": {"Status": "healthy"}}}

    def test_diagnostics_retain_network_state_and_hosts_after_probe(self):
        original = self.command

        def command(name, *args, **kwargs):
            if name == "devcontainer-exec":
                self.assertFalse(any("/archive" in route for _, route in self.server.routes))
            output = original(name, *args, **kwargs)
            if name == "devcontainer-up":
                for item in self.server.services.values():
                    item["NetworkSettings"] = {"Networks": {self.fixture.network_name: {"IPAddress": "192.0.2.3"}}}
                    item["HostConfig"] = {"NetworkMode": self.fixture.network_name}
                    item["Config"]["Env"] = ["SECRET=must-not-be-retained"]
            return output

        self.vm.command = command
        self.start()
        records = self.vm.journal.records()
        for role in SERVICES:
            payload = records["c02-" + role + "-network-state.json"]
            value = json.loads(payload)
            self.assertEqual(value["State"]["Status"], "running")
            self.assertEqual(value["NetworkMode"], self.fixture.network_name)
            self.assertEqual(value["NetworkSettings"]["Networks"][self.fixture.network_name]["IPAddress"], "192.0.2.3")
            self.assertNotIn(b"SECRET", payload)
        self.assertEqual(records["c02-app-hosts.tar"], b"retained hosts archive")
        self.assertEqual(json.loads(records["c02-app-hosts-status.json"]), {"status": 200})

    def test_diagnostic_failure_preserves_probe_observations(self):
        self.server.archive_status = 500
        result = self.start()
        self.assertEqual(result["dependency_dns"], "true")
        records = self.vm.journal.records()
        self.assertNotIn("c02-app-hosts.tar", records)
        self.assertEqual(json.loads(records["c02-app-hosts-status.json"]), {"status": 500})

    def test_diagnostic_exception_preserves_probe_observations(self):
        original = self.fixture.call

        def call(method, route, *args, **kwargs):
            if "/archive" in route:
                raise TimeoutError("diagnostic expired")
            return original(method, route, *args, **kwargs)

        with patch.object(self.fixture, "call", side_effect=call):
            self.assertEqual(self.start()["dependency_dns"], "true")
        self.assertEqual(json.loads(self.vm.journal.records()["c02-app-hosts-status.json"]), {"error": "TimeoutError"})

    def test_diagnostic_does_not_request_archive_for_stopped_app(self):
        original = self.command

        def command(name, *args, **kwargs):
            output = original(name, *args, **kwargs)
            if name == "devcontainer-exec":
                self.server.services[IDS["app"]]["State"]["Status"] = "exited"
            return output

        self.vm.command = command
        self.assertEqual(self.start()["dependency_dns"], "true")
        self.assertFalse(any("/archive" in route for _, route in self.server.routes))
        self.assertEqual(json.loads(self.vm.journal.records()["c02-app-hosts-status.json"]),
                         {"skipped": "container-not-running"})

    def command(self, name, arguments, *, timeout, separate_output=False):
        self.commands.append((name, arguments, timeout))
        self.vm.journal.put(name + "-intent.json", canonical({"arguments": arguments, "timeout": timeout}))
        self.vm.journal.put(name + "-exit.json", canonical({"code": self.up_exit if name == "devcontainer-up" else 0}))
        self.vm.journal.put(name + "-stopped.json", canonical({"verifiedStopped": True}))
        if name == "devcontainer-up":
            self.server.services = {IDS[role]: self.service(role) for role in SERVICES}
            self.server.network = {"Id": NETWORK, "Name": self.fixture.network_name,
                                   "Labels": {PROJECT_LABEL: self.fixture.project, NETWORK_LABEL: "default"},
                                   "Created": "2026-09-19T11:30:00Z", "Containers": {identifier: {} for identifier in IDS.values()}}
            return canonical({"outcome": "success", "containerId": IDS["app"]})
        return (b"startup_dependency_dns=true\ndependency_dns=true\ndependency_health=healthy\nrun_service=true\n"
                if name == "devcontainer-exec" else b"loaded")

    def test_later_success_cannot_replace_failed_startup_lookup(self):
        original = self.command

        def command(name, *args, **kwargs):
            output = original(name, *args, **kwargs)
            return output.replace(b"startup_dependency_dns=true", b"startup_dependency_dns=false")

        self.vm.command = command
        observations = self.start()
        self.assertEqual(observations["startup_dependency_dns"], "false")
        self.assertEqual(observations["dependency_dns"], "true")
        self.assertEqual(observations["dependency_health"], "healthy")
        expected = json.loads((Path(__file__).parents[2] / "Tests/Parity/fixtures" / FIXTURE / "contract.json").read_text())
        self.assertNotEqual(observations, contract_observations(expected["expected"]))

    def start(self):
        self.fixture.setup()
        return self.fixture.operation()

    def deletes(self):
        return [path for method, path in self.server.routes if method == "DELETE"]

    def test_original_fixture_all_observations_and_reverse_exact_cleanup(self):
        expected = json.loads((Path(__file__).parents[2] / "Tests/Parity/fixtures" / FIXTURE / "contract.json").read_text())
        self.assertEqual(self.start(), contract_observations(expected["expected"]))
        self.assertEqual((self.fixture.workspace / "compose.yaml").read_text(), self.inputs["devcontainerFixture"]["compose"])
        self.assertEqual(self.commands[0][1][-1], IMAGE)
        self.assertEqual(self.commands[1][1][-1], DATABASE_IMAGE)
        self.assertTrue(self.fixture.project.startswith("cf-c02-"))
        self.fixture.cleanup()
        self.assertEqual(self.deletes(), ["/v1.53/containers/" + IDS[role] + "?force=true&v=true" for role in SERVICES]
                         + ["/v1.53/networks/" + NETWORK])
        self.assertEqual(self.server.services, {})
        self.assertIsNone(self.server.network)
        self.reopen().cleanup()
        require_guest_resources_stopped(self.vm.journal.records())

    def test_foreign_replaced_or_changed_sibling_prevents_all_deletion(self):
        self.start()
        original = copy.deepcopy(self.server.services[IDS["database"]])
        for field, value in [("Created", "changed"), ("Name", "/foreign"), ("Image", "sha256:" + "0" * 64)]:
            with self.subTest(field=field):
                self.server.services[IDS["database"]] = {**original, field: value}
                with self.assertRaises(ValueError):
                    self.fixture.cleanup()
        self.server.services[IDS["database"]] = original
        for label in (PROJECT_LABEL, SERVICE_LABEL, "com.docker.compose.project.working_dir"):
            with self.subTest(label=label):
                changed = copy.deepcopy(original)
                changed["Config"]["Labels"][label] = "foreign"
                self.server.services[IDS["database"]] = changed
                with self.assertRaises(ValueError):
                    self.fixture.cleanup()
        self.assertEqual(self.deletes(), [])

    def test_unknown_extra_or_duplicate_service_refused(self):
        self.start()
        extra = copy.deepcopy(self.server.services[IDS["helper"]])
        extra["Id"] = "e" * 64
        self.server.services[extra["Id"]] = extra
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.fixture.cleanup()
        self.assertEqual(self.deletes(), [])

    def test_unhealthy_dependency_fails_after_ownership_capture_and_can_clean(self):
        self.fixture.setup()
        output = self.command("devcontainer-up", [], timeout=120)
        self.server.services[IDS["database"]]["State"]["Health"]["Status"] = "starting"
        with self.assertRaisesRegex(ValueError, "healthy"):
            self.fixture.up_identity(output)
        self.fixture.cleanup()
        self.assertEqual(self.server.services, {})

    def test_unknown_creation_and_incomplete_command_never_delete(self):
        self.fixture.setup()
        self.command("devcontainer-up", [], timeout=120)
        with self.assertRaisesRegex(ValueError, "unobserved"):
            self.reopen().cleanup()
        records = self.vm.journal.records()
        del records["devcontainer-up-exit.json"]
        with patch.object(self.vm.journal, "records", return_value=records), self.assertRaisesRegex(ValueError, "completion"):
            self.fixture.cleanup()
        self.assertEqual(self.deletes(), [])

    def failed_creation(self, roles=("database", "helper"), *, network=True):
        self.fixture.setup()
        self.up_exit = 1
        self.command("devcontainer-up", [], timeout=120)
        self.server.services = {IDS[role]: self.service(role) for role in roles}
        if network:
            self.server.network["Containers"] = {identifier: {} for identifier in self.server.services}
        else:
            self.server.network = None

    def test_failed_partial_creation_is_sealed_and_cleaned_without_success_receipt(self):
        self.failed_creation()
        self.reopen().cleanup()
        records = self.vm.journal.records()
        self.assertNotIn("c02-project-created.json", records)
        self.assertEqual(set(json.loads(records["c02-project-partial.json"])["services"]), {"database", "helper"})
        self.assertEqual(self.deletes(), ["/v1.53/containers/" + IDS[role] + "?force=true&v=true"
                                        for role in ("helper", "database")] + ["/v1.53/networks/" + NETWORK])
        self.reopen().cleanup()
        self.assertFalse(self.server.services)
        self.assertIsNone(self.server.network)

    def test_empty_failed_creation_can_close_without_inventing_resources(self):
        self.failed_creation((), network=False)
        self.reopen().cleanup()
        self.assertEqual(self.deletes(), [])
        known = json.loads(self.vm.journal.records()["c02-project-partial.json"])
        self.assertEqual(known, {"services": {}, "network": None})

    def test_partial_receipt_survives_failed_delete_and_resumes(self):
        self.failed_creation()
        self.server.ignore_delete = True
        with self.assertRaisesRegex(ValueError, "removal"):
            self.fixture.cleanup()
        self.assertIn("c02-project-partial.json", self.vm.journal.records())
        self.server.ignore_delete = False
        self.reopen().cleanup()
        self.assertFalse(self.server.services)

    def test_partial_recovery_requires_all_command_groups_stopped(self):
        self.failed_creation()
        records = self.vm.journal.records()
        del records["devcontainer-up-stopped.json"]
        with patch.object(self.vm.journal, "records", return_value=records), self.assertRaisesRegex(ValueError, "stopped"):
            self.fixture.cleanup()
        self.assertEqual(self.deletes(), [])

    def test_invisible_pending_native_creation_never_grants_cleanup(self):
        self.failed_creation((), network=False)
        self.server.pending_creation = True
        with self.assertRaisesRegex(ValueError, "pending"):
            self.reopen().cleanup()
        records = self.vm.journal.records()
        self.assertNotIn("c02-project-partial.json", records)
        self.assertNotIn("c02-project-removed.json", records)
        self.assertEqual(self.deletes(), [])

    def test_receiver_restart_invalidates_prior_quiescence(self):
        self.failed_creation()
        self.fixture.recovery_plan()
        self.server.epoch = "22222222-2222-2222-2222-222222222222"
        with self.assertRaisesRegex(ValueError, "pending"):
            self.reopen().cleanup()
        self.assertEqual(self.deletes(), [])

    def test_partial_recovery_rejects_foreign_network_before_sealing(self):
        self.failed_creation()
        self.server.network["Containers"]["foreign"] = {}
        with self.assertRaisesRegex(ValueError, "foreign"):
            self.fixture.cleanup()
        self.assertNotIn("c02-project-partial.json", self.vm.journal.records())
        self.assertEqual(self.deletes(), [])

    def test_partial_services_without_their_network_are_not_deleted(self):
        self.failed_creation(network=False)
        with self.assertRaisesRegex(ValueError, "network"):
            self.fixture.cleanup()
        self.assertEqual(self.deletes(), [])

    def test_partial_receipt_rejects_late_service_and_changed_incarnation(self):
        self.failed_creation()
        self.fixture.recovery_plan()
        self.server.services[IDS["app"]] = self.service("app")
        with self.assertRaisesRegex(ValueError, "unrecorded"):
            self.reopen().cleanup()
        del self.server.services[IDS["app"]]
        self.server.services[IDS["database"]]["Created"] = "new incarnation"
        with self.assertRaisesRegex(ValueError, "replaced"):
            self.reopen().cleanup()
        self.assertEqual(self.deletes(), [])

    def test_failed_delete_retains_receipts_and_resumes_remaining_services(self):
        self.start()
        self.server.ignore_delete = True
        with self.assertRaisesRegex(ValueError, "removal"):
            self.fixture.cleanup()
        self.assertNotIn("c02-project-removed.json", self.vm.journal.records())
        self.server.ignore_delete = False
        self.reopen().cleanup()
        self.assertEqual(self.server.services, {})

    def test_partial_cleanup_restart_and_closed_service_reappearance(self):
        self.start()
        app = self.server.services.pop(IDS["app"])
        self.server.network["Containers"].pop(IDS["app"])
        self.vm.journal.put("c02-app-removed.json", canonical({"id": IDS["app"], "absent": True}))
        self.server.services[IDS["app"]] = app
        with self.assertRaisesRegex(ValueError, "reappeared"):
            self.reopen().cleanup()
        self.assertEqual(self.deletes(), [])
        del self.server.services[IDS["app"]]
        self.reopen().cleanup()
        self.assertFalse(any(IDS["app"] in value for value in self.deletes()))

    def test_network_failure_or_reappearance_remains_unsealed(self):
        self.start()
        original = copy.deepcopy(self.server.network)
        self.server.keep_network = True
        with self.assertRaisesRegex(ValueError, "remains"):
            self.fixture.cleanup()
        self.server.keep_network = False
        self.reopen().cleanup()
        original["Containers"] = {}
        self.server.network = original
        count = len(self.deletes())
        with self.assertRaisesRegex(ValueError, "reappeared"):
            self.reopen().cleanup()
        self.assertEqual(len(self.deletes()), count)

    def test_existing_project_and_unclosed_recovery_are_refused(self):
        self.server.network = {"Name": self.fixture.network_name}
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.fixture.setup()
        self.assertEqual(self.commands, [])
        with self.assertRaisesRegex(ValueError, "C02"):
            require_guest_resources_stopped({"c02-project-intent.json": canonical(self.fixture.plan)})

    def test_empty_preparation_cleanup_and_plan_drift(self):
        self.fixture.cleanup()
        self.fixture.setup()
        self.fixture.cleanup()
        self.fixture.plan["owner"] = "foreign"
        with self.assertRaisesRegex(ValueError, "uncertain"):
            self.fixture.cleanup()

    def test_native_adapter_uses_native_image_pulls_and_compose_paths(self):
        from devcontainer_candidate import DevcontainerDependenciesCandidate
        self.inputs["devcontainerCandidate"] = {"executables": {"devcontainer": "/prepared/devcontainer"}}
        self.inputs["composeCandidate"] = {"executables": {"compose": "/prepared/compose"}, "runtimeProfile": "stock"}
        self.vm.container = "/prepared/container"
        self.fixture = DevcontainerDependenciesCandidate(self.vm, self.inputs, self.owner)
        self.assertEqual(self.start()["dependency_health"], "healthy")
        self.assertEqual([item[1][0] for item in self.commands[:2]], [self.vm.container] * 2)
        args = self.commands[2][1]
        self.assertIn("DEVCONTAINER_COMPOSE_BIN=/prepared/compose", args)
        self.assertIn("COMPOSE_PROJECT_NAME=" + self.fixture.project, args)
        self.assertNotIn("--docker-compose-path", args)

    def test_bad_creation_inventory_never_produces_receipt(self):
        self.fixture.setup()
        output = self.command("devcontainer-up", [], timeout=120)
        original = copy.deepcopy(self.server.services)
        for altered in ({}, {key: value for key, value in original.items() if key != IDS["helper"]},
                        {**original, "invalid": {"Id": "invalid", "Config": {"Labels": {PROJECT_LABEL: self.fixture.project}}}}):
            with self.subTest(services=list(altered)):
                self.server.services = copy.deepcopy(altered)
                with self.assertRaises(ValueError):
                    self.fixture.up_identity(output)
                self.assertNotIn("c02-project-created.json", self.vm.journal.records())
        self.assertEqual(self.deletes(), [])

    def test_network_membership_and_creation_identity_cannot_change(self):
        self.start()
        original = copy.deepcopy(self.server.network)
        for key, value in [("Created", "changed"), ("Containers", {"foreign": {}}), ("Labels", {})]:
            with self.subTest(key=key):
                self.server.network = {**original, key: value}
                with self.assertRaises(ValueError):
                    self.fixture.cleanup()
        self.assertEqual(self.deletes(), [])

    def test_uncertain_commands_and_corrupt_closure_receipt_refused(self):
        self.start()
        self.vm.uncertain = True
        with self.assertRaisesRegex(ValueError, "uncertain"):
            self.fixture.cleanup()
        self.vm.uncertain = False
        self.vm.journal.put("c02-app-removed.json", canonical({"id": "foreign", "absent": True}))
        with self.assertRaisesRegex(ValueError, "closure"):
            self.fixture.cleanup()
        self.assertEqual(self.deletes(), [])

    def test_extra_pull_process_requires_durable_shutdown(self):
        from guest_runtime import require_guest_commands_stopped
        records = {"devcontainer-dependency-pull-intent.json": b"{}"}
        with self.assertRaisesRegex(ValueError, "process"):
            require_guest_commands_stopped(records)
        records["devcontainer-dependency-pull-stopped.json"] = canonical({"verifiedStopped": True})
        self.assertEqual(require_guest_commands_stopped(records), ["devcontainer-dependency-pull"])


if __name__ == "__main__":
    unittest.main()
