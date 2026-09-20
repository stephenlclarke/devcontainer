"""Synthetic registration metadata only; never inspect the host's applications."""

from copy import deepcopy
import os
from pathlib import Path
import plistlib
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import background_items as btm
from host_runtime import cancellation, deadline
from service_switch import Launchd


class BackgroundItemsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.addCleanup(patch.stopall)
        patch.object(btm, "APPLICATION_ROOTS", (self.root,)).start()
        patch.object(btm.Path, "home", return_value=self.root).start()
        self.app = self.root / "Parent.app"
        self.uuid = "12345678-1234-1234-1234-123456789ABC"
        self.parent = {"UUID": "00000000-1234-1234-1234-123456789ABC", "Identifier": "2.test.parent",
                       "URL": str(self.app), "Bundle Identifier": "test.parent"}
        self.child = {"UUID": self.uuid, "Identifier": "8.test.agent", "Parent Identifier": "2.test.parent",
                      "URL": "Contents/Library/LaunchAgents/test.agent.plist",
                      "Executable Path": "Contents/MacOS/helper"}
        self.items = [self.parent, self.child]
        self.write(self.app / "Contents/Info.plist", {"CFBundleIdentifier": "test.parent", "CFBundleVersion": "1"})
        self.definition = self.app / self.child["URL"]
        self.write(self.definition, {"Label": "test.agent", "BundleProgram": "Contents/MacOS/helper"})
        self.executable = self.app / "Contents/MacOS/helper"
        self.executable.parent.mkdir()
        self.executable.write_bytes(b"fixture only; never executable")
        self.output = self.job("Contents/MacOS/helper", 2)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps(value))
        path.chmod(0o600)

    def job(self, identifier, mode):
        return ("\ttype = Submitted\n\tmanaged_by = com.apple.xpc.ServiceManagement\n"
                f"\tprogram identifier = {identifier} (mode: {mode})\n\tBTM uuid = {self.uuid}\n"
                "\tparent bundle identifier = test.parent\n\tparent bundle version = 1\n")

    def test_mode_two_binds_uuid_parent_and_registered_definition(self):
        self.assertEqual(btm.resolve(self.output, "test.agent", self.items), str(self.app / "Contents/MacOS/helper"))
        self.parent["URL"] = self.app.as_uri()
        self.assertEqual(btm.resolve(self.output, "test.agent", self.items), str(self.app / "Contents/MacOS/helper"))
        self.write(self.app / "Contents/Info.plist", {"CFBundleIdentifier": "test.parent", "CFBundleVersion": "2"})
        self.assertEqual(btm.resolve(self.output, "test.agent", self.items), str(self.app / "Contents/MacOS/helper"))
        for key, value in (("UUID", "absent"), ("Parent Identifier", "2.other"),
                           ("Identifier", "8.other"), ("Executable Path", "Contents/MacOS/other")):
            changed = deepcopy(self.items)
            changed[1][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                btm.resolve(self.output, "test.agent", changed)
        with self.assertRaises(ValueError):
            btm.resolve(self.output, "test.agent", self.items + [self.parent])
        self.write(self.definition, {"Label": "test.agent", "BundleProgram": "Contents/MacOS/helper", "Program": "/elsewhere"})
        with self.assertRaisesRegex(ValueError, "executable differs"):
            btm.resolve(self.output, "test.agent", self.items)

    def test_mode_one_uses_exact_embedded_bundle_not_a_global_bundle_lookup(self):
        self.child.update({"Identifier": "4.test.login", "Bundle Identifier": "test.login",
                           "URL": "Contents/Library/LoginItems/Login.app"})
        info = self.app / self.child["URL"] / "Contents/Info.plist"
        self.write(info, {"CFBundleIdentifier": "test.login", "CFBundleExecutable": "Login"})
        (info.parent / "MacOS").mkdir()
        (info.parent / "MacOS/Login").write_bytes(b"fixture only")
        output = self.job("test.login", 1)
        self.assertEqual(btm.resolve(output, "test.login", self.items), str(info.parent / "MacOS/Login"))
        for executable in ("../escape", "", ".", "..", 123):
            self.write(info, {"CFBundleIdentifier": "test.login", "CFBundleExecutable": executable})
            with self.subTest(executable=executable), self.assertRaises(ValueError):
                btm.resolve(output, "test.login", self.items)

    def test_metadata_changes_unsupported_modes_and_conflicting_fields_fail(self):
        for old, new in (("Submitted", "Unknown"), ("mode: 2", "mode: 3")):
            with self.subTest(old=old), self.assertRaises(ValueError):
                btm.resolve(self.output.replace(old, new), "test.agent", self.items)
        with self.assertRaises(ValueError):
            btm.resolve(self.output + "\tBTM uuid = other\n", "test.agent", self.items)
        self.parent["Bundle Identifier"] = "other"
        with self.assertRaisesRegex(ValueError, "bundle differs"):
            btm.resolve(self.output, "test.agent", self.items)

    def test_protected_paths_escapes_aliases_and_fifo_metadata_fail_before_reading(self):
        for location in ("/Volumes/Other/Parent.app", "/Users/example/Documents/Parent.app", "relative.app",
                         "file://server/Parent.app", self.app.as_uri() + "?query"):
            with self.subTest(location=location), self.assertRaises(ValueError):
                btm.bundle_root(location)
        for location in ("../escape", "/absolute", "", "Contents/./escape"):
            with self.subTest(location=location), self.assertRaises(ValueError):
                btm.embedded(self.app, location)
        alias = self.root / "Alias.app"
        alias.symlink_to(self.app)
        with self.assertRaisesRegex(ValueError, "alias"):
            btm.bundle_root(str(alias))
        self.definition.unlink()
        os.mkfifo(self.definition, 0o600)
        with deadline(0.5), self.assertRaisesRegex(ValueError, "regular file"):
            btm.read_plist(self.definition)
        self.definition.unlink()
        self.write(self.definition, {"Label": "test.agent", "BundleProgram": "Contents/MacOS/helper"})
        self.executable.unlink()
        self.executable.symlink_to("/Volumes/Other/protected")
        with self.assertRaisesRegex(ValueError, "alias"):
            btm.resolve(self.output, "test.agent", self.items)

    def test_parses_only_selected_uid_and_rejects_ambiguous_or_incomplete_records(self):
        def section(uid, items):
            return f" Records for UID {uid} : ABCD\n" + "".join(
                f" #{index}:\n" + "".join(f"  {key}: {value}\n" for key, value in item.items())
                for index, item in enumerate(items))
        payload = (section(-2, [self.parent]) + section(os.getuid(), self.items)).encode()
        self.assertEqual(btm.records(payload, os.getuid()), self.items)
        self.assertEqual(btm.records(payload + b"  Embedded Item Identifiers:\n    #1: 8.test.agent\n", os.getuid()), self.items)
        for broken in (b"", payload + b"  UUID: duplicate\n", section(os.getuid(), [{}]).encode(),
                       payload + b"Records for UID 0 : malformed!\n #1:\n", payload + b" #bad:\n",
                       b"x" * (4 * 1024 * 1024 + 1)):
            with self.assertRaises(ValueError):
                btm.records(broken, os.getuid())
        with patch.object(btm, "capture_inventory", return_value=payload):
            self.assertEqual(btm.host_records(), self.items)

    def test_launchd_rechecks_job_and_snapshot_without_new_permission_requests(self):
        backend = Launchd()
        result = SimpleNamespace(returncode=0, stdout=self.output.encode())
        with patch.object(backend, "command", return_value=result), patch.object(btm, "host_records", return_value=self.items) as load:
            expected = str(self.app / "Contents/MacOS/helper")
            self.assertEqual(backend.program("test.agent"), expected)
            self.assertEqual(backend.program("test.agent"), expected)
            load.assert_called_once()
            backend.require_background_unchanged()
            load.return_value = []
            with self.assertRaisesRegex(ValueError, "inventory changed"):
                backend.require_background_unchanged()
        backend = Launchd()
        changed = SimpleNamespace(returncode=113, stdout=b"")
        with patch.object(backend, "command", side_effect=[result, changed]), patch.object(btm, "host_records", return_value=self.items):
            with self.assertRaisesRegex(ValueError, "changed during"):
                backend.program("test.agent")

    def test_capture_has_fixed_read_only_command_and_discards_stderr(self):
        process = Mock()
        process.wait.return_value = 0
        process.poll.return_value = 0
        with patch.object(btm.subprocess, "Popen", return_value=process) as spawn, \
                patch.object(btm.selectors, "DefaultSelector"), patch.object(btm.os, "read", side_effect=[b"bounded", b""]):
            self.assertEqual(btm.capture_inventory(), b"bounded")
        self.assertEqual(spawn.call_args.args[0], ["/usr/bin/sudo", "-n", "/usr/bin/sfltool", "dumpbtm"])
        self.assertEqual(spawn.call_args.kwargs["stdin"], btm.subprocess.DEVNULL)
        self.assertEqual(spawn.call_args.kwargs["stderr"], btm.subprocess.DEVNULL)
        process.terminate.assert_not_called()
        process.stdout.close.assert_called_once()

    def test_capture_overflow_timeout_and_denied_authority_never_return_inventory(self):
        for kind in ("overflow", "timeout", "denied", "wait-timeout", "unreaped"):
            process = Mock(pid=123)
            process.poll.return_value = 1 if kind == "denied" else None
            process.wait.return_value = 1 if kind == "denied" else 0
            chunks = [b"x" * (4 * 1024 * 1024 + 1)] if kind == "overflow" else [b""]
            if kind == "wait-timeout":
                process.wait.side_effect = [btm.subprocess.TimeoutExpired("fixture", 10), 0]
            elif kind == "unreaped":
                process.wait.side_effect = btm.subprocess.TimeoutExpired("fixture", 2)
            with self.subTest(kind=kind), patch.object(btm.subprocess, "Popen", return_value=process), \
                    patch.object(btm.selectors, "DefaultSelector") as selector, \
                    patch.object(btm.os, "read", side_effect=chunks):
                if kind in {"timeout", "unreaped"}:
                    selector.return_value.__enter__.return_value.select.return_value = []
                with self.assertRaises((ValueError, TimeoutError, RuntimeError, btm.subprocess.TimeoutExpired)) as failure:
                    btm.capture_inventory()
                if kind == "unreaped":
                    self.assertIn("123 did not stop", str(failure.exception))
                if kind != "denied":
                    process.terminate.assert_called_once()
                process.stdout.close.assert_called_once()

    def test_metadata_dictionary_size_and_file_mode_guards(self):
        self.write(self.definition, ["not a dictionary"])
        with self.assertRaisesRegex(ValueError, "dictionary"):
            btm.read_plist(self.definition)
        self.definition.chmod(0o666)
        with self.assertRaisesRegex(ValueError, "trusted regular"):
            btm.read_plist(self.definition)

    def test_cancellation_forwards_termination_and_reaps_owned_supervisor(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.return_value = -15
        def cancel(_timeout):
            os.kill(os.getpid(), btm.signal.SIGTERM)
        with cancellation(), patch.object(btm.subprocess, "Popen", return_value=process), \
                patch.object(btm.selectors, "DefaultSelector") as selector:
            selector.return_value.__enter__.return_value.select.side_effect = cancel
            with self.assertRaises(KeyboardInterrupt):
                btm.capture_inventory()
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=2)

    def test_cleanup_alarm_grace_preserves_unreaped_identity_and_never_passes_expired_phase(self):
        for unreaped in (False, True):
            process = Mock(pid=123)
            def wait(*, timeout):
                self.assertEqual(timeout, 2)
                self.assertEqual(btm.signal.getitimer(btm.signal.ITIMER_REAL), (0, 0))
                if unreaped:
                    raise btm.subprocess.TimeoutExpired("fixture", 2)
                return -15
            process.wait.side_effect = wait
            with self.subTest(unreaped=unreaped), deadline(0.5), \
                    patch.object(btm.time, "monotonic", side_effect=[10, 11]):
                if unreaped:
                    with self.assertRaisesRegex(RuntimeError, "123 did not stop"):
                        btm.stop_capture(process)
                else:
                    with self.assertRaisesRegex(TimeoutError, "helper stopped"):
                        btm.stop_capture(process)


if __name__ == "__main__":
    unittest.main()
