"""Released service adapter must preserve failure and artifact provenance."""

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

from case_evidence import CaseStore, canonical
from host_runtime import HostGuard, runtime_lease
import released_engine
from released_engine import ReleasedCase, release_selection, write_junit
from test_case_evidence import identity, result


class ReleasedEngineTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name).resolve()
        self.store = CaseStore(self.root / "cases.sqlite")
        self.identity = identity()
        self.store.begin(self.identity)

    def test_junit_preserves_phase_timings_and_failures(self):
        output = self.root / "result.xml"
        failed = dict(result(), status="timeout", errors=["operation: TimeoutError"])
        write_junit(self.identity, failed, output)
        document = ET.parse(output).getroot()
        self.assertEqual(document.attrib["failures"], "1")
        self.assertEqual(document.find("testcase/failure").text, failed["errors"][0])
        properties = {entry.attrib["name"]: entry.attrib["value"] for entry in document.findall("testcase/properties/property")}
        self.assertEqual(properties["operation"], str(failed["durationsNS"]["operation"]))
        self.assertEqual(properties["runtimeSHA256"], self.identity["runtimeSHA256"])

    def test_required_release_and_supported_adapter_are_explicit(self):
        lock = json.loads((Path(__file__).parents[1] / "bazel/releases.lock.json").read_text())
        for lane in ("apple-stock", "container-compose"):
            self.assertEqual(len(release_selection(lock, lane)), 2)
        for lane in ("docker", "unknown"):
            with self.assertRaises(ValueError):
                release_selection(lock, lane)
        lock["assets"][0]["tag"] = "1.1.0"
        with self.assertRaisesRegex(ValueError, "reviewed"):
            release_selection(lock, "apple-stock")
        lock["assets"] = lock["assets"][1:]
        with self.assertRaisesRegex(ValueError, "missing"):
            release_selection(lock, "apple-stock")

    def test_admission_uses_only_existing_prepared_release_assets(self):
        lock = json.loads((Path(__file__).parents[1] / "bazel/releases.lock.json").read_text())
        with patch("released_engine.require_prepared", return_value={"verified": True}) as prepared:
            self.assertEqual(released_engine.admit(lock, "apple-stock", self.root, self.root), [{"verified": True}] * 2)
        self.assertEqual(prepared.call_count, 2)

    def test_version_probe_uses_minimal_environment_and_rejects_unexpected_output(self):
        with patch("released_engine.subprocess.run") as run:
            run.return_value.stdout, run.return_value.stderr = b"1.0.1\n", b""
            self.assertEqual(released_engine.version(Path("/released/tool")), "1.0.1")
            self.assertEqual(set(run.call_args.kwargs["env"]), {"PATH", "TMPDIR"})
            run.return_value.stderr = b"unexpected warning"
            command = Path("/released/tool")
            with self.assertRaisesRegex(ValueError, "Unexpected"):
                released_engine.version(command)

    def test_entrypoint_seals_then_resumes_without_launching_again(self):
        expected = {key: "true" for key in ("ping", "api_prefix", "head_ping", "error_envelope", "malformed_request")}
        releases = [{"executables": {"devcontainer": "/released/devcontainer"}},
                    {"executables": {"container": "/released/container", "container-apiserver": "/released/api"}}]
        guard = HostGuard(self.root / "admission.json")
        output = self.root / "case.xml"
        with patch("released_engine.SSD", self.root), patch("released_engine.RETAINED", Path.home()), \
                patch("released_engine.require_owned_volume", return_value={"ownersEnabled": True}) as volume, \
                patch("released_engine.admit", return_value=releases), patch("released_engine.version", return_value="fixture"), \
                patch("released_engine.CaseStore", return_value=self.store), patch("released_engine.HostGuard", return_value=guard), \
                patch("released_engine.runtime_lease", side_effect=lambda *_: runtime_lease(self.root / "lock", guard)), \
                patch("released_engine.ReleasedCase") as factory, patch("sys.stdout", new_callable=io.StringIO), \
                patch("sys.argv", ["released-engine", "--campaign=entrypoint", "--lane=apple-stock"]), \
                patch.dict(os.environ, XML_OUTPUT_FILE=str(output)):
            case = factory.return_value
            case.operation.return_value = expected
            case.cleanup.return_value = {"status": "passed", "remainingOwnedResources": []}
            for _ in range(2):
                with self.assertRaises(SystemExit) as exit_status:
                    released_engine.main()
                self.assertEqual(exit_status.exception.code, 0)
            case.setup.assert_called_once()
            case.operation.assert_called_once()
            case.cleanup.assert_called_once()
            revalidate = factory.call_args.args[4]
            self.assertEqual(revalidate(), releases)
            volume.return_value = {"ownersEnabled": False}
            with self.assertRaisesRegex(ValueError, "SSD ownership"):
                revalidate()
            self.assertEqual(factory.call_count, 2)
        self.assertEqual(ET.parse(output).getroot().attrib["failures"], "0")

    def test_artifacts_are_admission_bound_immutable_and_sealed(self):
        self.store.attach(self.identity, "owner.json", b"fixture")
        self.store.attach(self.identity, "owner.json", b"fixture")
        with self.assertRaisesRegex(ValueError, "overwrite"):
            self.store.attach(self.identity, "owner.json", b"different")
        with self.assertRaisesRegex(ValueError, "Invalid"):
            self.store.attach(self.identity, "../escape", b"fixture")
        unadmitted = dict(self.identity, campaign="unadmitted")
        with self.assertRaisesRegex(ValueError, "admitted"):
            self.store.attach(unadmitted, "owner.json", b"fixture")
        self.store.finish(self.identity, result())
        with self.assertRaisesRegex(ValueError, "admitted"):
            self.store.attach(self.identity, "after.log", b"fixture")

    def test_completed_result_cannot_resume_with_missing_or_corrupt_artifacts(self):
        for index, mutation in enumerate(["DELETE FROM artifacts", "UPDATE artifacts SET bytes=x'00'",
                                          "UPDATE artifacts SET name='renamed.log'"]):
            store = CaseStore(self.root / f"tamper-{index}.sqlite")
            store.begin(self.identity)
            store.attach(self.identity, "engine.log", b"original evidence")
            store.finish(self.identity, result())
            with store.connect() as database:
                database.execute(mutation)
            with self.assertRaisesRegex(ValueError, "Corrupt"):
                store.begin(self.identity)

    def case(self):
        releases = [{"executables": {"devcontainer-engine": "/released/engine"}}, {"executables": {"container": "/released/container"}}]
        return ReleasedCase(self.store, self.identity, releases, self.root, lambda: releases, HostGuard(self.root / "admission.json"))

    def test_setup_journals_owned_root_and_process_before_probe(self):
        case = self.case()
        with patch.object(case.child, "start"), patch.object(case.child, "wait_ready"), patch.object(case.child, "process") as process:
            process.pid = 42
            case.setup()
        self.assertEqual(json.loads((case.root / "owner.json").read_text())["identity"], self.identity)
        with self.store.connect() as database:
            names = [row[0] for row in database.execute("SELECT name FROM artifacts ORDER BY name")]
        self.assertEqual(names, ["owner.json", "process.json"])
        self.assertEqual(case.cleanup()["status"], "passed")
        self.assertFalse(case.root.exists())

    def test_operation_uses_direct_engine_probe_with_deadline(self):
        case = self.case()
        case.socket = self.root / "socket"
        with patch("released_engine.engine_negotiation", return_value={"ping": "true"}) as probe:
            self.assertEqual(case.operation(), {"ping": "true"})
        probe.assert_called_once_with(case.socket, observe=case.requests.append)

    def test_selected_runtime_is_started_then_restored_even_after_input_failure(self):
        case = self.case()
        runtime = Mock(service={"pid": 42})
        runtime.receipt.return_value = {"seal": "fixture", "visibility": "private-do-not-export"}
        case.runtime_factory = Mock(return_value=runtime)
        with patch.object(case.child, "start"), patch.object(case.child, "wait_ready"), patch.object(case.child, "process") as process:
            process.pid = 43
            case.setup()
        runtime.start.assert_called_once()
        case.revalidate = lambda: []
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            case.cleanup()
        runtime.verify.assert_called_once()
        runtime.restore.assert_called_once()
        self.assertTrue(case.root.exists())
        with self.assertRaisesRegex(ValueError, "quarantined"):
            case.guard.check()

    def test_failed_runtime_setup_still_restores_before_removing_owned_root(self):
        case = self.case()
        runtime = Mock(service=None)
        runtime.start.side_effect = RuntimeError("injected partial setup")
        runtime.receipt.return_value = {"status": "restored"}
        case.runtime_factory = Mock(return_value=runtime)
        with self.assertRaisesRegex(RuntimeError, "partial setup"):
            case.setup()
        self.assertEqual(case.cleanup()["status"], "passed")
        runtime.restore.assert_called_once()
        runtime.verify.assert_not_called()
        self.assertFalse(case.root.exists())
        case.guard.check()

    def test_failed_runtime_restore_keeps_quarantine_and_private_root(self):
        case = self.case()
        runtime = Mock(service=None)
        runtime.start.side_effect = RuntimeError("injected partial setup")
        runtime.restore.side_effect = RuntimeError("injected restore failure")
        runtime.receipt.return_value = {"status": "incomplete"}
        case.runtime_factory = Mock(return_value=runtime)
        with self.assertRaises(RuntimeError):
            case.setup()
        with self.assertRaisesRegex(RuntimeError, "restore failure"):
            case.cleanup()
        self.assertTrue(case.root.exists())
        with self.assertRaisesRegex(ValueError, "quarantined"):
            case.guard.check()

    def test_diagnostic_retention_failure_does_not_prevent_runtime_restoration(self):
        case = self.case()
        case.runtime = Mock(service={"pid": 42})
        case.runtime.receipt.return_value = {"status": "restored"}
        with patch.object(self.store, "attach", side_effect=OSError("retention unavailable")), self.assertRaises(OSError):
            case.cleanup()
        case.runtime.restore.assert_called_once()
        case.runtime.preserve_logs.assert_called_once()

    def test_cleanup_preserves_changed_inputs_and_unowned_root(self):
        case = self.case()
        case.root = self.root / "owned"
        case.root.mkdir()
        (case.root / "owner.json").write_bytes(canonical({"identity": {}, "root": str(case.root)}))
        with self.assertRaisesRegex(ValueError, "ownership"):
            case.cleanup()
        self.assertTrue(case.root.exists())
        case.revalidate = lambda: []
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            case.cleanup()

    def test_uncertain_spawn_preserves_global_quarantine_and_owned_root(self):
        case = self.case()
        with patch("host_runtime.subprocess.Popen", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            case.setup()
        with self.assertRaisesRegex(RuntimeError, "uncertain"):
            case.cleanup()
        self.assertTrue(case.output.closed)
        with self.store.connect() as database:
            artifacts = dict(database.execute("SELECT name,bytes FROM artifacts"))
        self.assertIn("engine.log", artifacts)
        self.assertIn("requests.json", artifacts)
        self.assertEqual(json.loads(artifacts["process-cleanup.json"]), {"verifiedStopped": False})
        with self.assertRaisesRegex(ValueError, "quarantined"):
            case.guard.check()
        self.assertTrue(case.root.is_dir())


if __name__ == "__main__":
    unittest.main()
