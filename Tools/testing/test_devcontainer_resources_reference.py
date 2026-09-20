"""C03 socket-level resource ownership, interruption and native-path regressions."""

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from case_evidence import canonical, contract_observations
from devcontainer_candidate import DevcontainerResourcesCandidate
from devcontainer_compose_reference import PROJECT_LABEL, NETWORK_LABEL
from devcontainer_reference import IMAGE
from devcontainer_resources_reference import DevcontainerResourcesReference, FIXTURE, VOLUME_LABEL, fixture_inputs
from guest_runtime import require_guest_resources_stopped
import test_devcontainer_dependencies_reference as dependencies


IDS = {"app": "a" * 64, "peer": "b" * 64}


class Handler(dependencies.Handler):
    def respond(self):
        server = self.server
        path = urlsplit(self.path).path.removeprefix("/v1.53")
        if not path.startswith("/volumes"):
            return super().respond()
        server.routes.append((self.command, self.path))
        status, body = 404, {"message": "absent"}
        if path == "/volumes":
            status, body = server.volume_list_status, {"Volumes": list(server.volumes.values()), "Warnings": server.warnings}
        elif path.startswith("/volumes/"):
            value = server.volumes.get(path[len("/volumes/"):])
            if value is not None:
                status, body = server.volume_status, value
                if self.command == "DELETE":
                    status, body = server.volume_delete_status, None
                    if status == 204 and not server.keep_volume:
                        del server.volumes[value["Name"]]
        payload = canonical(body) if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    do_GET = do_DELETE = do_POST = respond


class ResourcesTests(unittest.TestCase):
    stop = dependencies.DependenciesTests.stop
    deletes = dependencies.DependenciesTests.deletes
    start = dependencies.DependenciesTests.start

    def setUp(self):
        with patch.object(dependencies, "Handler", Handler):
            dependencies.DependenciesTests.setUp(self)
        self.server.volumes = {}
        self.server.volume_status = self.server.volume_list_status = 200
        self.server.volume_delete_status = 204
        self.server.keep_volume = False
        self.server.warnings = None

    def reopen(self):
        self.inputs["compose"] = {"executables": {"docker-compose": "/prepared/docker-compose"}}
        self.inputs["devcontainerFixture"] = fixture_inputs(Path(__file__).parents[2])
        return DevcontainerResourcesReference(self.vm, self.inputs, self.owner)

    def volume(self):
        return {"Name": self.fixture.volume_name, "CreatedAt": "2026-09-20T11:30:00Z", "Driver": "local",
                "Scope": "local", "Mountpoint": "/private/volume", "Options": {},
                "Labels": {PROJECT_LABEL: self.fixture.project, VOLUME_LABEL: "parity-cache", "devcontainer.parity": "C03"}}

    def service(self, role):
        # The original C02 helper's helper service has the same image/no mounts.
        value = dependencies.DependenciesTests.service(self, "helper" if role == "peer" else role)
        value["Id"] = IDS[role]
        value["Name"] = "/" + self.fixture.project + "-" + role + "-1"
        value["Config"]["Labels"]["com.docker.compose.service"] = role
        if role == "app":
            value["Mounts"].append({"Type": "volume", "Name": self.fixture.volume_name, "Destination": "/cache"})
        return value

    def command(self, name, arguments, *, timeout, separate_output=False):
        self.commands.append((name, arguments, timeout))
        self.vm.journal.put(name + "-intent.json", canonical({"arguments": arguments, "timeout": timeout}))
        self.vm.journal.put(name + "-exit.json", canonical({"code": 0}))
        self.vm.journal.put(name + "-stopped.json", canonical({"verifiedStopped": True}))
        if name == "devcontainer-up":
            self.server.services = {IDS[role]: self.service(role) for role in IDS}
            self.server.network = {"Id": dependencies.NETWORK, "Name": self.fixture.network_name,
                                   "Labels": {PROJECT_LABEL: self.fixture.project, NETWORK_LABEL: "parity"},
                                   "Created": "2026-09-20T11:30:00Z", "Containers": {identifier: {} for identifier in IDS.values()}}
            self.server.volumes[self.fixture.volume_name] = self.volume()
            return canonical({"outcome": "success", "containerId": IDS["app"]})
        return (b"env_file=compose-env-file\nnamed_volume=volume-data\nnetwork_alias=true\nnetwork_peer=true\n"
                if name == "devcontainer-exec" else b"loaded")

    def test_original_contract_workspace_and_exact_restartable_cleanup(self):
        contract = json.loads((Path(__file__).parents[2] / "Tests/Parity/fixtures" / FIXTURE / "contract.json").read_text())
        self.assertEqual(self.start(), contract_observations(contract["expected"]))
        self.assertEqual((self.fixture.workspace / "fixture.env").read_text(), "PARITY_ENV_FILE=compose-env-file\n")
        self.assertEqual((self.fixture.workspace / "compose.yaml").read_text(), self.inputs["devcontainerFixture"]["compose"])
        self.assertEqual([name for name, _, _ in self.commands], ["devcontainer-image-pull", "devcontainer-up", "devcontainer-exec"])
        self.assertEqual(self.commands[0][1][-1], IMAGE)
        self.assertIn("COMPOSE_PROJECT_NAME=" + self.fixture.project, self.commands[1][1])
        self.reopen().cleanup()
        self.assertEqual(self.deletes(), ["/v1.53/containers/" + identifier + "?force=true&v=true" for identifier in IDS.values()]
                         + ["/v1.53/networks/" + dependencies.NETWORK, "/v1.53/volumes/" + self.fixture.volume_name])
        self.reopen().cleanup()
        require_guest_resources_stopped(self.vm.journal.records())

    def test_volume_replacement_blocks_all_mutation(self):
        self.start()
        original = self.volume()
        for key in ("Name", "CreatedAt", "Driver", "Scope", "Mountpoint", "Options", "Labels"):
            with self.subTest(key=key):
                altered = copy.deepcopy(original)
                altered[key] = "changed"
                self.server.volumes[self.fixture.volume_name] = altered
                reopened = self.reopen()
                with self.assertRaises(ValueError):
                    reopened.cleanup()
        for label in (PROJECT_LABEL, VOLUME_LABEL, "devcontainer.parity"):
            with self.subTest(label=label):
                altered = copy.deepcopy(original)
                altered["Labels"][label] = "foreign"
                self.server.volumes[self.fixture.volume_name] = altered
                reopened = self.reopen()
                with self.assertRaises(ValueError):
                    reopened.cleanup()
        self.assertEqual(self.deletes(), [])

    def test_preexisting_volume_never_adopted(self):
        self.server.volumes[self.fixture.volume_name] = self.volume()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.fixture.setup()
        self.assertEqual(self.commands, [])
        self.assertEqual(self.deletes(), [])

    def test_extra_volume_blocks_deletion(self):
        self.start()
        self.server.volumes["foreign"] = {**self.volume(), "Name": "foreign"}
        with self.assertRaises(ValueError):
            self.fixture.cleanup()
        self.assertEqual(self.deletes(), [])

    def test_missing_creation_receipt_preserves_quarantine(self):
        self.fixture.setup()
        self.command("devcontainer-up", [], timeout=120)
        with self.assertRaisesRegex(ValueError, "unrecorded"):
            self.fixture.cleanup()
        self.assertEqual(self.deletes(), [])
        with self.assertRaisesRegex(ValueError, "C03"):
            require_guest_resources_stopped(self.vm.journal.records())

    def test_missing_volume_after_unknown_create_preserves_quarantine(self):
        self.fixture.setup()
        self.vm.journal.put("devcontainer-up-intent.json", b"{}")
        with self.assertRaisesRegex(ValueError, "uncertain"):
            self.fixture.cleanup()
        self.assertEqual(self.deletes(), [])

    def test_setup_before_up_can_be_cleaned(self):
        self.fixture.setup()
        self.reopen().cleanup()
        self.assertEqual(self.deletes(), [])
        require_guest_resources_stopped(self.vm.journal.records())

    def test_cleanup_before_setup_does_nothing(self):
        self.fixture.cleanup()
        self.assertEqual(self.deletes(), [])

    def test_volume_delete_refusal_can_resume_without_repeating_services(self):
        self.start()
        self.server.volume_delete_status = 409
        with self.assertRaisesRegex(ValueError, "deletion"):
            self.fixture.cleanup()
        self.assertEqual(self.server.services, {})
        self.assertIsNone(self.server.network)
        with self.assertRaisesRegex(ValueError, "C03"):
            require_guest_resources_stopped(self.vm.journal.records())
        self.server.volume_delete_status = 204
        self.reopen().cleanup()
        self.assertEqual(len([path for path in self.deletes() if "/containers/" in path]), 2)
        require_guest_resources_stopped(self.vm.journal.records())

    def test_falsely_successful_volume_delete_is_not_accepted(self):
        self.start()
        self.server.keep_volume = True
        with self.assertRaisesRegex(ValueError, "deletion"):
            self.fixture.cleanup()

    def test_reappearing_volume_is_not_deleted(self):
        self.start()
        self.fixture.cleanup()
        count = len(self.deletes())
        self.server.volumes[self.fixture.volume_name] = self.volume()
        reopened = self.reopen()
        with self.assertRaises(ValueError):
            reopened.cleanup()
        self.assertEqual(len(self.deletes()), count)

    def test_volume_inspection_and_list_errors_block_mutations(self):
        self.start()
        for field in ("volume_status", "volume_list_status", "warnings"):
            with self.subTest(field=field):
                old = getattr(self.server, field)
                setattr(self.server, field, ["warning"] if field == "warnings" else 500)
                with self.assertRaises(ValueError):
                    self.fixture.cleanup()
                setattr(self.server, field, old)
        self.assertEqual(self.deletes(), [])

    def test_missing_mount_cannot_pass(self):
        self.fixture.setup()
        output = self.command("devcontainer-up", [], timeout=120)
        self.server.services[IDS["app"]]["Mounts"].pop()
        with self.assertRaisesRegex(ValueError, "mount"):
            self.fixture.up_identity(output)

    def test_missing_or_extra_volume_cannot_pass(self):
        self.fixture.setup()
        output = self.command("devcontainer-up", [], timeout=120)
        self.server.volumes.clear()
        with self.assertRaisesRegex(ValueError, "declared volume"):
            self.fixture.up_identity(output)

    def test_changed_receipts_block_mutations(self):
        self.start()
        original = self.vm.journal.records()
        for name in ("c03-volume-intent.json", "c03-volume-removed.json", "c03-volume-delete.json"):
            with self.subTest(name=name), patch.object(self.vm.journal, "records", return_value={**original, name: b"{}"}):
                with self.assertRaises(ValueError):
                    self.fixture.cleanup()
        self.assertEqual(self.deletes(), [])

    def test_crash_after_deletion_resumes_from_sealed_identity(self):
        self.start()
        put = self.vm.journal.put

        def interrupt(name, data):
            if name == "c03-volume-removed.json":
                raise RuntimeError("simulated interruption")
            put(name, data)

        with patch.object(self.vm.journal, "put", side_effect=interrupt), self.assertRaises(RuntimeError):
            self.fixture.cleanup()
        self.assertEqual(self.server.volumes, {})
        self.reopen().cleanup()
        require_guest_resources_stopped(self.vm.journal.records())

    def test_native_frontend_and_image_acquisition_are_docker_free(self):
        self.inputs["devcontainerCandidate"] = {"executables": {"devcontainer": "/candidate/devcontainer"}}
        self.inputs["composeCandidate"] = {"executables": {"compose": "/candidate/compose"}, "runtimeProfile": "stock"}
        self.vm.container = "/stock/bin/container"
        fixture = DevcontainerResourcesCandidate(self.vm, self.inputs, self.owner)
        arguments = fixture.arguments("up")
        self.assertIn("/candidate/devcontainer", arguments)
        self.assertIn("DEVCONTAINER_COMPOSE_BIN=/candidate/compose", arguments)
        self.assertNotIn("--docker-compose-path", arguments)
        self.assertNotIn("--docker-path", arguments)
        fixture.prepare_image()
        self.assertEqual(self.commands[0][1], ["/stock/bin/container", "image", "pull", "--arch", "arm64", IMAGE])
        self.assertEqual(fixture.services, ("app", "peer"))

    def test_fixture_change_is_rejected(self):
        original = Path.read_text

        def changed(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            return "unexpected" if path.name == "fixture.env" else value

        repository = Path(__file__).parents[2]
        with patch.object(Path, "read_text", changed), self.assertRaisesRegex(ValueError, "fixture"):
            fixture_inputs(repository)


if __name__ == "__main__":
    unittest.main()
