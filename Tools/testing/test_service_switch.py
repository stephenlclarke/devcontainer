"""Fault tests for scoped service replacement; never invoke real launchctl."""

import os
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch

from service_switch import API, BASE_SERVICES, Launchd, ServiceSwitch, canonical_file, snapshot


class FakeLaunchd:
    def __init__(self):
        self.jobs = {}
        self.mutations = []
        self.fail = None
        self.cascade = set()

    def labels(self):
        return set(self.jobs)

    def inspect(self, label):
        return self.jobs.get(label)

    def process_id(self, label):
        return None  # These fake registrations have no actual host processes.

    def bootout(self, label):
        if self.fail == ("bootout", label):
            raise RuntimeError("injected removal failure")
        self.mutations.append(("bootout", label))
        del self.jobs[label]
        if label == API:
            for child in self.cascade:
                self.jobs.pop(child, None)

    def bootstrap(self, path):
        job = plistlib.loads(path.read_bytes())
        label = job["Label"]
        if label in self.jobs:
            raise RuntimeError("cannot replace an existing job")
        self.mutations.append(("bootstrap", label))
        self.jobs[label] = {"label": label, "path": str(path), "program": job["ProgramArguments"][0]}
        if self.fail == ("bootstrap-after", label):
            raise RuntimeError("interrupted after successful registration")


class ServiceSwitchTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name).resolve()
        self.original = self.root / "original"
        self.owned = self.root / "owned"
        self.original.mkdir()
        self.owned.mkdir()
        self.launchd = FakeLaunchd()
        for label in sorted(BASE_SERVICES):
            self.launchd.bootstrap(self.definition(self.original, label, "/original/bin/" + label))
        self.prior = snapshot(self.launchd, {label: self.original for label in BASE_SERVICES})
        self.launchd.mutations.clear()
        self.events = {}
        self.switch = ServiceSwitch(self.launchd, self.prior, self.owned, self.journal)
        self.selected = self.definition(self.owned, API, "/released/bin/container-apiserver")

    def definition(self, root, label, program):
        path = root / (label + ".plist")
        path.write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": [program, "start"]}))
        return path

    def journal(self, name, payload):
        if name in self.events:
            raise ValueError("journal event cannot be overwritten")
        self.events[name] = payload

    def assert_restored(self):
        self.assertEqual(self.launchd.labels(), BASE_SERVICES)
        for original in self.prior:
            self.assertEqual(self.launchd.inspect(original["label"]),
                             {key: original[key] for key in ("label", "path", "program")})
            self.assertEqual(Path(original["path"]).read_bytes(), original["payload"])

    def test_switch_and_restore_originals_without_editing_definitions(self):
        self.switch.prepare()
        self.assertEqual(self.launchd.labels(), set())
        self.switch.install(self.selected)
        plugin = "com.apple.container.container-network-vmnet.fixture"
        self.launchd.bootstrap(self.definition(self.owned, plugin, "/released/plugin"))
        self.switch.restore()
        self.assert_restored()
        self.assertIn("service-originals.plist", self.events)
        self.assertIn("service-selected.plist", self.events)
        self.assertEqual(self.events["service-event-0001.txt"], b"bootout-original\n" + API.encode() + b"\n")
        count = len(self.launchd.mutations)
        self.switch.restore()
        self.assertEqual(len(self.launchd.mutations), count)

    def test_unrecognised_workload_prevents_snapshot_before_mutation(self):
        extra = "com.apple.container.container-runtime-linux.user-workload"
        self.launchd.jobs[extra] = {"label": extra, "path": "/user/workload.plist", "program": "/user/runtime"}
        roots = {label: self.original for label in BASE_SERVICES}
        with self.assertRaisesRegex(ValueError, "Unaccounted"):
            snapshot(self.launchd, roots)
        self.assertEqual(self.launchd.mutations, [])

    def test_snapshot_rejects_outside_roots_changed_labels_and_missing_jobs(self):
        roots = {label: self.owned for label in BASE_SERVICES}
        with self.assertRaisesRegex(ValueError, "outside"):
            snapshot(self.launchd, roots)
        roots = {label: self.original for label in BASE_SERVICES}
        with patch.object(self.launchd, "inspect", return_value=None), self.assertRaisesRegex(ValueError, "inventory changed"):
            snapshot(self.launchd, roots)
        path = Path(self.prior[0]["path"])
        path.write_bytes(plistlib.dumps({"Label": "changed", "ProgramArguments": ["/different"]}))
        with self.assertRaisesRegex(ValueError, "disagree"):
            snapshot(self.launchd, roots)

    def test_unsafe_definition_and_root_are_rejected(self):
        path = self.root / "alias"
        path.symlink_to(self.selected)
        with self.assertRaisesRegex(ValueError, "canonical"):
            canonical_file(path)
        self.selected.chmod(0o666)
        with self.assertRaisesRegex(ValueError, "private owned"):
            canonical_file(self.selected)
        root = Path("/")
        with self.assertRaisesRegex(ValueError, "canonical"):
            ServiceSwitch(self.launchd, self.prior, root, self.journal)

    def test_journal_failure_occurs_before_any_service_mutation(self):
        with patch.object(self.switch, "journal", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.switch.prepare()
        self.assertEqual(self.launchd.mutations, [])

    def test_partial_removal_is_restored(self):
        self.launchd.fail = ("bootout", self.prior[1]["label"])
        with self.assertRaises(RuntimeError):
            self.switch.prepare()
        self.launchd.fail = None
        self.switch.restore()
        self.assert_restored()

    def test_parent_removal_can_remove_its_snapshotted_plugins(self):
        self.launchd.cascade = BASE_SERVICES - {API}
        self.switch.prepare()
        self.switch.install(self.selected)
        self.switch.restore()
        self.assert_restored()
        reconciled = [payload for payload in self.events.values() if payload.startswith(b"absent-after-parent-removal")]
        self.assertEqual(len(reconciled), 2)

    def test_interrupted_registration_is_reconciled_by_identity(self):
        self.switch.prepare()
        self.launchd.fail = ("bootstrap-after", API)
        with self.assertRaises(RuntimeError):
            self.switch.install(self.selected)
        self.launchd.fail = None
        self.switch.restore()
        self.assert_restored()

    def test_replaced_selected_definition_cannot_pass_registration(self):
        self.switch.prepare()
        original_bootstrap = self.launchd.bootstrap

        def replace_before_bootstrap(path):
            self.definition(self.owned, API, "/replaced/api")
            original_bootstrap(path)

        with patch.object(self.launchd, "bootstrap", side_effect=replace_before_bootstrap), \
                self.assertRaisesRegex(ValueError, "journalled definition"):
            self.switch.install(self.selected)
        self.switch.restore()
        self.assert_restored()

    def test_loaded_identity_cannot_differ_even_if_plist_is_unchanged(self):
        self.switch.prepare()
        original_bootstrap = self.launchd.bootstrap

        def different_registration(path):
            original_bootstrap(path)
            self.launchd.jobs[API]["program"] = "/different/api"

        with patch.object(self.launchd, "bootstrap", side_effect=different_registration), \
                self.assertRaisesRegex(ValueError, "journalled definition"):
            self.switch.install(self.selected)
        self.switch.restore()
        self.assert_restored()

    def test_restore_reentry_handles_already_registered_original(self):
        self.switch.prepare()
        self.launchd.fail = ("bootstrap-after", API)
        with self.assertRaises(RuntimeError):
            self.switch.restore()
        self.launchd.fail = None
        self.switch.restore()
        self.assert_restored()

    def test_restore_rejects_plist_replaced_during_bootstrap_with_same_identity(self):
        self.switch.prepare()
        original_bootstrap = self.launchd.bootstrap

        def replace_before_bootstrap(path):
            definition = plistlib.loads(path.read_bytes())
            definition["EnvironmentVariables"] = {"CHANGED": "yes"}
            path.write_bytes(plistlib.dumps(definition))
            original_bootstrap(path)

        with patch.object(self.launchd, "bootstrap", side_effect=replace_before_bootstrap), \
                self.assertRaisesRegex(ValueError, "Original service definition changed"):
            self.switch.restore()
        self.assertFalse(any(event == b"restored\nall\n" for event in self.events.values()))
        self.assertEqual(len(self.launchd.jobs), 1)
        # The unchanged public identity must not make a repeated restore pass.
        mutations = len(self.launchd.mutations)
        with self.assertRaisesRegex(ValueError, "Original service definition changed"):
            self.switch.restore()
        self.assertEqual(len(self.launchd.mutations), mutations)

    def test_restore_revalidates_already_registered_original_after_inspection(self):
        self.switch.prepare()
        self.switch.restore()
        inspect = self.launchd.inspect
        original = self.prior[0]

        def change_after_inspect(label):
            value = inspect(label)
            if label == original["label"]:
                definition = plistlib.loads(original["payload"])
                definition["MachServices"] = {"different.service": True}
                Path(original["path"]).write_bytes(plistlib.dumps(definition))
            return value

        count = len(self.launchd.mutations)
        with patch.object(self.launchd, "inspect", side_effect=change_after_inspect), \
                self.assertRaisesRegex(ValueError, "Original service definition changed"):
            self.switch.restore_originals()
        self.assertEqual(len(self.launchd.mutations), count)

    def test_fresh_worker_recovers_from_authenticated_snapshot_and_sequence(self):
        self.switch.prepare()
        self.switch.install(self.selected)
        recovered = ServiceSwitch.recover(self.launchd, self.prior, self.owned, self.journal, self.switch.sequence)
        recovered.restore()
        self.assert_restored()
        with self.assertRaisesRegex(ValueError, "sequence"):
            ServiceSwitch.recover(self.launchd, self.prior, self.owned, self.journal, -1)

    def test_new_runtime_job_after_snapshot_blocks_all_mutations(self):
        extra = "com.apple.container.new-workload"
        self.launchd.jobs[extra] = {"label": extra, "path": "/user/job.plist", "program": "/user/bin"}
        with self.assertRaisesRegex(ValueError, "inventory changed"):
            self.switch.prepare()
        self.assertEqual(self.launchd.mutations, [])

    def test_changed_original_or_foreign_survivor_prevents_restore(self):
        self.switch.prepare()
        self.switch.install(self.selected)
        mutations = len(self.launchd.mutations)
        self.launchd.jobs[API] = {"label": API, "path": "/foreign/api.plist", "program": "/foreign/api"}
        with self.assertRaisesRegex(ValueError, "Foreign"):
            self.switch.restore()
        self.assertEqual(len(self.launchd.mutations), mutations)
        Path(self.prior[0]["path"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "definition changed"):
            self.switch.restore()
        self.assertEqual(len(self.launchd.mutations), mutations)

    def test_changed_job_before_prepare_cannot_be_removed(self):
        self.launchd.jobs[API] = {"label": API, "path": "/foreign/api.plist", "program": "/foreign/api"}
        with self.assertRaisesRegex(ValueError, "changed before removal"):
            self.switch.prepare()
        self.assertEqual(self.launchd.mutations, [])

    def test_install_requires_preparation_owned_definition_and_available_label(self):
        with self.assertRaisesRegex(ValueError, "prepared owned"):
            self.switch.install(self.selected)
        self.switch.prepare()
        with self.assertRaisesRegex(ValueError, "already prepared"):
            self.switch.prepare()
        outside = Path(self.prior[0]["path"])
        with self.assertRaisesRegex(ValueError, "prepared owned"):
            self.switch.install(outside)
        self.switch.install(self.selected)
        with self.assertRaisesRegex(ValueError, "not available"):
            self.switch.install(self.selected)

    def test_unprepared_restore_does_not_touch_services(self):
        self.switch.restore()
        self.assertEqual(self.launchd.mutations, [])

    def test_launchctl_adapter_parses_only_identity_and_never_logs_raw_errors(self):
        backend = Launchd()
        with patch("service_switch.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b"PID\tStatus\tLabel\n42\t0\tjob\n"
            self.assertEqual(backend.labels(), {"job"})
            run.return_value.stdout = b"job = {\n\tpath = /owned/job.plist\n\tprogram = /owned/bin\n}\n"
            self.assertEqual(backend.inspect("job"), {"label": "job", "path": "/owned/job.plist", "program": "/owned/bin"})
            self.assertIsNone(backend.process_id("job"))
            run.return_value.stdout = b"job = {\n\tpid = 42\n}\n"
            self.assertEqual(backend.process_id("job"), 42)
            run.return_value.stdout = b"job = {\n\tpid = 0\n}\n"
            with self.assertRaisesRegex(ValueError, "Ambiguous"):
                backend.process_id("job")
            backend.bootout("job")
            backend.bootstrap(self.selected)
            self.assertEqual(run.call_args.kwargs["timeout"], 10)
            run.return_value.returncode = 113
            self.assertIsNone(backend.inspect("job"))
            run.return_value.returncode = 1
            run.return_value.stderr = b"sensitive data"
            for operation in (backend.labels, lambda: backend.inspect("job"), lambda: backend.process_id("job"), lambda: backend.bootout("job"),
                              lambda: backend.bootstrap(self.selected)):
                with self.assertRaises(RuntimeError) as error:
                    operation()
                self.assertNotIn("sensitive", str(error.exception))


if __name__ == "__main__":
    unittest.main()
