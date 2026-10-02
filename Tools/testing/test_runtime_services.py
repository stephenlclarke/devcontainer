"""Service adapter tests use real private files but never touch host launchd."""

from contextlib import nullcontext
import hashlib
import os
from pathlib import Path
import plistlib
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import runtime_services
from runtime_services import (ControlledRuntime, ProcessSurvivors, authorised_roots, capture_owned_processes, process_inventory,
                              process_programs, require_idle, selected_definition, wait_stopped)
from runtime_services import require_owned_volume
from service_journal import digest
from service_switch import API, BASE_SERVICES
from test_service_switch import FakeLaunchd


class RuntimeServicesTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name).resolve()
        self.owned = self.root / "case"
        self.owned.mkdir(mode=0o700)
        self.private = self.root / "private"
        self.private.mkdir(mode=0o700)
        self.home = self.root / "home"
        self.original = self.home / "Library/Application Support/com.apple.container"
        self.original.mkdir(parents=True)
        self.agents = self.home / "Library/LaunchAgents"
        self.agents.mkdir()
        self.executable = self.root / "released/bin/container-apiserver"
        self.executable.parent.mkdir(parents=True)
        self.executable.write_bytes(b"fixture executable, not executed")
        self.owner = {"root": str(self.owned), "identity": {"fixture": "fixture"}}
        self.launchd = FakeLaunchd()
        for label in BASE_SERVICES:
            self.register(self.original, label)
        self.register(self.agents, "com.stephenlclarke.container-family-ci")
        self.original_jobs = dict(self.launchd.jobs)
        self.processes = patch("runtime_services.process_programs", return_value=[]).start()
        self.addCleanup(patch.stopall)
        self.ready = patch("runtime_services.require_api_service", return_value={"pid": 42}).start()
        self.probe = patch("runtime_services.probe_api", create=True).start()
        self.inventory = patch("runtime_services.process_inventory", return_value={}).start()
        patch("runtime_services.wait_stopped", side_effect=lambda probe: probe()).start()

    def register(self, directory, label):
        if label == API:
            path = directory / "apiserver/apiserver.plist"
            path.parent.mkdir(parents=True, exist_ok=True)
            definition = {
                "Label": label,
                "EnvironmentVariables": {
                    "CONTAINER_APP_ROOT": "/original/container/",
                    "CONTAINER_INSTALL_ROOT": "/original/install/",
                    "CONTAINER_LOG_ROOT": "/original/logs",
                    "CONTAINER_SERVICE_NAMESPACE": "com.example.original",
                },
                "LimitLoadToSessionType": ["Aqua", "Background", "System"],
                "MachServices": {API: True},
                "ProgramArguments": ["/original/" + label, "start"],
                "RunAtLoad": True,
            }
        else:
            path = directory / (label + ".plist")
            definition = {"Label": label, "ProgramArguments": ["/original/" + label]}
        path.write_bytes(plistlib.dumps(definition))
        self.launchd.bootstrap(path)

    def runtime(self, owned=None):
        # Production requires separate internal and SSD devices. Tests stay
        # entirely on SSD; only this constructor's device comparison is faked.
        owned = owned or self.owned
        owned.mkdir(mode=0o700, exist_ok=True)
        owner = {**self.owner, "root": str(owned)}
        with patch("runtime_services.Path.stat", side_effect=[SimpleNamespace(st_dev=1), SimpleNamespace(st_dev=2)]):
            return ControlledRuntime(owned, owner, self.executable, self.private,
                                     launchd=self.launchd, home=self.home)

    def generated_provider_helpers(self, runtime):
        provider = self.root / "provider"
        helpers = {}
        for label, dirname, executable in (
            ("com.apple.container.container-core-images", "container-core-images", "container-core-images"),
            ("com.apple.container.machine-apiserver", "machine-apiserver", "machine-apiserver"),
        ):
            program = provider / "libexec/container/plugins" / dirname / "bin" / executable
            program.parent.mkdir(parents=True, exist_ok=True)
            program.write_bytes((label + " trusted fixture").encode())
            program.chmod(0o700)
            environment = {
                "CONTAINER_APP_ROOT": str(runtime.root / "container") + "/",
                "CONTAINER_INSTALL_ROOT": str(provider) + "/",
                "CONTAINER_LOG_ROOT": str(runtime.root / "container-logs"),
            }
            arguments = [str(program), "start"]
            mach_service = "com.apple.container.core." + dirname
            if dirname == "machine-apiserver":
                arguments.extend([
                    "--resources",
                    str(provider / "libexec/container/plugins/machine-apiserver/resources"),
                ])
            definition = {
                "Label": label,
                "EnvironmentVariables": environment,
                "LimitLoadToSessionType": ["Aqua", "Background", "System"],
                "MachServices": {mach_service: True},
                "ProgramArguments": arguments,
                "RunAtLoad": False,
            }
            path = runtime.root / "container/plugin-state" / dirname / "service.plist"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(plistlib.dumps(definition))
            path.chmod(0o644)
            self.launchd.bootstrap(path)
            helpers[label] = {"program": program,
                             "sha256": hashlib.sha256(program.read_bytes()).hexdigest()}
        return provider.resolve(), helpers

    def test_start_verify_and_restore_keep_original_bytes_and_private_receipt(self):
        runtime = self.runtime()
        runtime.start()
        self.assertEqual(runtime.service, {"pid": 42})
        self.assertEqual(set(self.launchd.jobs), {API})
        runtime.verify()
        runtime.restore()
        self.assertEqual(self.launchd.jobs, self.original_jobs)
        self.assertEqual(runtime.receipt()["visibility"], "private-do-not-export")
        self.assertNotIn("payload", runtime.receipt())
        self.assertEqual(self.launchd.mutations[-1], ("bootstrap", "com.stephenlclarke.container-family-ci"))

    def test_definition_uses_only_selected_release_and_disposable_roots(self):
        path = selected_definition(self.owned, self.executable)
        value = plistlib.loads(path.read_bytes())
        self.assertEqual(value["ProgramArguments"], [str(self.executable), "start"])
        self.assertEqual(value["MachServices"], {API: True})
        self.assertNotIn("StandardOutPath", value)
        self.assertNotIn("StandardErrorPath", value)
        environment = value["EnvironmentVariables"]
        self.assertEqual(environment["CONTAINER_INSTALL_ROOT"], str(self.executable.parent.parent))
        for key in ("HOME", "TMPDIR", "TMP", "TEMP", "CONTAINER_APP_ROOT", "CONTAINER_LOG_ROOT"):
            self.assertTrue(Path(environment[key]).is_relative_to(self.owned))
        alias = self.root / "alias"
        alias.symlink_to(self.owned)
        with self.assertRaisesRegex(ValueError, "canonical owned"):
            selected_definition(alias, self.executable)
        missing = self.root / "missing"
        with self.assertRaisesRegex(ValueError, "canonical prepared"):
            selected_definition(self.owned, missing)

    def test_shared_device_and_wrong_owner_are_rejected(self):
        for owner in (self.owner, {"root": "/different"}):
            with self.assertRaisesRegex(ValueError, "separate internal"):
                ControlledRuntime(self.owned, owner, self.executable, self.private)

    def test_ssd_ownership_disabled_missing_or_wrong_volume_fail_before_services(self):
        volume = Path("/Volumes/SSD")
        valid = {"GlobalPermissionsEnabled": True, "MountPoint": str(volume), "Internal": False, "VolumeUUID": "fixture"}
        with patch("runtime_services.subprocess.run") as command:
            command.return_value.stdout = plistlib.dumps(valid)
            self.assertEqual(require_owned_volume(volume), {"uuid": "fixture", "mount": str(volume), "ownersEnabled": True})
            for change in ({"GlobalPermissionsEnabled": False}, {"MountPoint": "/different"}, {"Internal": True}, {"VolumeUUID": ""}):
                command.return_value.stdout = plistlib.dumps(dict(valid, **change))
                with self.assertRaises(ValueError):
                    require_owned_volume(volume)

    def test_busy_worker_prevents_any_service_mutation(self):
        runtime = self.runtime()
        count = len(self.launchd.mutations)
        self.processes.return_value = ["/runner/bin/Runner.Worker"]
        self.inventory.return_value = {1: {"pid": 1, "parent": 0, "group": 1, "started": "fixture",
                                           "program": "/runner/bin/Runner.Worker"}}
        with self.assertRaisesRegex(ValueError, "Active worker"):
            runtime.start()
        runtime.restore()
        self.assertEqual(len(self.launchd.mutations), count)
        self.assertEqual(runtime.receipt(), {"status": "not-started"})

    def test_unrelated_listener_does_not_block_selected_start(self):
        runtime = self.runtime()
        self.processes.return_value = ["/runner/bin/Runner.Listener"]
        runtime.start()
        self.assertEqual(set(self.launchd.jobs), {API})
        runtime.restore()
        self.assertEqual(self.launchd.jobs, self.original_jobs)

    def test_captured_listener_survivor_still_blocks_selected_start(self):
        runtime = self.runtime()
        runtime.start()
        row = {"pid": 123, "parent": 1, "group": 123, "started": "captured",
               "program": "/runner/bin/Runner.Listener", "labels": ["actions.runner.fixture"]}
        runtime.original_processes = [row]
        self.inventory.return_value = {123: row}
        self.launchd.jobs.clear()
        with self.assertRaisesRegex(ProcessSurvivors, "owned original service process"):
            runtime.require_workers_stopped()
        self.inventory.return_value = {}
        runtime.restore()

    def test_surviving_original_provider_prevents_selected_registration(self):
        runtime = self.runtime()
        self.processes.return_value = ["/original/" + API]
        with self.assertRaisesRegex(ValueError, "outgoing provider"):
            runtime.start()
        self.assertEqual(self.launchd.jobs, {})
        self.assertNotIn("service-selected.plist", runtime.journal.records())
        with self.assertRaisesRegex(ValueError, "processes survived"):
            runtime.restore()
        self.assertEqual(self.launchd.jobs, {})
        self.processes.return_value = []
        runtime.restore()
        self.assertEqual(self.launchd.jobs, self.original_jobs)

    def test_readiness_wait_is_bounded_and_service_replacement_is_rejected(self):
        runtime = self.runtime()
        self.ready.side_effect = [ValueError("not running yet"), {"pid": 42}, {"pid": 43}]
        runtime.start()
        with self.assertRaisesRegex(ValueError, "changed during"):
            runtime.verify()
        runtime.restore()

    def test_running_process_is_not_ready_without_successful_rpc(self):
        runtime = self.runtime()
        self.probe.side_effect = TimeoutError("API request stalled")
        with self.assertRaisesRegex(TimeoutError, "API request stalled"):
            runtime.start()
        self.assertNotIn("service-ready.plist", runtime.journal.records())
        runtime.restore()
        self.assertEqual(self.launchd.jobs, self.original_jobs)

    def test_private_home_is_prepared_before_selected_api_can_start(self):
        runtime = self.runtime()
        def prepare(journal):
            self.assertIs(journal, runtime.journal)
            self.assertIn("service-originals.plist", journal.records())
            self.assertNotIn("service-selected.plist", journal.records())
            self.assertEqual(self.launchd.jobs, {})
            self.probe.assert_not_called()
        prepare_home = Mock(side_effect=prepare)
        runtime.start(prepare_home=prepare_home)
        prepare_home.assert_called_once_with(runtime.journal)
        self.probe.assert_called_once()
        runtime.restore()
        self.assertEqual(self.launchd.jobs, self.original_jobs)

    def test_private_home_failure_restores_originals_without_starting_api(self):
        runtime = self.runtime()
        mock_value = Mock(side_effect=RuntimeError("private home failure"))
        with self.assertRaisesRegex(RuntimeError, "private home failure"):
            runtime.start(prepare_home=mock_value)
        self.assertNotIn("service-selected.plist", runtime.journal.records())
        self.probe.assert_not_called()
        runtime.restore()
        self.assertEqual(self.launchd.jobs, self.original_jobs)

    def test_provider_helpers_receive_private_home_and_restore_exact_generated_bytes(self):
        runtime = self.runtime()
        runtime.start()
        provider, trusted = self.generated_provider_helpers(runtime)
        originals = {
            label: Path(self.launchd.inspect(label)["path"]).read_bytes()
            for label in trusted
        }
        paths = {
            label: Path(self.launchd.inspect(label)["path"])
            for label in trusted
        }
        quiescent = {
            "status": "running", "containerCount": 0, "resourceCount": 0,
            "guestCount": 0, "clientCount": 0,
        }
        result = runtime.propagate_provider_helper_home(
            provider, trusted, Mock(return_value=quiescent)
        )
        self.assertEqual(result, {"status": "private-home-ready", "helpers": 2})
        for label in trusted:
            job = self.launchd.inspect(label)
            definition = plistlib.loads(Path(job["path"]).read_bytes())
            environment = definition["EnvironmentVariables"]
            for key in ("HOME", "TMPDIR", "TMP", "TEMP"):
                self.assertEqual(environment[key], str(self.owned))
        runtime.restore_provider_helper_definitions()
        for label in trusted:
            self.assertEqual(paths[label].read_bytes(), originals[label])
            self.assertIsNone(self.launchd.inspect(label))
        runtime.restore()
        self.assertEqual(self.launchd.jobs, self.original_jobs)

    def test_provider_helper_home_refuses_unowned_path_and_extra_environment(self):
        runtime = self.runtime()
        runtime.start()
        provider, trusted = self.generated_provider_helpers(runtime)
        label = "com.apple.container.container-core-images"
        path = Path(self.launchd.inspect(label)["path"])
        outside = self.root / "foreign-service.plist"
        outside.write_bytes(path.read_bytes())
        self.launchd.jobs[label] = {
            "label": label, "path": str(outside),
            "program": str(trusted[label]["program"]),
        }
        before = list(self.launchd.mutations)
        self.assertRaisesRegex(
            ValueError,
            "registration differs",
            runtime.propagate_provider_helper_home,
            provider,
            trusted,
            Mock(return_value={
                "status": "running", "containerCount": 0, "resourceCount": 0,
                "guestCount": 0, "clientCount": 0,
            }),
        )
        self.assertEqual(self.launchd.mutations, before)
        self.launchd.jobs[label] = {
            "label": label, "path": str(path),
            "program": str(trusted[label]["program"]),
        }
        alias_target = path.with_name("service-private-copy.plist")
        path.rename(alias_target)
        path.symlink_to(alias_target.name)
        before = list(self.launchd.mutations)
        self.assertRaisesRegex(
            ValueError,
            "not canonical under its owner",
            runtime.propagate_provider_helper_home,
            provider,
            trusted,
            Mock(return_value={
                "status": "running", "containerCount": 0, "resourceCount": 0,
                "guestCount": 0, "clientCount": 0,
            }),
        )
        self.assertEqual(self.launchd.mutations, before)
        path.unlink()
        alias_target.rename(path)
        definition = plistlib.loads(path.read_bytes())
        definition["EnvironmentVariables"]["PATH"] = "/untrusted"
        path.write_bytes(plistlib.dumps(definition))
        before = list(self.launchd.mutations)
        self.assertRaisesRegex(
            ValueError,
            "not the admitted contract",
            runtime.propagate_provider_helper_home,
            provider,
            trusted,
            Mock(return_value={
                "status": "running", "containerCount": 0, "resourceCount": 0,
                "guestCount": 0, "clientCount": 0,
            }),
        )
        self.assertEqual(self.launchd.mutations, before)
        runtime.restore()

    def test_provider_helper_home_refuses_untrusted_binary_and_nonempty_provider(self):
        runtime = self.runtime()
        runtime.start()
        provider, trusted = self.generated_provider_helpers(runtime)
        bad_hash = {label: dict(identity) for label, identity in trusted.items()}
        bad_hash["com.apple.container.container-core-images"]["sha256"] = "0" * 64
        before = list(self.launchd.mutations)
        empty = Mock(return_value={
            "status": "running", "containerCount": 0, "resourceCount": 0,
            "guestCount": 0, "clientCount": 0,
        })
        self.assertRaisesRegex(
            ValueError,
            "differs from its admitted package",
            runtime.propagate_provider_helper_home,
            provider,
            bad_hash,
            empty,
        )
        empty.assert_not_called()
        self.assertEqual(self.launchd.mutations, before)
        nonempty = Mock(return_value={
            "status": "running", "containerCount": 1, "resourceCount": 1,
            "guestCount": 0, "clientCount": 0,
        })
        self.assertRaisesRegex(
            ValueError,
            "provider is empty",
            runtime.propagate_provider_helper_home,
            provider,
            trusted,
            nonempty,
        )
        self.assertEqual(self.launchd.mutations, before)
        runtime.restore()

    def test_provider_helper_partial_bootout_write_and_bootstrap_failures_restore(self):
        for failure in ("bootout", "write", "bootstrap"):
            with self.subTest(failure=failure):
                self.owned = self.root / ("case-" + failure)
                self.owned.mkdir(mode=0o700)
                self.owner = {"root": str(self.owned), "identity": {"fixture": failure}}
                runtime = self.runtime()
                runtime.start()
                provider, trusted = self.generated_provider_helpers(runtime)
                originals = {
                    label: Path(self.launchd.inspect(label)["path"]).read_bytes()
                    for label in trusted
                }
                if failure == "bootout":
                    self.launchd.fail = (
                        "bootout", "com.apple.container.machine-apiserver"
                    )
                elif failure == "bootstrap":
                    self.launchd.fail = (
                        "bootstrap-after", "com.apple.container.machine-apiserver"
                    )
                proof = Mock(return_value={
                    "status": "running", "containerCount": 0, "resourceCount": 0,
                    "guestCount": 0, "clientCount": 0,
                })
                if failure == "write":
                    original_replace = runtime_services.atomic_replace_private_file
                    replacements = 0

                    def injected_replace(*args, **kwargs):
                        nonlocal replacements
                        replacements += 1
                        if replacements == 2:
                            raise OSError("injected atomic replace failure")
                        original_replace(*args, **kwargs)

                    replace_patch = patch(
                        "runtime_services.atomic_replace_private_file",
                        side_effect=injected_replace,
                    )
                else:
                    replace_patch = patch(
                        "runtime_services.atomic_replace_private_file",
                        wraps=runtime_services.atomic_replace_private_file,
                    )
                with replace_patch:
                    expected_error = OSError if failure == "write" else RuntimeError
                    with self.assertRaises(expected_error):
                        runtime.propagate_provider_helper_home(provider, trusted, proof)
                self.launchd.fail = None
                helper_paths = {
                    label: Path(self.launchd.inspect(label)["path"])
                    for label in trusted if self.launchd.inspect(label) is not None
                }
                helper_paths.update({
                    label: runtime.root / "container/plugin-state" / (
                        "container-core-images" if label.endswith("container-core-images")
                        else "machine-apiserver"
                    ) / "service.plist"
                    for label in trusted
                })
                self.assertEqual(
                    runtime.restore_provider_helper_definitions()["status"], "restored"
                )
                for label, payload in originals.items():
                    self.assertEqual(helper_paths[label].read_bytes(), payload)
                runtime.restore()
                self.assertEqual(self.launchd.jobs, self.original_jobs)

    def test_system_start_global_api_plist_capture_and_restore_is_exact(self):
        runtime = self.runtime()
        runtime.start()
        provider = self.root / "provider"
        provider.mkdir()
        prior = next(item for item in runtime.switch.prior if item["label"] == API)
        path = Path(prior["path"])
        original = plistlib.loads(prior["payload"])
        generated = dict(original)
        environment = dict(original["EnvironmentVariables"])
        environment.pop("CONTAINER_SERVICE_NAMESPACE")
        environment.update({
            "CONTAINER_INSTALLATION_ROOT": str(provider),
            "CONTAINER_INSTALL_ROOT": str(provider),
            "CONTAINER_LOG_ROOT": str(runtime.root / "container-logs"),
        })
        generated["EnvironmentVariables"] = environment
        generated["ProgramArguments"] = [str(runtime.executable), "start"]
        path.write_bytes(plistlib.dumps(generated))
        finished = time.time()
        started = finished - 1
        capture = runtime.capture_system_start_api_definition(
            provider, started, finished, provider_lane="apple-stock")
        self.assertEqual(capture["status"], "captured")
        runtime.restore_system_start_api_definition()
        self.assertEqual(path.read_bytes(), prior["payload"])
        self.assertEqual(path.stat().st_mtime_ns, runtime.original_api_file_metadata["mtimeNS"])
        runtime.restore()
        self.assertEqual(self.launchd.jobs, self.original_jobs)

    def test_fork_system_start_preserves_exact_admitted_service_namespace(self):
        runtime = self.runtime()
        runtime.start()
        provider = self.root / "provider"
        provider.mkdir()
        prior = next(item for item in runtime.switch.prior if item["label"] == API)
        path = Path(prior["path"])
        original = plistlib.loads(prior["payload"])
        generated = dict(original)
        environment = dict(original["EnvironmentVariables"])
        environment.update({
            "CONTAINER_INSTALLATION_ROOT": str(provider),
            "CONTAINER_INSTALL_ROOT": str(provider),
            "CONTAINER_LOG_ROOT": str(runtime.root / "container-logs"),
        })
        generated["EnvironmentVariables"] = environment
        generated["ProgramArguments"] = [str(runtime.executable), "start"]
        path.write_bytes(plistlib.dumps(generated))
        finished = time.time()

        capture = runtime.capture_system_start_api_definition(
            provider, finished - 1, finished, provider_lane="container-compose")

        self.assertEqual(capture["status"], "captured")
        self.assertEqual(runtime.system_start_api_capture["metadata"]["providerLane"], "container-compose")
        runtime.restore_system_start_api_definition()
        self.assertEqual(path.read_bytes(), prior["payload"])
        runtime.restore()
        self.assertEqual(self.launchd.jobs, self.original_jobs)

    def test_fork_system_start_rejects_foreign_namespace_and_extra_environment(self):
        for mutation in ("namespace", "extra"):
            with self.subTest(mutation=mutation):
                runtime = self.runtime(self.root / f"case-{mutation}")
                runtime.start()
                provider = self.root / f"provider-{mutation}"
                provider.mkdir()
                prior = next(item for item in runtime.switch.prior if item["label"] == API)
                path = Path(prior["path"])
                original = plistlib.loads(prior["payload"])
                generated = dict(original)
                environment = dict(original["EnvironmentVariables"])
                environment.update({
                    "CONTAINER_INSTALLATION_ROOT": str(provider),
                    "CONTAINER_INSTALL_ROOT": str(provider),
                    "CONTAINER_LOG_ROOT": str(runtime.root / "container-logs"),
                })
                if mutation == "namespace":
                    environment["CONTAINER_SERVICE_NAMESPACE"] = "com.foreign.container"
                else:
                    environment["UNEXPECTED_SERVICE_SETTING"] = "value"
                generated["EnvironmentVariables"] = environment
                generated["ProgramArguments"] = [str(runtime.executable), "start"]
                path.write_bytes(plistlib.dumps(generated))
                finished = time.time()

                with self.assertRaisesRegex(ValueError, "changes exceed the selected package contract"):
                    runtime.capture_system_start_api_definition(
                        provider, finished - 1, finished, provider_lane="container-compose")

                path.write_bytes(prior["payload"])
                runtime.restore()
                self.assertEqual(self.launchd.jobs, self.original_jobs)

    def test_system_start_global_api_plist_unchanged_is_an_idempotent_noop(self):
        runtime = self.runtime()
        runtime.start()
        provider = self.root / "provider"
        provider.mkdir()
        now = time.time()
        capture = runtime.capture_system_start_api_definition(
            provider, now - 1, now, provider_lane="apple-stock")
        self.assertEqual(capture["status"], "unchanged")
        runtime.restore_system_start_api_definition()
        runtime.restore()
        self.assertEqual(self.launchd.jobs, self.original_jobs)

    def test_system_start_global_api_plist_refuses_change_after_capture(self):
        runtime = self.runtime()
        runtime.start()
        provider = self.root / "provider"
        provider.mkdir()
        prior = next(item for item in runtime.switch.prior if item["label"] == API)
        path = Path(prior["path"])
        original = plistlib.loads(prior["payload"])
        generated = dict(original)
        environment = dict(original["EnvironmentVariables"])
        environment.pop("CONTAINER_SERVICE_NAMESPACE")
        environment.update({
            "CONTAINER_INSTALLATION_ROOT": str(provider),
            "CONTAINER_INSTALL_ROOT": str(provider),
            "CONTAINER_LOG_ROOT": str(runtime.root / "container-logs"),
        })
        generated["EnvironmentVariables"] = environment
        generated["ProgramArguments"] = [str(runtime.executable), "start"]
        path.write_bytes(plistlib.dumps(generated))
        finished = time.time()
        runtime.capture_system_start_api_definition(
            provider, finished - 1, finished, provider_lane="apple-stock")
        captured_payload = path.read_bytes()
        captured_info = path.stat()
        tampered = dict(generated)
        tampered["RunAtLoad"] = False
        path.write_bytes(plistlib.dumps(tampered))
        os.utime(path, ns=(captured_info.st_atime_ns, captured_info.st_mtime_ns))
        self.assertRaisesRegex(
            ValueError,
            "changed after SystemStart capture",
            runtime.restore_system_start_api_definition,
        )
        path.write_bytes(captured_payload)
        os.utime(path, ns=(captured_info.st_atime_ns, captured_info.st_mtime_ns))
        runtime.restore_system_start_api_definition()
        self.assertEqual(path.read_bytes(), prior["payload"])
        runtime.restore()

    def test_uncertain_private_keychain_helper_prevents_runtime_restore(self):
        runtime = self.runtime()
        def interrupted(journal):
            journal.put("keychain-0001-intent.json", b'{"arguments":["fixture-helper"]}')
            raise RuntimeError("lost helper lifetime")
        with self.assertRaisesRegex(RuntimeError, "lost helper lifetime"):
            runtime.start(prepare_home=interrupted)
        with self.assertRaisesRegex(ValueError, "keychain helper needs explicit"):
            runtime.restore()
        self.assertEqual(self.launchd.jobs, {})
        self.assertNotIn("service-selected.plist", runtime.journal.records())

    def test_readiness_receipt_follows_rpc_and_uses_selected_executable(self):
        runtime = self.runtime()
        def observe(root, executable, journal, verify):
            self.assertEqual((root, executable), (self.owned, self.executable.parent / "container"))
            self.assertIn("service-started.plist", journal.records())
            self.assertNotIn("service-ready.plist", journal.records())
            verify()
        self.probe.side_effect = observe
        runtime.start()
        self.probe.assert_called_once()
        self.assertIn("service-ready.plist", runtime.journal.records())
        runtime.restore()

    def test_surviving_released_process_keeps_original_workers_suspended(self):
        runtime = self.runtime()
        runtime.start()
        self.processes.return_value = [str(self.executable)]
        with self.assertRaisesRegex(ValueError, "processes survived"):
            runtime.restore()
        self.assertEqual(self.launchd.jobs, {})
        self.processes.return_value = []
        runtime.restore()
        self.assertEqual(self.launchd.jobs, self.original_jobs)

    def test_unrelated_job_never_becomes_authorised(self):
        self.register(self.agents, "actions.runner.unrelated-project.host")
        self.register(self.agents, "actions.runner.stephenlclarke-devcontainer.fixture")
        roots = authorised_roots(self.launchd, self.home)
        self.assertNotIn("actions.runner.unrelated-project.host", roots)
        self.assertEqual(roots["actions.runner.stephenlclarke-devcontainer.fixture"], self.agents)

    def test_private_bounded_startup_diagnostics_survive_restoration(self):
        runtime = self.runtime()
        runtime.start()
        path = self.owned / "container-logs/container-apiserver.log"
        path.write_bytes(b"x" * (80 * 1024) + b"startup error fixture")
        runtime.restore()
        runtime.preserve_logs()
        data = runtime.journal.records()["service-container-apiserver.log"]
        self.assertEqual(len(data), 64 * 1024)
        self.assertTrue(data.endswith(b"startup error fixture"))
        self.assertNotIn("startup error fixture", str(runtime.receipt()))
        path.unlink()
        path.symlink_to(self.executable)
        with self.assertRaisesRegex(ValueError, "log path changed"):
            runtime.preserve_logs()

    def test_private_worker_diagnostics_survive_restoration(self):
        runtime = self.runtime()
        runtime.start()
        names = ("container-runtime-linux-cf-test-fixture.log",
                 "container-runtime-linux-AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE.log",
                 "container-runtime-linux-shared-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.log")
        for name in names:
            (self.owned / "container-logs" / name).write_bytes(b"x" * (80 * 1024) + b"worker tail")
        (self.owned / "container-logs/unrelated.log").write_bytes(b"not selected")
        runtime.restore()
        runtime.preserve_logs()
        records = runtime.journal.records()
        for name in names:
            key = "worker-" + digest(name.encode())[:48]
            self.assertEqual(records[key + ".name"], name.encode())
            self.assertEqual(len(records[key + ".log"]), 64 * 1024)
            self.assertTrue(records[key + ".log"].endswith(b"worker tail"))
        self.assertNotIn("service-unrelated.log", records)
        self.assertNotIn("worker tail", str(runtime.receipt()))

    def test_private_worker_log_aliases_and_nonregular_files_are_rejected(self):
        runtime = self.runtime()
        runtime.start()
        path = self.owned / "container-logs/container-runtime-linux-fixture.log"
        path.symlink_to(self.executable)
        with self.assertRaisesRegex(ValueError, "log path changed"):
            runtime.preserve_logs()
        path.unlink()
        os.link(self.executable, path)
        with self.assertRaisesRegex(ValueError, "log ownership changed"):
            runtime.preserve_logs()
        path.unlink()
        os.mkfifo(path)
        with self.assertRaisesRegex(ValueError, "log ownership changed"):
            runtime.preserve_logs()

    def test_private_worker_log_count_is_bounded(self):
        runtime = self.runtime()
        runtime.start()
        for index in range(65):
            (self.owned / f"container-logs/container-runtime-linux-{index}.log").touch()
        with self.assertRaisesRegex(ValueError, "Too many selected worker logs"):
            runtime.preserve_logs()

    def test_process_inventory_is_bounded_and_contains_no_arguments_or_environment(self):
        with patch("runtime_services.subprocess.run") as command:
            command.return_value.stdout = b"42 1 42 Thu Sep 17 12:00:00 2026 /program/Runner.Worker\n"
            self.assertEqual(process_inventory()[42]["program"], "/program/Runner.Worker")
            self.assertEqual(command.call_args.args[0], ["/bin/ps", "-axo", "pid=,ppid=,pgid=,lstart=,comm="])
            self.assertEqual(command.call_args.kwargs["timeout"], 5)
        require_idle(["/program/unrelated"])
        for program in ("container", "compose", "docker", "docker-compose", "colima", "container-runtime-linux",
                        "com.apple.Virtualization.VirtualMachine"):
            with self.assertRaisesRegex(ValueError, "Active"):
                require_idle(["/program/" + program])

    def test_shared_interpreter_is_scoped_to_original_job_not_bazel_launcher(self):
        label = "com.stephenlclarke.container-family-ci"
        self.launchd.jobs.pop(label)
        path = self.agents / (label + ".plist")
        path.write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": ["/bin/bash", "worker.sh"]}))
        self.launchd.bootstrap(path)
        runtime = self.runtime()
        self.processes.return_value = ["/bin/bash"]
        runtime.start()
        runtime.restore()
        self.assertEqual(self.launchd.inspect(label)["program"], "/bin/bash")

    def test_owned_interpreter_and_descendants_are_tracked_without_signalling_reused_pids(self):
        rows = {10: {"pid": 10, "parent": 1, "group": 10, "started": "original", "program": "/bin/bash"},
                11: {"pid": 11, "parent": 10, "group": 10, "started": "child", "program": "/runner/worker"},
                20: {"pid": 20, "parent": 1, "group": 20, "started": "other", "program": "/bin/bash"}}
        self.inventory.return_value = rows
        with patch.object(self.launchd, "process_id", return_value=10):
            owned = capture_owned_processes(self.launchd, [{"label": API}])
        self.assertEqual([item["pid"] for item in owned], [10, 11])
        runtime = self.runtime()
        runtime.original_processes = owned
        with self.assertRaisesRegex(ValueError, "owned original"):
            runtime.require_original_processes_stopped()
        for change in ({"program": "/different/helper"}, {"group": 99}):
            self.inventory.return_value = {10: dict(rows[10], **change)}
            with self.assertRaisesRegex(ValueError, "owned original"):
                runtime.require_original_processes_stopped()
        self.inventory.return_value = {10: dict(rows[10], started="new process"), 20: rows[20]}
        runtime.require_original_processes_stopped()

    def test_partial_prepare_may_preserve_a_still_registered_original(self):
        runtime = self.runtime()
        runtime.start()
        runtime.restore()
        label = "com.stephenlclarke.container-family-ci"
        row = {"pid": 10, "parent": 1, "group": 10, "started": "original", "program": "/bin/bash", "labels": [label]}
        runtime.original_processes = [row]
        self.inventory.return_value = {10: row}
        with patch.object(self.launchd, "process_id", return_value=10):
            runtime.require_original_processes_stopped(allow_registered=True)
        self.launchd.jobs.pop(label)
        with self.assertRaisesRegex(ValueError, "owned original"):
            runtime.require_original_processes_stopped(allow_registered=True)

    def test_restored_definition_does_not_exempt_stale_root_or_detached_descendant(self):
        runtime = self.runtime()
        runtime.start()
        runtime.restore()
        label = "com.stephenlclarke.container-family-ci"
        original = {"pid": 10, "parent": 1, "group": 10, "started": "original", "program": "/original/api", "labels": [label]}
        child = dict(original, pid=11, started="child")
        replacement = dict(original, pid=20, started="replacement")
        self.inventory.return_value = {10: original, 11: child, 20: replacement}
        with patch.object(self.launchd, "process_id", return_value=20):
            for survivor in (original, child):
                runtime.original_processes = [survivor]
                with self.subTest(pid=survivor["pid"]), self.assertRaisesRegex(ProcessSurvivors, "original service"):
                    runtime.require_original_processes_stopped(allow_registered=True)
            # A captured descendant that really belongs to the current tree is allowed.
            self.inventory.return_value[11] = dict(child, parent=20)
            runtime.original_processes = [child]
            runtime.require_original_processes_stopped(allow_registered=True)

    def test_asynchronous_process_shutdown_waits_but_never_retries_other_failures(self):
        probe = Mock(side_effect=[ProcessSurvivors("still stopping"), None])
        with patch("runtime_services.time.sleep") as pause:
            wait_stopped(probe)
        self.assertEqual(probe.call_count, 2)
        pause.assert_called_once_with(0.05)
        invalid = Mock(side_effect=ValueError("changed ownership"))
        with self.assertRaisesRegex(ValueError, "changed ownership"):
            wait_stopped(invalid)
        invalid.assert_called_once()
        blocked = Mock(side_effect=ProcessSurvivors("still stopping"))
        with self.assertRaises(TimeoutError):
            wait_stopped(blocked, seconds=0.02)

    def test_monotonic_deadline_bounds_wait_when_signal_alarm_is_inert(self):
        now = [0.0]

        def sleep(seconds):
            now[0] += seconds

        blocked = Mock(side_effect=ProcessSurvivors("still stopping"))
        with patch("runtime_services.deadline", return_value=nullcontext()), \
                patch("runtime_services.time.monotonic", side_effect=lambda: now[0]), \
                patch("runtime_services.time.sleep", side_effect=sleep):
            with self.assertRaisesRegex(TimeoutError, "monotonic|deadline"):
                wait_stopped(blocked, seconds=0.12)
        self.assertGreaterEqual(now[0], 0.12)
        self.assertLess(now[0], 0.13)
        self.assertEqual(blocked.call_count, 4)

    def test_lingering_original_registration_blocks_new_provider(self):
        runtime = self.runtime()
        runtime.start()
        self.launchd.jobs["com.stephenlclarke.container-family-ci"] = self.original_jobs["com.stephenlclarke.container-family-ci"]
        with self.assertRaisesRegex(ProcessSurvivors, "registrations"):
            runtime.require_workers_stopped()
        runtime.restore()

    def test_only_captured_homebrew_autostart_is_exempt_from_cli_idle_check(self):
        runtime = self.runtime()
        row = {"pid": 10, "parent": 1, "group": 10, "started": "original", "program": "/released/container"}
        runtime.original_processes = [row]
        self.inventory.return_value = {10: row}
        prior = [{"label": "sh.brew.container", "payload": plistlib.dumps(
            {"ProgramArguments": ["/released/container", "system", "start"]})}]
        with patch.object(self.launchd, "process_id", return_value=10):
            runtime.require_idle_before_selection(prior)
            self.inventory.return_value = {10: row, 20: dict(row, pid=20)}
            with self.assertRaisesRegex(ValueError, "Active"):
                runtime.require_idle_before_selection(prior)
            self.inventory.return_value = {10: dict(row, started="reused")}
            with self.assertRaisesRegex(ValueError, "Active"):
                runtime.require_idle_before_selection(prior)

    def test_captured_administrative_cli_is_waited_for_after_removal(self):
        runtime = self.runtime()
        runtime.start()
        # The selected fake registration is irrelevant to original shutdown.
        self.launchd.jobs.clear()
        row = {"pid": 10, "parent": 1, "group": 10, "started": "original", "program": "/released/container",
               "labels": ["sh.brew.container"]}
        runtime.original_processes = [row]
        self.inventory.side_effect = [{10: row}, {}]
        with patch("runtime_services.time.sleep") as pause:
            wait_stopped(runtime.require_workers_stopped)
        pause.assert_called_once_with(0.05)


if __name__ == "__main__":
    unittest.main()
