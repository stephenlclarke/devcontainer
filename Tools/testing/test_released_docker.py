"""Downloaded Docker orchestration tests; no daemon, downloads or VM startup."""

import json
import os
from pathlib import Path
import tempfile
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import released_docker
import released_engine
from case_evidence import canonical


class DockerCaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.store, self.guard, self.revalidate = Mock(), Mock(), Mock()
        self.inputs = {"tools": {"docker": "/prepared/docker"}, "pins": {},
                       "workload": {"path": "/retained/image.tar", "image": {"config": "sha256:" + "a" * 64,
                                    "manifest": "sha256:" + "b" * 64}}}
        self.revalidate.return_value = self.inputs
        self.identity = {"fixture": "E02-container-lifecycle"}
        self.case = released_docker.DockerCase(self.store, self.identity, self.inputs, self.parent,
                                              self.revalidate, self.guard, self.parent)
        self.vm = Mock(socket=self.parent / "docker.sock")
        self.vm.journal.receipt.return_value = {"verified": "test-double"}
        self.guest = Mock()

    def setup_case(self):
        with patch.object(released_docker, "DockerVM", return_value=self.vm), \
                patch.object(released_docker, "ReleasedGuest", return_value=self.guest) as guest, \
                patch.object(released_docker, "request", return_value=(200, canonical({"Id": "sha256:" + "b" * 64,
                    "Descriptor": {"digest": "sha256:" + "b" * 64}, "Os": "linux", "Architecture": "arm64"}))):
            self.case.setup()
        return guest

    def test_setup_loads_exact_retained_workload_and_uses_shared_fixture(self):
        guest = self.setup_case()
        self.vm.start.assert_called_once_with()
        self.guard.begin.assert_called_once_with(self.case.owner)
        arguments = self.vm.command.call_args.args[1]
        self.assertEqual(arguments[-4:], ["image", "load", "--input", "/retained/image.tar"])
        self.assertEqual(guest.call_args.args[2], self.case.root / "workspace")
        self.assertEqual(guest.call_args.kwargs["image_id"], "sha256:" + "b" * 64)
        self.assertEqual(self.case.operation(), self.guest.operation.return_value)
        self.vm.verify.assert_called_once_with()

    def test_cleanup_stops_guest_then_vm_retains_receipt_and_clears_guard(self):
        self.setup_case()
        events = []
        self.guest.cleanup.side_effect = lambda: events.append("guest")
        self.vm.stop.side_effect = lambda: events.append("vm")
        self.guard.clear.side_effect = lambda _: events.append("clear")
        self.assertEqual(self.case.cleanup(), {"status": "passed", "remainingOwnedResources": []})
        self.assertEqual(events, ["guest", "vm", "clear"])
        self.assertFalse(self.case.root.exists())
        self.assertIn("docker-journal.json", [call.args[1] for call in self.store.attach.call_args_list])

    def test_uncertain_guest_cleanup_preserves_running_vm_and_quarantine(self):
        self.setup_case()
        self.guest.cleanup.side_effect = ValueError("pending deletion")
        with self.assertRaisesRegex(ValueError, "pending deletion"):
            self.case.cleanup()
        self.vm.stop.assert_not_called()
        self.guard.clear.assert_not_called()
        self.assertTrue(self.case.root.is_dir())

    def test_uncertain_vm_cleanup_keeps_root_but_retains_diagnostics(self):
        self.setup_case()
        self.vm.stop.side_effect = ValueError("pending shutdown")
        with self.assertRaisesRegex(ValueError, "pending shutdown"):
            self.case.cleanup()
        self.guard.clear.assert_not_called()
        self.assertTrue(self.case.root.is_dir())
        self.assertIn("docker-journal.json", [call.args[1] for call in self.store.attach.call_args_list])

    def test_engine_only_case_does_not_load_workload(self):
        self.identity["fixture"] = "E01-engine-negotiation"
        self.setup_case()
        self.vm.command.assert_not_called()
        self.assertIsNone(self.case.guest)
        with patch.object(released_docker, "engine_negotiation", return_value={"verified": "true"}):
            self.assertEqual(self.case.operation(), {"verified": "true"})

    def test_setup_rejects_different_loaded_image(self):
        with patch.object(released_docker, "DockerVM", return_value=self.vm), \
                patch.object(released_docker, "request", return_value=(200, b'{"Id":"wrong"}')), \
                self.assertRaisesRegex(ValueError, "exact admitted"):
            self.case.setup()
        self.assertIsNone(self.case.guest)

    def test_matching_image_id_does_not_hide_wrong_manifest_or_architecture(self):
        identifier = "sha256:" + "b" * 64
        for descriptor, architecture in (("sha256:" + "c" * 64, "arm64"), (identifier, "amd64")):
            value = {"Id": identifier, "Descriptor": {"digest": descriptor}, "Os": "linux", "Architecture": architecture}
            with patch.object(released_docker, "DockerVM", return_value=self.vm), \
                    patch.object(released_docker, "request", return_value=(200, canonical(value))), \
                    self.assertRaisesRegex(ValueError, "exact admitted"):
                self.case.setup()
            self.assertIsNone(self.case.guest)
            self.assertEqual(json.loads(self.vm.journal.put.call_args.args[1])["inspection"], value)

    def test_changed_inputs_are_not_accepted_as_cleanup_success(self):
        self.setup_case()
        self.revalidate.return_value = {}
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            self.case.cleanup()
        self.guard.clear.assert_not_called()
        self.assertTrue(self.case.root.is_dir())


class DockerAdmissionTests(unittest.TestCase):
    def test_admission_uses_only_retained_pinned_releases(self):
        repository = Path(__file__).parents[2]
        lock = json.loads((repository / "Tools/bazel/docker-oracle.lock.json").read_text())
        assets = [dict(executables={"colima": "/colima"}), dict(executables={"limactl": "/limactl"},
                  files={"guest-agent": "/agent"}), dict(executables={}, files={"disk-image": "/image"})]
        with patch.object(released_docker, "require_retained", side_effect=assets) as retained, \
                patch.object(released_docker, "prepare_cli", return_value={"executables": {"docker": "/docker"}}) as cli, \
                patch.object(released_docker, "require_image", return_value={"verified": True}):
            inputs = released_docker.admit_docker(lock, {}, {}, {"images": [{"name": "alpine-workload"}]},
                                                  Path("/scratch"), Path("/retained"))
        self.assertEqual(retained.call_count, 3)
        self.assertEqual(set(inputs["tools"]), {"colima", "limactl", "guest-agent", "disk-image", "docker"})
        self.assertEqual(cli.call_args.kwargs, {"offline": True})

    def test_unreviewed_release_set_is_rejected_before_preparation(self):
        with patch.object(released_docker, "validate_lock", return_value=[]), \
                patch.object(released_docker, "prepare_cli") as cli, self.assertRaisesRegex(ValueError, "reviewed"):
            released_docker.admit_docker({}, {}, {}, {}, Path("/scratch"), Path("/retained"))
        cli.assert_not_called()


class DockerEntryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.scratch, self.retained = root / "scratch", root / "retained"
        self.scratch.mkdir()
        self.retained.mkdir()
        self.args = SimpleNamespace(candidate_invocation=None, campaign="docker-component", fixture="E01-engine-negotiation")
        self.real_stat = Path.stat

    def separate_volumes(self, path, **kwargs):
        info = self.real_stat(path, **kwargs)
        if path == self.retained:
            fields = list(info)
            fields[2] += 1
            return os.stat_result(fields)
        return info

    def invoke(self):
        with patch.object(released_engine, "SSD", self.scratch), patch.object(released_engine, "RETAINED", self.retained), \
                patch.object(released_docker, "require_owned_volume", return_value={"uuid": "fixture"}), \
                patch.object(released_docker, "runtime_lease", return_value=nullcontext()), \
                patch.object(released_docker, "cancellation", return_value=nullcontext()):
            return released_docker.run_docker(self.args)

    def test_rejects_candidate_and_same_volume_before_admission(self):
        self.args.candidate_invocation = "not-a-reference"
        with self.assertRaisesRegex(ValueError, "released binaries"):
            self.invoke()
        self.args.candidate_invocation = None
        with self.assertRaisesRegex(ValueError, "separate canonical"):
            self.invoke()

    def test_rejects_redirected_scratch_before_case_creation(self):
        (self.scratch / "live").symlink_to(self.retained, target_is_directory=True)
        with patch.object(Path, "stat", lambda path, **kw: self.separate_volumes(path, **kw)), \
                self.assertRaisesRegex(ValueError, "Symlinked"):
            self.invoke()
        self.assertFalse(list(self.retained.glob("docker-*")))

    def test_entry_binds_identity_and_emits_case_evidence(self):
        result = {"status": "passed", "durationsNS": {"setup": 1, "operation": 2, "cleanup": 3}, "errors": []}
        output = self.scratch / "junit.xml"
        with patch.object(Path, "stat", lambda path, **kw: self.separate_volumes(path, **kw)), \
                patch.object(released_docker, "admit_docker", return_value={"verified": True}) as admit, \
                patch.object(released_docker, "DockerCase") as case, \
                patch.object(released_docker, "run_case", return_value=result), \
                patch.object(released_docker, "print") as printed, \
                patch.dict(os.environ, {"XML_OUTPUT_FILE": str(output)}), self.assertRaises(SystemExit) as exited:
            self.invoke()
        self.assertEqual(exited.exception.code, 0)
        self.assertEqual(admit.call_count, 1)
        identity = case.call_args.args[1]
        self.assertEqual(identity["lane"], "docker")
        self.assertEqual(identity["campaign"], self.args.campaign)
        self.assertTrue(output.is_file())
        self.assertEqual(json.loads(printed.call_args.args[0])["scope"], "released-docker-case-only")


if __name__ == "__main__":
    unittest.main()
