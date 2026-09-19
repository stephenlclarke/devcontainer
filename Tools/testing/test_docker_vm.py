"""Isolated Docker VM ownership tests; never launch a real VM in this suite."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import docker_vm
from case_evidence import canonical
from service_journal import ServiceJournal


class DockerVMTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.owner = {"root": str(self.root), "identity": {"fixture": "E01-engine-negotiation"}}
        (self.root / "owner.json").write_bytes(canonical(self.owner))
        self.journal = ServiceJournal(self.root / "journal.sqlite", self.owner, create=True)
        self.tools = {}
        for name in ("colima", "limactl", "docker", "disk-image", "guest-agent"):
            path = self.root / ("tool-" + name)
            path.write_bytes(b"test double; never executable")
            self.tools[name] = str(path)
        self.pins = {"engineVersion": "29.5.2", "engineCommit": "568f755", "engineApiVersion": "1.54",
                     "engineSHA256": "a" * 64}
        self.inventory = Mock(return_value={})
        self.vm = docker_vm.DockerVM(self.root, self.owner, self.tools, self.pins, self.journal,
                                     inventory=self.inventory)
        # Configuration tests never bind a VM socket. Bazel's deliberately long
        # sandbox path is valid for these files, not a real live-runtime root.
        self.socket_check = patch.object(docker_vm, "require_socket_paths")
        self.real_socket_check = docker_vm.require_socket_paths
        self.socket_check.start()
        self.addCleanup(self.socket_check.stop)

    def test_live_socket_length_limit_is_not_relaxed_for_sandbox_paths(self):
        self.real_socket_check(Path("/Volumes/SSD/cf/bazel/live/docker-12345678"))
        with self.assertRaisesRegex(ValueError, "socket path limit"):
            self.real_socket_check(Path("/" + "x" * 100))

    def instance(self, status="Running"):
        return {"name": "colima-parity", "dir": str(self.root / "lima/colima-parity"),
                "vmType": "vz", "arch": "aarch64", "status": status}

    def process(self, pid=321, parent=1, program=None):
        return {"pid": pid, "parent": parent, "group": pid, "started": "Thu Sep 17 22:00:00 2026",
                "program": program or self.tools["limactl"]}

    def host_arguments(self):
        directory = self.root / "lima/colima-parity"
        return " ".join([self.tools["limactl"], "hostagent", "--pidfile", str(directory / "ha.pid"),
                         "--socket", str(directory / "ha.sock"), "--guestagent", self.tools["guest-agent"],
                         "colima-parity"])

    def test_configuration_owns_all_mutable_paths_and_disables_ambient_features(self):
        with patch.dict(os.environ, {"DOCKER_HOST": "unix:///operator.sock", "SSH_AUTH_SOCK": "/operator/key"}):
            self.vm.configure()
        self.assertNotIn("DOCKER_HOST", self.vm.env)
        self.assertNotIn("SSH_AUTH_SOCK", self.vm.env)
        for key in ("HOME", "COLIMA_HOME", "LIMA_HOME", "DOCKER_CONFIG", "COLIMA_CACHE_HOME", "TMPDIR"):
            self.assertTrue(Path(self.vm.env[key]).is_relative_to(self.root))
        self.assertTrue(Path(self.vm.env["COLIMA_HOME"]).is_dir())
        args = docker_vm.start_arguments(self.tools, self.root)
        for flag in ("--activate=false", "--template=false", "--binfmt=false", "--kubernetes=false",
                     "--force-disk-image=false", "--ssh-agent=false", "--ssh-config=false", "--port-forwarder=none"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--mount") + 1], str(self.root / "workspace") + ":w")
        override = json.loads((self.root / "lima/_config/override.yaml").read_bytes())
        self.assertTrue(override["provision"][0]["skipDefaultDependencyResolution"])
        self.assertNotIn("apt", override["provision"][0]["script"])
        with self.assertRaisesRegex(ValueError, "recovery"):
            self.vm.configure()

    def test_busy_foreign_vm_and_changed_owner_prevent_configuration(self):
        for program in ("/usr/bin/docker", "/tools/limactl", "/tools/gvproxy"):
            self.inventory.return_value = {12: self.process(12, program=program)}
            with self.assertRaises(ValueError):
                self.vm.configure()
        self.assertNotIn("docker-plan.json", self.journal.records())
        (self.root / "owner.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "ownership"):
            self.vm.configure()

    def ports_owner(self):
        self.owner["identity"]["fixture"] = "D06-ports"
        (self.root / "owner.json").write_bytes(canonical(self.owner))
        self.vm.journal = ServiceJournal(self.root / "ports.sqlite", self.owner, create=True)

    def test_ports_reference_forwards_only_fixture_loopback_tcp_before_catchall_ignore(self):
        self.ports_owner()
        with patch("devcontainer_ports_reference.require_free_port") as check:
            self.vm.configure()
        check.assert_called_once_with()
        plan = json.loads(self.vm.journal.records()["docker-plan.json"])
        self.assertIn("--port-forwarder=ssh", plan["start"])
        self.assertEqual(plan["start"], docker_vm.start_arguments(self.tools, self.root, "D06-ports"))
        rules = json.loads((self.root / "lima/_config/override.yaml").read_bytes())["portForwards"]
        self.assertEqual(rules, [
            {"guestIP": "127.0.0.1", "guestPort": 49277, "hostIP": "127.0.0.1", "hostPort": 49277, "proto": "tcp"},
            {"guestIP": "0.0.0.0", "guestIPMustBeZero": False, "proto": "any", "ignore": True}])
        self.assertEqual(self.vm.journal.records()["docker-lima-override.json"],
                         (self.root / "lima/_config/override.yaml").read_bytes())

    def test_ports_foreign_listener_fails_before_vm_intent_or_configuration(self):
        self.ports_owner()
        with patch("devcontainer_ports_reference.require_free_port", side_effect=OSError("busy")), self.assertRaises(OSError):
            self.vm.configure()
        self.assertNotIn("docker-plan.json", self.vm.journal.records())
        self.assertFalse((self.root / "lima").exists())

    def test_alias_executable_is_not_admitted(self):
        alias = self.root / "alias"
        alias.symlink_to(self.tools["docker"])
        with self.assertRaisesRegex(ValueError, "canonical"):
            docker_vm.environment(self.root, dict(self.tools, docker=str(alias)))

    def test_instance_validation_rejects_foreign_duplicate_broken_and_wrong_arch(self):
        self.assertEqual(docker_vm.inspect_instance(canonical(self.instance()), self.root), self.instance())
        self.assertIsNone(docker_vm.inspect_instance(b"", self.root, allow_absent=True))
        for value in ({}, dict(self.instance(), name="operator"), dict(self.instance(), arch="x86_64"),
                      dict(self.instance(), errors=["broken"]), self.instance("Broken")):
            payload = canonical(value)
            with self.assertRaises(ValueError):
                docker_vm.inspect_instance(payload, self.root)
        duplicate = canonical(self.instance()) + b"\n" + canonical(self.instance())
        with self.assertRaises(ValueError):
            docker_vm.inspect_instance(duplicate, self.root)

    def test_engine_version_never_accepts_bridge_or_changed_oracle(self):
        value = {"Version": "29.5.2", "GitCommit": "568f755", "ApiVersion": "1.54", "Os": "linux", "Arch": "arm64"}
        with patch.object(docker_vm, "request", return_value=(200, canonical(value))):
            self.assertEqual(docker_vm.verify_engine(self.root / "socket", self.pins), value)
        for changed in (dict(value, Version="1.0.1"), dict(value, Arch="amd64"), dict(value, Os="darwin")):
            with patch.object(docker_vm, "request", return_value=(200, canonical(changed))), self.assertRaises(ValueError):
                docker_vm.verify_engine(self.root / "socket", self.pins)

    def test_real_bounded_command_journals_before_spawn_and_rejects_repeat(self):
        self.vm.configure()
        self.assertEqual(self.vm.command("docker-test", ["/usr/bin/printf", "fixture"]), b"fixture")
        records = self.journal.records()
        self.assertIn("docker-test-process.json", records)
        self.assertEqual(json.loads(records["docker-test-exit.json"])["code"], 0)
        with self.assertRaisesRegex(ValueError, "repeated"):
            self.vm.command("docker-test", ["/usr/bin/printf", "repeat"])
        self.vm.retain_logs()
        self.assertEqual(self.journal.records()["docker-test.log"], b"fixture")

    def test_failed_command_is_recorded_but_not_retried(self):
        self.vm.configure()
        with self.assertRaisesRegex(RuntimeError, "failed"):
            self.vm.command("docker-fail", ["/usr/bin/false"])
        self.assertFalse(self.vm.uncertain)
        self.assertEqual(json.loads(self.journal.records()["docker-fail-exit.json"])["code"], 1)

    def test_reference_cli_success_and_failure_logs_survive_source_log_removal(self):
        self.vm.configure()
        for name in sorted(docker_vm.DEVCONTAINER_COMMANDS):
            if name == "devcontainer-up":
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    self.vm.command(name, ["/bin/sh", "-c", "printf 'CLI failure'; exit 1"])
            else:
                self.vm.command(name, ["/usr/bin/printf", "CLI success"])
        self.vm.retain_logs()
        for name in docker_vm.DEVCONTAINER_COMMANDS:
            (self.root / (name + ".log")).unlink()
        records = self.journal.records()
        for name in docker_vm.DEVCONTAINER_COMMANDS:
            expected = b"CLI failure" if name == "devcontainer-up" else b"CLI success"
            self.assertEqual(records[name + ".log"], expected)
            self.assertFalse(json.loads(records[name + "-log.json"])["truncated"])
        self.assertFalse(docker_vm.command_record("devcontainer-delete-intent.json", "-intent.json"))

    def test_reference_stdout_never_contains_stderr_warning_and_both_are_retained(self):
        self.vm.configure()
        output = self.vm.command("devcontainer-up", ["/bin/sh", "-c", "printf 'result'; printf 'Node warning' >&2"],
                                 separate_output=True)
        self.assertEqual(output, b"result")
        self.vm.retain_logs()
        records = self.journal.records()
        self.assertEqual(records["devcontainer-up.log"], b"result")
        self.assertEqual(records["devcontainer-up.stderr.log"], b"Node warning")
        self.assertFalse(json.loads(records["devcontainer-up-stderr-log.json"])["truncated"])

    def test_interrupted_spawn_preserves_quarantine_and_never_stops_unknown_pid(self):
        self.vm.configure()
        with patch.object(docker_vm.subprocess, "Popen", side_effect=KeyboardInterrupt), \
                patch.object(docker_vm.os, "killpg") as kill, self.assertRaises(KeyboardInterrupt):
            self.vm.command("docker-spawn", [self.tools["colima"]])
        kill.assert_not_called()
        self.assertIn("docker-spawn-intent.json", self.journal.records())
        self.assertNotIn("docker-spawn-process.json", self.journal.records())
        with self.assertRaisesRegex(ValueError, "reconciliation"):
            self.vm.stop()

    def test_timeout_terminates_only_owned_group_and_leaves_uncertain_evidence(self):
        self.vm.configure()
        child = Mock(pid=321)
        child.wait.side_effect = [subprocess.TimeoutExpired("test", 1), 0]
        child.poll.return_value = None
        with patch.object(docker_vm.subprocess, "Popen", return_value=child), patch.object(docker_vm.os, "killpg") as kill, \
                self.assertRaises(subprocess.TimeoutExpired):
            self.vm.command("docker-timeout", [self.tools["colima"]], timeout=1)
        self.assertEqual(kill.call_args.args[0], 321)
        self.assertTrue(self.vm.uncertain)
        self.assertNotIn("docker-timeout-exit.json", self.journal.records())

    def test_scoped_inventory_includes_escaped_ssh_and_descendants(self):
        processes = {321: self.process(), 322: self.process(322, 321, "/usr/bin/helper"),
                     323: self.process(323, 1, "/usr/bin/ssh"), 324: self.process(324, 1, "/usr/bin/unrelated")}
        output = f"321 limactl {self.root}/lima\n323 ssh -F {self.root}/lima/ssh.config\n324 unrelated\n".encode()
        with patch.object(docker_vm.subprocess, "run", return_value=SimpleNamespace(stdout=output)) as run:
            self.assertEqual(set(docker_vm.scoped_processes(self.root, self.tools, processes)), {"321", "322", "323"})
        self.assertEqual(run.call_args.args[0], ["/bin/ps", "-ww", "-axo", "pid=,args="])

    def test_pid_files_must_name_proven_processes_not_arbitrary_live_pids(self):
        directory = self.root / "lima/colima-parity"
        directory.mkdir(parents=True)
        pidfile = directory / "ha.pid"
        pidfile.write_text("321\n")
        self.assertEqual(docker_vm.verify_pid_files(self.root, self.tools, {321: self.process()},
                                                   arguments=lambda _: self.host_arguments()),
                         {"lima/colima-parity/ha.pid": self.process()})
        with self.assertRaisesRegex(ValueError, "proven"):
            docker_vm.verify_pid_files(self.root, self.tools, {})
        for arguments in (self.host_arguments().replace(str(self.root), "/another-root"),
                          self.tools["limactl"] + " unrelated " + str(self.root) + "/lima"):
            processes = {321: self.process()}
            with self.assertRaisesRegex(ValueError, "exact owned"):
                docker_vm.verify_pid_files(self.root, self.tools, processes, arguments=lambda _: arguments)
        pidfile.chmod(0o666)
        processes = {321: self.process()}
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            docker_vm.verify_pid_files(self.root, self.tools, processes)

    def test_unknown_pid_file_is_never_signalling_authority(self):
        directory = self.root / "lima/foreign"
        directory.mkdir(parents=True)
        (directory / "ha.pid").write_text("321")
        processes = {321: self.process()}
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            docker_vm.verify_pid_files(self.root, self.tools, processes)

    def test_failed_start_without_capture_never_signals(self):
        self.vm.configure()
        self.journal.put("docker-vm-start-intent.json", b"{}")
        self.journal.put("docker-vm-start-exit.json", b'{"code":1}')
        with patch.object(self.vm, "command") as command, self.assertRaisesRegex(ValueError, "ownership capture"):
            self.vm.stop()
        command.assert_not_called()

    def test_shutdown_requires_stopped_instance_dead_socket_and_no_helpers(self):
        self.vm.configure()
        self.journal.put("docker-vm-start-intent.json", b"{}")
        self.journal.put("docker-vm-start-exit.json", b"{}")
        self.journal.put("docker-vm-pids.json", b"{}")
        with patch.object(self.vm, "command", side_effect=[b"", canonical(self.instance("Stopped"))]), \
                patch.object(docker_vm, "scoped_processes", return_value={}), \
                patch.object(docker_vm, "require_unreachable_socket"), patch.object(self.vm, "retain_logs"):
            self.vm.stop()
        self.assertEqual(json.loads(self.journal.records()["docker-vm-closed.json"]), {"verifiedStopped": True})

    def test_shutdown_rejects_replaced_pid_before_running_stop(self):
        self.vm.configure()
        self.journal.put("docker-vm-start-intent.json", b"{}")
        self.journal.put("docker-vm-start-exit.json", b"{}")
        self.journal.put("docker-vm-pids.json", canonical({"lima/ha.pid": self.process()}))
        with patch.object(docker_vm, "scoped_processes", return_value={}), \
                patch.object(docker_vm, "verify_pid_files", return_value={"lima/ha.pid": dict(self.process(), started="later")}), \
                patch.object(self.vm, "command") as command, self.assertRaisesRegex(ValueError, "incarnation"):
            self.vm.stop()
        command.assert_not_called()

    def test_process_identity_survives_exec_but_not_pid_reuse(self):
        old = self.process()
        self.assertTrue(docker_vm.same_incarnation(old, dict(old, program="/bin/other", group=100, parent=1)))
        self.assertFalse(docker_vm.same_incarnation(old, dict(old, started="later")))

    def test_start_requires_all_roles_exact_engine_and_executable_digest(self):
        pids = {name: self.process() for name in docker_vm.pid_roles(self.root, self.tools)}
        version = {"Version": "29.5.2"}
        with patch.object(self.vm, "command", side_effect=[b"", canonical(self.instance()),
                         (self.pins["engineSHA256"] + "  /usr/bin/dockerd\n").encode()]) as command, \
                patch.object(docker_vm, "scoped_processes", return_value={"321": self.process()}), \
                patch.object(docker_vm, "verify_pid_files", return_value=pids), \
                patch.object(docker_vm, "verify_engine", return_value=version):
            self.vm.start()
            self.vm.verify()
        self.assertEqual(json.loads(self.journal.records()["docker-vm-pids.json"]), pids)
        self.assertEqual(command.call_args.args[1][1:5], ["--profile", "parity", "ssh", "--"])

    def test_engine_verify_requires_initial_identity_and_rejects_changes(self):
        with self.assertRaisesRegex(ValueError, "completed identity"):
            self.vm.verify()
        self.journal.put("docker-engine-version.json", b"{}")
        with patch.object(docker_vm, "verify_engine", return_value={"Version": "different"}), \
                self.assertRaisesRegex(ValueError, "identity changed"):
            self.vm.verify()

    def test_pid_roles_require_vz_and_hostagent_same_incarnation(self):
        directory = self.root / "lima/colima-parity"
        directory.mkdir(parents=True)
        (directory / "ha.pid").write_text("321")
        (directory / "vz.pid").write_text("322")
        processes = {321: self.process(), 322: self.process(322)}
        with self.assertRaisesRegex(ValueError, "authenticated hostagent"):
            docker_vm.verify_pid_files(self.root, self.tools, processes,
                                       arguments=lambda _: self.host_arguments())

    def test_shutdown_accepts_reused_pid_but_rejects_surviving_incarnation(self):
        self.vm.configure()
        self.journal.put("docker-vm-start-intent.json", b"{}")
        self.journal.put("docker-vm-start-exit.json", b"{}")
        self.journal.put("docker-vm-pids.json", b"{}")
        self.journal.put("docker-vm-processes.json", canonical({"321": self.process()}))
        survivor = dict(self.process(), program="/bin/changed", group=1)
        self.inventory.side_effect = [{}, {}, {321: survivor}]
        with patch.object(self.vm, "command", side_effect=[b"", canonical(self.instance("Stopped"))]), \
                patch.object(docker_vm, "scoped_processes", return_value={}), \
                self.assertRaisesRegex(ValueError, "processes remain"):
            self.vm.stop()
        self.inventory.side_effect = [{}, {}, {321: dict(survivor, started="later")}]
        with patch.object(self.vm, "command", side_effect=[b"", canonical(self.instance("Stopped"))]), \
                patch.object(docker_vm, "scoped_processes", return_value={}), \
                patch.object(docker_vm, "require_unreachable_socket"), patch.object(self.vm, "retain_logs"):
            self.vm.stop()
        self.assertIn("docker-vm-closed.json", self.journal.records())

    def test_shutdown_rejects_new_pid_mapping_before_running_stop(self):
        self.vm.configure()
        self.journal.put("docker-vm-start-intent.json", b"{}")
        self.journal.put("docker-vm-start-exit.json", b"{}")
        self.journal.put("docker-vm-pids.json", b"{}")
        with patch.object(docker_vm, "scoped_processes", return_value={}), \
                patch.object(docker_vm, "verify_pid_files", return_value={"lima/ha.pid": self.process()}), \
                patch.object(self.vm, "command") as command, self.assertRaisesRegex(ValueError, "incarnation"):
            self.vm.stop()
        command.assert_not_called()

    def closed_records(self):
        self.vm.configure()
        self.journal.put("docker-vm-closed.json", canonical({"verifiedStopped": True}))
        return self.journal.records()

    def closed_check(self, records):
        with patch.object(docker_vm, "scoped_processes", return_value={}), \
                patch.object(docker_vm, "require_unreachable_socket"):
            docker_vm.require_closed_vm(self.root, self.owner, records, inventory=self.inventory)

    def test_closed_recovery_requires_authentic_plan_and_shutdown_receipt(self):
        records = self.closed_records()
        self.closed_check(records)
        for key, payload in (("docker-vm-closed.json", b"{}"), ("docker-plan.json", b"{}")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.closed_check({**records, key: payload})
        plan = json.loads(records["docker-plan.json"])
        for field in ("environment", "start"):
            invalid_records = {**records, "docker-plan.json": canonical({**plan, field: None})}
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "admitted runtime"):
                self.closed_check(invalid_records)

    def test_closed_recovery_rejects_live_socket_and_uncertain_socket_error(self):
        records = self.closed_records()
        with patch.object(docker_vm, "scoped_processes", return_value={}), \
                patch.object(docker_vm, "require_unreachable_socket", side_effect=ValueError("reachable")), \
                self.assertRaisesRegex(ValueError, "reachable"):
            docker_vm.require_closed_vm(self.root, self.owner, records, inventory=self.inventory)
        with patch.object(docker_vm, "scoped_processes", return_value={}), \
                patch.object(docker_vm, "require_unreachable_socket", side_effect=TimeoutError), self.assertRaises(TimeoutError):
            docker_vm.require_closed_vm(self.root, self.owner, records, inventory=self.inventory)

    def test_shutdown_socket_probe_never_waits_for_nonresponding_http_peer(self):
        connection = Mock()
        with patch.object(docker_vm.socket, "socket") as factory, patch.object(docker_vm, "request") as http:
            factory.return_value.__enter__.return_value = connection
            with self.assertRaisesRegex(ValueError, "reachable"):
                docker_vm.require_unreachable_socket(self.root / "docker.sock")
            connection.settimeout.assert_called_once_with(1)
            connection.connect.assert_called_once_with(str(self.root / "docker.sock"))
            connection.recv.assert_not_called()
            connection.sendall.assert_not_called()
            http.assert_not_called()
            for error in (FileNotFoundError, ConnectionRefusedError):
                connection.connect.side_effect = error
                docker_vm.require_unreachable_socket(self.root / "docker.sock")
            for error in (TimeoutError, PermissionError, ConnectionResetError):
                connection.connect.side_effect = error
                with self.subTest(error=error), self.assertRaises(error):
                    docker_vm.require_unreachable_socket(self.root / "docker.sock")

    def test_closed_recovery_rejects_captured_survivor_even_after_exec(self):
        records = self.closed_records()
        for name in ("docker-vm-processes.json", "docker-vm-stop-processes.json", "docker-vm-pids.json"):
            evidence = {**records, name: canonical({"321": self.process()})}
            self.inventory.return_value = {321: dict(self.process(), program="/bin/changed", group=1)}
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "survived shutdown"):
                self.closed_check(evidence)
            self.inventory.return_value = {321: dict(self.process(), program="/bin/changed", started="later")}
            self.closed_check(evidence)

    def test_closed_recovery_rejects_uncertain_commands_and_legacy_pid_reuse(self):
        records = {**self.closed_records(), "docker-command-intent.json": b"{}"}
        with self.assertRaisesRegex(ValueError, "exit receipt"):
            self.closed_check(records)
        records["docker-command-exit.json"] = b'{"code":0}'
        with self.assertRaisesRegex(ValueError, "process record"):
            self.closed_check(records)
        records["docker-command-process.json"] = canonical({"pid": 321})
        self.closed_check(records)
        for process in (self.process(program="/bin/changed"), dict(self.process(322, program="/bin/changed"), group=321)):
            self.inventory.return_value = {process["pid"]: process}
            with self.subTest(process=process), self.assertRaisesRegex(ValueError, "process group"):
                self.closed_check(records)

    def test_closed_recovery_rejects_new_scoped_process_and_busy_worker(self):
        records = self.closed_records()
        with patch.object(docker_vm, "scoped_processes", return_value={"321": self.process()}), \
                self.assertRaisesRegex(ValueError, "processes remain"):
            docker_vm.require_closed_vm(self.root, self.owner, records, inventory=self.inventory)
        self.inventory.return_value = {321: self.process(program="/runner/Runner.Worker")}
        with self.assertRaisesRegex(ValueError, "Active worker"):
            self.closed_check(records)

    def test_closed_cli_requires_exit_process_and_retained_diagnostics(self):
        records = {**self.closed_records(), "devcontainer-up-intent.json": b"{}"}
        with self.assertRaisesRegex(ValueError, "exit receipt"):
            self.closed_check(records)
        records["devcontainer-up-exit.json"] = b'{"code":1}'
        with self.assertRaisesRegex(ValueError, "diagnostics"):
            self.closed_check(records)
        records.update({"devcontainer-up.log": b"failure", "devcontainer-up-log.json": b"{}"})
        with self.assertRaisesRegex(ValueError, "process record"):
            self.closed_check(records)
        records["devcontainer-up-process.json"] = canonical({"pid": 321})
        self.closed_check(records)
        self.inventory.return_value = {321: self.process(program="/prepared/node")}
        with self.assertRaisesRegex(ValueError, "process group"):
            self.closed_check(records)


if __name__ == "__main__":
    unittest.main()
