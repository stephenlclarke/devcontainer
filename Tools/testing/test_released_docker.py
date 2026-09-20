"""Downloaded Docker orchestration tests; no daemon, downloads or VM startup."""

import json
import os
from pathlib import Path
import tempfile
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import released_docker
import released_engine
from case_evidence import canonical


class DockerCaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.store, self.guard, self.revalidate = Mock(), Mock(), Mock()
        self.inputs = {"tools": {"docker": "/prepared/docker"}, "pins": {},
                       "workload": {"path": "/retained/image.tar", "image": {"config": "sha256:" + "a" * 64,
                                    "manifest": "sha256:" + "b" * 64}}}
        self.revalidate.return_value = self.inputs
        self.identity = {"fixture": "E02-container-lifecycle"}
        self.case = released_docker.DockerCase(self.store, self.identity, self.inputs, self.parent,
                                              self.revalidate, self.guard, self.parent)
        self.vm = Mock(socket=self.parent / "docker.sock")
        self.vm.journal.receipt.return_value = {"verified": "test-double"}
        self.guest = Mock()

    def setup_case(self):
        with patch.object(released_docker, "DockerVM", return_value=self.vm), \
                patch.object(released_docker, "ReleasedGuest", return_value=self.guest) as guest, \
                patch.object(released_docker, "request", return_value=(200, canonical({"Id": "sha256:" + "b" * 64,
                    "Descriptor": {"digest": "sha256:" + "b" * 64}, "Os": "linux", "Architecture": "arm64"}))):
            self.case.setup()
        return guest

    def test_setup_loads_exact_retained_workload_and_uses_shared_fixture(self):
        guest = self.setup_case()
        self.vm.start.assert_called_once_with()
        self.guard.begin.assert_called_once_with(self.case.owner)
        arguments = self.vm.command.call_args.args[1]
        self.assertEqual(arguments[-4:], ["image", "load", "--input", "/retained/image.tar"])
        self.assertEqual(guest.call_args.args[2], self.case.root / "workspace")
        self.assertEqual(guest.call_args.kwargs["image_id"], "sha256:" + "b" * 64)
        self.assertEqual(self.case.operation(), self.guest.operation.return_value)
        self.vm.verify.assert_called_once_with()

    def test_cleanup_stops_guest_then_vm_retains_receipt_and_clears_guard(self):
        self.setup_case()
        events = []
        self.guest.cleanup.side_effect = lambda: events.append("guest")
        self.vm.stop.side_effect = lambda: events.append("vm")
        self.guard.clear.side_effect = lambda _: events.append("clear")
        self.assertEqual(self.case.cleanup(), {"status": "passed", "remainingOwnedResources": []})
        self.assertEqual(events, ["guest", "vm", "clear"])
        self.assertFalse(self.case.root.exists())
        self.assertIn("docker-journal.json", [call.args[1] for call in self.store.attach.call_args_list])
        self.assertEqual(self.vm.journal.put.call_args.args[0], "docker-cleanup-authorized.json")
        authorization = json.loads(self.vm.journal.put.call_args.args[1])
        self.assertIn("rootIdentity", authorization)

    def test_cleanup_authorization_failure_preserves_root_and_guard(self):
        self.setup_case()
        self.vm.journal.put.side_effect = OSError("retention failed")
        with self.assertRaisesRegex(OSError, "retention failed"):
            self.case.cleanup()
        self.assertTrue(self.case.root.exists())
        self.guard.clear.assert_not_called()

    def test_uncertain_guest_cleanup_preserves_running_vm_and_quarantine(self):
        self.setup_case()
        self.guest.cleanup.side_effect = ValueError("pending deletion")
        with self.assertRaisesRegex(ValueError, "pending deletion"):
            self.case.cleanup()
        self.vm.stop.assert_not_called()
        self.guard.clear.assert_not_called()
        self.assertTrue(self.case.root.is_dir())

    def test_uncertain_vm_cleanup_keeps_root_but_retains_diagnostics(self):
        self.setup_case()
        self.vm.stop.side_effect = ValueError("pending shutdown")
        with self.assertRaisesRegex(ValueError, "pending shutdown"):
            self.case.cleanup()
        self.guard.clear.assert_not_called()
        self.assertTrue(self.case.root.is_dir())
        self.assertIn("docker-journal.json", [call.args[1] for call in self.store.attach.call_args_list])

    def test_engine_only_case_does_not_load_workload(self):
        self.identity["fixture"] = "E01-engine-negotiation"
        self.setup_case()
        self.vm.command.assert_not_called()
        self.assertIsNone(self.case.guest)
        with patch.object(released_docker, "engine_negotiation", return_value={"verified": "true"}):
            self.assertEqual(self.case.operation(), {"verified": "true"})

    def test_d01_uses_official_cli_adapter_not_engine_negotiation(self):
        self.identity["fixture"] = "D01-image-config"
        with patch.object(released_docker, "DevcontainerReference", return_value=self.guest) as adapter:
            self.setup_case()
        self.assertEqual(adapter.call_args.args, (self.vm, self.inputs, self.case.owner))
        self.guest.setup.assert_called_once_with()
        self.vm.command.assert_not_called()
        self.assertEqual(self.case.operation(), self.guest.operation.return_value)

    def test_d02_uses_build_adapter_before_any_generic_engine_path(self):
        self.identity["fixture"] = "D02-dockerfile-config"
        with patch.object(released_docker, "DevcontainerBuildReference", return_value=self.guest) as adapter:
            self.setup_case()
        self.assertEqual(adapter.call_args.args, (self.vm, self.inputs, self.case.owner))
        self.guest.setup.assert_called_once_with()
        self.vm.command.assert_not_called()

    def test_setup_rejects_different_loaded_image(self):
        with patch.object(released_docker, "DockerVM", return_value=self.vm), \
                patch.object(released_docker, "request", return_value=(200, b'{"Id":"wrong"}')), \
                self.assertRaisesRegex(ValueError, "exact admitted"):
            self.case.setup()
        self.assertIsNone(self.case.guest)

    def test_d03_uses_users_adapter_and_keeps_normal_owned_cleanup(self):
        self.identity["fixture"] = "D03-users-environment"
        with patch.object(released_docker, "DevcontainerUsersReference", return_value=self.guest) as adapter:
            self.setup_case()
        self.assertEqual(adapter.call_args.args, (self.vm, self.inputs, self.case.owner))
        self.guest.setup.assert_called_once_with()
        self.vm.command.assert_not_called()
        self.case.cleanup()
        self.guest.cleanup.assert_called_once_with()
        self.assertFalse(self.case.root.exists())

    def test_d04_uses_lifecycle_adapter_and_keeps_normal_owned_cleanup(self):
        self.identity["fixture"] = "D04-lifecycle-hooks"
        with patch.object(released_docker, "DevcontainerLifecycleReference", return_value=self.guest) as adapter:
            self.setup_case()
        self.assertEqual(adapter.call_args.args, (self.vm, self.inputs, self.case.owner))
        self.guest.setup.assert_called_once_with()
        self.vm.command.assert_not_called()
        self.case.cleanup()
        self.guest.cleanup.assert_called_once_with()
        self.assertFalse(self.case.root.exists())

    def test_c01_uses_compose_adapter_and_keeps_normal_owned_cleanup(self):
        self.identity["fixture"] = "C01-compose-service"
        with patch.object(released_docker, "DevcontainerComposeReference", return_value=self.guest) as adapter:
            self.setup_case()
        self.assertEqual(adapter.call_args.args, (self.vm, self.inputs, self.case.owner))
        self.guest.setup.assert_called_once_with()
        self.vm.command.assert_not_called()
        self.case.cleanup()
        self.guest.cleanup.assert_called_once_with()
        self.assertFalse(self.case.root.exists())

    def test_c02_uses_dependency_adapter_and_keeps_normal_owned_cleanup(self):
        self.identity["fixture"] = "C02-compose-dependencies"
        with patch.object(released_docker, "DevcontainerDependenciesReference", return_value=self.guest) as adapter:
            self.setup_case()
        self.assertEqual(adapter.call_args.args, (self.vm, self.inputs, self.case.owner))
        self.guest.setup.assert_called_once_with()
        self.vm.command.assert_not_called()
        self.case.cleanup()
        self.guest.cleanup.assert_called_once_with()
        self.assertFalse(self.case.root.exists())

    def test_d07_uses_reuse_adapter_and_keeps_normal_owned_cleanup(self):
        self.identity["fixture"] = "D07-reuse-cleanup"
        with patch.object(released_docker, "DevcontainerReuseReference", return_value=self.guest) as adapter:
            self.setup_case()
        self.assertEqual(adapter.call_args.args, (self.vm, self.inputs, self.case.owner))
        self.guest.setup.assert_called_once_with()
        self.vm.command.assert_not_called()
        self.case.cleanup()
        self.guest.cleanup.assert_called_once_with()
        self.assertFalse(self.case.root.exists())

    def test_matching_image_id_does_not_hide_wrong_manifest_or_architecture(self):
        identifier = "sha256:" + "b" * 64
        for descriptor, architecture in (("sha256:" + "c" * 64, "arm64"), (identifier, "amd64")):
            value = {"Id": identifier, "Descriptor": {"digest": descriptor}, "Os": "linux", "Architecture": architecture}
            with patch.object(released_docker, "DockerVM", return_value=self.vm), \
                    patch.object(released_docker, "request", return_value=(200, canonical(value))), \
                    self.assertRaisesRegex(ValueError, "exact admitted"):
                self.case.setup()
            self.assertIsNone(self.case.guest)
            self.assertEqual(json.loads(self.vm.journal.put.call_args.args[1])["inspection"], value)

    def test_changed_inputs_are_not_accepted_as_cleanup_success(self):
        self.setup_case()
        self.revalidate.return_value = {}
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            self.case.cleanup()
        self.guard.clear.assert_not_called()
        self.assertTrue(self.case.root.is_dir())


class DockerAdmissionTests(unittest.TestCase):
    def test_foreground_cases_admit_pinned_compose_without_devcontainer_cli(self):
        repository = Path(__file__).parents[2]
        lock = json.loads((repository / "Tools/bazel/docker-oracle.lock.json").read_text())
        pins = json.loads((repository / "Tests/Parity/manifest.json").read_text())["referencePins"]["docker"]
        with patch.object(released_docker, "require_retained", side_effect=lambda asset, *args: {
                "executables": {}, "repository": asset["repository"]}) as prepared, \
                patch.object(released_docker, "prepare_cli", return_value={"executables": {"docker": "/docker"}}), \
                patch.object(released_docker, "prepare_devcontainers") as devcontainers, \
                patch.object(released_docker, "require_image", return_value={"verified": True}):
            for name in ("E09-compose-foreground", "E10-compose-quiet"):
                with self.subTest(fixture=name):
                    prepared.reset_mock()
                    result = released_docker.admit_docker(lock, {}, pins, {"images": [{"name": "alpine-workload"}]},
                        Path("/scratch"), Path("/retained"), fixture=name, repository=repository)
                    self.assertEqual(result["compose"]["repository"], "docker/compose")
                    self.assertEqual(prepared.call_count, 4)
                    self.assertNotIn("devcontainerFixture", result)
                    devcontainers.assert_not_called()
                    with self.assertRaisesRegex(ValueError, "pinned published"):
                        released_docker.admit_docker(lock, {}, {**pins, "composeVersion": "other"},
                            {"images": [{"name": "alpine-workload"}]}, Path("/scratch"), Path("/retained"),
                            fixture=name, repository=repository)

    def test_c02_admits_both_exact_workload_images_and_published_compose(self):
        repository = Path(__file__).parents[2]
        lock = json.loads((repository / "Tools/bazel/docker-oracle.lock.json").read_text())
        pins = json.loads((repository / "Tests/Parity/manifest.json").read_text())["referencePins"]["docker"]
        images = json.loads((repository / "Tools/bazel/guest-images.lock.json").read_text())
        with patch.object(released_docker, "require_retained", return_value={"executables": {}}), \
                patch.object(released_docker, "prepare_cli", return_value={"executables": {"docker": "/docker"}}), \
                patch.object(released_docker, "prepare_devcontainers", return_value={"verified": True}), \
                patch.object(released_docker, "require_image", side_effect=lambda image, _: {"image": image}):
            result = released_docker.admit_docker(lock, {}, pins, images, Path("/scratch"), Path("/retained"),
                                                 fixture="C02-compose-dependencies", repository=repository)
            self.assertEqual(result["workload"]["image"]["name"], "alpine-workload")
            self.assertEqual(result["dependencyWorkload"]["image"]["name"], "python-workload")
            self.assertIn("compose", result)
            self.assertIn("service_healthy", result["devcontainerFixture"]["compose"])
            for dependencies in ([], [{"name": "python-workload"}] * 2):
                with self.assertRaisesRegex(ValueError, "dependency image"):
                    released_docker.admit_docker(lock, {}, pins, {"images": [{"name": "alpine-workload"}, *dependencies]},
                        Path("/scratch"), Path("/retained"), fixture="C02-compose-dependencies", repository=repository)

    def test_c01_admits_published_compose_and_unchanged_configuration(self):
        repository = Path(__file__).parents[2]
        lock = json.loads((repository / "Tools/bazel/docker-oracle.lock.json").read_text())
        pins = json.loads((repository / "Tests/Parity/manifest.json").read_text())["referencePins"]["docker"]
        with patch.object(released_docker, "require_retained", side_effect=lambda asset, *args: {
                "executables": {}, "repository": asset["repository"]}) as prepared, \
                patch.object(released_docker, "prepare_cli", return_value={"executables": {"docker": "/docker"}}), \
                patch.object(released_docker, "prepare_devcontainers", return_value={"verified": True}), \
                patch.object(released_docker, "require_image", return_value={"verified": True}):
            result = released_docker.admit_docker(lock, {}, pins, {"images": [{"name": "alpine-workload"}]},
                Path("/scratch"), Path("/retained"), fixture="C01-compose-service", repository=repository)
            self.assertEqual(result["compose"]["repository"], "docker/compose")
            self.assertEqual(prepared.call_count, 4)
            self.assertIn("COMPOSE_VALUE: compose-service", result["devcontainerFixture"]["compose"])
            with self.assertRaisesRegex(ValueError, "pinned published"):
                released_docker.admit_docker(lock, {}, {**pins, "composeVersion": "other"},
                    {"images": [{"name": "alpine-workload"}]}, Path("/scratch"), Path("/retained"),
                    fixture="C01-compose-service", repository=repository)

    def test_d05_admits_ubuntu_and_original_feature_lock(self):
        repository = Path(__file__).parents[2]
        lock = json.loads((repository / "Tools/bazel/docker-oracle.lock.json").read_text())
        images = json.loads((repository / "Tools/bazel/guest-images.lock.json").read_text())
        with patch.object(released_docker, "require_retained", return_value={"executables": {}}), \
                patch.object(released_docker, "prepare_cli", return_value={"executables": {"docker": "/docker"}}), \
                patch.object(released_docker, "prepare_devcontainers", return_value={"verified": True}), \
                patch.object(released_docker, "require_image", side_effect=lambda image, _: {"image": image}):
            result = released_docker.admit_docker(lock, {}, {}, images, Path("/scratch"), Path("/retained"),
                                                 fixture="D05-features", repository=repository)
        self.assertEqual(result["workload"]["image"]["name"], "ubuntu-workload")
        self.assertIn("2.5.9", result["devcontainerFixture"]["lockfile"])
        self.assertNotIn("dockerfile", result["devcontainerFixture"])

    def test_d04_admission_binds_original_host_and_guest_hooks_without_builder(self):
        repository = Path(__file__).parents[2]
        lock = json.loads((repository / "Tools/bazel/docker-oracle.lock.json").read_text())
        with patch.object(released_docker, "require_retained", return_value={"executables": {}}), \
                patch.object(released_docker, "prepare_cli", return_value={"executables": {"docker": "/docker"}}), \
                patch.object(released_docker, "prepare_devcontainers", return_value={"verified": True}), \
                patch.object(released_docker, "require_image", return_value={"verified": True}):
            result = released_docker.admit_docker(lock, {}, {}, {"images": [{"name": "alpine-workload"}]},
                Path("/scratch"), Path("/retained"), fixture="D04-lifecycle-hooks", repository=repository)
        root = repository / "Tests/Parity/fixtures/D04-lifecycle-hooks"
        self.assertEqual(result["devcontainerFixture"], {
            "configuration": (root / ".devcontainer/devcontainer.json").read_text(),
            "probe": (root / "probe.sh").read_text()})
        self.assertNotIn("builder", result)

    def test_d03_admission_binds_nonroot_configuration_and_dockerfile(self):
        repository = Path(__file__).parents[2]
        lock = json.loads((repository / "Tools/bazel/docker-oracle.lock.json").read_text())
        with patch.object(released_docker, "require_retained", return_value={"executables": {}}), \
                patch.object(released_docker, "prepare_cli", return_value={"executables": {"docker": "/docker"}}), \
                patch.object(released_docker, "prepare_devcontainers", return_value={"verified": True}), \
                patch.object(released_docker, "require_image", return_value={"verified": True}):
            result = released_docker.admit_docker(lock, {}, {}, {"images": [{"name": "alpine-workload"}]},
                Path("/scratch"), Path("/retained"), fixture="D03-users-environment", repository=repository)
        self.assertIn("adduser -D -u 1000", result["devcontainerFixture"]["dockerfile"])
        config = json.loads(result["devcontainerFixture"]["configuration"])
        self.assertEqual(config["remoteUser"], "vscode")
        self.assertFalse(config["updateRemoteUserUID"])
        self.assertEqual(config["remoteEnv"]["EXPANDED_VALUE"], "${containerEnv:CONTAINER_VALUE}")

    def test_d02_admission_includes_exact_dockerfile_source(self):
        repository = Path(__file__).parents[2]
        lock = json.loads((repository / "Tools/bazel/docker-oracle.lock.json").read_text())
        with patch.object(released_docker, "require_retained", return_value={"executables": {}}), \
                patch.object(released_docker, "prepare_cli", return_value={"executables": {"docker": "/docker"}}), \
                patch.object(released_docker, "prepare_devcontainers", return_value={"verified": True}), \
                patch.object(released_docker, "require_image", return_value={"verified": True}):
            result = released_docker.admit_docker(lock, {}, {}, {"images": [{"name": "alpine-workload"}]},
                Path("/scratch"), Path("/retained"), fixture="D02-dockerfile-config", repository=repository)
        self.assertIn("PARITY_BUILD_ARG", result["devcontainerFixture"]["dockerfile"])
        self.assertEqual(result["devcontainers"], {"verified": True})

    def test_admission_uses_only_retained_pinned_releases(self):
        repository = Path(__file__).parents[2]
        lock = json.loads((repository / "Tools/bazel/docker-oracle.lock.json").read_text())
        assets = [dict(executables={"colima": "/colima"}), dict(executables={"limactl": "/limactl"},
                  files={"guest-agent": "/agent"}), dict(executables={}, files={"disk-image": "/image"})]
        with patch.object(released_docker, "require_retained", side_effect=assets + assets) as retained, \
                patch.object(released_docker, "prepare_cli", return_value={"executables": {"docker": "/docker"}}) as cli, \
                patch.object(released_docker, "prepare_devcontainers", return_value={"verified": True}) as reference, \
                patch.object(released_docker, "require_image", return_value={"verified": True}):
            inputs = released_docker.admit_docker(lock, {}, {}, {"images": [{"name": "alpine-workload"}]},
                                                  Path("/scratch"), Path("/retained"))
            reference.assert_not_called()
            d01 = released_docker.admit_docker(lock, {}, {}, {"images": [{"name": "alpine-workload"}]},
                                               Path("/scratch"), Path("/retained"), fixture="D01-image-config", repository=repository)
        self.assertEqual(retained.call_count, 6)
        self.assertEqual(set(inputs["tools"]), {"colima", "limactl", "guest-agent", "disk-image", "docker"})
        self.assertEqual(cli.call_args.kwargs, {"offline": True})
        self.assertEqual(reference.call_args.kwargs, {"offline": True})
        self.assertEqual(d01["devcontainers"], {"verified": True})
        self.assertIn("DEVCONTAINER_PARITY", d01["devcontainerFixture"]["probe"])

    def test_unreviewed_release_set_is_rejected_before_preparation(self):
        with patch.object(released_docker, "validate_lock", return_value=[]), \
                patch.object(released_docker, "prepare_cli") as cli, self.assertRaisesRegex(ValueError, "reviewed"):
            released_docker.admit_docker({}, {}, {}, {}, Path("/scratch"), Path("/retained"))
        cli.assert_not_called()


class DockerEntryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.scratch, self.retained = root / "scratch", root / "retained"
        self.scratch.mkdir()
        self.retained.mkdir()
        self.args = SimpleNamespace(candidate_invocation=None, campaign="docker-component", fixture="E01-engine-negotiation")
        self.real_stat = Path.stat

    def separate_volumes(self, path, **kwargs):
        info = self.real_stat(path, **kwargs)
        if path == self.retained:
            fields = list(info)
            fields[2] += 1
            return os.stat_result(fields)
        return info

    def invoke(self):
        with patch.object(released_engine, "SSD", self.scratch), patch.object(released_engine, "RETAINED", self.retained), \
                patch.object(released_docker, "require_owned_volume", return_value={"uuid": "fixture"}), \
                patch.object(released_docker, "runtime_lease", return_value=nullcontext()), \
                patch.object(released_docker, "cancellation", return_value=nullcontext()):
            return released_docker.run_docker(self.args)

    def test_rejects_candidate_and_same_volume_before_admission(self):
        self.args.candidate_invocation = "not-a-reference"
        with self.assertRaisesRegex(ValueError, "released binaries"):
            self.invoke()
        self.args.candidate_invocation = None
        with self.assertRaisesRegex(ValueError, "separate canonical"):
            self.invoke()

    def test_rejects_redirected_scratch_before_case_creation(self):
        (self.scratch / "live").symlink_to(self.retained, target_is_directory=True)
        with patch.object(Path, "stat", lambda path, **kw: self.separate_volumes(path, **kw)), \
                self.assertRaisesRegex(ValueError, "Symlinked"):
            self.invoke()
        self.assertFalse(list(self.retained.glob("docker-*")))

    def test_entry_binds_identity_and_emits_case_evidence(self):
        result = {"status": "passed", "durationsNS": {"setup": 1, "operation": 2, "cleanup": 3}, "errors": []}
        output = self.scratch / "junit.xml"
        with patch.object(Path, "stat", lambda path, **kw: self.separate_volumes(path, **kw)), \
                patch.object(released_docker, "admit_docker", return_value={"verified": True}) as admit, \
                patch.object(released_docker, "DockerCase") as case, \
                patch.object(released_docker, "run_case", return_value=result), \
                patch.object(released_docker, "print") as printed, \
                patch.dict(os.environ, {"XML_OUTPUT_FILE": str(output)}), self.assertRaises(SystemExit) as exited:
            self.invoke()
        self.assertEqual(exited.exception.code, 0)
        self.assertEqual(admit.call_count, 1)
        identity = case.call_args.args[1]
        self.assertEqual(identity["lane"], "docker")
        self.assertEqual(identity["campaign"], self.args.campaign)
        self.assertTrue(output.is_file())
        self.assertEqual(json.loads(printed.call_args.args[0])["scope"], "released-docker-case-only")


if __name__ == "__main__":
    unittest.main()
