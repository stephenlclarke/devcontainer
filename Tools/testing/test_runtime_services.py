"""Service adapter tests use real private files but never touch host launchd."""

import os
from pathlib import Path
import plistlib
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from runtime_services import (ControlledRuntime, authorised_roots, capture_owned_processes, process_inventory,
                              process_programs, require_idle, selected_definition)
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
        self.inventory = patch("runtime_services.process_inventory", return_value={}).start()

    def register(self, directory, label):
        path = directory / (label + ".plist")
        path.write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": ["/original/" + label]}))
        self.launchd.bootstrap(path)

    def runtime(self):
        # Production requires separate internal and SSD devices. Tests stay
        # entirely on SSD; only this constructor's device comparison is faked.
        with patch("runtime_services.Path.stat", side_effect=[SimpleNamespace(st_dev=1), SimpleNamespace(st_dev=2)]):
            return ControlledRuntime(self.owned, self.owner, self.executable, self.private,
                                     launchd=self.launchd, home=self.home)

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

    def test_busy_worker_prevents_any_service_mutation(self):
        runtime = self.runtime()
        count = len(self.launchd.mutations)
        self.processes.return_value = ["/runner/bin/Runner.Worker"]
        with self.assertRaisesRegex(ValueError, "Active worker"):
            runtime.start()
        runtime.restore()
        self.assertEqual(len(self.launchd.mutations), count)
        self.assertEqual(runtime.receipt(), {"status": "not-started"})

    def test_surviving_listener_blocks_selected_start_and_originals_can_be_restored(self):
        runtime = self.runtime()
        self.processes.return_value = ["/runner/bin/Runner.Listener"]
        with self.assertRaisesRegex(ValueError, "survived"):
            runtime.start()
        self.assertEqual(self.launchd.jobs, {})
        self.processes.return_value = []
        runtime.restore()
        self.assertEqual(self.launchd.jobs, self.original_jobs)

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
        path = self.owned / "container-logs/apiserver.stderr"
        path.write_bytes(b"x" * (80 * 1024) + b"startup error fixture")
        runtime.restore()
        runtime.preserve_logs()
        data = runtime.journal.records()["service-stderr-snapshot.log"]
        self.assertEqual(len(data), 64 * 1024)
        self.assertTrue(data.endswith(b"startup error fixture"))
        self.assertNotIn("startup error fixture", str(runtime.receipt()))
        path.unlink()
        path.symlink_to(self.executable)
        with self.assertRaisesRegex(ValueError, "log path changed"):
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
        runtime.require_original_processes_stopped(allow_registered=True)
        self.launchd.jobs.pop(label)
        with self.assertRaisesRegex(ValueError, "owned original"):
            runtime.require_original_processes_stopped(allow_registered=True)


if __name__ == "__main__":
    unittest.main()
