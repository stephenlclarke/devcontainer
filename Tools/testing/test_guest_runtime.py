"""Released guest admission/provisioning tests; no host services or VMs."""

import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from case_evidence import canonical
import guest_runtime
from guest_runtime import ReleasedGuest, admit_guest, diagnostic_snapshot, guest_diagnostic_plan, require_guest_cleanup
from service_journal import ServiceJournal


class GuestRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.owner = {"root": str(self.root), "identity": {"fixture": "E05-archive-copy"}}
        self.journal = ServiceJournal(self.root / "private.sqlite", self.owner, create=True)
        self.runtime = Mock(journal=self.journal)
        kernel = self.root / "retained-kernel"
        kernel.write_bytes(b"released kernel fixture")
        self.inputs = {"kernel": {"files": {"kernel": str(kernel)}},
                       "initialization": {"path": "/retained/init.tar"},
                       "workload": {"path": "/retained/alpine.tar", "image": {"config": "sha256:" + "a" * 64}}}
        self.case = ReleasedGuest(self.inputs, "E05-archive-copy", self.root, self.owner,
                                   self.runtime, "/released/container", self.root / "socket")
        request = patch('guest_runtime.request', return_value=(200, canonical({
            'MinAPIVersion': '1.44', 'ApiVersion': '1.53'})))
        self.request = request.start()
        self.addCleanup(request.stop)

    def test_fixed_guest_contract_accepts_stock_and_newer_oracle_without_changing_version(self):
        for maximum in ('1.53', '1.54'):
            self.request.return_value = (200, canonical({'MinAPIVersion': '1.44', 'ApiVersion': maximum}))
            self.assertEqual(guest_runtime.require_guest_api(self.root / 'socket'),
                             {'requested': '1.53', 'minimum': '1.44', 'maximum': maximum})

    def test_build_requires_apple_builder_but_docker_uses_its_owned_vm(self):
        self.inputs['workload']['image'].update(repository='docker.io/library/alpine', manifest='sha256:' + 'b' * 64)
        case = ReleasedGuest(self.inputs, 'E04-image-build', self.root, self.owner, self.runtime,
                             '/released/container', self.root / 'socket')
        with self.assertRaisesRegex(ValueError, 'admitted private builder'):
            case.operation()
        for container in ('/released/container', ''):
            case.container = container
            case.builder = Mock() if container else None
            with patch('guest_runtime.BuildFixture') as fixture, patch('guest_runtime.deadline') as deadline:
                self.assertEqual(case.operation(), fixture.return_value.operation.return_value)
                self.assertEqual(fixture.call_args.args[5], 'docker.io/library/alpine@sha256:' + 'b' * 64)
                deadline.assert_called_once_with(390)
                self.assertEqual(case.cleanup(), fixture.return_value.cleanup.return_value)
                if case.builder:
                    self.assertEqual(fixture.call_args.kwargs['before_submit'], case.builder.verify_for_build)
                    case.builder.cleanup.assert_called_once()

    def test_compose_foreground_selects_exact_bundle_provider_and_quiet_mode(self):
        self.inputs['composeCandidate'] = {'executables': {'compose': '/native/compose'}}
        self.inputs['compose'] = {'executables': {'docker-compose': '/reference/compose'}}
        for name, quiet in (('E09-compose-foreground', False), ('E10-compose-quiet', True)):
            self.case.fixture = name
            for container, executable, install in (('/provider/bin/container', '/native/compose', Path('/provider')),
                                                   ('', '/reference/compose', None)):
                self.case.container = container
                with self.subTest(fixture=name, container=container), \
                        patch('guest_runtime.ComposeForegroundFixture') as fixture:
                    self.assertEqual(self.case.operation(), fixture.return_value.operation.return_value)
                    self.assertEqual(fixture.call_args.kwargs['executable'], executable)
                    self.assertEqual(fixture.call_args.kwargs['provider_install'], install)
                    self.assertEqual(fixture.call_args.kwargs['quiet'], quiet)
                    self.assertEqual(self.case.cleanup(), fixture.return_value.cleanup.return_value)

    def test_uncertain_image_cleanup_prevents_builder_shutdown(self):
        self.case.guest, self.case.builder = Mock(), Mock()
        self.case.guest.cleanup.side_effect = ValueError('uncertain build')
        with self.assertRaisesRegex(ValueError, 'uncertain build'):
            self.case.cleanup()
        self.case.builder.cleanup.assert_not_called()

    def test_fault_adapter_reuses_owned_guest_with_whole_operation_deadline(self):
        self.case.fixture = 'F01-fault-recovery'
        with patch('guest_runtime.FaultFixture') as fixture, \
                patch('guest_runtime.deadline') as deadline:
            self.assertEqual(self.case.operation(), fixture.return_value.operation.return_value)
            deadline.assert_called_once_with(90)
            self.assertEqual(self.case.cleanup(), fixture.return_value.cleanup.return_value)

    def test_d01_setup_is_separate_from_measured_cli_operation(self):
        self.case.fixture = 'D01-image-config'
        with self.assertRaisesRegex(ValueError, 'setup did not complete'):
            self.case.operation()
        with patch('devcontainer_candidate.CandidateCommands') as commands, \
                patch('devcontainer_candidate.DevcontainerCandidate') as fixture:
            self.case.setup_devcontainer()
            fixture.return_value.setup.assert_called_once()
            fixture.return_value.operation.assert_not_called()
            self.assertEqual(self.case.operation(), fixture.return_value.operation.return_value)
            self.assertEqual(commands.call_args.args, (self.root, self.root / 'socket', self.runtime, '/released/container'))
            self.assertEqual(self.case.cleanup(), fixture.return_value.cleanup.return_value)
        with self.assertRaisesRegex(ValueError, 'fresh candidate'):
            self.case.setup_devcontainer()

    def test_d04_uses_lifecycle_adapter_without_builder(self):
        self.case.fixture = 'D04-lifecycle-hooks'
        with patch('devcontainer_candidate.DevcontainerLifecycleCandidate') as fixture:
            self.case.setup_devcontainer()
            fixture.return_value.setup.assert_called_once()
            self.assertEqual(self.case.operation(), fixture.return_value.operation.return_value)
            self.assertIsNone(self.case.builder)
            self.case.cleanup()
            fixture.return_value.cleanup.assert_called_once()

    def test_c01_uses_native_compose_adapter_and_owned_cleanup(self):
        self.case.fixture = 'C01-compose-service'
        with patch('devcontainer_candidate.DevcontainerComposeCandidate') as fixture:
            self.case.setup_devcontainer()
            fixture.return_value.setup.assert_called_once()
            self.assertEqual(self.case.operation(), fixture.return_value.operation.return_value)
            self.assertEqual(self.case.cleanup(), fixture.return_value.cleanup.return_value)
            self.assertIsNone(self.case.builder)
            fixture.return_value.cleanup.assert_called_once()

    def test_c02_uses_dependency_adapter_and_owned_cleanup(self):
        self.case.fixture = 'C02-compose-dependencies'
        with patch('devcontainer_candidate.DevcontainerDependenciesCandidate') as fixture:
            self.case.setup_devcontainer()
            fixture.return_value.setup.assert_called_once()
            self.assertEqual(self.case.operation(), fixture.return_value.operation.return_value)
            self.assertEqual(self.case.cleanup(), fixture.return_value.cleanup.return_value)
            self.assertIsNone(self.case.builder)
            fixture.return_value.cleanup.assert_called_once()

    def test_d02_requires_owned_builder_and_removes_guest_before_builder(self):
        self.case.fixture = 'D02-dockerfile-config'
        with self.assertRaisesRegex(ValueError, 'private builder'):
            self.case.setup_devcontainer()
        self.case.builder = Mock()
        events = []
        self.case.builder.cleanup.side_effect = lambda: events.append('builder')
        with patch('devcontainer_candidate.DevcontainerBuildCandidate') as fixture:
            self.case.setup_devcontainer()
            fixture.return_value.setup.assert_called_once()
            self.assertEqual(fixture.call_args.kwargs['before_build'], self.case.builder.verify_for_build)
            self.assertEqual(self.case.operation(), fixture.return_value.operation.return_value)
            fixture.return_value.cleanup.side_effect = lambda: events.append('guest')
            self.case.cleanup()
        self.assertEqual(events, ['guest', 'builder'])

    def test_d06_uses_ports_adapter_without_builder(self):
        self.case.fixture = 'D06-ports'
        with patch('devcontainer_candidate.DevcontainerPortsCandidate') as fixture:
            self.case.setup_devcontainer()
            fixture.return_value.setup.assert_called_once()
            self.assertEqual(self.case.operation(), fixture.return_value.operation.return_value)
            self.assertIsNone(self.case.builder)
            self.case.cleanup()
            fixture.return_value.cleanup.assert_called_once()

    def test_d07_uses_reuse_adapter_without_builder(self):
        self.case.fixture = 'D07-reuse-cleanup'
        with patch('devcontainer_candidate.DevcontainerReuseCandidate') as fixture:
            self.case.setup_devcontainer()
            fixture.return_value.setup.assert_called_once()
            self.assertEqual(self.case.operation(), fixture.return_value.operation.return_value)
            self.assertIsNone(self.case.builder)
            self.case.cleanup()
            fixture.return_value.cleanup.assert_called_once()

    def test_d03_requires_owned_builder_and_preserves_cleanup_order(self):
        self.case.fixture = 'D03-users-environment'
        with self.assertRaisesRegex(ValueError, 'private builder'):
            self.case.setup_devcontainer()
        self.case.builder = Mock()
        events = []
        self.case.builder.cleanup.side_effect = lambda: events.append('builder')
        with patch('devcontainer_candidate.DevcontainerUsersCandidate') as fixture:
            self.case.setup_devcontainer()
            fixture.return_value.setup.assert_called_once()
            self.assertEqual(fixture.call_args.kwargs['before_build'], self.case.builder.verify_for_build)
            self.assertEqual(self.case.operation(), fixture.return_value.operation.return_value)
            fixture.return_value.cleanup.side_effect = lambda: events.append('guest')
            self.case.cleanup()
        self.assertEqual(events, ['guest', 'builder'])

    def test_fault_recovery_requires_all_resource_closure(self):
        records = {'fault-intent.json': canonical({'owner': 'case'})}
        with self.assertRaisesRegex(ValueError, 'explicit reconciliation'):
            require_guest_cleanup(records)
        records['fault-removed.json'] = canonical({'intentSHA256': guest_runtime.digest(records['fault-intent.json']),
                                                  'absent': True})
        require_guest_cleanup(records)

    def test_d05_requires_builder_and_selects_features_adapter(self):
        self.case.fixture = 'D05-features'
        with self.assertRaisesRegex(ValueError, 'private builder'):
            self.case.setup_devcontainer()
        self.case.builder = Mock()
        with patch('devcontainer_candidate.DevcontainerFeaturesCandidate') as fixture:
            self.case.setup_devcontainer()
            fixture.return_value.setup.assert_called_once()
            self.assertEqual(fixture.call_args.kwargs['before_build'], self.case.builder.verify_for_build)
            self.assertEqual(self.case.operation(), fixture.return_value.operation.return_value)
            self.case.cleanup()
            fixture.return_value.cleanup.assert_called_once()
            self.case.builder.cleanup.assert_called_once()

    def test_recovery_requires_both_build_output_and_builder_closure(self):
        for prefix in ('e04-images', 'e04-builder'):
            intent = canonical({'owner': 'test'})
            records = {prefix + '-intent.json': intent}
            with self.assertRaisesRegex(ValueError, 'explicit reconciliation'):
                require_guest_cleanup(records)
            removed = {'absent': True}
            if prefix == 'e04-builder':
                removed['intentSHA256'] = guest_runtime.digest(intent)
            records[prefix + '-removed.json'] = canonical(removed)
            require_guest_cleanup(records)

    def test_admitted_manifest_identity_is_preserved_without_rewriting_config_pin(self):
        manifest = "sha256:" + "b" * 64
        self.inputs["workload"]["image"]["manifest"] = manifest
        case = ReleasedGuest(self.inputs, "E05-archive-copy", self.root, self.owner,
                             self.runtime, "", self.root / "socket", image_id=manifest)
        with patch('guest_runtime.GuestFixture') as factory:
            case.operation()
        self.assertEqual(factory.call_args.args[2], manifest)
        self.assertEqual(self.inputs["workload"]["image"]["config"], "sha256:" + "a" * 64)
        self.assertEqual(self.case.image_id, "sha256:" + "a" * 64)
        with self.assertRaisesRegex(ValueError, "outside the admitted"):
            ReleasedGuest(self.inputs, "E05-archive-copy", self.root, self.owner,
                          self.runtime, "", self.root / "socket", image_id="sha256:" + "c" * 64)

    def test_invalid_or_incompatible_api_cannot_launch_guest(self):
        for status, value in ((500, {}), (200, []), (200, {}),
                              (200, {'MinAPIVersion': '1.54', 'ApiVersion': '1.54'}),
                              (200, {'MinAPIVersion': '1.44', 'ApiVersion': '1.52'}),
                              (200, {'MinAPIVersion': '1.60', 'ApiVersion': '1.44'}),
                              (200, {'MinAPIVersion': '1.44', 'ApiVersion': True})):
            self.request.return_value = (status, canonical(value))
            with patch('guest_runtime.GuestFixture') as factory, self.assertRaises(ValueError):
                self.case.operation()
            factory.assert_not_called()
        self.assertNotIn('guest-api.json', self.journal.records())

    def locks(self):
        directory = Path(__file__).parents[1] / "bazel"
        return [json.loads((directory / name).read_text())
                for name in ("guest-kernel.lock.json", "guest-images.lock.json")]

    def test_complete_stock_admission_preserves_every_input_identity(self):
        kernel, images = self.locks()
        with patch("guest_runtime.require_retained", return_value={"kernel": "verified"}) as k, \
                patch("guest_runtime.require_image", side_effect=lambda image, root: {"image": image, "root": str(root)}) as i:
            result = admit_guest(kernel, images, "apple-stock", self.root)
        self.assertEqual(result["kernel"], {"kernel": "verified"})
        by_name = {image["name"]: image for image in images["images"]}
        self.assertEqual(result["initialization"]["image"], by_name["stock-vminit"])
        self.assertEqual(result["workload"]["image"], by_name["alpine-workload"])
        self.assertEqual(i.call_count, 2)
        self.assertEqual(k.call_args.args[2], self.root / "prepared-releases")

    def test_missing_enhanced_image_never_falls_back_to_stock_or_mutates_storage(self):
        kernel, images = self.locks()
        images["images"] = [image for image in images["images"] if image["name"] != "enhanced-vminit"]
        with patch("guest_runtime.require_retained") as k, patch("guest_runtime.require_image") as i:
            with self.assertRaisesRegex(ValueError, "missing or ambiguous"):
                admit_guest(kernel, images, "container-compose", self.root)
            k.assert_not_called()
            i.assert_not_called()

    def test_complete_enhanced_admission_selects_only_its_exact_image(self):
        kernel, images = self.locks()
        with patch("guest_runtime.require_retained", return_value={}), \
                patch("guest_runtime.require_image", side_effect=lambda image, _: {"image": image}) as selected:
            result = admit_guest(kernel, images, "container-compose", self.root)
        self.assertEqual(result["initialization"]["image"]["name"], "enhanced-vminit")
        self.assertEqual([call.args[0]["name"] for call in selected.call_args_list],
                         ["enhanced-vminit", "alpine-workload"])

    def test_d05_admission_selects_ubuntu_without_changing_other_fixtures(self):
        kernel, images = self.locks()
        with patch("guest_runtime.require_retained", return_value={"kernel": "verified"}), \
                patch("guest_runtime.require_image", side_effect=lambda image, _: {"image": image}):
            result = admit_guest(kernel, images, "apple-stock", self.root, fixture="D05-features")
        self.assertEqual(result["workload"]["image"]["name"], "ubuntu-workload")
        self.assertEqual(result["initialization"]["image"]["name"], "stock-vminit")
        images["images"] = [image for image in images["images"] if image["name"] != "ubuntu-workload"]
        with self.assertRaisesRegex(ValueError, "missing or ambiguous"):
            admit_guest(kernel, images, "apple-stock", self.root, fixture="D05-features")

    def test_c02_admits_distinct_dependency_image_and_refuses_missing_pin(self):
        kernel, images = self.locks()
        with patch("guest_runtime.require_retained", return_value={"kernel": "verified"}), \
                patch("guest_runtime.require_image", side_effect=lambda image, _: {"image": image}):
            result = admit_guest(kernel, images, "apple-stock", self.root, fixture="C02-compose-dependencies")
            self.assertEqual(result["workload"]["image"]["name"], "alpine-workload")
            self.assertEqual(result["dependencyWorkload"]["image"]["name"], "python-workload")
            images["images"] = [item for item in images["images"] if item["name"] != "python-workload"]
            with self.assertRaisesRegex(ValueError, "dependency image"):
                admit_guest(kernel, images, "apple-stock", self.root, fixture="C02-compose-dependencies")

    def test_malformed_locks_unknown_provider_and_wrong_init_reference_fail(self):
        kernel, images = self.locks()
        for lane, k, i in [("other", kernel, images), ("apple-stock", kernel, {}),
                            ("apple-stock", kernel, dict(images, images=images["images"] * 2)),
                            ("apple-stock", dict(kernel, assets=kernel["assets"] * 2), images)]:
            with self.subTest(lane=lane), self.assertRaises(ValueError):
                admit_guest(k, i, lane, self.root)
        wrong = copy.deepcopy(images)
        next(image for image in wrong["images"] if image["name"] == "stock-vminit")["reference"] = "ghcr.io/apple/containerization/vminit:latest"
        with self.assertRaisesRegex(ValueError, "reference differs"):
            admit_guest(kernel, wrong, "apple-stock", self.root)
        wrong_kernel = copy.deepcopy(kernel)
        wrong_kernel["assets"][0]["tag"] = "3.33.0"
        with self.assertRaisesRegex(ValueError, "reviewed recommended"):
            admit_guest(wrong_kernel, images, "apple-stock", self.root)

    def test_provision_installs_only_admitted_data_then_verifies_private_kernel(self):
        installed = self.root / "container/kernels/vmlinux"
        installed.parent.mkdir(parents=True)
        installed.write_bytes(Path(self.inputs["kernel"]["files"]["kernel"]).read_bytes())
        (installed.parent / "default.kernel-arm64").symlink_to(installed)
        with patch.object(self.case, "command") as command:
            self.case.provision()
        self.assertEqual([call.args[0] for call in command.call_args_list],
                         ["guest-kernel", "guest-initialization", "guest-workload"])
        self.assertEqual(command.call_args_list[1].args[1], ["image", "load", "--input", "/retained/init.tar"])
        self.assertIn("guest-provisioned.json", self.journal.records())
        installed.write_bytes(b"wrong kernel")
        with patch.object(self.case, "command") as command, self.assertRaisesRegex(ValueError, "does not match"):
            self.case.provision()
        self.assertEqual(command.call_count, 1)

    def test_command_records_intent_before_launch_and_private_timing_after_verified_stop(self):
        child = Mock()
        child.process.pid, child.process.wait.return_value = 42, 0
        child.start.side_effect = lambda *_, **__: self.assertIn("guest-kernel-intent.json", self.journal.records())
        with patch("guest_runtime.OwnedProcess", return_value=child):
            self.case.command("guest-kernel", ["system", "kernel", "set"])
        child.start.assert_called_once()
        child.process.wait.assert_called_once_with(timeout=60)
        child.stop.assert_called_once()
        records = self.journal.records()
        stopped = json.loads(records["guest-kernel-stopped.json"])
        self.assertTrue(stopped["verifiedStopped"])
        self.assertGreater(stopped["durationNS"], 0)
        self.assertIn("guest-kernel.log", records)
        require_guest_cleanup(records)

    def test_failed_command_and_timeout_stop_owned_child(self):
        for index, code in enumerate((1, subprocess.TimeoutExpired("fixture", 60))):
            child = Mock()
            child.process.pid = 42
            if isinstance(code, Exception):
                child.process.wait.side_effect = code
            else:
                child.process.wait.return_value = code
            expected_error = subprocess.TimeoutExpired if isinstance(code, Exception) else RuntimeError
            name = "command-" + str(index)
            with patch("guest_runtime.OwnedProcess", return_value=child), self.assertRaises(expected_error):
                self.case.command(name, ["image", "load"])
            child.stop.assert_called_once()
            self.assertIn("command-" + str(index) + ".log", self.journal.records())

    def test_uncertain_child_never_records_successful_stop(self):
        child = Mock()
        child.start.side_effect = KeyboardInterrupt()
        child.stop.side_effect = RuntimeError("uncertain child")
        with patch("guest_runtime.OwnedProcess", return_value=child), self.assertRaisesRegex(ValueError, "still live"):
            self.case.command("guest-kernel", ["system", "kernel", "set"])
        self.assertNotIn("guest-kernel-stopped.json", self.journal.records())
        records = self.journal.records()
        with self.assertRaisesRegex(ValueError, "process needs explicit"):
            require_guest_cleanup(records)

    def test_archive_and_lifecycle_use_shared_guest_ownership_then_cleanup(self):
        for fixture in ("E05-archive-copy", "E02-container-lifecycle"):
            case = ReleasedGuest(self.inputs, fixture, self.root, self.owner, self.runtime,
                                 "/released/container", self.root / "socket")
            with patch("guest_runtime.GuestFixture") as factory, patch("guest_runtime.lifecycle", return_value={"lifecycle": "ok"}):
                guest = factory.return_value
                guest.archive.return_value = {"archive": "ok"}
                observed = case.operation()
                self.assertEqual(observed, {"archive": "ok"} if fixture == "E05-archive-copy" else {"lifecycle": "ok"})
                self.assertEqual(factory.call_args.args[2], self.inputs["workload"]["image"]["config"])
                self.assertEqual(factory.call_args.args[3], '1.53')
                self.assertEqual(case.cleanup(), guest.cleanup.return_value)
                guest.cleanup.assert_called_once()
        self.assertEqual(self.case.cleanup()["status"], "passed")
        self.case.commands = [Mock()]
        self.case.cleanup()
        self.case.commands[0].stop.assert_called_once()

    def test_exec_uses_admitted_guest_and_preserves_binary_transfer_deadline(self):
        case = ReleasedGuest(self.inputs, "E03-exec-streams", self.root, self.owner, self.runtime,
                             "/released/container", self.root / "socket")
        with patch("guest_runtime.GuestFixture") as factory, patch("guest_runtime.exec_streams", return_value={"exec": "ok"}) as probe, \
                patch("guest_runtime.deadline") as deadline:
            self.assertEqual(case.operation(), {"exec": "ok"})
            self.assertEqual(factory.call_args.kwargs["command"], ("sleep", "600"))
            factory.return_value.setup.assert_called_once()
            probe.assert_called_once_with(factory.return_value)
            deadline.assert_called_once_with(360)
            self.assertEqual(case.cleanup(), factory.return_value.cleanup.return_value)

    def test_network_volume_adapter_is_bounded_and_cleanup_precedes_provider_stop(self):
        case = ReleasedGuest(self.inputs, "E06-network-volume", self.root, self.owner, self.runtime,
                             "/released/container", self.root / "socket")
        with patch("guest_runtime.NetworkVolumeFixture") as factory, patch("guest_runtime.deadline") as deadline:
            self.assertEqual(case.operation(), factory.return_value.operation.return_value)
            deadline.assert_called_once_with(180)
            self.assertEqual(factory.call_args.args[2], self.inputs["workload"]["image"]["config"])
            self.assertEqual(case.cleanup(), factory.return_value.cleanup.return_value)

    def test_init_attachment_retains_guest_for_cleanup_under_whole_operation_deadline(self):
        case = ReleasedGuest(self.inputs, "E07-init-attachment", self.root, self.owner, self.runtime,
                             "/released/container", self.root / "socket")
        with patch("guest_runtime.AttachmentFixture") as factory, patch("guest_runtime.deadline") as deadline:
            self.assertEqual(case.operation(), factory.return_value.operation.return_value)
            deadline.assert_called_once_with(150)
            self.assertEqual(factory.call_args.args[2], self.inputs["workload"]["image"]["config"])
            self.assertEqual(case.cleanup(), factory.return_value.cleanup.return_value)

    def test_terminal_foreground_retains_guest_and_bounds_the_operation(self):
        case = ReleasedGuest(self.inputs, "E08-foreground-terminal", self.root, self.owner, self.runtime,
                             "/released/container", self.root / "socket")
        with patch("guest_runtime.ForegroundFixture") as factory, patch("guest_runtime.deadline") as deadline:
            self.assertEqual(case.operation(), factory.return_value.operation.return_value)
            deadline.assert_called_once_with(90)
            self.assertEqual(factory.call_args.args[2], self.inputs["workload"]["image"]["config"])
            self.assertEqual(case.cleanup(), factory.return_value.cleanup.return_value)

    def test_service_recovery_requires_verified_network_volume_cleanup(self):
        intent = canonical({"fixture": "E06"})
        records = {"network-volume-intent.json": intent}
        with self.assertRaisesRegex(ValueError, "Network/volume resources"):
            require_guest_cleanup(records)
        records["network-volume-removed.json"] = canonical({"intentSHA256": "wrong", "absent": True})
        with self.assertRaises(ValueError):
            require_guest_cleanup(records)
        records["network-volume-removed.json"] = canonical({"intentSHA256": guest_runtime.digest(intent), "absent": True})
        require_guest_cleanup(records)

    def test_recovery_requires_verified_guest_removal_and_each_command_stop(self):
        require_guest_cleanup({})
        records = {"container-intent.json": canonical({"name": "owned"})}
        with self.assertRaisesRegex(ValueError, "Guest resource"):
            require_guest_cleanup(records)
        records["container-removed.json"] = canonical({"name": "foreign", "absent": True})
        with self.assertRaisesRegex(ValueError, "Guest resource"):
            require_guest_cleanup(records)
        records["container-removed.json"] = canonical({"name": "owned", "absent": True})
        require_guest_cleanup(records)
        for name in ("guest-kernel", "guest-initialization", "guest-workload"):
            records[name + "-intent.json"] = b"{}"
            with self.assertRaisesRegex(ValueError, "process needs explicit"):
                require_guest_cleanup(records)
            records[name + "-stopped.json"] = canonical({"verifiedStopped": True})
            with self.assertRaisesRegex(ValueError, "diagnostics must be retained"):
                require_guest_cleanup(records)
            records[name + ".log"] = b""
            records[name + "-log.json"] = canonical({"bytes": 0, "sha256": guest_runtime.digest(b""), "truncated": False})
            require_guest_cleanup(records)

    def test_oversized_command_log_retains_explicit_bounded_snapshot(self):
        child = Mock()
        child.process.pid, child.process.wait.return_value = 42, 0
        def output_log(_arguments, _root, output, **_kwargs):
            output.write(b"x" * (9 * 1024**2))
        child.start.side_effect = output_log
        with patch("guest_runtime.OwnedProcess", return_value=child):
            self.case.command("guest-kernel", ["system", "kernel", "set"])
        records = self.journal.records()
        self.assertEqual(len(records["guest-kernel.log"]), 1024**2)
        self.assertTrue(json.loads(records["guest-kernel-log.json"])["truncated"])
        require_guest_cleanup(records)

    def test_transient_log_write_failure_is_retried_during_cleanup(self):
        child = Mock()
        child.process.pid, child.process.wait.return_value = 42, 0
        put = self.journal.put
        def fail_log(name, data):
            if name == "guest-kernel.log":
                raise OSError("retention unavailable")
            put(name, data)
        with patch("guest_runtime.OwnedProcess", return_value=child), \
                patch.object(self.journal, "put", side_effect=fail_log), self.assertRaisesRegex(OSError, "retention"):
            self.case.command("guest-kernel", ["system", "kernel", "set"])
        self.assertTrue(self.case.pending_logs)
        self.case.cleanup()
        self.assertFalse(self.case.pending_logs)
        require_guest_cleanup(self.journal.records())

    def test_unknown_fixture_fails_before_provisioning(self):
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            ReleasedGuest(self.inputs, "unknown", self.root, self.owner, self.runtime,
                          "/released/container", self.root / "socket")

    def test_diagnostic_snapshot_refuses_alias_special_file_and_writable_log(self):
        original = self.root / "log"
        original.write_bytes(b"private diagnostic")
        alias = self.root / "alias"
        alias.symlink_to(original)
        with self.assertRaisesRegex(ValueError, "canonical"):
            diagnostic_snapshot(alias)
        alias.unlink()
        os.link(original, alias)
        with self.assertRaisesRegex(ValueError, "singly owned"):
            diagnostic_snapshot(original)
        alias.unlink()
        original.chmod(0o666)
        with self.assertRaisesRegex(ValueError, "regular file"):
            diagnostic_snapshot(original)
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, "regular file"):
            diagnostic_snapshot(fifo)

    def test_diagnostic_plan_never_replaces_partial_or_invalid_complete_evidence(self):
        records = {"guest-kernel-intent.json": b"{}",
                   "guest-kernel-stopped.json": canonical({"verifiedStopped": True})}
        path = self.root / "guest-kernel.log"
        path.write_bytes(b"original")
        before = dict(records)
        plan = guest_diagnostic_plan(self.root, records)
        self.assertEqual(records, before)
        self.assertEqual(plan["guest-kernel.log"], b"original")
        self.assertEqual(guest_diagnostic_plan(self.root, dict(records, **plan)), {})
        records["guest-kernel.log"] = b"changed"
        with self.assertRaisesRegex(ValueError, "Partial.*changed"):
            guest_diagnostic_plan(self.root, records)
        records["guest-kernel-log.json"] = b"{}"
        with self.assertRaisesRegex(ValueError, "diagnostics must be retained"):
            guest_diagnostic_plan(self.root, records)
