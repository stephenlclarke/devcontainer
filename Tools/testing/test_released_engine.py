"""Released service adapter must preserve failure and artifact provenance."""

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

from case_evidence import CaseStore, canonical, compare_cases
from host_runtime import HostGuard, runtime_lease
from service_journal import ServiceJournal
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
        keychains = patch("released_engine.run_keychain", return_value={"status": "fixture-only"})
        self.keychains = keychains.start()
        self.addCleanup(keychains.stop)
        incarnation = patch("released_engine.OwnedProcess.identity", return_value={"captured": "fixture"})
        incarnation.start()
        self.addCleanup(incarnation.stop)

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

    def test_d02_without_private_candidate_refuses_before_runtime_or_asset_mutation(self):
        with patch("sys.argv", ["case", "--campaign=test", "--lane=apple-stock", "--fixture=D02-dockerfile-config"]), \
                patch("released_engine.platform.system", return_value="Darwin"), \
                patch("released_engine.platform.machine", return_value="arm64"), \
                patch("released_engine.admit") as admit, \
                self.assertRaisesRegex(ValueError, "private-runtime candidate"):
            released_engine.main()
        admit.assert_not_called()
        self.keychains.assert_not_called()

    def test_d03_without_private_candidate_refuses_before_runtime_or_asset_mutation(self):
        with patch("sys.argv", ["case", "--campaign=test", "--lane=apple-stock", "--fixture=D03-users-environment"]), \
                patch("released_engine.platform.system", return_value="Darwin"), \
                patch("released_engine.platform.machine", return_value="arm64"), \
                patch("released_engine.admit") as admit, \
                self.assertRaisesRegex(ValueError, "private-runtime candidate"):
            released_engine.main()
        admit.assert_not_called()
        self.keychains.assert_not_called()

    def test_d04_without_private_candidate_refuses_before_runtime_or_asset_mutation(self):
        with patch("sys.argv", ["case", "--campaign=test", "--lane=apple-stock", "--fixture=D04-lifecycle-hooks"]), \
                patch("released_engine.platform.system", return_value="Darwin"), \
                patch("released_engine.platform.machine", return_value="arm64"), \
                patch("released_engine.admit") as admit, \
                self.assertRaisesRegex(ValueError, "private-runtime candidate"):
            released_engine.main()
        admit.assert_not_called()
        self.keychains.assert_not_called()

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
        with patch("released_engine.require_retained", return_value={"verified": True}) as prepared:
            self.assertEqual(released_engine.admit(lock, "apple-stock", self.root), [{"verified": True}] * 2)
        self.assertEqual(prepared.call_count, 2)
        self.assertTrue(all(call.args[2] == self.root / "prepared-releases" for call in prepared.call_args_list))

    def test_d01_requires_a_complete_admitted_private_bundle(self):
        repository = Path(__file__).parents[2]
        required = ("devcontainer", "devcontainer-docker", "devcontainer-compose", "devcontainer-engine", "reference-node")
        candidate = {"scope": "local-candidate-integration-only", "executables": {name: "/prepared/" + name for name in required}}
        selected = released_engine.fixture_guest_inputs({"workload": "pin"}, "D01-image-config", candidate, repository)
        self.assertEqual(selected["devcontainerCandidate"], candidate)
        self.assertEqual(selected["workload"], "pin")
        self.assertIn("configuration", selected["devcontainerFixture"])
        build = released_engine.fixture_guest_inputs({"workload": "pin"}, "D02-dockerfile-config", candidate, repository)
        self.assertIn("dockerfile", build["devcontainerFixture"])
        self.assertEqual(build["devcontainerCandidate"], candidate)
        users = released_engine.fixture_guest_inputs({"workload": "pin"}, "D03-users-environment", candidate, repository)
        self.assertEqual(users["devcontainerCandidate"], candidate)
        self.assertIn("adduser", users["devcontainerFixture"]["dockerfile"])
        self.assertEqual(json.loads(users["devcontainerFixture"]["configuration"])["containerUser"], "vscode")
        lifecycle = released_engine.fixture_guest_inputs({"workload": "pin"}, "D04-lifecycle-hooks", candidate, repository)
        self.assertEqual(lifecycle["devcontainerCandidate"], candidate)
        self.assertIn("initializeCommand", json.loads(lifecycle["devcontainerFixture"]["configuration"]))
        self.assertNotIn("dockerfile", lifecycle["devcontainerFixture"])
        reuse = released_engine.fixture_guest_inputs({"workload": "pin"}, "D07-reuse-cleanup", candidate, repository)
        self.assertEqual(reuse["devcontainerCandidate"], candidate)
        self.assertEqual(json.loads(reuse["devcontainerFixture"]["configuration"])["mounts"],
                         ["source=dcparity-d07-reuse,target=/cache,type=volume"])
        self.assertNotIn("dockerfile", reuse["devcontainerFixture"])
        for changed in ({}, {**candidate, "scope": "release"}, {**candidate, "executables": {}}):
            with self.assertRaisesRegex(ValueError, "private-runtime"):
                released_engine.fixture_guest_inputs({}, "D01-image-config", changed, repository)
        original = {"unchanged": True}
        self.assertIs(released_engine.fixture_guest_inputs(original, "E01-engine-negotiation", {}, repository), original)

    def test_candidate_admission_does_not_substitute_the_published_runtime(self):
        lock = json.loads((Path(__file__).parents[1] / "bazel/releases.lock.json").read_text())
        for lane, profile in [("apple-stock", "stock"), ("container-compose", "enhanced")]:
            with patch("released_engine.admit_candidate", return_value={"scope": "local-candidate-integration-only"}) as local, \
                    patch("released_engine.require_retained", return_value={"released": True}) as prepared:
                selected = released_engine.admit(lock, lane, self.root, "candidate-invocation")
                local.assert_called_once_with(self.root, "candidate-invocation", profile)
                self.assertEqual(selected, [{"scope": "local-candidate-integration-only"}, {"released": True}])
                self.assertEqual(prepared.call_count, 1)
                self.assertNotEqual(prepared.call_args.args[0]["repository"], "stephenlclarke/devcontainer")

    def test_native_compose_options_fail_before_any_admission_when_incomplete(self):
        cases = [(["--fixture=C01-compose-service"], "prepared native Compose"),
                 (["--fixture=C02-compose-dependencies"], "prepared native Compose"),
                 (["--fixture=C02-compose-dependencies", "--compose-candidate-invocation=compose"], "private-runtime"),
                 (["--fixture=C01-compose-service", "--compose-candidate-invocation=compose"], "private-runtime"),
                 (["--compose-candidate-invocation=compose"], "only valid for native C01")]
        for options, message in cases:
            with self.subTest(options=options), patch("sys.argv", ["case", "--campaign=test", "--lane=apple-stock", *options]), \
                    patch("released_engine.admit", side_effect=AssertionError("must not admit")), \
                    self.assertRaisesRegex(ValueError, message):
                released_engine.main()

    def test_c01_entrypoint_binds_compose_inputs_and_refuses_artifact_drift(self):
        candidate = {"scope": released_engine.CANDIDATE_SCOPE, "runtimeProfile": "stock", "executables": {
            name: "/candidate/" + name for name in
            ("devcontainer", "devcontainer-engine", "devcontainer-compose", "devcontainer-docker", "reference-node")}}
        compose = {"scope": released_engine.CANDIDATE_SCOPE, "runtimeProfile": "stock", "productFamily": "container-compose",
                   "executables": {name: "/compose/" + name for name in released_engine.COMPOSE_PRODUCTS}, "assetSHA256": "a" * 64}
        releases = [candidate, {"executables": {"container": "/released/container", "container-apiserver": "/released/api"}}]
        guard = HostGuard(self.root / "admission.json")
        argv = ["case", "--campaign=c01-entrypoint", "--lane=apple-stock", "--fixture=C01-compose-service",
                "--candidate-invocation=dev", "--compose-candidate-invocation=compose"]
        with patch("released_engine.SSD", self.root), patch("released_engine.RETAINED", Path.home()), \
                patch("released_engine.require_owned_volume", return_value={"ownersEnabled": True}), \
                patch("released_engine.admit", return_value=releases), \
                patch("released_engine.admit_candidate", return_value=compose) as selected, \
                patch("released_engine.admit_guest", return_value={"workload": "fixture"}), \
                patch("released_engine.version", return_value="fixture"), \
                patch("released_engine.CaseStore", return_value=self.store), patch("released_engine.HostGuard", return_value=guard), \
                patch("released_engine.runtime_lease", side_effect=lambda *_: runtime_lease(self.root / "lock", guard)), \
                patch("released_engine.ReleasedCase") as factory, patch("sys.stdout", new_callable=io.StringIO), \
                patch("sys.argv", argv):
            case = factory.return_value
            case.operation.return_value = {"compose_env": "compose-service", "post_create": "compose-post-create",
                                           "workspace": "/workspaces/devcontainer-parity"}
            case.cleanup.return_value = {"status": "passed", "remainingOwnedResources": []}
            with self.assertRaises(SystemExit) as status:
                released_engine.main()
            self.assertEqual(status.exception.code, 0)
            selected.assert_called_once_with(Path.home(), "compose", "stock", "container-compose")
            self.assertEqual(factory.call_args.kwargs["guest_inputs"]["composeCandidate"], compose)
            runtime = factory.call_args.kwargs["admission"]["runtime"]
            self.assertEqual(runtime["guestInputs"]["composeCandidate"], compose)
            revalidate = factory.call_args.args[4]
            self.assertEqual(revalidate(), releases)
            selected.return_value = {**compose, "assetSHA256": "b" * 64}
            with self.assertRaisesRegex(ValueError, "guest inputs changed"):
                revalidate()

    def test_c01_requires_matching_complete_compose_candidate(self):
        candidate = {"scope": released_engine.CANDIDATE_SCOPE, "runtimeProfile": "stock", "executables": {
            name: "/candidate/" + name for name in
            ("devcontainer", "devcontainer-engine", "devcontainer-compose", "devcontainer-docker", "reference-node")}}
        compose = {"scope": released_engine.CANDIDATE_SCOPE, "runtimeProfile": "stock", "productFamily": "container-compose",
                   "executables": {name: "/compose/" + name for name in released_engine.COMPOSE_PRODUCTS}}
        repository = Path(__file__).parents[2]
        selected = released_engine.fixture_guest_inputs({}, "C01-compose-service", candidate, repository, compose)
        self.assertEqual(selected["composeCandidate"], compose)
        self.assertIn("compose", selected["devcontainerFixture"])
        for name in ("E09-compose-foreground", "E10-compose-quiet"):
            with self.subTest(fixture=name):
                self.assertEqual(released_engine.fixture_guest_inputs({}, name, candidate, repository, compose),
                                 {"composeCandidate": compose})
                for invalid in (None, {**compose, "runtimeProfile": "enhanced"}, {**compose, "executables": {}}):
                    with self.assertRaisesRegex(ValueError, "matching native Compose"):
                        released_engine.fixture_guest_inputs({}, name, candidate, repository, invalid)
        dependencies = released_engine.fixture_guest_inputs(
            {"dependencyWorkload": "admitted"}, "C02-compose-dependencies", candidate, repository, compose)
        self.assertEqual(dependencies["composeCandidate"], compose)
        self.assertEqual(dependencies["dependencyWorkload"], "admitted")
        self.assertEqual(json.loads(dependencies["devcontainerFixture"]["configuration"])["runServices"],
                         ["app", "database", "helper"])
        for invalid in (None, {}, {**compose, "runtimeProfile": "enhanced"}, {**compose, "scope": "release"},
                        {**compose, "productFamily": "docker-compose"}, {**compose, "executables": {}}):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "matching native Compose"):
                released_engine.fixture_guest_inputs({}, "C01-compose-service", candidate, repository, invalid)

    def test_candidate_evidence_cannot_compare_as_published_release_parity(self):
        published = released_engine.release_set_identity({"lock": "published"}, None, None)
        candidate = released_engine.release_set_identity({"lock": "published"}, None, {"candidateInvocation": "first"})
        self.assertNotEqual(published, candidate)
        self.assertNotEqual(candidate, released_engine.release_set_identity(
            {"lock": "published"}, None, {"candidateInvocation": "second"}))
        records = [{"identity": dict(self.identity, lane=lane, releaseSetSHA256=fingerprint), "result": result()}
                   for lane, fingerprint in [("docker", published), ("apple-stock", candidate), ("container-compose", published)]]
        with self.assertRaises(ValueError):
            compare_cases(records, {"ping": "true"})
        output = self.root / "candidate.xml"
        write_junit(records[1]["identity"], result(), output, released_engine.CANDIDATE_SCOPE)
        properties = {entry.attrib["name"]: entry.attrib["value"] for entry in ET.parse(output).findall("testcase/properties/property")}
        self.assertEqual(properties["scope"], "local-candidate-integration-only")

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

    def test_guest_entrypoint_binds_closure_and_revalidates_before_cleanup(self):
        fixture = "E05-archive-copy"
        inputs = {"kernel": {"sha256": "fixed"}, "workload": {"sha256": "fixed"}}
        releases = [{"executables": {"devcontainer": "/released/devcontainer"}},
                    {"executables": {"container": "/released/container", "container-apiserver": "/released/api"}}]
        guard = HostGuard(self.root / "admission.json")
        with patch("released_engine.SSD", self.root), patch("released_engine.RETAINED", Path.home()), \
                patch("released_engine.require_owned_volume", return_value={"ownersEnabled": True}), \
                patch("released_engine.admit", return_value=releases), \
                patch("released_engine.admit_guest", return_value=inputs) as admit_guest, \
                patch("released_engine.version", return_value="fixture"), \
                patch("released_engine.CaseStore", return_value=self.store), patch("released_engine.HostGuard", return_value=guard), \
                patch("released_engine.runtime_lease", side_effect=lambda *_: runtime_lease(self.root / "lock", guard)), \
                patch("released_engine.ReleasedCase") as factory, patch("sys.stdout", new_callable=io.StringIO), \
                patch("sys.argv", ["released-engine", "--campaign=guest-entrypoint", "--lane=apple-stock", "--fixture=" + fixture]):
            case = factory.return_value
            case.operation.return_value = {"content": "true", "large_file": "true", "long_path": "true",
                                           "mode": "0o750", "symlink": "true"}
            case.cleanup.return_value = {"status": "passed", "remainingOwnedResources": []}
            with self.assertRaises(SystemExit) as status:
                released_engine.main()
            self.assertEqual(status.exception.code, 0)
            self.assertEqual(factory.call_args.kwargs["guest_inputs"], inputs)
            self.assertEqual(factory.call_args.args[1]["fixture"], fixture)
            revalidate = factory.call_args.args[4]
            self.assertEqual(revalidate(), releases)
            admit_guest.return_value = {"changed": True}
            with self.assertRaisesRegex(ValueError, "guest inputs changed"):
                revalidate()

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
        case.admission = {"scope": released_engine.CANDIDATE_SCOPE, "runtime": {"candidateInvocation": "fixture"}}
        with patch.object(case.child, "start"), patch.object(case.child, "wait_ready"), patch.object(case.child, "process") as process:
            process.pid = 42
            case.setup()
        self.assertEqual(json.loads((case.root / "owner.json").read_text())["identity"], self.identity)
        with self.store.connect() as database:
            names = [row[0] for row in database.execute("SELECT name FROM artifacts ORDER BY name")]
            manifest = database.execute("SELECT bytes FROM artifacts WHERE name='admission.json'").fetchone()[0]
        self.assertEqual(names, ["admission.json", "keychain-intent.json", "keychain-ready.json",
                                 "owner.json", "process-incarnation.json", "process-intent.json", "process.json"])
        self.assertEqual(json.loads(manifest), case.admission)
        self.assertEqual(case.cleanup()["status"], "passed")
        self.assertFalse(case.root.exists())
        self.assertEqual([call.args[1] for call in self.keychains.call_args_list], ["create", "delete"])

    def test_operation_uses_direct_engine_probe_with_deadline(self):
        case = self.case()
        case.socket = self.root / "socket"
        with patch("released_engine.engine_negotiation", return_value={"ping": "true"}) as probe:
            self.assertEqual(case.operation(), {"ping": "true"})
        probe.assert_called_once_with(case.socket, observe=case.requests.append)

    def test_failed_keychain_setup_cannot_launch_engine(self):
        case = self.case()
        self.keychains.side_effect = RuntimeError('keychain failure')
        with patch.object(case.child, 'start') as start:
            with self.assertRaisesRegex(RuntimeError, 'keychain failure'):
                case.setup()
            start.assert_not_called()
        self.keychains.side_effect = None
        self.assertEqual(case.cleanup()['status'], 'passed')

    def test_failed_keychain_cleanup_keeps_owned_root_and_guard(self):
        case = self.case()
        with patch.object(case.child, 'start'), patch.object(case.child, 'wait_ready'), \
                patch.object(case.child, 'process') as process:
            process.pid = 42
            case.setup()
        self.keychains.side_effect = RuntimeError('keychain cleanup failure')
        with self.assertRaisesRegex(RuntimeError, 'keychain cleanup failure'):
            case.cleanup()
        self.assertTrue(case.root.exists())
        self.assertTrue(case.guard.path.exists())

    def test_guest_provision_and_operation_precede_verified_teardown(self):
        case = self.case()
        runtime = Mock(service={"pid": 42})
        runtime.receipt.return_value = {"status": "restored"}
        case.runtime_factory = Mock(return_value=runtime)
        case.guest_inputs = {"fixture": "admitted data"}
        ordering = []
        def start_runtime(*, prepare_home):
            prepare_home(runtime.journal)
            ordering.append("api-start")
        runtime.start.side_effect = start_runtime
        self.keychains.side_effect = lambda _root, action, _journal: ordering.append("keychain-" + action)
        with patch("released_engine.ReleasedGuest") as factory, \
                patch.object(case.child, "start", side_effect=lambda *_, **__: ordering.append("engine-start")), \
                patch.object(case.child, "stop", side_effect=lambda: ordering.append("engine-stop")), \
                patch.object(case.child, "wait_ready"), patch.object(case.child, "process") as process:
            process.pid = 43
            guest = factory.return_value
            guest.provision.side_effect = lambda: ordering.append("provision")
            guest.cleanup.side_effect = lambda: ordering.append("guest-cleanup")
            runtime.restore.side_effect = lambda: ordering.append("restore")
            case.setup()
            self.assertEqual(case.operation(), guest.operation.return_value)
            self.assertEqual(case.cleanup()["status"], "passed")
        self.assertEqual(ordering, ["keychain-create", "api-start", "provision", "engine-start",
                                   "guest-cleanup", "engine-stop", "restore", "keychain-delete"])

    def test_provider_restore_failure_keeps_private_keychain_and_root(self):
        case = self.case()
        runtime = Mock(service={"pid": 42})
        runtime.start.side_effect = lambda *, prepare_home: prepare_home(runtime.journal)
        runtime.restore.side_effect = RuntimeError("provider still running")
        runtime.receipt.return_value = {"status": "incomplete"}
        case.runtime_factory = Mock(return_value=runtime)
        with patch.object(case.child, "start"), patch.object(case.child, "wait_ready"), \
                patch.object(case.child, "process") as process:
            process.pid = 43
            case.setup()
        with self.assertRaisesRegex(RuntimeError, "provider still running"):
            case.cleanup()
        self.assertEqual([call.args[1] for call in self.keychains.call_args_list], ["create"])
        self.assertTrue(case.root.exists())
        self.assertTrue(case.guard.path.exists())

    def test_final_service_receipt_includes_private_keychain_deletion(self):
        case = self.case()
        runtime = Mock(service={"pid": 42})
        def start_runtime(*, prepare_home):
            runtime.journal = ServiceJournal(self.root / "receipt.sqlite", case.owner, create=True)
            runtime.receipt.side_effect = runtime.journal.receipt
            prepare_home(runtime.journal)
        runtime.start.side_effect = start_runtime
        def keychain(_root, action, journal):
            journal.put("fixture-keychain-" + action + ".json", b'{}')
            return {"status": action}
        self.keychains.side_effect = keychain
        case.runtime_factory = Mock(return_value=runtime)
        with patch.object(case.child, "start"), patch.object(case.child, "wait_ready"), \
                patch.object(case.child, "process") as process:
            process.pid = 43
            case.setup()
        self.assertEqual(case.cleanup()["status"], "passed")
        self.assertIn("fixture-keychain-delete.json", runtime.journal.records())
        with self.store.connect() as database:
            receipt = database.execute("SELECT bytes FROM artifacts WHERE name='service-journal.json'").fetchone()[0]
        self.assertEqual(json.loads(receipt), runtime.journal.receipt())

    def test_uncertain_guest_cleanup_keeps_engine_provider_root_and_guard(self):
        case = self.case()
        with patch.object(case.child, "start"), patch.object(case.child, "wait_ready"), \
                patch.object(case.child, "process") as process:
            process.pid = 43
            case.setup()
        case.runtime = Mock()
        case.guest = Mock()
        case.guest.cleanup.side_effect = ValueError("uncertain guest")
        with patch.object(case.child, "stop") as stop, self.assertRaisesRegex(ValueError, "uncertain guest"):
            case.cleanup()
        stop.assert_not_called()
        case.runtime.restore.assert_not_called()
        self.assertTrue(case.root.exists())
        with self.assertRaisesRegex(ValueError, "quarantined"):
            case.guard.check()
        case.output.close()  # Fake Engine was never started; close only the test handle.

    def test_provision_log_failure_or_large_output_cannot_lose_diagnostics_at_teardown(self):
        for oversized in (False, True):
            with self.subTest(oversized=oversized):
                case = self.case()
                case.identity = dict(case.identity, fixture="E05-archive-copy", campaign="log-" + str(oversized))
                self.store.begin(case.identity)
                runtime = Mock(service={"pid": 42})
                runtime.receipt.return_value = {"status": "restored"}
                case.runtime_factory = Mock(return_value=runtime)
                case.guest_inputs = {"workload": {"image": {"config": "sha256:" + "a" * 64}}}
                unavailable = [not oversized]
                def start_runtime(*, prepare_home):
                    runtime.journal = ServiceJournal(self.root / ("logs-" + str(oversized) + ".sqlite"),
                                                     case.owner, create=True)
                    put = runtime.journal.put
                    def retain(name, data):
                        if name == "guest-kernel.log" and unavailable[0]:
                            raise OSError("log retention unavailable")
                        put(name, data)
                    runtime.journal.put = retain
                    prepare_home(runtime.journal)
                runtime.start.side_effect = start_runtime
                child = Mock()
                child.process.pid, child.process.wait.return_value = 44, 0
                payload = b"x" * (9 * 1024**2) if oversized else b"important setup diagnostic"
                child.start.side_effect = lambda _a, _r, output, **_k: output.write(payload)
                def provision(guest):
                    guest.command("guest-kernel", ["system", "kernel", "set"])
                with patch("guest_runtime.OwnedProcess", return_value=child), \
                        patch("guest_runtime.ReleasedGuest.provision", new=provision), \
                        patch.object(case.child, "start"), patch.object(case.child, "wait_ready"), \
                        patch.object(case.child, "stop"), patch.object(case.child, "process") as engine:
                    engine.pid = 43
                    if oversized:
                        case.setup()
                    else:
                        with self.assertRaisesRegex(OSError, "log retention"):
                            case.setup()
                        with self.assertRaisesRegex(OSError, "log retention"):
                            case.cleanup()
                        self.assertTrue(case.root.exists())
                        self.assertTrue(case.guard.path.exists())
                        runtime.restore.assert_not_called()
                        unavailable[0] = False
                    self.assertEqual(case.cleanup()["status"], "passed")
                self.assertFalse(case.root.exists())
                self.assertFalse(case.guard.path.exists())
                records = runtime.journal.records()
                self.assertEqual(records["guest-kernel.log"], payload[:1024**2])
                self.assertEqual(json.loads(records["guest-kernel-log.json"])["truncated"], oversized)

    def test_oversized_engine_log_is_bounded_without_blocking_cleanup(self):
        case = self.case()
        with patch.object(case.child, "start"), patch.object(case.child, "wait_ready"), \
                patch.object(case.child, "process") as process:
            process.pid = 43
            case.setup()
        case.output.write(b"x" * (9 * 1024**2))
        self.assertEqual(case.cleanup()["status"], "passed")
        self.assertFalse(case.root.exists())
        case.guard.check()
        with self.store.connect() as database:
            artifacts = dict(database.execute("SELECT name,bytes FROM artifacts"))
        self.assertEqual(artifacts["engine.log"], b"x" * 1024**2)
        self.assertEqual(json.loads(artifacts["engine-log.json"]), {
            "bytes": 1024**2, "sha256": released_engine.digest(artifacts["engine.log"]), "truncated": True})
        self.assertEqual(json.loads(artifacts["process-cleanup.json"]), {"verifiedStopped": True})

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
