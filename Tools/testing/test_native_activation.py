"""Stable-path publication and interruption tests; never call host launchd."""

from contextlib import nullcontext
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import native_activation as activation
from case_evidence import canonical, digest
from prepare_releases import durable_file, inventory
from host_runtime import deadline


class NativeActivationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.retained = self.root / "retained"
        self.retained.mkdir(mode=0o700)
        self.source = self.release("one")
        self.lane = "apple-stock"
        self.slot = self.retained / "active-runtimes" / self.lane
        self.processes = patch("native_activation.process_inventory", return_value={}).start()
        self.launchd = patch("native_activation.Launchd").start().return_value
        self.launchd.labels.return_value = set()
        self.addCleanup(patch.stopall)

    def release(self, version):
        install = self.root / ("release-" + version)
        install.mkdir(mode=0o700)
        (install / "bin").mkdir(mode=0o755)
        paths = {}
        for name in ("container", "container-apiserver"):
            path = install / "bin" / name
            path.write_bytes((version + name).encode())
            path.chmod(0o755)
            paths[name] = str(path)
        return {"root": str(install), "assetSHA256": digest(version.encode()),
                "inventorySHA256": digest(canonical(inventory(install))), "executables": paths}

    def activate(self, source=None, revalidate=None):
        source = self.source if source is None else source
        return activation.activate(source, self.retained, self.lane, revalidate or (lambda: source))

    def test_first_activation_reuses_paths_and_never_changes_original_or_launches(self):
        before = inventory(Path(self.source["root"]))
        first = self.activate()
        executable = Path(first["executables"]["container"])
        self.assertEqual(executable, self.slot / "payload/bin/container")
        self.assertEqual(first["activation"]["source"], self.source)
        inode = executable.stat().st_ino
        self.assertEqual(self.activate(), first)
        self.assertEqual(executable.stat().st_ino, inode)
        next_source = self.release("two")
        second = self.activate(next_source)
        self.assertEqual(second["executables"], first["executables"])
        self.assertNotEqual(second["activation"], first["activation"])
        self.assertEqual(executable.read_bytes(), b"twocontainer")
        self.assertEqual(inventory(Path(self.source["root"])), before)
        self.assertEqual(set(path.name for path in self.slot.iterdir()), {"active.json", "payload"})
        self.launchd.bootstrap.assert_not_called()
        self.launchd.bootout.assert_not_called()

    def test_read_only_admission_rejects_missing_interrupted_changed_and_symlinked_inputs(self):
        with self.assertRaises(FileNotFoundError):
            activation.require_active(self.source, self.retained, self.lane)
        self.assertFalse(self.slot.exists())
        self.activate()
        (self.slot / "pending.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "unfinished"):
            activation.require_active(self.source, self.retained, self.lane)
        (self.slot / "pending.json").unlink()
        changed = dict(self.source, assetSHA256="a" * 64)
        with self.assertRaisesRegex(ValueError, "differs"):
            activation.require_active(changed, self.retained, self.lane)
        binary = self.slot / "payload/bin/container"
        binary.unlink()
        binary.symlink_to(self.source["executables"]["container"])
        with self.assertRaisesRegex(ValueError, "link or special"):
            activation.require_active(self.source, self.retained, self.lane)

    def test_resume_copy_interruption_preserves_prior_until_success(self):
        self.activate()
        next_source = self.release("two")
        def fail_copy(path, source, data=None, *, mode=0o600):
            if source is not None:
                durable_file(path, None, b"partial", mode=mode)
                raise RuntimeError("copy interrupted")
            durable_file(path, source, data, mode=mode)
        with patch("native_activation.durable_file", side_effect=fail_copy):
            with self.assertRaisesRegex(RuntimeError, "copy interrupted"):
                self.activate(next_source)
        self.assertEqual((self.slot / "previous/bin/container").read_bytes(), b"onecontainer")
        with self.assertRaisesRegex(ValueError, "unfinished"):
            activation.require_active(next_source, self.retained, self.lane)
        with self.assertRaisesRegex(ValueError, "different inputs"):
            self.activate()
        self.activate(next_source)
        self.assertFalse((self.slot / "previous").exists())
        self.assertFalse((self.slot / "pending.json").exists())

    def test_resume_interrupted_receipt_and_partial_previous_cleanup(self):
        self.activate()
        next_source = self.release("two")
        def fail_receipt(path, source, data=None, *, mode=0o600):
            if path.name == "active.json":
                durable_file(path, None, b"{", mode=mode)
                raise RuntimeError("receipt interrupted")
            durable_file(path, source, data, mode=mode)
        with patch("native_activation.durable_file", side_effect=fail_receipt):
            with self.assertRaisesRegex(RuntimeError, "receipt interrupted"):
                self.activate(next_source)
        def fail_retire(path):
            (path / "bin/container").unlink()
            raise RuntimeError("cleanup interrupted")
        with patch("native_activation.shutil.rmtree", side_effect=fail_retire):
            with self.assertRaisesRegex(RuntimeError, "cleanup interrupted"):
                self.activate(next_source)
        self.activate(next_source)
        self.assertEqual(activation.require_active(next_source, self.retained, self.lane)["root"],
                         str(self.slot / "payload"))

    def test_resume_interruption_after_prior_rename(self):
        self.activate()
        next_source = self.release("two")
        synchronize = activation.sync_directory
        def fail_after_rename(path):
            synchronize(path)
            if (self.slot / "previous").exists() and not (self.slot / "payload").exists():
                raise RuntimeError("rename interrupted")
        with patch("native_activation.sync_directory", side_effect=fail_after_rename):
            with self.assertRaisesRegex(RuntimeError, "rename interrupted"):
                self.activate(next_source)
        self.activate(next_source)

    def test_foreign_files_and_changed_retired_bytes_are_never_removed(self):
        self.activate()
        next_source = self.release("two")
        with patch("native_activation.retire_previous", side_effect=RuntimeError("pause")):
            with self.assertRaises(RuntimeError):
                self.activate(next_source)
        foreign = self.slot / "previous/operator.txt"
        foreign.write_text("preserve")
        with self.assertRaisesRegex(ValueError, "Previous runtime changed"):
            self.activate(next_source)
        self.assertEqual(foreign.read_text(), "preserve")
        self.assertTrue((self.slot / "pending.json").exists())

    def test_changed_source_or_busy_runtime_prevents_publication(self):
        with self.assertRaisesRegex(ValueError, "source changed before"):
            self.activate(revalidate=lambda: {})
        self.processes.return_value = {1: {"program": str(self.slot / "payload/bin/container-apiserver")}}
        with self.assertRaisesRegex(ValueError, "process still owns"):
            self.activate()
        self.assertFalse((self.slot / "pending.json").exists())
        self.processes.return_value = {}
        self.launchd.labels.return_value = {"io.github.stephenlclarke.container.engine"}
        self.launchd.program.return_value = str(self.slot / "payload/bin/container-apiserver")
        with self.assertRaisesRegex(ValueError, "registration still owns"):
            self.activate()
        self.launchd.labels.return_value = set()
        source = Mock(side_effect=[self.source, {}])
        with self.assertRaisesRegex(ValueError, "bytes changed during"):
            self.activate(revalidate=source)
        self.assertTrue((self.slot / "pending.json").exists())
        self.activate()

    def test_special_receipts_fail_without_waiting_for_a_fifo_peer(self):
        self.activate()
        for name in ("pending.json", "active.json"):
            path = self.slot / name
            if path.exists():
                path.unlink()
            os.mkfifo(path, 0o600)
            with deadline(0.5), self.assertRaisesRegex(ValueError, "regular file"):
                activation.read_record(path)
            with deadline(0.5), self.assertRaises((ValueError, OSError)):
                durable_file(path, None, b"never written")
            path.unlink()

    def test_dormant_alias_registration_is_rejected_before_copy(self):
        self.activate()
        alias = self.root / "alias"
        alias.symlink_to(self.slot / "payload/bin/container")
        self.launchd.labels.return_value = {"custom.provider"}
        self.launchd.program.return_value = str(alias)
        with self.assertRaisesRegex(ValueError, "registration still owns"):
            self.activate(self.release("two"))
        self.assertFalse((self.slot / "pending.json").exists())

    def test_source_layout_and_slot_aliases_are_rejected(self):
        for source in ({}, dict(self.source, files={"unexpected": "data"}),
                       dict(self.source, executables={**self.source["executables"], "escape": "/bin/sh"})):
            with self.assertRaises(ValueError):
                self.activate(source)
        with self.assertRaises(ValueError):
            activation.slot_path(self.retained, "../escape", create=True)
        parent = self.retained / "active-runtimes"
        parent.mkdir(mode=0o700)
        self.slot.symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, "canonical"):
            self.activate()

    def test_cli_takes_family_lease_and_emits_no_permission_claim(self):
        with patch("released_engine.RETAINED", self.retained), patch("pathlib.Path.home", return_value=self.root), \
                patch("released_engine.admit", return_value=[{}, self.source]), \
                patch("native_activation.runtime_lease", return_value=nullcontext()) as lease, \
                patch("sys.argv", ["activate-runtime", "--lane", self.lane]), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            activation.main()
        self.assertEqual(lease.call_args.args[0], Path(f"/private/tmp/container-compose-runtime-{os.getuid()}.lock"))
        self.assertEqual(lease.call_args.args[1].path, self.retained / "runtime-admission.json")
        result = json.loads(output.getvalue())
        self.assertFalse(result["servicesStarted"])
        self.assertFalse(result["authorizationVerified"])


if __name__ == "__main__":
    unittest.main()
