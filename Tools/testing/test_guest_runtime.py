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
        self.assertEqual(result["initialization"]["image"], images["images"][0])
        self.assertEqual(result["workload"]["image"], images["images"][1])
        self.assertEqual(i.call_count, 2)
        self.assertEqual(k.call_args.args[2], self.root / "prepared-releases")

    def test_missing_enhanced_image_never_falls_back_to_stock_or_mutates_storage(self):
        kernel, images = self.locks()
        with patch("guest_runtime.require_retained") as k, patch("guest_runtime.require_image") as i:
            with self.assertRaisesRegex(ValueError, "missing or ambiguous"):
                admit_guest(kernel, images, "container-compose", self.root)
            k.assert_not_called()
            i.assert_not_called()

    def test_malformed_locks_unknown_provider_and_wrong_init_reference_fail(self):
        kernel, images = self.locks()
        for lane, k, i in [("other", kernel, images), ("apple-stock", kernel, {}),
                            ("apple-stock", kernel, dict(images, images=images["images"] * 2)),
                            ("apple-stock", dict(kernel, assets=kernel["assets"] * 2), images)]:
            with self.subTest(lane=lane), self.assertRaises(ValueError):
                admit_guest(k, i, lane, self.root)
        wrong = copy.deepcopy(images)
        wrong["images"][0]["reference"] = "ghcr.io/apple/containerization/vminit:latest"
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
            with patch("guest_runtime.OwnedProcess", return_value=child), self.assertRaises(Exception):
                self.case.command("command-" + str(index), ["image", "load"])
            child.stop.assert_called_once()
            self.assertIn("command-" + str(index) + ".log", self.journal.records())

    def test_uncertain_child_never_records_successful_stop(self):
        child = Mock()
        child.start.side_effect = KeyboardInterrupt()
        child.stop.side_effect = RuntimeError("uncertain child")
        with patch("guest_runtime.OwnedProcess", return_value=child), self.assertRaisesRegex(ValueError, "still live"):
            self.case.command("guest-kernel", ["system", "kernel", "set"])
        self.assertNotIn("guest-kernel-stopped.json", self.journal.records())
        with self.assertRaisesRegex(ValueError, "process needs explicit"):
            require_guest_cleanup(self.journal.records())

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
                self.assertEqual(case.cleanup(), guest.cleanup.return_value)
                guest.cleanup.assert_called_once()
        self.assertEqual(self.case.cleanup()["status"], "passed")
        self.case.commands = [Mock()]
        self.case.cleanup()
        self.case.commands[0].stop.assert_called_once()

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
