"""Recovery uses real private evidence and fake launchd; no host service changes."""

from contextlib import nullcontext
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from case_evidence import CaseStore, canonical, digest, validate_identity
from host_runtime import HostGuard
from recover_runtime import cleanup_receipt, main, recover, recovery_idle
from runtime_services import ControlledRuntime
from service_journal import ServiceJournal
from service_switch import API, ServiceSwitch, snapshot
from test_service_switch import FakeLaunchd


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.scratch.cleanup)
        self.base = Path(self.scratch.name).resolve()
        self.retained, self.ssd = self.base / "retained", self.base / "ssd"
        self.retained.mkdir(mode=0o700)
        self.ssd.mkdir()
        (self.retained / "private-runtime").mkdir(mode=0o700)
        (self.ssd / "live").mkdir()
        self.root = self.ssd / "live/case-fixture"
        self.root.mkdir(mode=0o700)
        self.exe = self.ssd / "prepared-releases/fixture/bin/container-apiserver"
        self.exe.parent.mkdir(parents=True)
        self.exe.write_text("fake executable; never executed")
        self.identity = {"campaign": "recovery", "fixture": "E01-engine-negotiation", "lane": "apple-stock",
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
        self.result = {"status": "timeout", "observations": {}, "errors": ["operation: TimeoutError"],
                       "durationsNS": {"setup": 10, "operation": 5000, "cleanup": 1},
                       "cleanup": {"status": "unknown", "remainingOwnedResources": []}}
        self.store.finish(self.identity, self.result)
        self.launchd = FakeLaunchd()
        self.old = self.base / "original.plist"
        self.old.write_bytes(plistlib.dumps({"Label": API, "ProgramArguments": ["/original/api"]}))
        self.launchd.bootstrap(self.old)
        self.original = dict(self.launchd.jobs)
        self.journal = ServiceJournal(self.retained / "private-runtime" / (digest(str(self.root).encode()) + ".sqlite"),
                                      self.owner, create=True)
        self.journal.put("runtime-context.json", canonical({"apiExecutable": str(self.exe)}))
        self.journal.put("original-processes.plist", plistlib.dumps([]))
        self.switch = ServiceSwitch(self.launchd, snapshot(self.launchd, {API: self.base}), self.root, self.journal.put)
        self.switch.prepare()
        self.selected = self.root / "selected.plist"
        self.selected.write_bytes(plistlib.dumps({"Label": API, "ProgramArguments": [str(self.exe), "start"]}))
        self.switch.install(self.selected)
        (self.root / "container-logs").mkdir()
        (self.root / "container-logs/container-apiserver.log").write_bytes(b"private diagnostic fixture")
        self.inventory = patch("recover_runtime.process_inventory", return_value={}).start()
        patch("runtime_services.process_inventory", return_value={}).start()
        patch("runtime_services.process_programs", return_value=[]).start()
        self.addCleanup(patch.stopall)
        # Only the constructor's cross-volume assertion is mocked. Production
        # recovery requires genuine separate devices; all test files stay on SSD.
        def runtime(*args, **kwargs):
            with patch("runtime_services.Path.stat", side_effect=[SimpleNamespace(st_dev=1), SimpleNamespace(st_dev=2)]):
                return ControlledRuntime(*args, **kwargs)
        patch("recover_runtime.ControlledRuntime", side_effect=runtime).start()

    def run_recovery(self, apply=True, key=None):
        return recover(self.retained, self.ssd, apply=apply, expected_case=self.key if key is None else key,
                       launchd=self.launchd)

    def assert_quarantined(self):
        self.assertTrue(self.guard.path.exists())
        self.assertTrue(self.root.exists())
        self.assertEqual(self.store.begin(self.identity), self.result)

    def test_report_does_not_mutate_then_apply_restores_and_preserves_failed_case(self):
        before = list(self.launchd.mutations)
        report = self.run_recovery(apply=False)
        self.assertEqual(report, {"status": "ready-to-restore", "caseID": self.key, "changed": False})
        self.assertEqual(self.launchd.mutations, before)
        self.assert_quarantined()
        result = self.run_recovery()
        self.assertEqual(result["status"], "restored")
        self.assertEqual(self.launchd.jobs, self.original)
        self.assertFalse(self.guard.path.exists())
        self.assertFalse(self.root.exists())
        self.assertEqual(self.store.begin(self.identity), self.result)
        self.assertEqual(self.journal.records()["service-container-apiserver.log"], b"private diagnostic fixture")
        self.assertEqual(self.run_recovery(), {"status": "clear", "changed": False})

    def test_wrong_case_confirmation_refuses_mutation(self):
        before = list(self.launchd.mutations)
        with self.assertRaisesRegex(ValueError, "exact case ID"):
            self.run_recovery(key="b" * 64)
        self.assertEqual(before, self.launchd.mutations)
        self.assert_quarantined()

    def test_foreign_service_or_changed_original_keeps_quarantine(self):
        self.launchd.jobs[API]["path"] = "/foreign/api.plist"
        with self.assertRaisesRegex(ValueError, "Foreign service"):
            self.run_recovery()
        self.assert_quarantined()
        self.old.write_bytes(b"changed original")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.run_recovery()
        self.assert_quarantined()

    def test_live_engine_or_active_worker_prevents_any_recovery_mutation(self):
        before = list(self.launchd.mutations)
        for program in ("/released/devcontainer-engine", "/runner/Runner.Worker", str(self.root / "unknown-client")):
            self.inventory.return_value = {42: {"pid": 42, "started": "fixture", "program": program}}
            with self.subTest(program=program), self.assertRaises(ValueError):
                self.run_recovery()
            self.assertEqual(before, self.launchd.mutations)
            self.assert_quarantined()

    def test_journal_failure_after_restore_retains_root_and_guard(self):
        original = ServiceJournal.put
        def fail_receipt(journal, name, data):
            if name == "recovery-cleanup-authorized.json":
                raise OSError("injected retention failure")
            return original(journal, name, data)
        with patch.object(ServiceJournal, "put", fail_receipt), self.assertRaises(OSError):
            self.run_recovery()
        self.assertEqual(self.launchd.jobs, self.original)
        self.assert_quarantined()
        self.assertEqual(self.run_recovery()["status"], "restored")

    def test_crash_after_root_removal_resumes_only_with_matching_receipt(self):
        with patch.object(HostGuard, "clear", side_effect=OSError("injected crash")), self.assertRaises(OSError):
            self.run_recovery()
        self.assertFalse(self.root.exists())
        self.assertTrue(self.guard.path.exists())
        self.assertEqual(self.run_recovery(apply=False)["status"], "ready-to-clear")
        self.inventory.return_value = {42: {"pid": 42, "started": "fixture", "program": str(self.exe)}}
        with self.assertRaisesRegex(ValueError, "survived recovery"):
            self.run_recovery()
        self.inventory.return_value = {}
        self.assertEqual(self.run_recovery()["status"], "restored")
        self.assertEqual(self.store.begin(self.identity), self.result)

    def test_missing_root_without_receipt_or_changed_post_cleanup_service_fails(self):
        receipt = cleanup_receipt(self.owner, self.root)
        shutil.rmtree(self.root)
        with self.assertRaisesRegex(ValueError, "no verified"):
            self.run_recovery()
        self.journal.put("recovery-cleanup-authorized.json", receipt)
        with self.assertRaisesRegex(ValueError, "changed after"):
            self.run_recovery()
        self.assertTrue(self.guard.path.exists())

    def test_marker_change_and_symlinked_root_or_guard_are_rejected(self):
        (self.root / "owner.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "marker changed"):
            self.run_recovery()
        (self.root / "owner.json").write_bytes(canonical(self.owner))
        moved = self.root.with_name("moved")
        self.root.rename(moved)
        self.root.symlink_to(moved)
        with self.assertRaisesRegex(ValueError, "canonical owned"):
            self.run_recovery()
        self.root.unlink()
        moved.rename(self.root)
        self.guard.path.unlink()
        self.guard.path.symlink_to(self.root / "owner.json")
        with self.assertRaisesRegex(ValueError, "canonical"):
            self.run_recovery()

    def test_corrupted_sealed_case_cannot_authorize_recovery(self):
        with self.store.connect() as db:
            db.execute("UPDATE cases SET sha256=?", ("0" * 64,))
        before = list(self.launchd.mutations)
        with self.assertRaisesRegex(ValueError, "Corrupt"):
            self.run_recovery()
        self.assertEqual(before, self.launchd.mutations)
        self.assertTrue(self.guard.path.exists())

    def test_unchanged_registered_original_clients_are_not_treated_as_orphans(self):
        self.switch.restore()
        row = {"pid": 42, "started": "original", "program": "/original/devcontainer-engine"}
        self.inventory.return_value = {42: row}
        with patch("recover_runtime.capture_owned_processes", return_value=[row]), patch.object(self.launchd, "process_id", return_value=42):
            recovery_idle(self.launchd, self.switch.prior, self.root)
            self.inventory.return_value = {42: dict(row, started="different")}
            with self.assertRaisesRegex(ValueError, "Live case"):
                recovery_idle(self.launchd, self.switch.prior, self.root)

    def test_partial_root_deletion_resumes_only_for_the_same_directory(self):
        def partial_remove(root):
            (root / "owner.json").unlink()
            raise OSError("injected partial removal")
        with patch("recover_runtime.shutil.rmtree", side_effect=partial_remove), self.assertRaises(OSError):
            self.run_recovery()
        self.assertTrue(self.root.exists())
        self.assertFalse((self.root / "owner.json").exists())
        self.assertTrue(self.guard.path.exists())
        self.assertEqual(self.run_recovery()["status"], "restored")

    def test_replaced_root_cannot_inherit_an_authorized_cleanup(self):
        with patch("recover_runtime.shutil.rmtree", side_effect=OSError("injected crash")), self.assertRaises(OSError):
            self.run_recovery()
        self.root.rename(self.root.with_name("preserved-original"))
        self.root.mkdir(mode=0o700)
        with self.assertRaisesRegex(ValueError, "matching partial-cleanup"):
            self.run_recovery()
        (self.root / "owner.json").write_bytes(canonical(self.owner))
        with self.assertRaisesRegex(ValueError, "directory identity changed"):
            self.run_recovery()

    def add_process_artifact(self, name, value):
        # Reopen an interrupted case fixture before adding pre-seal ownership.
        with self.store.connect() as db:
            db.execute("UPDATE cases SET result=NULL,sha256=NULL")
        self.store.attach(self.identity, name, canonical(value))
        self.store.finish(self.identity, self.result)

    def test_uncertain_spawn_and_live_recorded_groups_are_never_guessed_away(self):
        self.add_process_artifact("process-intent.json", {"root": str(self.root), "program": "/release/devcontainer-engine"})
        with self.assertRaisesRegex(ValueError, "Interrupted client spawn"):
            self.run_recovery()
        self.add_process_artifact("process.json", {"root": str(self.root), "pid": 42})
        before = list(self.launchd.mutations)
        for pid, group in ((42, 42), (43, 42)):
            self.inventory.return_value = {pid: {"pid": pid, "group": group, "started": "fixture", "program": "/bin/sh"}}
            with self.assertRaisesRegex(ValueError, "Recorded client PID"):
                self.run_recovery()
            self.assertEqual(self.launchd.mutations, before)
        self.inventory.return_value = {}
        self.assertEqual(self.run_recovery()["status"], "restored")

    def test_registered_original_does_not_exempt_active_workers_or_guests(self):
        self.switch.restore()
        worker = {"pid": 43, "started": "worker", "program": "/runner/Runner.Worker"}
        listener = {"pid": 42, "started": "listener", "program": "/runner/Runner.Listener"}
        with patch("recover_runtime.capture_owned_processes", return_value=[listener, worker]), \
                patch.object(self.launchd, "process_id", return_value=42):
            for program in ("/runner/Runner.Worker", "/release/container-runtime-linux", "/release/container"):
                self.inventory.return_value = {42: listener, 43: dict(worker, program=program)}
                with self.assertRaisesRegex(ValueError, "Active worker"):
                    recovery_idle(self.launchd, self.switch.prior, self.root)

    def test_incomplete_or_foreign_runtime_context_cannot_restore_services(self):
        before = list(self.launchd.mutations)
        for context in (None, {"apiExecutable": "/foreign/bin/container-apiserver"}):
            self.journal.path.unlink()
            self.journal = ServiceJournal(self.journal.path, self.owner, create=True)
            if context is not None:
                self.journal.put("runtime-context.json", canonical(context))
            with self.subTest(context=context), self.assertRaises(ValueError):
                self.run_recovery()
            self.assertEqual(self.launchd.mutations, before)
            self.assert_quarantined()

    def test_missing_case_owner_artifact_or_public_database_refuses_before_mutation(self):
        before = list(self.launchd.mutations)
        self.store.path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "private canonical"):
            self.run_recovery()
        self.store.path.chmod(0o600)
        with self.store.connect() as db:
            db.execute("DELETE FROM artifacts WHERE name='owner.json'")
        with self.assertRaisesRegex(ValueError, "ownership artifact"):
            self.run_recovery()
        self.assertEqual(self.launchd.mutations, before)

    def test_invalid_recorded_pid_refuses_even_if_no_process_is_running(self):
        self.add_process_artifact("process.json", {"root": str(self.root), "pid": -1})
        before = list(self.launchd.mutations)
        with self.assertRaisesRegex(ValueError, "Invalid client"):
            self.run_recovery()
        self.assertEqual(self.launchd.mutations, before)

    def test_main_checks_enrolled_storage_and_redacts_operational_errors(self):
        fake_home = self.base / "home"
        retained = fake_home / "Library/Application Support/ContainerFamily/retained/workflow"
        retained.mkdir(parents=True)
        enrollment = retained / "ssd-volume.uuid"
        enrollment.write_text("fixture-uuid\n")
        real_stat = Path.stat
        def filesystem_info(path, *args, **kwargs):
            if path == self.ssd:
                return SimpleNamespace(st_dev=real_stat(retained).st_dev + 1)
            return real_stat(path, *args, **kwargs)
        def resolve_path(value):
            return self.ssd if value == "/Volumes/SSD/cf/bazel" else Path(value)
        with patch("recover_runtime.Path", side_effect=resolve_path) as paths, \
                patch("recover_runtime.Path.home", return_value=fake_home), \
                patch.object(Path, "stat", filesystem_info), \
                patch("recover_runtime.require_owned_volume", return_value={"uuid": "fixture-uuid"}), \
                patch("recover_runtime.runtime_lease", return_value=nullcontext()) as lease, \
                patch("recover_runtime.recover", return_value={"status": "clear", "changed": False}) as recovery, \
                patch("sys.argv", ["recover-runtime"]), patch("sys.stdout", new_callable=io.StringIO) as output:
            paths.home.return_value = fake_home
            main()
            self.assertEqual(json.loads(output.getvalue())["status"], "clear")
            recovery.assert_called_once_with(retained, self.ssd, apply=False, expected_case=None)
            lease.assert_called_once()
            output.truncate(0)
            output.seek(0)
            recovery.side_effect = ValueError("fixture-secret-must-not-appear")
            with self.assertRaises(SystemExit) as status:
                main()
            self.assertEqual(status.exception.code, 1)
            self.assertEqual(json.loads(output.getvalue())["error"], "ValueError")
            self.assertNotIn("fixture-secret", output.getvalue())
            recovery.reset_mock()
            enrollment.write_text("different-uuid\n")
            with self.assertRaisesRegex(ValueError, "enrolled SSD"):
                main()
            recovery.assert_not_called()


if __name__ == "__main__":
    unittest.main()
