"""Completed-build recovery is cleanup-only; interrupted work stays quarantined."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import recover_build
from case_evidence import CaseStore, canonical, digest, validate_identity
from docker_vm import DockerVM, environment, pid_roles, start_arguments
from host_runtime import HostGuard
from recover_runtime import recover
from service_journal import ServiceJournal


class BuildRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.retained, self.ssd = self.base / "retained", self.base / "ssd"
        self.retained.mkdir(mode=0o700)
        (self.retained / "private-runtime").mkdir(mode=0o700)
        self.root = self.ssd / "live/docker-fixture"
        self.root.mkdir(mode=0o700, parents=True)
        self.identity = {"campaign": "recovery", "fixture": getattr(self, "fixture_name", "E04-image-build"), "lane": "docker",
                         **{key: "a" * 64 for key in ("harnessSHA256", "runtimeSHA256", "releaseSetSHA256", "contractSHA256")}}
        self.owner = {"root": str(self.root), "identity": self.identity}
        self.key = validate_identity(self.identity)
        (self.root / "owner.json").write_bytes(canonical(self.owner))
        self.guard = HostGuard(self.retained / "runtime-admission.json")
        self.guard.begin(self.owner)
        tools = {}
        for name in ("colima", "limactl", "docker", "disk-image", "guest-agent"):
            path = self.retained / name
            path.write_bytes(b"test fixture; never execute")
            tools[name] = str(path)
        self.inputs = {"tools": tools, "pins": {}, "workload": {"image": {
            "repository": "docker.io/library/alpine", "manifest": "sha256:" + "b" * 64}}}
        self.store = CaseStore(self.retained / "runtime-cases.sqlite")
        self.store.path.chmod(0o600)
        self.store.begin(self.identity)
        self.store.attach(self.identity, "owner.json", canonical(self.owner))
        self.store.attach(self.identity, "admission.json", canonical(self.inputs))
        self.result = {"status": "failed", "observations": {}, "errors": ["cleanup: ValueError"],
                       "durationsNS": {"setup": 1, "operation": 2, "cleanup": 3},
                       "cleanup": {"status": "unknown", "remainingOwnedResources": []}}
        self.store.finish(self.identity, self.result)
        self.journal = ServiceJournal(self.retained / "private-runtime" / (digest(canonical(self.owner)) + ".sqlite"),
                                      self.owner, create=True)
        self.vm = DockerVM(self.root, self.owner, tools, {}, self.journal, inventory=Mock(return_value={}))
        self.journal.put("docker-plan.json", canonical({"owner": self.owner, "tools": tools,
            "environment": environment(self.root, tools), "start": start_arguments(tools, self.root),
            "socket": str(self.vm.socket)}))
        self.images = Mock()
        self.images.recovery_plan.return_value = [{"id": "sha256:" + "c" * 64}]
        self.images.intermediate_plan.return_value = []

    def invoke(self, apply=False):
        return recover(self.retained, self.ssd, apply=apply, expected_case=self.key)

    def patches(self):
        stack = ExitStack()
        stack.enter_context(patch.object(recover_build, "recovery_inputs", return_value=self.inputs))
        stack.enter_context(patch.object(recover_build, "DockerVM", return_value=self.vm))
        stack.enter_context(patch.object(recover_build, "verify_running"))
        stack.enter_context(patch.object(recover_build, "BuildImages", return_value=self.images))
        return stack

    def test_report_does_not_mutate_start_stop_or_rewrite_case(self):
        before = self.journal.records()
        with self.patches(), patch.object(self.vm, "stop") as stop, patch.object(self.vm, "start") as start:
            result = self.invoke()
        self.assertEqual(result["status"], "ready-to-clean-completed-docker-build")
        self.assertFalse(result["changed"])
        self.assertEqual(self.journal.records(), before)
        self.images.cleanup.assert_not_called()
        stop.assert_not_called()
        start.assert_not_called()
        self.assertEqual(self.store.begin(self.identity), self.result)

    def test_apply_cleans_then_stops_then_removes_owned_root_and_preserves_failed_case(self):
        order = []
        self.images.cleanup.side_effect = lambda: order.append("images")

        def stopped():
            order.append("stop")
            self.journal.put("docker-vm-closed.json", canonical({"verifiedStopped": True}))

        with self.patches(), patch.object(self.vm, "stop", side_effect=stopped), \
                patch("recover_runtime.require_closed_vm"), patch.object(self.vm, "start") as start:
            self.assertEqual(self.invoke(True)["status"], "restored")
        self.assertEqual(order, ["images", "stop"])
        start.assert_not_called()
        self.assertFalse(self.root.exists())
        self.assertFalse(self.guard.path.exists())
        self.assertEqual(self.store.begin(self.identity), self.result)

    def test_unknown_completion_or_foreign_image_refuses_before_stop(self):
        for message in ("completion uncertain", "foreign image"):
            with self.patches(), patch.object(self.vm, "stop") as stop:
                self.images.recovery_plan.side_effect = ValueError(message)
                with self.assertRaisesRegex(ValueError, message):
                    self.invoke(True)
            self.images.cleanup.assert_not_called()
            stop.assert_not_called()
            self.assertTrue(self.guard.path.exists())

    def test_changed_inputs_and_failed_stop_keep_quarantine(self):
        with self.patches(), patch.object(recover_build, "recovery_inputs", side_effect=[self.inputs, {}]), \
                self.assertRaisesRegex(ValueError, "inputs changed"):
            self.invoke(True)
        self.images.cleanup.assert_not_called()
        with self.patches(), patch.object(self.vm, "stop", side_effect=TimeoutError), self.assertRaises(TimeoutError):
            self.invoke(True)
        self.assertTrue(self.root.exists())
        self.assertTrue(self.guard.path.exists())
        self.assertEqual(self.store.begin(self.identity), self.result)

    def test_original_admission_is_revalidated_offline_and_cannot_upgrade(self):
        with patch.object(recover_build, "admit_docker", return_value=self.inputs) as admit:
            self.assertEqual(recover_build.recovery_inputs(self.retained, self.ssd, self.owner), self.inputs)
            self.assertEqual(admit.call_args.args[-2:], (self.ssd, self.retained))
        with patch.object(recover_build, "admit_docker", return_value={}), self.assertRaisesRegex(ValueError, "differ"):
            recover_build.recovery_inputs(self.retained, self.ssd, self.owner)
        with self.store.connect() as db:
            db.execute("UPDATE artifacts SET sha256='corrupt' WHERE name='admission.json'")
        with self.assertRaisesRegex(ValueError, "corrupt"):
            recover_build.recovery_inputs(self.retained, self.ssd, self.owner)

    def running_records(self):
        process = {"pid": 321, "parent": 1, "group": 321, "started": "unchanged", "program": self.inputs["tools"]["limactl"]}
        self.vm.inventory.return_value = {321: process}
        pids = {name: process for name in pid_roles(self.root, self.inputs["tools"])}
        self.journal.put("docker-vm-pids.json", canonical(pids))
        self.journal.put("docker-vm-processes.json", canonical({"321": process}))
        self.journal.put("docker-vm-start-intent.json", canonical({"arguments": start_arguments(self.inputs["tools"], self.root)}))
        self.journal.put("docker-vm-start-process.json", canonical({"pid": 123}))
        self.journal.put("docker-vm-start-exit.json", canonical({"code": 0}))
        return pids, process

    def test_running_admission_rejects_replaced_pid_unknown_command_and_stale_plan(self):
        pids, process = self.running_records()
        with patch.object(recover_build, "verify_pid_files", return_value=pids) as inspect, \
                patch.object(recover_build, "scoped_processes", return_value={"321": process}) as scoped, \
                patch.object(self.vm, "verify") as verify:
            recover_build.verify_running(self.vm)
            verify.assert_called_once_with()
            self.vm.inventory.return_value[654] = dict(process, pid=654, program="/system/com.apple.Virtualization.VirtualMachine")
            recover_build.verify_running(self.vm)
            self.vm.inventory.return_value[654] = dict(process, pid=654, program="/tools/Runner.Worker")
            with self.assertRaisesRegex(ValueError, "Active worker"):
                recover_build.verify_running(self.vm)
            del self.vm.inventory.return_value[654]
            inspect.return_value = {}
            with self.assertRaisesRegex(ValueError, "PID ownership"):
                recover_build.verify_running(self.vm)
            inspect.return_value = pids
            scoped.return_value = {"321": dict(process, started="replacement")}
            with self.assertRaisesRegex(ValueError, "changed process"):
                recover_build.verify_running(self.vm)
            scoped.return_value = {"321": process}
            self.vm.inventory.return_value[123] = dict(process, pid=123)
            with self.assertRaisesRegex(ValueError, "process remains"):
                recover_build.verify_running(self.vm)
            del self.vm.inventory.return_value[123]
            self.journal.put("docker-lost-intent.json", b"{}")
            with self.assertRaisesRegex(ValueError, "durable exit"):
                recover_build.verify_running(self.vm)
        self.vm.tools = dict(self.vm.tools, **{"guest-agent": "/different/guest-agent"})
        with self.assertRaisesRegex(ValueError, "plan differs"):
            recover_build.verify_running(self.vm)

    def test_interrupted_stop_is_never_resubmitted(self):
        self.journal.put("docker-vm-stop-intent.json", b"{}")
        with self.assertRaisesRegex(ValueError, "Interrupted Docker shutdown"):
            recover_build.verify_running(self.vm)


class DevcontainerRecoveryTests(unittest.TestCase):
    fixture_name = "D01-image-config"
    setUp = BuildRecoveryTests.setUp
    invoke = BuildRecoveryTests.invoke
    running_records = BuildRecoveryTests.running_records

    def patches(self):
        stack = ExitStack()
        stack.enter_context(patch.object(recover_build, "recovery_inputs", return_value=self.inputs))
        stack.enter_context(patch.object(recover_build, "DockerVM", return_value=self.vm))
        stack.enter_context(patch.object(recover_build, "verify_running"))
        self.fixture = Mock()
        self.fixture.recovery_plan.return_value = "a" * 64
        stack.enter_context(patch("devcontainer_reference.DevcontainerReference", return_value=self.fixture))
        return stack

    def test_report_and_apply_cleanup_only_preserve_failed_result(self):
        with self.patches(), patch.object(self.vm, "stop") as stop, \
                patch("recover_runtime.recover_closed_docker", return_value={"status": "clear"}) as finish:
            report = self.invoke()
            self.assertEqual(report["status"], "ready-to-clean-completed-devcontainer")
            self.assertFalse(report["changed"])
            self.fixture.remove_owned.assert_not_called()
            stop.assert_not_called()
            self.assertEqual(self.invoke(True), {"status": "clear"})
            self.fixture.remove_owned.assert_called_once_with()
            stop.assert_called_once_with()
            finish.assert_called_once()
            self.assertEqual(finish.call_args.args[:2], (self.retained, self.owner))
            self.assertEqual(finish.call_args.args[2].path, self.guard.path)
            self.assertEqual(finish.call_args.kwargs, {"apply": True})
            self.assertEqual(self.store.begin(self.identity), self.result)
            self.assertIn("d01-recovery-authorized.json", self.journal.records())

    def test_uncertain_ownership_and_changed_inputs_never_stop_vm(self):
        with self.patches(), patch.object(self.vm, "stop") as stop:
            self.fixture.recovery_plan.side_effect = ValueError("uncertain")
            with self.assertRaisesRegex(ValueError, "uncertain"):
                self.invoke(True)
            self.fixture.remove_owned.assert_not_called()
            stop.assert_not_called()
        with self.patches(), patch.object(recover_build, "recovery_inputs", side_effect=[self.inputs, {}]), \
                self.assertRaisesRegex(ValueError, "inputs changed"):
            self.invoke(True)
        self.fixture.remove_owned.assert_not_called()
        self.assertTrue(self.guard.path.exists())

    def test_cli_completion_requires_process_absence_even_after_nonzero_exit(self):
        pids, process = self.running_records()
        self.journal.put("devcontainer-up-intent.json", b"{}")
        with patch.object(recover_build, "verify_pid_files", return_value=pids), \
                patch.object(recover_build, "scoped_processes", return_value={"321": process}), \
                patch.object(self.vm, "verify"):
            with self.assertRaisesRegex(ValueError, "durable exit"):
                recover_build.verify_running(self.vm)
            self.journal.put("devcontainer-up-process.json", canonical({"pid": 789}))
            self.journal.put("devcontainer-up-exit.json", canonical({"code": 1}))
            recover_build.verify_running(self.vm)
            # HTTP resource deletion intent is not an external command receipt.
            self.journal.put("devcontainer-delete-intent.json", canonical({"id": "a" * 64}))
            recover_build.verify_running(self.vm)
            self.vm.inventory.return_value[789] = dict(process, pid=789)
            with self.assertRaisesRegex(ValueError, "process remains"):
                recover_build.verify_running(self.vm)


if __name__ == "__main__":
    unittest.main()
