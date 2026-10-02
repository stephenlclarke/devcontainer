#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# SPDX-License-Identifier: Apache-2.0
"""Inert lifecycle and sealing-boundary tests for final-package qualification."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pwd
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPOSITORY = Path(__file__).resolve().parents[2]
ACCOUNT_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
sys.path.insert(0, str(REPOSITORY / "Tools/testing"))
sys.path.insert(0, str(REPOSITORY / "Tools/bazel"))

import qualify_finalized_package as qualify


def helper_service_path(root: Path, label: str) -> str:
    from runtime_services import PROVIDER_HELPER_LAYOUT

    return str(root / "container/plugin-state" / PROVIDER_HELPER_LAYOUT[label][0] / "service.plist")


class NativeComposeFrontendEnvironmentTests(unittest.TestCase):
    def test_signed_compose_and_q_runtime_have_independent_source_pins(self) -> None:
        manifest = {"referencePins": {
            "containerRuntime": {"repository": "stephenlclarke/container", "stableCommit": "b" * 40},
            "containerCompose": {"source": "stephenlclarke/container-compose",
                                 "stableVersion": "0.16.0", "stableCommit": "a" * 40},
        }}
        identity = {"format": "signed-compose-q-runtime",
                    "containerRuntime": {"repository": "stephenlclarke/container", "commit": "b" * 40},
                    "containerCompose": {"repository": "stephenlclarke/container-compose",
                                         "version": "0.16.0", "commit": "a" * 40}}
        qualify.validate_native_provider_manifest_identity(manifest, identity)
        manifest["referencePins"]["containerRuntime"]["stableCommit"] = "a" * 40
        with self.assertRaisesRegex(ValueError, "distinct Compose and Container"):
            qualify.validate_native_provider_manifest_identity(manifest, identity)

    def test_fork_fingerprint_binds_container_output_to_runtime_not_compose(self) -> None:
        identity = {"format": "signed-compose-q-runtime",
                    "containerRuntime": {"commit": "b" * 40},
                    "containerCompose": {"version": "0.16.0", "commit": "a" * 40}}
        expected = {"stockContainer": "1" * 64, "composeContainer": "2" * 64,
                    "composeProvider": "3" * 64}
        manifest = {"referencePins": {"containerCompose": {
            "stableVersion": "0.16.0", "stableCommit": "a" * 40}}}
        with tempfile.TemporaryDirectory() as temporary:
            fingerprint = Path(temporary) / "fingerprint.json"
            value = {"providerBinarySHA256": {
                "DEVCONTAINER_CONTAINER_BIN": expected["composeContainer"],
                "DEVCONTAINER_COMPOSE_BIN": expected["composeProvider"]},
                "commands": {"container": {"stdout": json.dumps({"commit": "b" * 40})},
                             "containerCompose": {"stdout": json.dumps({
                                 "version": "0.16.0", "commit": "a" * 40})}}}
            fingerprint.write_text(json.dumps(value))
            qualify.validate_provider_fingerprint(
                fingerprint, "container-compose", expected, manifest, identity)
            value["commands"]["container"]["stdout"] = json.dumps({"commit": "a" * 40})
            fingerprint.write_text(json.dumps(value))
            with self.assertRaisesRegex(RuntimeError, "checked-in version and commit"):
                qualify.validate_provider_fingerprint(
                    fingerprint, "container-compose", expected, manifest, identity)

    def test_stock_wrapper_uses_pinned_docker_cli_and_compose(self) -> None:
        args = argparse.Namespace(
            docker_bin=Path("/pinned/docker"),
            docker_compose_bin=Path("/pinned/docker-compose"),
            compose_provider_bin=Path("/pinned/container-compose"),
            compose_provider_sha256="a" * 64,
        )
        self.assertEqual(qualify.native_compose_frontend_environment(args, "apple-stock"), {
            "DEVCONTAINER_BACKEND": "stock",
            "DEVCONTAINER_COMPOSE_PROVIDER": "docker",
            "DEVCONTAINER_DOCKER_BIN": "/pinned/docker",
            "DEVCONTAINER_DOCKER_COMPOSE_BIN": "/pinned/docker-compose",
        })

    def test_fork_wrapper_uses_only_admitted_external_compose_provider(self) -> None:
        args = argparse.Namespace(
            docker_bin=Path("/pinned/docker"),
            docker_compose_bin=Path("/pinned/docker-compose"),
            compose_provider_bin=Path("/pinned/container-compose"),
            compose_provider_sha256="a" * 64,
        )
        self.assertEqual(qualify.native_compose_frontend_environment(args, "container-compose"), {
            "DEVCONTAINER_BACKEND": "container-compose",
            "DEVCONTAINER_COMPOSE_PROVIDER": "container-compose",
            "DEVCONTAINER_COMPOSE_BIN": "/pinned/container-compose",
            "DEVCONTAINER_COMPOSE_PROVIDER_SHA256": "a" * 64,
        })

    def test_compose_frontend_selection_rejects_non_native_lane(self) -> None:
        args = argparse.Namespace(docker_bin=Path("/pinned/docker"),
                                  docker_compose_bin=Path("/pinned/docker-compose"),
                                  compose_provider_bin=Path("/pinned/container-compose"),
                                  compose_provider_sha256="a" * 64)
        with self.assertRaisesRegex(ValueError, "unsupported native Compose lane"):
            qualify.native_compose_frontend_environment(args, "docker")


class SuiteLifecycleTests(unittest.TestCase):
    def test_explicit_component_runs_cli_only_and_selects_exact_fixture(self) -> None:
        calls = []
        cli = subprocess.CompletedProcess(["cli"], 0, "", "")
        actual_cli, actual_vscode, passed = qualify.run_suite_pair(
            lambda: (calls.append("cli"), cli)[1],
            lambda: calls.append("vscode"), lambda: True, lambda: True,
            component_fixture=qualify.COMPONENT_FIXTURE)
        self.assertEqual(calls, ["cli"])
        self.assertIs(actual_cli, cli)
        self.assertIsNone(actual_vscode)
        self.assertTrue(passed)
        environment = {"PATH": "/usr/bin"}
        self.assertEqual(qualify.selected_fixture_environment(
            environment, qualify.COMPONENT_FIXTURE),
            {"PATH": "/usr/bin", "DEVCONTAINER_PARITY_FIXTURES": qualify.COMPONENT_FIXTURE})

    def test_component_cleanup_requires_only_exact_cli_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary).resolve()
            lane_root = evidence / "docker"
            lane_root.mkdir()
            (lane_root / "results.json").write_text(json.dumps({
                "fixtures": [{"id": qualify.COMPONENT_FIXTURE}], "cleanupDifferences": []}))
            self.assertTrue(qualify.cli_cleanup_is_complete(
                evidence, "docker", qualify.COMPONENT_FIXTURE))
            (lane_root / "results.json").write_text(json.dumps({
                "fixtures": [{"id": qualify.COMPONENT_FIXTURE}, {"id": "E01"}],
                "cleanupDifferences": []}))
            self.assertFalse(qualify.cli_cleanup_is_complete(
                evidence, "docker", qualify.COMPONENT_FIXTURE))

    def test_component_recheck_accepts_only_initialized_evidence_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary).resolve() / "component"
            evidence.mkdir(mode=0o700)
            os.chmod(evidence, 0o700)
            inputs = {"sourceCommit": "a" * 40, "sourceTree": "b" * 40,
                      "campaign": "native-component-test"}
            inputs_path = evidence / "operator-inputs.json"
            qualify.write_json(inputs_path, inputs)
            os.chmod(inputs_path, 0o600)
            identity = qualify.capture_component_evidence_identity(evidence, inputs)
            (evidence / "docker").mkdir()
            args = argparse.Namespace(evidence=evidence, component_fixture=qualify.COMPONENT_FIXTURE,
                                      source_commit=inputs["sourceCommit"], campaign=inputs["campaign"],
                                      _source_tree=inputs["sourceTree"])

            qualify.validate_evidence_root(args, identity)
            with self.assertRaisesRegex(ValueError, "fresh"):
                qualify.validate_evidence_root(args)

    def test_component_recheck_rejects_foreign_preexisting_or_changed_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            original = parent / "original"
            foreign = parent / "foreign"
            original.mkdir(mode=0o700)
            foreign.mkdir(mode=0o700)
            os.chmod(original, 0o700)
            os.chmod(foreign, 0o700)
            inputs = {"sourceCommit": "a" * 40, "sourceTree": "b" * 40,
                      "campaign": "native-component-test"}
            for root in (original, foreign):
                path = root / "operator-inputs.json"
                qualify.write_json(path, inputs)
                os.chmod(path, 0o600)
            identity = qualify.capture_component_evidence_identity(original, inputs)
            args = argparse.Namespace(evidence=foreign, component_fixture=qualify.COMPONENT_FIXTURE,
                                      source_commit=inputs["sourceCommit"], campaign=inputs["campaign"],
                                      _source_tree=inputs["sourceTree"])
            with self.assertRaisesRegex(ValueError, "identity"):
                qualify.validate_evidence_root(args, identity)

            args.evidence = original
            args.campaign = "different-campaign"
            with self.assertRaisesRegex(ValueError, "identity"):
                qualify.validate_evidence_root(args, identity)

            args.campaign = inputs["campaign"]
            (original / "operator-inputs.json").write_text(json.dumps(inputs), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "operator inputs changed"):
                qualify.validate_evidence_root(args, identity)

    def test_component_success_writes_non_authoritative_result_without_sealing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary).resolve()
            args = argparse.Namespace(evidence=evidence, source_commit="a" * 40,
                                      _source_tree="b" * 40, provenance_sha256="c" * 64,
                                      state_sha256="d" * 64,
                                      _component_package_proof={"archiveSHA256": "e" * 64})
            cleanup = {lane: {"status": "restored", "cliCleanupComplete": True,
                              "vscodeCleanupComplete": False, "vscodeStatus": "skipped"}
                       for lane in qualify.LANES}
            host = {"status": "restored", "initialColima": "stopped", "finalColima": "stopped",
                    "initialServiceSetSHA256": "f" * 64, "finalServiceSetSHA256": "f" * 64,
                    "initialServiceCount": 8, "finalServiceCount": 8, "hostGuardCleared": True,
                    "restoration": {lane: "restored" for lane in qualify.LANES}}
            comparison = {"schemaVersion": 3, "suite": "cli", "status": "passed",
                          "expectedFixtures": [qualify.COMPONENT_FIXTURE],
                          "evidenceStatus": "passed", "functionalParityStatus": "passed",
                          "timingStatus": "passed", "requireZeroFunctionalDifferences": True}
            qualify.write_json(evidence / "component-comparison.json", comparison)
            with mock.patch.object(qualify, "seal_after_host_cleanup") as sealer:
                result = qualify.finalize_component_result(
                    args, cleanup, comparison, host, {"docker": "1" * 64}, {}, [])
            sealer.assert_not_called()
            self.assertEqual(result["scope"], "component-only")
            self.assertFalse(result["releaseAuthority"])
            self.assertEqual(result["fixtureCounts"], {"cliPerLane": 1, "vscodePerLane": 0,
                                                        "laneCount": 3, "totalLaneFixtureResults": 3})
            self.assertEqual(json.loads((evidence / "component-result.json").read_text()), result)
            self.assertFalse((evidence / "qualification.json").exists())

    def test_component_restoration_rejects_missing_or_mislabeled_vscode_skip(self) -> None:
        cleanup = {lane: {"status": "restored", "cliCleanupComplete": True,
                          "vscodeCleanupComplete": False, "vscodeStatus": "skipped"}
                   for lane in qualify.LANES}
        host = {"status": "restored", "hostGuardCleared": True,
                "initialColima": "stopped", "finalColima": "stopped",
                "initialServiceSetSHA256": "a", "finalServiceSetSHA256": "a",
                "initialServiceCount": 8, "finalServiceCount": 8}
        self.assertTrue(qualify.component_restoration_is_complete(cleanup, host))
        cleanup["apple-stock"]["vscodeStatus"] = "passed"
        self.assertFalse(qualify.component_restoration_is_complete(cleanup, host))

    def test_component_comparator_requires_exact_measured_signal_count(self) -> None:
        import hashlib

        def stream(count):
            stdout = b"compose-stdout\n" + b"signal:USR1\n" * count + b"signal:TERM\n"
            return {"stdoutSHA256": hashlib.sha256(stdout).hexdigest(),
                    "signals": ["SIGUSR1"] * count + ["SIGTERM"],
                    "counts": {"SIGUSR1": count, "SIGTERM": 1}}

        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary).resolve()
            for lane in qualify.LANES:
                lane_root = evidence / lane
                lane_root.mkdir()
                (lane_root / "results.json").write_text(json.dumps({
                    "backend": lane, "status": "passed", "durationSeconds": 1.0,
                    "cleanupDifferences": [],
                    "fixtures": [{"id": qualify.COMPONENT_FIXTURE, "status": "passed",
                                  "durationSeconds": 1.0, "observations": {"exit": "23"},
                                  "signalStream": stream(1)}]}))
            passing = qualify.compare_component_results(evidence, qualify.COMPONENT_FIXTURE)
            self.assertEqual(passing["status"], "passed")
            compose_result = evidence / "container-compose/results.json"
            compose_payload = json.loads(compose_result.read_text())
            compose_payload["fixtures"][0]["signalStream"] = stream(2)
            compose_result.write_text(json.dumps(compose_payload))
            comparison = qualify.compare_component_results(evidence, qualify.COMPONENT_FIXTURE)
            self.assertEqual(comparison["status"], "failed")
            self.assertEqual(comparison["expectedFixtures"], [qualify.COMPONENT_FIXTURE])
            self.assertTrue((evidence / "component-comparison.json").is_file())

    def test_success_sealer_reads_the_durable_host_cleanup_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary).resolve()
            host_payload = {"schemaVersion": 1, "status": "restored",
                            "initialColima": "stopped", "finalColima": "stopped",
                            "hostGuardCleared": True}
            (evidence / "host-cleanup.json").write_text(json.dumps(host_payload))
            expected = (evidence / "qualification", "a" * 64)
            with mock.patch.object(qualify, "seal_qualification", return_value=expected) as seal:
                actual = qualify.seal_after_host_cleanup(
                    argparse.Namespace(), evidence, {"docker": {"status": "restored"}},
                    {"cli": {"status": "passed"}}, {"docker": {}}, {"archiveSHA256": "b" * 64})

        self.assertEqual(actual, expected)
        self.assertEqual(seal.call_args.args[3], host_payload)

    def test_nonzero_cli_with_complete_cleanup_still_runs_vscode(self) -> None:
        calls = []
        cli = subprocess.CompletedProcess(["cli"], 1, "", "fixture failed")
        vscode = subprocess.CompletedProcess(["vscode"], 0, "", "")

        actual_cli, actual_vscode, passed = qualify.run_suite_pair(
            lambda: (calls.append("cli"), cli)[1],
            lambda: (calls.append("vscode"), vscode)[1],
            lambda: True, lambda: True)

        self.assertEqual(calls, ["cli", "vscode"])
        self.assertIs(actual_cli, cli)
        self.assertIs(actual_vscode, vscode)
        self.assertFalse(passed)

    def test_incomplete_cli_cleanup_prevents_vscode(self) -> None:
        calls = []
        with self.assertRaisesRegex(RuntimeError, "CLI cleanup is incomplete"):
            qualify.run_suite_pair(
                lambda: (calls.append("cli"), subprocess.CompletedProcess(["cli"], 1))[1],
                lambda: calls.append("vscode"), lambda: False, lambda: True)
        self.assertEqual(calls, ["cli"])

    def test_docker_profile_stays_running_when_suite_cleanup_is_uncertain(self) -> None:
        self.assertFalse(qualify.docker_stop_allowed(
            started_here=True, suites_started=True, suite_cleanup_complete=False))
        self.assertFalse(qualify.docker_restore_is_safe(
            started_here=True, suites_started=True, suite_cleanup_complete=False,
            initial="stopped", final="running"))

    def test_docker_can_stop_owned_profile_before_any_suite_started(self) -> None:
        self.assertTrue(qualify.docker_stop_allowed(
            started_here=True, suites_started=False, suite_cleanup_complete=False))
        self.assertTrue(qualify.docker_restore_is_safe(
            started_here=True, suites_started=False, suite_cleanup_complete=False,
            initial="stopped", final="stopped"))

    def test_host_restoration_rejects_partial_lane_or_uncleared_guard(self) -> None:
        cleanup = {lane: {"status": "restored"} for lane in qualify.LANES}
        with self.assertRaisesRegex(RuntimeError, "guard"):
            qualify.require_host_restoration(cleanup, "stopped", "stopped", False)
        cleanup["container-compose"]["status"] = "uncertain"
        with self.assertRaisesRegex(RuntimeError, "lanes"):
            qualify.require_host_restoration(cleanup, "stopped", "stopped", True)

    def test_cancelled_campaign_clears_guard_only_after_mutated_lanes_restore(self) -> None:
        cleanup = {"docker": {"status": "restored"}, "apple-stock": {"status": "restored"},
                   "container-compose": {"status": "not-started"}}
        self.assertTrue(qualify.host_can_clear_guard(cleanup, "stopped", "stopped", "same", "same"))
        # Restoration permits another campaign; it cannot qualify absent suites.
        with self.assertRaisesRegex(RuntimeError, "lanes"):
            qualify.require_host_restoration(cleanup, "stopped", "stopped", True)

    def test_guard_remains_when_any_started_lane_or_host_identity_is_uncertain(self) -> None:
        cleanup = {lane: {"status": "restored"} for lane in qualify.LANES}
        for status in ("active", "uncertain", None):
            with self.subTest(status=status):
                cleanup["apple-stock"]["status"] = status
                self.assertFalse(qualify.host_can_clear_guard(
                    cleanup, "stopped", "stopped", "same", "same"))
        cleanup["apple-stock"]["status"] = "restored"
        self.assertFalse(qualify.host_can_clear_guard(cleanup, "stopped", "running", "same", "same"))
        self.assertFalse(qualify.host_can_clear_guard(cleanup, "stopped", "stopped", "before", "after"))

    def test_host_observation_failure_writes_failure_receipts_and_retains_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary).resolve()
            cleanup = {lane: {"status": "not-started"} for lane in qualify.LANES}
            guard = mock.Mock()
            errors = []
            with (mock.patch.object(qualify, "colima_state", return_value=("stopped", "still stopped")),
                  mock.patch.object(qualify, "host_service_digest",
                                    side_effect=RuntimeError("injected observation fault"))):
                guard_cleared = qualify._finalize_host_cleanup(
                    evidence, argparse.Namespace(colima_bin=Path("colima")), {}, cleanup,
                    "stopped", "initial Colima status", "1" * 64, 8, guard,
                    {"identity": "owner"}, errors, interrupted=True)

            self.assertFalse(guard_cleared)
            guard.clear.assert_not_called()
            host = json.loads((evidence / "host-cleanup.json").read_text())
            state = json.loads((evidence / "cleanup-state.json").read_text())
            failure = json.loads((evidence / "controller-failure.json").read_text())
            self.assertEqual(host["status"], "uncertain")
            self.assertIsNone(host["finalServiceSetSHA256"])
            self.assertIsNone(host["finalServiceCount"])
            self.assertTrue(any("host service final observation failed" in item for item in failure["errors"]))
            self.assertFalse(state["guardCleared"])
            self.assertEqual(host["restoration"]["docker"], "not-started")
            self.assertTrue(any("host service final observation failed" in item for item in errors))

    def test_pre_mutation_start_failure_is_not_started_but_not_a_fixture_pass(self) -> None:
        error = ValueError("gateway admission failed")

        class Runtime:
            root = None

            def failure_disposition(self):
                return {"status": "not-started", "phase": "preflight",
                        "hostMutationStarted": False}

            def retain_primary_failure(self, _error):
                return {"location": "private-case-root", "sha256": "a" * 64}

        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime()
            runtime.root = Path(temporary)
            owner = runtime.root / "owner.json"
            owner.write_text('{"fixture":"owner"}\n')
            owner_sha = hashlib.sha256(owner.read_bytes()).hexdigest()
            row = qualify.retain_lane_start_failure(runtime, error)
        self.assertEqual(row["status"], "not-started")
        self.assertFalse(row["hostMutationStarted"])
        self.assertEqual(row["primaryFailureType"], "builtins.ValueError")
        self.assertEqual(row["primaryFailureSHA256"], "a" * 64)
        self.assertEqual(row["ownerSHA256"], owner_sha)
        self.assertEqual(row["failureLocation"], "private-case-root")

        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            cleanup = {lane: {"status": "restored"} for lane in qualify.LANES}
            cleanup["apple-stock"] = row
            guard = mock.Mock()
            with (mock.patch.object(qualify, "colima_state", return_value=("stopped", "still stopped")),
                  mock.patch.object(qualify, "host_service_digest", return_value=("same", 8))):
                cleared = qualify._finalize_host_cleanup(
                    evidence, argparse.Namespace(colima_bin=Path("colima")), {}, cleanup,
                    "stopped", "initial state", "same", 8, guard, {"identity": "owner"},
                    [], interrupted=False)
            self.assertTrue(cleared)
            guard.clear.assert_called_once_with({"identity": "owner"})
            lane_cleanup = json.loads((evidence / "apple-stock-cleanup.json").read_text())
            self.assertEqual(lane_cleanup["status"], "not-started")
            self.assertFalse(lane_cleanup["cliCleanupComplete"])
            self.assertEqual(lane_cleanup["primaryFailureSHA256"], "a" * 64)
            self.assertEqual(lane_cleanup["ownerSHA256"], owner_sha)
            self.assertNotIn("fixtureStatus", lane_cleanup)
            self.assertFalse(qualify.cli_cleanup_is_complete(evidence, "apple-stock"))
            self.assertFalse((evidence / "qualification.json").exists())

    def test_missing_or_mutated_start_authority_never_claims_not_started(self) -> None:
        runtime = argparse.Namespace(journal=None)
        error = RuntimeError("failure")
        with self.assertRaisesRegex(RuntimeError, "explicit mutation authority"):
            qualify.retain_lane_start_failure(runtime, error)

        class MutatedRuntime:
            def failure_disposition(self):
                return {"status": "uncertain", "phase": "service-switch-prepare",
                        "hostMutationStarted": True}

            def retain_primary_failure(self, _error):
                return {"location": "private-journal", "sha256": "b" * 64}

        self.assertIsNone(qualify.retain_lane_start_failure(MutatedRuntime(), RuntimeError("failure")))
        cleanup = {lane: {"status": "restored"} for lane in qualify.LANES}
        cleanup["apple-stock"] = {"status": "uncertain"}
        self.assertFalse(qualify.host_can_clear_guard(
            cleanup, "stopped", "stopped", "same", "same"))

    def test_provider_quiescence_accepts_native_default_network_id_and_name(self) -> None:
        from service_switch import API

        helper_programs = {
            "com.apple.container.container-core-images": {"program": Path("/provider/core"), "sha256": "a" * 64},
            "com.apple.container.machine-apiserver": {"program": Path("/provider/machine"), "sha256": "b" * 64},
        }

        class Launchd:
            def inspect(self, label):
                if label == API:
                    return {"label": API, "path": "/runtime/selected-apiserver.plist",
                            "program": "/provider/container-apiserver"}
                path = "/foreign/helper.plist" if getattr(self, "foreign_helper_path", False) else helper_service_path(runtime.root, label)
                return {"label": label, "path": path,
                        "program": str(helper_programs[label]["program"])}

            def process_id(self, label):
                if label == "com.apple.container.machine-apiserver":
                    return None
                return {API: 101, "com.apple.container.container-core-images": 200}[label]

        runtime = argparse.Namespace(service={"pid": 101}, launchd=Launchd(), root=Path("/runtime"),
                                     executable=Path("/provider/container-apiserver"), verify=lambda: None)
        for default_row in ({"id": "default"}, {"name": "default"},
                            {"configuration": {"name": "default"}}):
            with self.subTest(default_row=default_row):
                outputs = ["[]", "[]", json.dumps([default_row]), "[]"]
                with (mock.patch.object(qualify, "run", side_effect=lambda *_a, **_k:
                                        subprocess.CompletedProcess([], 0, outputs.pop(0), "")),
                      mock.patch("runtime_services.process_inventory", return_value={
                          101: {"program": "/provider/container-apiserver"},
                          200: {"program": "/provider/core"}})):
                    proof = qualify.provider_quiescence(runtime, Path("/provider/container"), {}, helper_programs)
                self.assertEqual(proof["resourceCount"], 0)
                self.assertEqual(proof["clientCount"], 0)

        runtime.launchd.foreign_helper_path = True
        outputs = ["[]", "[]", '[{"id":"default"}]', "[]"]
        with (mock.patch.object(qualify, "run", side_effect=lambda *_a, **_k:
                                subprocess.CompletedProcess([], 0, outputs.pop(0), "")),
              mock.patch("runtime_services.process_inventory", return_value={
                  101: {"program": "/provider/container-apiserver"},
                  200: {"program": "/provider/core"}})):
            with self.assertRaisesRegex(RuntimeError, "helper identity differs"):
                qualify.provider_quiescence(runtime, Path("/provider/container"), {}, helper_programs)

    def test_provider_quiescence_counts_unowned_engine_and_foreign_network(self) -> None:
        from service_switch import API

        provider = Path("/provider/container")
        helper_programs = {
            "com.apple.container.container-core-images": {"program": Path("/provider/core"), "sha256": "a" * 64},
            "com.apple.container.machine-apiserver": {"program": Path("/provider/machine"), "sha256": "b" * 64},
        }

        class Launchd:
            def inspect(self, label):
                if label == API:
                    return {"label": API, "path": "/runtime/selected-apiserver.plist",
                            "program": "/provider/container-apiserver"}
                return {"label": label, "path": helper_service_path(runtime.root, label),
                        "program": str(helper_programs[label]["program"])}

            def process_id(self, label):
                return {API: 101, **{key: 200 + index for index, key in enumerate(helper_programs)}}[label]

        runtime = argparse.Namespace(
            service={"pid": 101}, launchd=Launchd(),
            root=Path("/runtime"), executable=Path("/provider/container-apiserver"), verify=lambda: None)
        outputs = ["[]", "[]", json.dumps([{"id": "default"}]), "[]"]
        process_map = {101: {"program": "/provider/container-apiserver"},
                       200: {"program": "/provider/core"},
                       201: {"program": "/provider/machine"},
                       999: {"program": "/runtime/foreign/devcontainer-engine"}}
        with (mock.patch.object(qualify, "run", side_effect=lambda *_a, **_k:
                                subprocess.CompletedProcess([], 0, outputs.pop(0), "")),
              mock.patch("runtime_services.process_inventory", return_value=process_map)):
            proof = qualify.provider_quiescence(runtime, provider, {}, helper_programs)

        self.assertEqual(proof, {"status": "running", "containerCount": 0, "resourceCount": 0,
                                 "guestCount": 0, "clientCount": 1})

    def test_provider_quiescence_counts_nondefault_provider_resources(self) -> None:
        from service_switch import API

        helper_programs = {
            "com.apple.container.container-core-images": {"program": Path("/provider/core"), "sha256": "a" * 64},
            "com.apple.container.machine-apiserver": {"program": Path("/provider/machine"), "sha256": "b" * 64},
        }

        class Launchd:
            def inspect(self, label):
                if label == API:
                    return {"label": API, "path": "/runtime/selected-apiserver.plist",
                            "program": "/provider/container-apiserver"}
                return {"label": label, "path": helper_service_path(runtime.root, label),
                        "program": str(helper_programs[label]["program"])}

            def process_id(self, label):
                return {API: 101, **{key: 200 + index for index, key in enumerate(helper_programs)}}[label]

        runtime = argparse.Namespace(service={"pid": 101}, launchd=Launchd(), root=Path("/runtime"),
                                     executable=Path("/provider/container-apiserver"), verify=lambda: None)
        outputs = ["[]", '[{"configuration":{"name":"project-vol"}}]',
                   json.dumps([{"name": "default"},
                               {"id": "default", "configuration": {"name": "project-net"}}]), "[]"]
        with (mock.patch.object(qualify, "run", side_effect=lambda *_a, **_k:
                                subprocess.CompletedProcess([], 0, outputs.pop(0), "")),
              mock.patch("runtime_services.process_inventory", return_value={
                  101: {"program": "/provider/container-apiserver"},
                  200: {"program": "/provider/core"}, 201: {"program": "/provider/machine"}})):
            proof = qualify.provider_quiescence(runtime, Path("/provider/container"), {}, helper_programs)

        self.assertEqual(proof["resourceCount"], 2)
        self.assertEqual(proof["clientCount"], 0)

    def test_provider_quiescence_counts_docker_bridge_as_custom_native_network(self) -> None:
        from service_switch import API

        helper_programs = {
            "com.apple.container.container-core-images": {"program": Path("/provider/core"), "sha256": "a" * 64},
            "com.apple.container.machine-apiserver": {"program": Path("/provider/machine"), "sha256": "b" * 64},
        }

        class Launchd:
            def inspect(self, label):
                if label == API:
                    return {"label": API, "path": "/runtime/selected-apiserver.plist",
                            "program": "/provider/container-apiserver"}
                return {"label": label, "path": helper_service_path(runtime.root, label),
                        "program": str(helper_programs[label]["program"])}

            def process_id(self, label):
                return {API: 101, **{key: 200 + index for index, key in enumerate(helper_programs)}}[label]

        runtime = argparse.Namespace(service={"pid": 101}, launchd=Launchd(), root=Path("/runtime"),
                                     executable=Path("/provider/container-apiserver"), verify=lambda: None)
        outputs = ["[]", "[]", json.dumps([{"configuration": {"name": "bridge"}}]), "[]"]
        with (mock.patch.object(qualify, "run", side_effect=lambda *_a, **_k:
                                subprocess.CompletedProcess([], 0, outputs.pop(0), "")),
              mock.patch("runtime_services.process_inventory", return_value={
                  101: {"program": "/provider/container-apiserver"},
                  200: {"program": "/provider/core"}, 201: {"program": "/provider/machine"}})):
            proof = qualify.provider_quiescence(runtime, Path("/provider/container"), {}, helper_programs)

        self.assertEqual(proof["resourceCount"], 1)

    def test_provider_quiescence_rejects_network_without_native_identity(self) -> None:
        from service_switch import API

        helper_programs = {
            "com.apple.container.container-core-images": {"program": Path("/provider/core"), "sha256": "a" * 64},
            "com.apple.container.machine-apiserver": {"program": Path("/provider/machine"), "sha256": "b" * 64},
        }

        class Launchd:
            def inspect(self, label):
                if label == API:
                    return {"label": API, "path": "/runtime/selected-apiserver.plist",
                            "program": "/provider/container-apiserver"}
                return {"label": label, "path": helper_service_path(runtime.root, label),
                        "program": str(helper_programs[label]["program"])}

            def process_id(self, label):
                return {API: 101, **{key: 200 + index for index, key in enumerate(helper_programs)}}[label]

        runtime = argparse.Namespace(service={"pid": 101}, launchd=Launchd(), root=Path("/runtime"),
                                     executable=Path("/provider/container-apiserver"), verify=lambda: None)
        outputs = ["[]", "[]", json.dumps([{}]), "[]"]
        with (mock.patch.object(qualify, "run", side_effect=lambda *_a, **_k:
                                subprocess.CompletedProcess([], 0, outputs.pop(0), "")),
              mock.patch("runtime_services.process_inventory", return_value={
                  101: {"program": "/provider/container-apiserver"},
                  200: {"program": "/provider/core"}, 201: {"program": "/provider/machine"}})):
            with self.assertRaisesRegex(RuntimeError, "omits network identity"):
                qualify.provider_quiescence(runtime, Path("/provider/container"), {}, helper_programs)

    def test_provider_quiescence_rejects_images_before_guest_provisioning(self) -> None:
        from service_switch import API

        helper_programs = {
            "com.apple.container.container-core-images": {"program": Path("/provider/core"), "sha256": "a" * 64},
            "com.apple.container.machine-apiserver": {"program": Path("/provider/machine"), "sha256": "b" * 64},
        }

        class Launchd:
            def inspect(self, label):
                if label == API:
                    return {"label": API, "path": "/runtime/selected-apiserver.plist",
                            "program": "/provider/container-apiserver"}
                return {"label": label, "path": helper_service_path(runtime.root, label),
                        "program": str(helper_programs[label]["program"])}

            def process_id(self, label):
                return {API: 101, **{key: 200 + index for index, key in enumerate(helper_programs)}}[label]

        runtime = argparse.Namespace(service={"pid": 101}, launchd=Launchd(), root=Path("/runtime"),
                                     executable=Path("/provider/container-apiserver"), verify=lambda: None)
        outputs = ["[]", "[]", "[]", '[{"reference":"unadmitted"}]']
        with (mock.patch.object(qualify, "run", side_effect=lambda *_a, **_k:
                                subprocess.CompletedProcess([], 0, outputs.pop(0), "")),
              mock.patch("runtime_services.process_inventory", return_value={
                  101: {"program": "/provider/container-apiserver"},
                  200: {"program": "/provider/core"}, 201: {"program": "/provider/machine"}})):
            with self.assertRaisesRegex(RuntimeError, "already contains images"):
                qualify.provider_quiescence(runtime, Path("/provider/container"), {}, helper_programs)


class TimeoutOwnershipTests(unittest.TestCase):
    def test_runner_can_recreate_disposable_output_without_losing_controller_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "evidence"
            suite_output = evidence / "docker"
            suite_output.mkdir(parents=True)
            (suite_output / "stale.json").write_text("stale")
            capture = qualify.controller_capture_directory(evidence, "docker", "cli")
            vscode_output = evidence / "vscode" / "docker"
            self.assertFalse(capture.is_relative_to(suite_output))
            self.assertFalse(capture.is_relative_to(vscode_output))
            script = (
                "import pathlib,shutil,sys; out=pathlib.Path(sys.argv[1]); "
                "shutil.rmtree(out); out.mkdir(parents=True); "
                "print('builder stdout'); print('builder stderr',file=sys.stderr); sys.exit(7)"
            )

            with mock.patch.object(qualify, "REPOSITORY", Path(__file__).resolve().parents[2]):
                outcome = qualify.command_outcome(
                    [sys.executable, "-c", script, str(suite_output)],
                    env=dict(os.environ), timeout=10, capture_directory=capture)

            self.assertEqual(outcome.returncode, 7)
            self.assertEqual((capture / "stdout.log").read_text(), "builder stdout\n")
            self.assertEqual((capture / "stderr.log").read_text(), "builder stderr\n")
            command = json.loads((capture / "controller-command.json").read_text())
            self.assertEqual(command["executable"], Path(sys.executable).name)
            self.assertEqual(command["exitCode"], 7)
            self.assertFalse(command["timedOut"])
            self.assertEqual(command["stdoutBytes"], len(b"builder stdout\n"))
            self.assertEqual(command["stderrBytes"], len(b"builder stderr\n"))
            self.assertFalse((suite_output / "stale.json").exists())

    def test_controller_capture_rejects_unknown_suite_or_lane(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            for lane, suite in (("unknown", "cli"), ("docker", "unknown")):
                with self.subTest(lane=lane, suite=suite):
                    with self.assertRaisesRegex(ValueError, "unknown parity lane or suite"):
                        qualify.controller_capture_directory(evidence, lane, suite)

    def test_timeout_sends_term_then_kill_to_the_new_session_process_group(self) -> None:
        process = mock.Mock()
        process.pid = 9123
        process.stdout = None
        process.stderr = None
        process.wait.side_effect = [subprocess.TimeoutExpired(["runner"], 1), None]
        with (mock.patch.object(qualify.os, "killpg") as killpg,
              mock.patch.object(qualify, "process_group_exists", side_effect=[True, False, False, False])):
            qualify.terminate_process_group(process, grace=0.01, reap_grace=0.01)
        self.assertEqual(killpg.call_args_list, [
            mock.call(9123, signal.SIGTERM), mock.call(9123, signal.SIGKILL)])

    def test_timeout_with_surviving_descendant_group_is_not_reported_clean(self) -> None:
        process = mock.Mock()
        process.pid = 8123
        process.stdout = None
        process.stderr = None
        process.wait.side_effect = [None]
        with (mock.patch.object(qualify.os, "killpg"),
              mock.patch.object(qualify, "process_group_exists", return_value=True),
              self.assertRaisesRegex(RuntimeError, "did not disappear")):
            qualify.terminate_process_group(process, grace=0, reap_grace=0)

    def test_timed_out_command_uses_new_session_and_retains_logs_without_success_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            process = mock.Mock()
            process.pid = 1234
            process.returncode = -15
            process.wait.side_effect = subprocess.TimeoutExpired(["runner"], 1)
            with (mock.patch.object(qualify.subprocess, "Popen", return_value=process) as popen,
                  mock.patch.object(qualify, "terminate_process_group", side_effect=RuntimeError("group remains")),
                  self.assertRaisesRegex(RuntimeError, "group remains")):
                qualify.command_outcome(["runner"], env={}, timeout=1, capture_directory=root / "logs")
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            self.assertTrue((root / "logs/stdout.log").is_file())
            self.assertFalse((root / "logs/controller-command.json").exists())

    def test_sigterm_is_converted_to_catchable_cleanup_exception_and_restored(self) -> None:
        path = Path(__file__).resolve().parents[2] / "Tools/testing/host_runtime.py"
        sys.path.insert(0, str(path.parent))
        spec = importlib.util.spec_from_file_location("qualification_host_runtime", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)
        previous = signal.getsignal(signal.SIGTERM)
        with runtime.cancellation():
            handler = signal.getsignal(signal.SIGTERM)
            with self.assertRaises(KeyboardInterrupt):
                handler(signal.SIGTERM, None)
        self.assertIs(signal.getsignal(signal.SIGTERM), previous)


class AdmissionBoundaryTests(unittest.TestCase):
    def test_provider_helper_hashes_come_from_validated_locked_receipt(self) -> None:
        sys.path.insert(0, str(REPOSITORY / "Tools/testing"))
        sys.path.insert(0, str(REPOSITORY / "Tools/bazel"))
        import prepare_releases
        import released_engine

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            retained = base / "workflow"
            for relative in ("release-objects", "prepared-releases", "prepared-receipts"):
                (retained / relative).mkdir(parents=True, mode=0o700)
            prepared_root = retained / "prepared-releases/key"
            provider_root = prepared_root / "Payload"
            core = provider_root / "libexec/container/plugins/container-core-images/bin/container-core-images"
            machine = provider_root / "libexec/container/plugins/machine-apiserver/bin/machine-apiserver"
            for path, data in ((core, b"locked core helper"), (machine, b"locked API helper")):
                path.parent.mkdir(parents=True, mode=0o700)
                path.write_bytes(data)
                path.chmod(0o755)
            container = provider_root / "bin/container"
            container.parent.mkdir(mode=0o700)
            container.write_bytes(b"provider CLI")
            container.chmod(0o755)
            preparation_sha = "d" * 64
            receipt_path = retained / "prepared-receipts" / f"{preparation_sha}.json"
            receipt_path.write_text("{}")
            asset = {"sha256": "c" * 64}
            specification = {"schemaVersion": 1, "assetSHA256": asset["sha256"],
                             "layout": {"format": "pkg"}}
            inventory = {
                "Payload/libexec/container/plugins/container-core-images/bin/container-core-images": {
                    "kind": "file", "mode": 0o755, "sha256": hashlib.sha256(core.read_bytes()).hexdigest()},
                "Payload/libexec/container/plugins/machine-apiserver/bin/machine-apiserver": {
                    "kind": "file", "mode": 0o755, "sha256": hashlib.sha256(machine.read_bytes()).hexdigest()},
            }
            arguments = argparse.Namespace(_guest_retained_root=retained, stock_container_bin=container)
            with (mock.patch.object(released_engine, "provider_runtime_selection", return_value=asset),
                  mock.patch.object(prepare_releases, "layout", return_value=specification["layout"]),
                  mock.patch.object(prepare_releases, "require_retained", return_value={
                      "root": str(prepared_root), "preparationSHA256": preparation_sha,
                      "inventorySHA256": "e" * 64}),
                  mock.patch.object(prepare_releases, "validate_prepared", return_value={"inventory": inventory})):
                with mock.patch.object(qualify, "REPOSITORY", REPOSITORY):
                    helpers = qualify.admit_provider_helper_programs("apple-stock", arguments)

            self.assertEqual(helpers["com.apple.container.container-core-images"],
                             {"program": core, "sha256": inventory[
                                 "Payload/libexec/container/plugins/container-core-images/bin/container-core-images"]["sha256"]})
            self.assertEqual(arguments._provider_helper_evidence["apple-stock"], {
                "assetSHA256": asset["sha256"], "preparationSHA256": preparation_sha,
                "preparedReceiptSHA256": qualify.sha256(receipt_path),
                "inventorySHA256": "e" * 64,
                "helperExecutables": {
                    "container-core-images": {
                        "path": "libexec/container/plugins/container-core-images/bin/container-core-images",
                        "sha256": inventory[
                            "Payload/libexec/container/plugins/container-core-images/bin/container-core-images"]["sha256"]},
                    "machine-apiserver": {
                        "path": "libexec/container/plugins/machine-apiserver/bin/machine-apiserver",
                        "sha256": inventory[
                            "Payload/libexec/container/plugins/machine-apiserver/bin/machine-apiserver"]["sha256"]},
                },
            })
            core.write_bytes(b"changed helper bytes")
            with (mock.patch.object(released_engine, "provider_runtime_selection", return_value=asset),
                  mock.patch.object(prepare_releases, "layout", return_value=specification["layout"]),
                  mock.patch.object(prepare_releases, "require_retained", return_value={
                      "root": str(prepared_root), "preparationSHA256": preparation_sha}),
                  mock.patch.object(prepare_releases, "validate_prepared", return_value={"inventory": inventory})):
                with mock.patch.object(qualify, "REPOSITORY", REPOSITORY):
                    with self.assertRaisesRegex(ValueError, "differ from the locked prepared package"):
                        qualify.admit_provider_helper_programs("apple-stock", arguments)

    def test_guest_assets_use_workflow_retained_not_finalized_retained(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            workflow_retained = root / "retained/workflow"
            workflow_retained.mkdir(parents=True)
            finalized_retained = root / "retained/devcontainer"
            finalized_retained.mkdir()
            compose = root / "devcontainer-compose"
            compose.write_bytes(b"admitted package compose")
            digest = qualify.sha256(compose)
            arguments = argparse.Namespace(
                _guest_retained_root=workflow_retained,
                retained_root=finalized_retained,
                finalized_directory=root / "finalized",
                provenance_sha256="a" * 64,
                source_commit="b" * 40,
                accepted_state=root / "state",
            )
            admission = {
                "scope": "finalized-native-package-runtime-input",
                "kind": "signed-notarized-native-package",
                "distributionReady": False,
                "sourceCommit": "b" * 40,
                "runtimeProfile": "stock",
                "providerLane": "apple-stock",
                "archiveSHA256": "c" * 64,
                "finalizationProvenanceSHA256": "a" * 64,
                "signatureInventorySHA256": "d" * 64,
                "executables": {"devcontainer-compose": str(compose)},
                "productionBinarySHA256": {"bin/devcontainer-compose": digest},
            }
            calls = []
            lane_loader = mock.Mock()
            lane_loader.load_finalized_admitter.return_value = (
                lambda **kwargs: {**admission, "providerLane": kwargs["provider_lane"]})
            with (mock.patch("owned_guest_fixture.preflight_guest_inputs",
                             side_effect=lambda _repo, lane, retained: (
                                 calls.append((lane, retained)), {"workload": "admitted"})[1]),
                  mock.patch.object(qualify, "load_run_lane", return_value=lane_loader),
                  mock.patch.object(qualify, "sha256", return_value=digest),
                  mock.patch.object(qualify, "admit_provider_helper_programs",
                                    return_value={"locked": "helper identities"})):
                result = qualify.admit_package_before_runtime(arguments)

            self.assertEqual([lane for lane, _root in calls],
                             ["docker", "apple-stock", "container-compose"])
            self.assertTrue(all(path == workflow_retained for _lane, path in calls))
            self.assertEqual(set(result), {"apple-stock", "container-compose"})

    def test_guest_asset_root_must_be_private_internal_and_canonical(self) -> None:
        with tempfile.TemporaryDirectory(dir=ACCOUNT_HOME) as temporary:
            root = Path(temporary).resolve() / "workflow"
            root.mkdir(mode=0o700)
            self.assertEqual(qualify.validate_guest_asset_retained_root(root), root)
            root.chmod(0o755)
            with self.assertRaisesRegex(ValueError, "private internal workflow storage"):
                qualify.validate_guest_asset_retained_root(root)

    def test_package_admission_failure_happens_before_evidence_or_runtime_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            app = root / "VS Code.app"
            code = app / "Contents/MacOS/Code"
            code.parent.mkdir(parents=True)
            code.write_text("launcher")
            vsix = root / "extension.vsix"
            vsix.write_bytes(b"fixture")
            evidence = root / "new-evidence"
            values = argparse.Namespace(
                repository=root, ssd_root=root, retained_root=root,
                qualification_directory=root, evidence=evidence, campaign="test",
                source_commit="a" * 40, finalized_directory=root,
                provenance_sha256="b" * 64, state_sha256="c" * 64,
                accepted_state=root, docker_bin=root, docker_compose_bin=root,
                stock_container_bin=root, compose_container_bin=root,
                compose_provider_bin=root, colima_bin=root, vscode_bin=root,
                vscode_app=app, vscode_vsix=vsix, stock_container_sha256="d" * 64,
                stock_api_sha256="e" * 64, compose_container_sha256="f" * 64,
                compose_api_sha256="0" * 64, compose_provider_sha256="1" * 64,
                colima_sha256="2" * 64, execute=True,
            )
            manifest = {"referencePins": {"vscode": {"devContainersExtension": {"vsixSHA256": "3" * 64}}}}
            with (mock.patch.object(qualify, "parse_args", return_value=values),
                  mock.patch.object(qualify, "REPOSITORY", REPOSITORY),
                  mock.patch.object(qualify, "validate_inputs", return_value={}),
                  mock.patch.object(qualify, "sha256", return_value="3" * 64),
                  mock.patch.object(qualify, "admit_package_before_runtime",
                                    side_effect=ValueError("package rejected")),
                  mock.patch.object(qualify.subprocess, "check_output") as host_command,
                  self.assertRaisesRegex(ValueError, "package rejected")):
                values._manifest = manifest
                qualify.main()
            self.assertFalse(evidence.exists())
            host_command.assert_not_called()


class ProviderPinTests(unittest.TestCase):
    def test_provider_receipt_binds_observed_buildx_version_and_binary_hash(self) -> None:
        manifest_path = Path(__file__).resolve().parents[2] / "Tests/Parity/manifest.json"
        manifest = json.loads(manifest_path.read_text())
        docker_pins = manifest["referencePins"]["docker"]
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary).resolve()
            qualify.write_json(evidence / "docker-runtime-identity.json", {
                "clientVersion": docker_pins["cliVersion"],
                "engineVersion": docker_pins["engineVersion"],
                "engineCommit": docker_pins["engineCommit"],
                "engineApiVersion": docker_pins["engineApiVersion"],
                "dockerdSHA256": docker_pins["engineSHA256"],
                "composeVersion": docker_pins["composeVersion"],
                "buildxVersion": docker_pins["buildxVersion"],
                "buildxSHA256": docker_pins["buildxSHA256"],
            })
            hashes = {"docker": docker_pins["cliSHA256"],
                      "dockerBuildx": docker_pins["buildxSHA256"],
                      "dockerCompose": docker_pins["composeSHA256"],
                      "stockContainer": "1" * 64, "stockAPIServer": "2" * 64,
                      "composeContainer": "3" * 64, "composeAPIServer": "4" * 64,
                      "composeProvider": "5" * 64, "colima": "6" * 64,
                      "vscode": "7" * 64}
            args = argparse.Namespace(_manifest=manifest, _provider_hashes=hashes,
                                      colima_bin=Path("/pinned/colima"),
                                      _provider_helper_evidence={
                                          lane: {"assetSHA256": "8" * 64,
                                                 "preparationSHA256": "9" * 64,
                                                 "preparedReceiptSHA256": "a" * 64,
                                                 "inventorySHA256": "b" * 64,
                                                 "helperExecutables": {
                                                     "container-core-images": {
                                                         "path": "libexec/container/plugins/container-core-images/bin/container-core-images",
                                                         "sha256": "c" * 64},
                                                     "machine-apiserver": {
                                                         "path": "libexec/container/plugins/machine-apiserver/bin/machine-apiserver",
                                                         "sha256": "d" * 64},
                                                 }}
                                          for lane in ("apple-stock", "container-compose")})
            colima = subprocess.CompletedProcess(["colima", "--version"], 0, "colima 0.10.3\n", "")
            with mock.patch.object(qualify, "run", return_value=colima):
                providers = qualify.make_provider_tools(args, evidence)
            self.assertEqual(providers["docker"]["buildxVersion"], "0.37.1")
            self.assertEqual(providers["docker"]["buildxSHA256"], hashes["dockerBuildx"])
            docker_evidence = json.loads((evidence / "providers/docker.json").read_text())
            self.assertEqual(docker_evidence["buildxVersion"], "0.37.1")
            self.assertEqual(docker_evidence["buildxSHA256"], hashes["dockerBuildx"])

    def test_buildx_hash_mismatch_is_rejected_before_version_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary).resolve() / "docker-buildx"
            executable.write_text("pinned fixture")
            executable.chmod(0o755)
            pins = {"buildxSHA256": "a" * 64, "buildxVersion": "0.37.1"}
            with (mock.patch.object(qualify, "sha256", return_value="b" * 64),
                  mock.patch.object(qualify, "docker_buildx_version") as version,
                  self.assertRaisesRegex(ValueError, "bytes differ")):
                qualify.admit_docker_buildx(executable, pins)
            version.assert_not_called()

    def test_buildx_symlink_is_rejected_as_noncanonical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            executable = root / "docker-buildx"
            alias = root / "buildx"
            executable.write_text("pinned fixture")
            executable.chmod(0o755)
            alias.symlink_to(executable)
            with (mock.patch.object(qualify, "sha256") as digest,
                  self.assertRaisesRegex(ValueError, "canonical absolute path")):
                qualify.admit_docker_buildx(alias, {"buildxSHA256": "a" * 64,
                                                    "buildxVersion": "0.37.1"})
            digest.assert_not_called()

    def test_buildx_version_mismatch_is_rejected_after_hash_admission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary).resolve() / "docker-buildx"
            executable.write_text("pinned fixture")
            executable.chmod(0o755)
            pins = {"buildxSHA256": "a" * 64, "buildxVersion": "0.37.1"}
            with (mock.patch.object(qualify, "sha256", return_value="a" * 64),
                  mock.patch.object(qualify, "docker_buildx_version", return_value="0.37.2"),
                  self.assertRaisesRegex(ValueError, "version differs")):
                qualify.admit_docker_buildx(executable, pins)

    def test_isolated_docker_config_contains_only_explicit_compose_and_buildx(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            compose = root / "docker-compose"
            buildx = root / "docker-buildx"
            compose.write_text("compose")
            buildx.write_text("buildx")
            args = argparse.Namespace(docker_compose_bin=compose, docker_buildx_bin=buildx)

            config = qualify.create_docker_cli_config(root / "evidence", args)

            plugins = config / "cli-plugins"
            self.assertEqual(sorted(path.name for path in plugins.iterdir()),
                             ["docker-buildx", "docker-compose"])
            self.assertEqual((plugins / "docker-buildx").resolve(), buildx)
            self.assertEqual((plugins / "docker-compose").resolve(), compose)

    def test_buildx_version_is_read_from_the_explicit_executable(self) -> None:
        executable = Path("/opt/homebrew/Cellar/docker-buildx/0.37.1/bin/docker-buildx")
        result = subprocess.CompletedProcess([str(executable), "version"], 0,
                                            "github.com/docker/buildx v0.37.1 Homebrew\n", "")
        with mock.patch.object(qualify, "run", return_value=result) as run:
            self.assertEqual(qualify.docker_buildx_version(executable), "0.37.1")
        run.assert_called_once_with([str(executable), "version"],
                                    env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                                    timeout=10, capture=True)

    def test_buildx_version_rejects_unparseable_output(self) -> None:
        with mock.patch.object(qualify, "run", return_value=subprocess.CompletedProcess(
                ["buildx", "version"], 0, "Buildx development build\n", "")):
            with self.assertRaisesRegex(ValueError, "parseable semantic version"):
                qualify.docker_buildx_version(Path("/bin/docker-buildx"))

    def test_stopped_colima_needs_no_docker_context_or_existing_socket(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            profile = home / ".colima/default"
            profile.mkdir(parents=True)
            with mock.patch.object(qualify, "run") as command:
                endpoint = qualify.default_colima_endpoint(home)
            self.assertEqual(endpoint, "unix://" + str(profile / "docker.sock"))
            self.assertFalse((profile / "docker.sock").exists())
            self.assertFalse((home / ".docker/contexts").exists())
            command.assert_not_called()

    def test_colima_endpoint_rejects_aliased_or_writable_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            parent = home / ".colima"
            parent.mkdir()
            target = home / "other-profile"
            target.mkdir()
            profile = parent / "default"
            profile.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "canonical"):
                qualify.default_colima_endpoint(home)
            profile.unlink()
            profile.mkdir(mode=0o777)
            profile.chmod(0o777)
            with self.assertRaisesRegex(ValueError, "protected"):
                qualify.default_colima_endpoint(home)

    def test_provider_versions_and_commits_must_appear_in_actual_json_output(self) -> None:
        pin = {"stableVersion": "1.2.3", "stableCommit": "a" * 40}
        self.assertTrue(qualify.output_contains_pin(
            json.dumps({"version": "1.2.3", "commit": "a" * 40}), pin))
        self.assertFalse(qualify.output_contains_pin(
            json.dumps({"version": "1.2.4", "commit": "a" * 40}), pin))


if __name__ == "__main__":
    unittest.main()
