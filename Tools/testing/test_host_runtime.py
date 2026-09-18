"""Exercise host isolation using private leases and owned disposable children."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from host_runtime import HostGuard, OwnedProcess, cancellation, deadline, require_api_service, runtime_lease


class HostRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name).resolve()

    def test_live_child_incarnation_is_captured_before_handle_is_lost(self):
        process = OwnedProcess()
        with (self.root / "child.log").open("xb") as output:
            process.start(["/bin/sleep", "30"], self.root, output)
            try:
                identity = process.identity()
                self.assertEqual(identity["pid"], process.process.pid)
                self.assertEqual(identity["group"], process.process.pid)
                self.assertEqual(identity["parent"], os.getpid())
                self.assertEqual(identity["program"], "/bin/sleep")
                self.assertEqual(identity["arguments"], ["/bin/sleep", "30"])
                self.assertTrue(identity["started"])
            finally:
                process.stop()
        with self.assertRaisesRegex(ValueError, "absent or exited"):
            process.identity()

    def test_capture_refuses_absent_replaced_or_exiting_children(self):
        process = OwnedProcess()
        with self.assertRaisesRegex(ValueError, "absent or exited"):
            process.identity()
        process.process = Mock(pid=42, args=["/owned/engine"])
        process.process.poll.return_value = None
        identity = {"pid": 42, "parent": os.getpid(), "group": 42,
                    "started": "original", "program": "/owned/engine"}
        for mutation in (None, {"parent": 1}, {"group": 1}, {"program": "/replacement"}):
            inventory = {} if mutation is None else {42: {**identity, **mutation}}
            with patch("runtime_services.process_inventory", return_value=inventory), \
                    self.assertRaisesRegex(ValueError, "incarnation"):
                process.identity()
        process.process.poll.side_effect = [None, 0]
        with patch("runtime_services.process_inventory", return_value={42: identity}), \
                self.assertRaisesRegex(ValueError, "incarnation"):
            process.identity()

    def test_api_service_admission_requires_exact_running_release_and_redacts_environment(self):
        executable = self.root / "container-apiserver"
        output = f"job = {{\n\tprogram = {executable}\n\tstate = running\n\tpid = 42\n\tenvironment = {{\n\t\tSECRET = hidden\n\t}}\n}}\n"
        with patch("host_runtime.subprocess.run") as run:
            run.side_effect = [SimpleNamespace(returncode=0, stdout=output.encode()),
                               SimpleNamespace(returncode=0, stdout=(str(executable) + "\n").encode())]
            actual = require_api_service(executable)
            self.assertEqual(actual, {"service": f"gui/{os.getuid()}/com.apple.container.apiserver",
                                      "program": str(executable), "pid": 42})
            self.assertEqual(run.call_args_list[0].args[0][0:2], ["/bin/launchctl", "print"])
            self.assertEqual(run.call_args.args[0], ["/bin/ps", "-p", "42", "-o", "comm="])
            self.assertEqual(run.call_args.kwargs["timeout"], 5)
            self.assertEqual(run.call_args.kwargs["env"], {"PATH": "/usr/bin:/bin"})

    def test_registered_running_job_does_not_admit_xpcproxy_wrong_binary_or_exited_process(self):
        executable = self.root / "container-apiserver"
        output = f"\tprogram = {executable}\n\tstate = running\n\tpid = 42\n".encode()
        for status, image in ((0, b"/usr/libexec/xpcproxy\n"), (0, b"/other/container-apiserver\n"), (1, b"")):
            with self.subTest(status=status, image=image), patch("host_runtime.subprocess.run") as run:
                run.side_effect = [SimpleNamespace(returncode=0, stdout=output),
                                   SimpleNamespace(returncode=status, stdout=image)]
                with self.assertRaisesRegex(ValueError, "executable has not started"):
                    require_api_service(executable)

    def test_api_service_admission_rejects_mixed_stopped_missing_and_ambiguous_services(self):
        executable = self.root / "container-apiserver"
        valid = f"\tprogram = {executable}\n\tstate = running\n\tpid = 42\n"
        invalid = [valid.replace(str(executable), "/other/provider"), valid.replace("running", "waiting"),
                   valid.replace("42", "0"), valid.replace("42", "invalid"),
                   valid.replace("\tpid = 42\n", ""), valid + "\tpid = 43\n"]
        with patch("host_runtime.subprocess.run") as run:
            run.return_value.returncode = 0
            for output in invalid:
                run.return_value.stdout = output.encode()
                with self.assertRaises(ValueError):
                    require_api_service(executable)
            run.return_value.returncode = 113
            with self.assertRaisesRegex(ValueError, "not registered"):
                require_api_service(executable)
        relative = Path("relative")
        with self.assertRaisesRegex(ValueError, "canonical"):
            require_api_service(relative)

    def test_lease_is_exclusive_and_released_without_unlinking(self):
        lock = self.root / "runtime.lock"
        with runtime_lease(lock):
            inode = lock.stat().st_ino
            with self.assertRaises(BlockingIOError), runtime_lease(lock):
                self.fail("a second owner entered")
        with runtime_lease(lock):
            self.assertEqual(lock.stat().st_ino, inode)

    def test_quarantine_survives_lock_release_and_blocks_other_campaigns(self):
        lock = self.root / "runtime.lock"
        guard = HostGuard(self.root / "admission.json")
        owner = {"campaign": "first", "root": str(self.root)}
        with runtime_lease(lock, guard):
            guard.begin(owner)
        with self.assertRaisesRegex(ValueError, "quarantined"), runtime_lease(lock, guard):
            self.fail("another campaign entered an unreconciled runtime")
        with self.assertRaisesRegex(ValueError, "ownership changed"):
            guard.clear({"campaign": "other"})
        guard.clear(owner)
        with runtime_lease(lock, guard):
            guard.check()

    def test_lease_rejects_aliases_hardlinks_and_writable_files(self):
        original = self.root / "original"
        original.touch(mode=0o600)
        alias = self.root / "alias"
        alias.symlink_to(original)
        with self.assertRaises(OSError), runtime_lease(alias):
            self.fail("symlink lease entered")
        hardlink = self.root / "hardlink"
        os.link(original, hardlink)
        with self.assertRaises(ValueError), runtime_lease(original):
            self.fail("hardlinked lease entered")
        hardlink.unlink()
        original.chmod(0o666)
        with self.assertRaises(ValueError), runtime_lease(original):
            self.fail("writable lease entered")
        relative = Path("relative.lock")
        with self.assertRaises(ValueError), runtime_lease(relative):
            self.fail("relative lease entered")

    def test_whole_phase_deadline_interrupts_wait_and_restores_handler(self):
        previous = signal.getsignal(signal.SIGALRM)
        with self.assertRaises(TimeoutError), deadline(0.01):
            time.sleep(1)
        self.assertEqual(signal.getsignal(signal.SIGALRM), previous)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        with self.assertRaises(ValueError), deadline(0):
            self.fail("invalid deadline entered")
        with deadline(1):
            with self.assertRaises(ValueError), deadline(1):
                self.fail("nested alarm replaced outer deadline")

    def test_sigterm_uses_cleanup_path_and_restores_handler(self):
        previous = signal.getsignal(signal.SIGTERM)
        process_id = os.getpid()
        with self.assertRaises(KeyboardInterrupt), cancellation():
            os.kill(process_id, signal.SIGTERM)
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def child(self, code):
        process = OwnedProcess()
        output = (self.root / "child.log").open("wb")
        self.addCleanup(output.close)
        self.addCleanup(process.stop)
        process.start([sys.executable, "-c", code], self.root, output)
        return process

    def test_owned_child_ready_then_stopped_without_inheriting_secrets(self):
        with patch.dict(os.environ, TEST_SECRET="not-for-child"):
            process = self.child("import os,time; assert 'TEST_SECRET' not in os.environ; print('ready',flush=True); time.sleep(60)")
        process.wait_ready(lambda: (self.root / "child.log").read_text().strip() == "ready", seconds=3)
        with self.assertRaisesRegex(ValueError, "already owns"):
            process.start([], self.root, None)
        process.stop()
        self.assertIsNotNone(process.process.returncode)

    def test_early_exit_and_readiness_timeout_do_not_pass(self):
        process = self.child("raise SystemExit(7)")
        process.process.wait(timeout=3)
        with self.assertRaisesRegex(RuntimeError, "before readiness"):
            process.wait_ready(lambda: True)
        other = OwnedProcess()
        with self.assertRaises(ValueError):
            other.wait_ready(lambda: True)
        other.stop()

    def test_unready_process_is_bounded_and_cleaned(self):
        process = self.child("import time; time.sleep(60)")

        def unavailable():
            raise FileNotFoundError()

        with self.assertRaises(TimeoutError):
            process.wait_ready(unavailable, seconds=0.03)
        process.stop()

    def test_stubborn_owned_child_is_killed_after_grace_period(self):
        process = self.child("import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(60)")
        process.wait_ready(lambda: (self.root / "child.log").read_text().strip() == "ready", seconds=3)
        process.stop(seconds=0.03)
        self.assertEqual(process.process.returncode, -signal.SIGKILL)

    def test_exited_during_signal_is_reaped(self):
        process = OwnedProcess()
        with patch("host_runtime.subprocess.Popen") as spawn:
            process.start(["fixture"], self.root, None)
            spawn.return_value.poll.return_value = None
            with patch("host_runtime.os.killpg", side_effect=ProcessLookupError):
                process.stop()
            spawn.return_value.wait.assert_called_once()

    def test_selected_provider_paths_do_not_inherit_operator_configuration(self):
        process = OwnedProcess()
        with patch.dict(os.environ, CONTAINER_APP_ROOT="/operator", CONTAINER_LOG_ROOT="/operator/logs"), \
                patch("host_runtime.subprocess.Popen") as spawn:
            process.start(["fixture"], self.root, None, provider_install=self.root)
        environment = spawn.call_args.kwargs["env"]
        self.assertEqual(environment["CONTAINER_APP_ROOT"], str(self.root / "container"))
        self.assertEqual(environment["CONTAINER_INSTALL_ROOT"], str(self.root))
        self.assertEqual(environment["CONTAINER_LOG_ROOT"], str(self.root / "container-logs"))
        invalid = OwnedProcess()
        with patch("host_runtime.subprocess.Popen") as spawn, self.assertRaisesRegex(ValueError, "canonical"):
            invalid.start(["fixture"], self.root, None, provider_install=Path("relative"))
        spawn.assert_not_called()
        self.assertFalse(invalid.spawn_pending)

    def test_interrupted_spawn_is_not_mistaken_for_no_child(self):
        process = OwnedProcess()
        with patch("host_runtime.subprocess.Popen", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            process.start(["fixture"], self.root, None)
        with self.assertRaisesRegex(RuntimeError, "ownership is uncertain"):
            process.stop()

    def test_surviving_descendant_is_not_reported_clean(self):
        process = OwnedProcess()
        with patch("host_runtime.subprocess.Popen") as spawn:
            process.start(["fixture"], self.root, None)
            spawn.return_value.poll.return_value = 0
            with patch("host_runtime.os.killpg"), self.assertRaisesRegex(RuntimeError, "descendants"):
                process.stop()


if __name__ == "__main__":
    unittest.main()
