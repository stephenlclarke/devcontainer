"""Resume verified Docker cleanup with real journals; never start or stop a VM."""

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from case_evidence import CaseStore, canonical, digest, validate_identity
from docker_vm import environment, start_arguments
from host_runtime import HostGuard, cleanup_receipt
from recover_runtime import recover
from service_journal import ServiceJournal


class DockerRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.retained, self.ssd = self.base / "retained", self.base / "ssd"
        self.retained.mkdir(mode=0o700)
        (self.retained / "private-runtime").mkdir(mode=0o700)
        self.root = self.ssd / "live/docker-fixture"
        self.root.mkdir(mode=0o700, parents=True)
        self.identity = {"campaign": "recovery", "fixture": "E01-engine-negotiation", "lane": "docker",
                         **{key: "a" * 64 for key in ("harnessSHA256", "runtimeSHA256", "releaseSetSHA256", "contractSHA256")}}
        self.owner = {"root": str(self.root), "identity": self.identity}
        (self.root / "owner.json").write_bytes(canonical(self.owner))
        self.guard = HostGuard(self.retained / "runtime-admission.json")
        self.guard.begin(self.owner)
        self.key = validate_identity(self.identity)
        self.store = CaseStore(self.retained / "runtime-cases.sqlite")
        self.store.path.chmod(0o600)
        self.store.begin(self.identity)
        self.store.attach(self.identity, "owner.json", canonical(self.owner))
        self.result = {"status": "failed", "observations": {}, "errors": ["cleanup: OSError"],
                       "durationsNS": {"setup": 1, "operation": 2, "cleanup": 3},
                       "cleanup": {"status": "unknown", "remainingOwnedResources": []}}
        self.store.finish(self.identity, self.result)
        self.journal = ServiceJournal(self.retained / "private-runtime" / (digest(canonical(self.owner)) + ".sqlite"),
                                      self.owner, create=True)
        tools = {}
        for name in ("colima", "limactl", "docker", "disk-image", "guest-agent"):
            path = self.retained / name
            path.write_bytes(b"fixture; never executed")
            tools[name] = str(path)
        self.journal.put("docker-plan.json", canonical({"owner": self.owner, "tools": tools,
                         "environment": environment(self.root, tools), "start": start_arguments(tools, self.root),
                         "socket": str(self.root / "colima/parity/docker.sock")}))
        self.journal.put("docker-vm-closed.json", canonical({"verifiedStopped": True}))
        for target, kwargs in (("docker_vm.process_inventory", {"return_value": {}}),
                               ("docker_vm.scoped_processes", {"return_value": {}}),
                               ("docker_vm.require_unreachable_socket", {"return_value": None})):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def invoke(self, apply=True, key=None):
        return recover(self.retained, self.ssd, apply=apply, expected_case=self.key if key is None else key)

    def test_report_is_read_only_and_apply_never_rewrites_failed_case(self):
        records = self.journal.records()
        self.assertEqual(self.invoke(False), {"status": "ready-to-remove-stopped-docker", "caseID": self.key, "changed": False})
        self.assertEqual(self.journal.records(), records)
        self.assertTrue(self.root.exists())
        with patch("subprocess.Popen") as spawn, patch("os.kill") as kill, patch("os.killpg") as killpg:
            self.assertEqual(self.invoke()["status"], "restored")
        spawn.assert_not_called()
        kill.assert_not_called()
        killpg.assert_not_called()
        self.assertFalse(self.root.exists())
        self.assertFalse(self.guard.path.exists())
        self.assertEqual(self.store.begin(self.identity), self.result)

    def test_crash_after_removal_resumes_from_authorization_without_recreating_root(self):
        with patch.object(HostGuard, "clear", side_effect=OSError("crash")), self.assertRaises(OSError):
            self.invoke()
        self.assertFalse(self.root.exists())
        self.assertEqual(self.invoke(False)["status"], "ready-to-clear")
        self.assertEqual(self.invoke()["status"], "restored")
        self.assertFalse(self.guard.path.exists())
        self.assertEqual(self.store.begin(self.identity), self.result)

    def test_normal_cleanup_authorization_supports_recovery_after_root_removal(self):
        self.journal.put("docker-cleanup-authorized.json", cleanup_receipt(self.owner, self.root))
        shutil.rmtree(self.root)
        self.assertEqual(self.invoke()["status"], "restored")

    def test_partial_removal_resumes_only_for_original_directory(self):
        def interrupted(root):
            (root / "owner.json").unlink()
            raise OSError("partial removal")
        with patch("recover_runtime.shutil.rmtree", side_effect=interrupted), self.assertRaises(OSError):
            self.invoke()
        self.assertTrue(self.guard.path.exists())
        self.assertEqual(self.invoke()["status"], "restored")

    def test_replacement_directory_cannot_inherit_cleanup_receipt(self):
        self.journal.put("docker-cleanup-authorized.json", cleanup_receipt(self.owner, self.root))
        self.root.rename(self.root.with_name("preserved-original"))
        self.root.mkdir(mode=0o700)
        (self.root / "owner.json").write_bytes(canonical(self.owner))
        with self.assertRaisesRegex(ValueError, "directory identity"):
            self.invoke()
        self.assertTrue(self.guard.path.exists())

    def test_missing_root_without_receipt_cannot_clear_guard(self):
        shutil.rmtree(self.root)
        with self.assertRaisesRegex(ValueError, "no verified"):
            self.invoke()
        self.assertTrue(self.guard.path.exists())

    def test_changed_marker_or_wrong_case_never_removes_root(self):
        with self.assertRaisesRegex(ValueError, "exact case ID"):
            self.invoke(key="b" * 64)
        (self.root / "owner.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "marker changed"):
            self.invoke()
        self.assertTrue(self.root.exists())

    def test_shutdown_receipt_is_required_even_when_no_processes_are_found(self):
        records = self.journal.records()
        records.pop("docker-vm-closed.json")
        with patch.object(ServiceJournal, "records", return_value=records):
            for apply in (False, True):
                with self.subTest(apply=apply), self.assertRaisesRegex(ValueError, "verified shutdown"):
                    self.invoke(apply)
        self.assertTrue(self.guard.path.exists())
        self.assertTrue(self.root.exists())

    def test_failed_authorization_or_changed_process_state_preserves_root(self):
        with patch.object(ServiceJournal, "put", side_effect=OSError("retention failed")), self.assertRaises(OSError):
            self.invoke()
        self.assertTrue(self.root.exists())
        with patch("recover_runtime.require_closed_vm", side_effect=[None, ValueError("late survivor")]), \
                self.assertRaisesRegex(ValueError, "late survivor"):
            self.invoke()
        self.assertTrue(self.root.exists())
        self.assertTrue(self.guard.path.exists())


if __name__ == "__main__":
    unittest.main()
