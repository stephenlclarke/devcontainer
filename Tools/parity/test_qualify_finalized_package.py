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
# Positive socket fixtures need a short parent independent of ambient TMPDIR.
# This Mac must keep transient fixtures on its enrolled SSD; hosted CI can use /tmp.
NATIVE_SOCKET_TMPDIR = Path(os.environ.get("DEVCONTAINER_TEST_SCRATCH_ROOT", "/tmp")).resolve()
if sys.platform == "darwin" and (Path("/Volumes/SSD").exists() or os.environ.get("GITHUB_ACTIONS") != "true"):
    NATIVE_SOCKET_TMPDIR = Path(os.environ.get("DEVCONTAINER_TEST_SCRATCH_ROOT", "/Volumes/SSD/q")).resolve()
    if not NATIVE_SOCKET_TMPDIR.is_relative_to("/Volumes/SSD"):
        raise ValueError("Native socket fixtures on this Mac require enrolled SSD scratch")
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
    def test_failed_native_diagnostic_continues_only_after_preflight_and_cli_restoration(self) -> None:
        restored = {"status": "restored", "cliCleanupComplete": True}
        self.assertTrue(qualify.may_continue_native_diagnostic_lane(True, restored))
        for preflight, prior in (
                (False, restored),
                (True, {"status": "uncertain", "cliCleanupComplete": True}),
                (True, {"status": "restored", "cliCleanupComplete": False}),
                (True, {"status": "restored"})):
            with self.subTest(preflight=preflight, prior=prior):
                self.assertFalse(qualify.may_continue_native_diagnostic_lane(preflight, prior))

    def test_init_io_trace_is_limited_to_one_e07_native_diagnostic(self) -> None:
        qualify.validate_init_io_trace_request(True, ("E07-init-attachment",), None)
        for fixtures, component in (
                (None, None),
                (("C03-compose-resources", "E07-init-attachment"), None),
                (("E07-init-attachment",), "E13-compose-signals")):
            with self.subTest(fixtures=fixtures, component=component), self.assertRaisesRegex(
                    ValueError, "only the E07"):
                qualify.validate_init_io_trace_request(True, fixtures, component)

    def test_warm_d05_cache_is_limited_to_the_single_native_diagnostic(self) -> None:
        qualify.validate_d05_cache_request("cold", None, None)
        qualify.validate_d05_cache_request("warm", ("D05-features",), None)
        for state, fixtures, component in (
                ("warm", None, None),
                ("warm", ("C03-compose-resources", "D05-features"), None),
                ("warm", ("D05-features",), "E13-compose-signals"),
                ("other", ("D05-features",), None)):
            with self.subTest(state=state, fixtures=fixtures, component=component), self.assertRaisesRegex(
                    ValueError, "warm D05 cache mode" if state == "warm" else "cache state"):
                qualify.validate_d05_cache_request(state, fixtures, component)

    def test_warm_native_diagnostic_command_uses_tool_runner_and_keeps_product_checkout(self) -> None:
        args = argparse.Namespace(
            diagnostic_fixtures=("D05-features",), d05_cache_state="warm",
            candidate_invocation="candidate", source_commit="a" * 40,
            finalized_directory=None, provenance_sha256=None, state_sha256=None, accepted_state=None,
        )
        cli, vscode = qualify.lane_commands(args, "apple-stock", Path("/evidence"))
        self.assertEqual(Path(cli[1]), qualify.CONTROLLER.parent / "run_lane.py")
        self.assertIn(str(qualify.REPOSITORY), cli)
        self.assertIn("--d05-cache-state", cli)
        self.assertIn("warm", cli)
        self.assertNotIn("--d05-cache-state", vscode)

    def test_trace_native_diagnostic_command_passes_explicit_e07_opt_in(self) -> None:
        args = argparse.Namespace(
            diagnostic_fixtures=("E07-init-attachment",), trace_init_io=True,
            candidate_invocation="candidate", source_commit="a" * 40,
            finalized_directory=None, provenance_sha256=None, state_sha256=None, accepted_state=None,
        )
        cli, _ = qualify.lane_commands(args, "apple-stock", Path("/evidence"))
        self.assertIn("--trace-init-io", cli)

    def test_unsigned_candidate_mode_cannot_enter_full_or_finalized_qualification(self) -> None:
        fixtures = ("C03-compose-resources", "E07-init-attachment")
        self.assertTrue(qualify.validate_package_mode(
            candidate_invocation="candidate", diagnostic_fixtures=fixtures,
            finalized_values=(None, None, None, None)))
        for candidate, selected, finalized in (
                ("candidate", None, (None, None, None, None)),
                ("candidate", fixtures, (Path("/final"), None, None, None)),
                (None, None, (None, None, None, None))):
            with self.subTest(candidate=candidate, selected=selected, finalized=finalized), self.assertRaises(
                    ValueError):
                qualify.validate_package_mode(candidate_invocation=candidate,
                                              diagnostic_fixtures=selected,
                                              finalized_values=finalized)
        self.assertFalse(qualify.validate_package_mode(
            candidate_invocation=None, diagnostic_fixtures=fixtures,
            finalized_values=(Path("/final"), "a" * 64, "b" * 64, Path("/state"))))

    def test_diagnostic_requires_the_same_exact_finalized_package_proof(self) -> None:
        proof = {"sourceCommit": "a" * 40, "trustedStateSHA256": "b" * 64,
                 "archiveSHA256": "c" * 64}
        admissions = {"apple-stock": {"archiveSHA256": "c" * 64}}
        qualify.validate_finalized_package_proof(proof, "a" * 40, "b" * 64, admissions)
        for altered in ({}, {**proof, "sourceCommit": "d" * 40},
                        {**proof, "trustedStateSHA256": "e" * 64},
                        {**proof, "archiveSHA256": "f" * 64}):
            with self.subTest(altered=altered), self.assertRaisesRegex(
                    ValueError, "exact accepted archive and source"):
                qualify.validate_finalized_package_proof(altered, "a" * 40, "b" * 64, admissions)

    def test_tooling_identity_is_separate_and_requires_clean_committed_controller(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary).resolve()
            controller = repository / "Tools/parity/qualify_finalized_package.py"
            controller.parent.mkdir(parents=True)
            controller.write_text("# committed diagnostic controller\n")
            subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
            subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repository, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repository, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repository, check=True)
            subprocess.run(["git", "add", "Tools/parity/qualify_finalized_package.py"],
                           cwd=repository, check=True)
            subprocess.run(["git", "commit", "-qm", "test: seed controller fixture"],
                           cwd=repository, check=True)
            identity = qualify.tooling_source_identity(controller)
            self.assertEqual(identity["repository"], str(repository))
            self.assertEqual(identity["controllerSHA256"], qualify.sha256(controller))
            controller.write_text("# uncommitted change\n")
            with self.assertRaisesRegex(ValueError, "tooling checkout must be clean"):
                qualify.tooling_source_identity(controller)

    def test_native_diagnostic_selection_is_finite_unique_and_separate(self) -> None:
        selected = ("C03-compose-resources", "D05-features", "E07-init-attachment")
        self.assertEqual(qualify.diagnostic_fixture_selection(list(selected), None), selected)
        self.assertIsNone(qualify.diagnostic_fixture_selection(None, None))
        for values, component in (([], None), ([selected[0], selected[0]], None),
                                  (["E01-engine-negotiation"], None), ([selected[0]], "E13-compose-signals")):
            with self.subTest(values=values, component=component), self.assertRaisesRegex(
                    ValueError, "Native diagnostic fixtures"):
                qualify.diagnostic_fixture_selection(values, component)

    def test_native_diagnostic_runs_only_the_selected_cli_fixtures(self) -> None:
        fixtures = ("C03-compose-resources", "D05-features")
        environment = {"PATH": "/usr/bin", "DOCKER_HOST": "unused"}
        self.assertEqual(qualify.reference_fixture_environment(environment, fixtures), {
            **environment, "DEVCONTAINER_PARITY_FIXTURES": "C03-compose-resources,D05-features"})
        calls = []
        cli = subprocess.CompletedProcess(["cli"], 0, "", "")
        actual_cli, actual_vscode, passed = qualify.run_suite_pair(
            lambda: (calls.append("cli"), cli)[1], lambda: calls.append("vscode"),
            lambda: True, lambda: True, diagnostic_fixtures=fixtures)
        self.assertEqual(calls, ["cli"])
        self.assertIs(actual_cli, cli)
        self.assertIsNone(actual_vscode)
        self.assertTrue(passed)
        with self.assertRaisesRegex(ValueError, "Unsupported native diagnostic"):
            qualify.reference_fixture_environment(environment, ("C03-compose-resources", "E01"))

    def test_diagnostic_preflight_never_invokes_docker_and_full_mode_keeps_ordered_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary).resolve() / "evidence"
            evidence.mkdir(mode=0o700)
            cleanup = {lane: {"status": "not-started"} for lane in qualify.LANES}
            docker = mock.Mock()
            with mock.patch.object(qualify, "preflight_native_api_startup") as preflight:
                qualify.preflight_selected_runtime_lanes(
                    argparse.Namespace(), evidence, cleanup, docker,
                    ("C03-compose-resources", "D05-features"))
            self.assertEqual([call.args[1] for call in preflight.call_args_list],
                             ["apple-stock", "container-compose"])
            docker.assert_not_called()
            with mock.patch.object(qualify, "preflight_native_providers_before_docker") as full:
                qualify.preflight_selected_runtime_lanes(
                    argparse.Namespace(), evidence, cleanup, docker, None)
            full.assert_called_once_with(mock.ANY, evidence, cleanup, docker)

    def test_diagnostic_reference_requires_exact_sha_and_is_retained_separately(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            original = root / "docker-reference.json"
            original.write_bytes(b'{"status":"passed","source":"original"}\n')
            digest = qualify.sha256(original)
            reference = qualify.validate_diagnostic_reference(original, digest)
            evidence = root / "evidence"
            evidence.mkdir(mode=0o700)
            retained = qualify.retain_diagnostic_reference(evidence, reference)
            self.assertEqual(retained["scope"], "separate-reference-input")
            self.assertEqual(qualify.sha256(Path(retained["retainedPath"])), digest)
            self.assertFalse((evidence / "docker" / "results.json").exists())
            with self.assertRaisesRegex(ValueError, "both its exact file path"):
                qualify.validate_diagnostic_reference(original, None)
            with self.assertRaisesRegex(ValueError, "differs from its independently supplied SHA"):
                qualify.validate_diagnostic_reference(original, "0" * 64)

    def test_diagnostic_receipt_cannot_claim_release_or_comparison_authority(self) -> None:
        args = argparse.Namespace(source_commit="a" * 40, _source_tree="b" * 40,
                                  _parity_harness_sha256="c" * 64,
                                  _tooling_identity={"commit": "1" * 40, "tree": "2" * 40,
                                                     "controllerSHA256": "3" * 64},
                                  provenance_sha256="d" * 64, state_sha256="e" * 64,
                                  _component_package_proof={"archiveSHA256": "f" * 64},
                                  diagnostic_fixtures=("C03-compose-resources",))
        cleanup = {"docker": {"status": "not-started"}}
        cleanup.update({lane: {"status": "restored"} for lane in ("apple-stock", "container-compose")})
        payload = qualify.diagnostic_result_payload(
            args, cleanup, {"status": "restored"}, {}, None, "passed", [])
        self.assertEqual(payload["scope"], "native-only-fixture-diagnostic")
        self.assertFalse(payload["releaseQualified"])
        self.assertFalse(payload["releaseAuthority"])
        self.assertEqual(payload["dockerOracle"], "not-run")
        self.assertEqual(payload["comparison"], "not-run")
        self.assertNotIn("docker", payload["laneCleanup"])
        args.candidate_invocation = "candidate"
        args._candidate_receipt = {"runtimeProfile": "stock", "commit": "a" * 40,
                                   "archiveSHA256": "f" * 64}
        args._candidate_admissions = {"apple-stock": {"scope": "local-candidate-integration-only"}}
        candidate_payload = qualify.diagnostic_result_payload(
            args, cleanup, {"status": "restored"}, {}, None, "passed", [])
        self.assertEqual(candidate_payload["package"]["kind"], "unsigned-native-candidate")
        self.assertFalse(candidate_payload["package"]["signatureVerified"])
        self.assertFalse(candidate_payload["releaseQualified"])
        self.assertNotIn("finalizationProvenanceSHA256", candidate_payload["package"])

    def test_native_diagnostic_restoration_requires_both_native_lanes_and_no_docker(self) -> None:
        cleanup = {"docker": {"status": "not-started"}}
        cleanup.update({lane: {"status": "restored", "cliCleanupComplete": True,
                              "vscodeCleanupComplete": False, "vscodeStatus": "skipped"}
                       for lane in ("apple-stock", "container-compose")})
        host = {"status": "restored", "hostGuardCleared": True,
                "initialColima": "stopped", "finalColima": "stopped",
                "initialServiceSetSHA256": "same", "finalServiceSetSHA256": "same",
                "initialServiceCount": 8, "finalServiceCount": 8}
        self.assertTrue(qualify.native_diagnostic_restoration_is_complete(cleanup, host))
        cleanup["docker"]["status"] = "restored"
        self.assertFalse(qualify.native_diagnostic_restoration_is_complete(cleanup, host))
        cleanup["docker"]["status"] = "not-started"
        cleanup["container-compose"]["status"] = "uncertain"
        self.assertFalse(qualify.native_diagnostic_restoration_is_complete(cleanup, host))

    def test_candidate_lane_evidence_must_match_exact_unsigned_admission(self) -> None:
        identity = {"scope": "local-candidate-integration-only",
                    "candidateInvocation": "candidate", "candidateReceiptSHA256": "a" * 64,
                    "sourceCommit": "b" * 40, "runtimeProfile": "stock",
                    "assetSHA256": "c" * 64, "preparationSHA256": "d" * 64,
                    "inventorySHA256": "e" * 64, "dependencyLockSHA256": "f" * 64,
                    "executables": {"devcontainer": "/candidate/devcontainer"},
                    "referenceRuntime": {"lockSHA256": "1" * 64}}
        with tempfile.TemporaryDirectory() as temporary:
            result = Path(temporary) / "results.json"
            result.write_text(json.dumps({"unsignedCandidate": identity}))
            qualify.validate_candidate_lane_identity(result, "apple-stock", identity)
            result.write_text(json.dumps({"unsignedCandidate": {**identity, "sourceCommit": "2" * 40}}))
            with self.assertRaisesRegex(RuntimeError, "does not bind"):
                qualify.validate_candidate_lane_identity(result, "apple-stock", identity)
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


    def test_e14_component_runs_cli_only_and_selects_exact_fixture(self) -> None:
        fixture = "E14-compose-terminal-size"
        self.assertIn(fixture, qualify.COMPONENT_FIXTURES)
        calls = []
        cli = subprocess.CompletedProcess(["cli"], 0, "", "")
        actual_cli, actual_vscode, passed = qualify.run_suite_pair(
            lambda: (calls.append("cli"), cli)[1],
            lambda: calls.append("vscode"), lambda: True, lambda: True,
            component_fixture=fixture)
        self.assertEqual(calls, ["cli"])
        self.assertIs(actual_cli, cli)
        self.assertIsNone(actual_vscode)
        self.assertTrue(passed)
        self.assertEqual(qualify.selected_fixture_environment({"PATH": "/usr/bin"}, fixture),
                         {"PATH": "/usr/bin", "DEVCONTAINER_PARITY_FIXTURES": fixture})

    def test_e04_component_runs_cli_only_and_selects_exact_legacy_fixture(self) -> None:
        fixture = "E04-image-build"
        self.assertIn(fixture, qualify.COMPONENT_FIXTURES)
        calls = []
        cli = subprocess.CompletedProcess(["cli"], 0, "", "")
        actual_cli, actual_vscode, passed = qualify.run_suite_pair(
            lambda: (calls.append("cli"), cli)[1],
            lambda: calls.append("vscode"), lambda: True, lambda: True,
            component_fixture=fixture)
        self.assertEqual(calls, ["cli"])
        self.assertIs(actual_cli, cli)
        self.assertIsNone(actual_vscode)
        self.assertTrue(passed)
        self.assertEqual(qualify.selected_fixture_environment({"PATH": "/usr/bin"}, fixture),
                         {"PATH": "/usr/bin", "DEVCONTAINER_PARITY_FIXTURES": fixture})

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
                                      _component_package_proof={"archiveSHA256": "e" * 64},
                                      component_fixture=qualify.COMPONENT_FIXTURE)
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
                                  "signalStream": stream(1),
                                  "signalContract": {"modeVersion": 2, "tty": True, "openStdin": False}}]}))
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
    @staticmethod
    def _native_preflight_fixture(root: Path):
        scratch = root / "ssd"
        retained = root / "retained"
        evidence = root / "evidence"
        for directory in (scratch, retained, evidence):
            directory.mkdir(mode=0o700)
        providers = {}
        hashes = {}
        for lane, prefix in (("apple-stock", "a"), ("container-compose", "b")):
            payload = root / "active-runtimes" / lane / "payload"
            bin_dir = payload / "bin"
            bin_dir.mkdir(parents=True, mode=0o700)
            container = bin_dir / "container"
            api = bin_dir / "container-apiserver"
            container.write_bytes((prefix + "-container").encode())
            api.write_bytes((prefix + "-api").encode())
            providers[lane] = {
                "asset": {"sha256": prefix * 64, "commit": prefix * 40},
                "prepared": {"preparationSHA256": ("c" if prefix == "a" else "d") * 64,
                             "inventorySHA256": ("e" if prefix == "a" else "f") * 64},
                "active": {"root": str(payload), "executables": {
                    "container": str(container), "container-apiserver": str(api)},
                    "inventorySHA256": ("1" if prefix == "a" else "2") * 64,
                    "activation": {"receiptSHA256": ("3" if prefix == "a" else "4") * 64}}}
            cli_key = "stockContainer" if lane == "apple-stock" else "composeContainer"
            api_key = "stockAPIServer" if lane == "apple-stock" else "composeAPIServer"
            hashes[cli_key], hashes[api_key] = qualify.sha256(container), qualify.sha256(api)
        args = argparse.Namespace(
            _active_provider_runtimes=providers, _provider_hashes=hashes,
            campaign="preflight-fixture", source_commit="1" * 40)
        return scratch, retained, evidence, args

    def test_native_api_preflight_socket_fits_physical_ssd_and_rejects_utf8_length(self) -> None:
        suffix = "/container/engine-provider/provider.sock"
        ascii_length = 103 - len(os.fsencode(suffix)) - 1
        self.assertEqual(len(os.fsencode(qualify.require_native_api_preflight_socket_path(
            Path("/" + "x" * ascii_length)))), 103)
        with self.assertRaisesRegex(ValueError, "provider socket exceeds"):
            qualify.require_native_api_preflight_socket_path(Path("/" + "x" * (ascii_length + 1)))
        utf8_root = Path("/" + "é" * 10 + "x" * (ascii_length - 20))
        self.assertEqual(len(os.fsencode(qualify.require_native_api_preflight_socket_path(utf8_root))), 103)
        with self.assertRaisesRegex(ValueError, "provider socket exceeds"):
            qualify.require_native_api_preflight_socket_path(
                Path("/" + "é" * 10 + "x" * (ascii_length - 19)))

        for lane in ("apple-stock", "container-compose"):
            root = Path(f"/Volumes/SSD/cf/bazel/api-{lane}-abcdefgh")
            socket = qualify.require_native_api_preflight_socket_path(root)
            self.assertLess(len(os.fsencode(socket)), 104)
            self.assertEqual(socket, root / "container/engine-provider/provider.sock")

        qualify.require_native_provider_socket_layouts(Path("/Volumes/SSD/cf/bazel/abc"))
        with self.assertRaisesRegex(ValueError, "provider socket exceeds"):
            qualify.require_native_api_preflight_socket_path(
                Path("/Volumes/SSD/cf/bazel/abc/native-parity-container-compose-abcdefgh"))

        for root in (Path("/Volumes/SSD/cf/bazel/" + "x" * 32 + "/api-apple-stock-abcdefgh"),
                     Path("/Volumes/SSD/cf/bazel/" + "é" * 16 + "/api-apple-stock-abcdefgh")):
            with self.subTest(root=root), self.assertRaisesRegex(ValueError, "provider socket exceeds"):
                qualify.require_native_api_preflight_socket_path(root)

    def test_oversized_native_api_preflight_root_fails_before_host_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            _, retained, evidence, args = self._native_preflight_fixture(base)
            scratch = base / ("é" * 32) / "ssd"
            scratch.mkdir(parents=True, mode=0o700)
            cleanup = {lane: {"status": "not-started"} for lane in qualify.LANES}
            with (mock.patch.object(qualify, "SSD", scratch),
                  mock.patch.object(qualify, "JOURNAL_PARENT", retained),
                  mock.patch("runtime_services.ControlledRuntime") as runtime,
                  mock.patch("private_keychain.run_keychain") as keychain):
                with self.assertRaisesRegex(RuntimeError, "setup failed.*provider socket exceeds"):
                    qualify.preflight_native_api_startup(args, "container-compose", evidence, cleanup)
            runtime.assert_not_called()
            keychain.assert_not_called()
            record = json.loads((evidence / "container-compose-startup-preflight.json").read_text())
            self.assertEqual(record["serviceState"], "not-started")
            self.assertTrue(record["serviceRestored"])
            self.assertFalse((Path(record["scratchRoot"]) / "owner.json").exists())
            self.assertEqual(list(Path(record["scratchRoot"]).iterdir()), [])

    def test_native_provider_layouts_reject_long_root_before_docker_work(self) -> None:
        with self.assertRaisesRegex(ValueError, "provider socket exceeds"):
            qualify.require_native_provider_socket_layouts(
                Path("/Volumes/SSD/cf/bazel/" + "é" * 16 + "/deeper"))

    def test_native_parity_root_rejects_actual_oversize_before_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            allocated = base / ("é" * 24) / "par-container-compose-abcdefgh"
            allocated.mkdir(parents=True, mode=0o700)
            args = argparse.Namespace(campaign="socket-bound", source_commit="1" * 40)
            with (mock.patch.object(qualify.tempfile, "mkdtemp", return_value=str(allocated)),
                  mock.patch("runtime_services.ControlledRuntime") as runtime,
                  mock.patch("private_keychain.run_keychain") as keychain):
                with self.assertRaisesRegex(ValueError, "provider socket exceeds"):
                    qualify.apple_lane(args, "container-compose", base, base / "api", {}, {})
            runtime.assert_not_called()
            keychain.assert_not_called()
            self.assertFalse(allocated.exists())

    def test_native_startup_preflights_both_providers_before_docker_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            evidence = root / "evidence"
            evidence.mkdir(mode=0o700)
            cleanup = {lane: {"status": "not-started"} for lane in qualify.LANES}
            calls = []

            def lane_probe(_args, lane, _evidence, _cleanup):
                calls.append(lane)
                return {"status": "passed", "lane": lane}

            with mock.patch.object(qualify, "preflight_native_api_startup", side_effect=lane_probe):
                qualify.preflight_native_providers_before_docker(
                    argparse.Namespace(), evidence, cleanup, lambda: calls.append("docker"))
            self.assertEqual(calls, ["apple-stock", "container-compose", "docker"])

            calls.clear()

            def failed_probe(_args, lane, _evidence, _cleanup):
                calls.append(lane)
                raise RuntimeError("preflight failed")

            failed_arguments = argparse.Namespace()
            failed_evidence = evidence / "failed"
            docker_callback = lambda: calls.append("docker")
            with mock.patch.object(qualify, "preflight_native_api_startup", side_effect=failed_probe):
                with self.assertRaisesRegex(RuntimeError, "preflight failed"):
                    qualify.preflight_native_providers_before_docker(
                        failed_arguments, failed_evidence, cleanup, docker_callback)
            self.assertEqual(calls, ["apple-stock"])

    def test_native_api_preflight_restores_service_after_bounded_ready_probe(self) -> None:
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory(dir=NATIVE_SOCKET_TMPDIR) as temporary:
            root = Path(temporary).resolve()
            scratch = root / "ssd"
            scratch.mkdir(mode=0o700)
            retained = root / "retained"
            retained.mkdir(mode=0o700)
            evidence = root / "evidence"
            evidence.mkdir(mode=0o700)
            binaries = {}
            providers = {}
            for lane, prefix in (("apple-stock", "a"), ("container-compose", "b")):
                payload = root / "active-runtimes" / lane / "payload"
                bin_dir = payload / "bin"
                bin_dir.mkdir(parents=True, mode=0o700)
                container = bin_dir / "container"
                api = bin_dir / "container-apiserver"
                container.write_bytes((prefix + "-container").encode())
                api.write_bytes((prefix + "-api").encode())
                api_key = "stockAPIServer" if lane == "apple-stock" else "composeAPIServer"
                cli_key = "stockContainer" if lane == "apple-stock" else "composeContainer"
                binaries[api_key] = qualify.sha256(api)
                binaries[cli_key] = qualify.sha256(container)
                providers[lane] = {
                    "asset": {"sha256": prefix * 64, "commit": prefix * 40},
                    "prepared": {"preparationSHA256": ("c" if prefix == "a" else "d") * 64,
                                 "inventorySHA256": ("e" if prefix == "a" else "f") * 64},
                    "active": {"root": str(payload),
                               "executables": {"container": str(container),
                                               "container-apiserver": str(api)},
                               "inventorySHA256": ("1" if prefix == "a" else "2") * 64,
                               "activation": {"receiptSHA256": ("3" if prefix == "a" else "4") * 64}}}

            events = []

            class FakeRuntime:
                def __init__(self, case_root, owner, executable, journal_parent, *, home):
                    self.root, self.owner, self.executable = case_root, owner, executable
                    self.switch = None
                    self.host_mutation_started = False
                    self.journal = object()

                def start(self, *, prepare_home):
                    events.append("start")
                    prepare_home(self.journal)
                    self.switch = object()
                    self.host_mutation_started = True

                def verify(self):
                    events.append("verify")

                def restore(self):
                    events.append("restore")

                def preserve_logs(self):
                    events.append("preserve")

                def receipt(self):
                    return {"ownerSHA256": "a" * 64, "records": 2, "seal": "b" * 64}

            args = argparse.Namespace(_active_provider_runtimes=providers,
                                      _provider_hashes=binaries,
                                      campaign="preflight-fixture", source_commit="f" * 40)
            cleanup = {lane: {"status": "not-started"} for lane in qualify.LANES}
            with (mock.patch.object(qualify, "SSD", scratch),
                  mock.patch.object(qualify, "JOURNAL_PARENT", retained),
                  mock.patch.object(qualify, "ACCOUNT_HOME", root),
                  mock.patch("runtime_services.ControlledRuntime", FakeRuntime),
                  mock.patch("private_keychain.run_keychain",
                             side_effect=lambda _root, action, _journal: events.append(action))):
                record = qualify.preflight_native_api_startup(args, "apple-stock", evidence, cleanup)

            self.assertEqual(record["status"], "passed")
            self.assertEqual(events, ["start", "create", "verify", "restore", "delete", "preserve"])
            self.assertTrue(record["serviceRestored"])
            self.assertFalse(list(scratch.iterdir()))

    def test_native_api_preflight_retains_failures_and_attempts_exact_restore(self) -> None:
        from types import SimpleNamespace

        for mode in ("constructor-error", "before-mutation", "during-start", "retention-error",
                     "restore-error", "keychain-delete-error", "journal-incomplete",
                     "owner-missing", "owner-unreadable", "owner-malformed"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=NATIVE_SOCKET_TMPDIR) as temporary:
                root = Path(temporary).resolve()
                scratch, retained, evidence, args = self._native_preflight_fixture(root)
                events = []

                class FakeRuntime:
                    def __init__(self, case_root, owner, executable, journal_parent, *, home):
                        self.root, self.owner, self.executable = case_root, owner, executable
                        self.switch = None
                        self.host_mutation_started = False
                        self.journal = None

                    def start(self, *, prepare_home):
                        events.append("start")
                        if mode == "before-mutation":
                            raise ValueError("preflight rejected provider")
                        prepare_home(SimpleNamespace())
                        self.switch = object()
                        self.host_mutation_started = True
                        self.journal = object()
                        if mode == "owner-missing":
                            (self.root / "owner.json").unlink()
                        elif mode == "owner-unreadable":
                            (self.root / "owner.json").unlink()
                            (self.root / "owner.json").mkdir()
                        elif mode == "owner-malformed":
                            (self.root / "owner.json").write_text("{")
                        if mode in {"during-start", "retention-error"}:
                            raise RuntimeError("service startup failed")

                    def failure_disposition(self):
                        return {"status": "uncertain" if self.host_mutation_started else "not-started",
                                "phase": "fixture", "hostMutationStarted": self.host_mutation_started}

                    def retain_primary_failure(self, _error):
                        events.append("retain")
                        if mode == "retention-error":
                            raise OSError("private failure write failed")
                        return {"location": "private-journal", "sha256": "a" * 64}

                    def verify(self):
                        events.append("verify")

                    def restore(self):
                        events.append("restore")
                        if mode == "restore-error":
                            raise OSError("service restoration failed")

                    def preserve_logs(self):
                        events.append("preserve")

                    def receipt(self):
                        return {"ownerSHA256": "b" * 64,
                                "records": [] if mode == "journal-incomplete" else 2,
                                "seal": "c" * 64}

                def keychain(_root, action, _journal):
                    events.append(action)
                    if mode == "keychain-delete-error" and action == "delete":
                        raise OSError("private Keychain removal failed")

                cleanup = {lane: {"status": "not-started"} for lane in qualify.LANES}
                runtime_factory = FakeRuntime
                if mode == "constructor-error":
                    runtime_factory = mock.Mock(side_effect=OSError("journal volume unavailable"))
                with (mock.patch.object(qualify, "SSD", scratch),
                      mock.patch.object(qualify, "JOURNAL_PARENT", retained),
                      mock.patch.object(qualify, "ACCOUNT_HOME", root),
                      mock.patch("runtime_services.ControlledRuntime", runtime_factory),
                      mock.patch("private_keychain.run_keychain", side_effect=keychain)):
                    with self.assertRaisesRegex(RuntimeError,
                                                "preflight failed|restoration failed|retention failed|setup failed|cleanup failed"):
                        qualify.preflight_native_api_startup(args, "apple-stock", evidence, cleanup)

                record = json.loads((evidence / "apple-stock-startup-preflight.json").read_text())
                self.assertEqual(record["status"], "failed")
                self.assertTrue(Path(record["scratchRoot"]).is_dir())
                if mode in {"owner-missing", "owner-unreadable"}:
                    self.assertNotIn("ownerSHA256", record)
                else:
                    self.assertEqual(qualify.sha256(Path(record["scratchRoot"]) / "owner.json"),
                                     record["ownerSHA256"])
                if mode in {"owner-missing", "owner-unreadable", "owner-malformed"}:
                    journal = evidence / "apple-stock-startup-preflight-journal.json"
                    self.assertEqual(record["startupJournalSHA256"], qualify.sha256(journal))
                if mode == "constructor-error":
                    self.assertEqual(record["serviceState"], "not-started")
                    self.assertEqual(events, [])
                    self.assertEqual(record["primaryFailureType"], "builtins.OSError")
                    self.assertEqual(cleanup["apple-stock"]["status"], "not-started")
                elif mode == "before-mutation":
                    self.assertEqual(record["serviceState"], "not-started")
                    self.assertEqual(events, ["start", "retain"])
                    self.assertEqual(cleanup["apple-stock"]["status"], "not-started")
                elif mode == "restore-error":
                    self.assertEqual(events, ["start", "create", "verify", "restore", "preserve"])
                    self.assertEqual(record["serviceState"], "uncertain")
                    self.assertEqual(cleanup["apple-stock"]["status"], "uncertain")
                else:
                    self.assertIn("restore", events)
                    self.assertEqual(record["serviceState"], "restored")
                    self.assertTrue(record["serviceRestored"])
                    if mode == "keychain-delete-error":
                        self.assertIn("delete", events)
                        self.assertEqual(cleanup["apple-stock"]["status"], "uncertain")
                    elif mode == "journal-incomplete":
                        self.assertEqual(record["cleanupFailureType"], "builtins.ValueError")
                        self.assertEqual(cleanup["apple-stock"]["status"], "uncertain")
                    elif mode in {"owner-missing", "owner-unreadable", "owner-malformed"}:
                        self.assertIsNotNone(record["cleanupFailureType"])
                        self.assertEqual(cleanup["apple-stock"]["status"], "uncertain")
                    elif mode == "retention-error":
                        self.assertIn("restore", events)
                        self.assertEqual(record["primaryFailureRetentionErrorType"], "builtins.OSError")
                        self.assertEqual(cleanup["apple-stock"]["status"], "uncertain")
                    else:
                        self.assertIn("delete", events)
                        self.assertEqual(cleanup["apple-stock"]["status"], "not-started")

    def test_native_cli_and_api_paths_must_share_each_exact_active_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            providers = {}
            for lane in ("apple-stock", "container-compose"):
                payload = root / "active-runtimes" / lane / "payload"
                providers[lane] = {"active": {"root": str(payload), "executables": {
                    "container": str(payload / "bin/container"),
                    "container-apiserver": str(payload / "bin/container-apiserver")}}}
            args = argparse.Namespace(
                stock_container_bin=Path(providers["apple-stock"]["active"]["executables"]["container"]),
                compose_container_bin=Path(providers["container-compose"]["active"]["executables"]["container"]))
            qualify.require_active_provider_cli_paths(args, providers)
            args.compose_container_bin = root / "prepared-releases/34db/bin/container"
            with self.assertRaisesRegex(ValueError, "exact active runtime payload"):
                qualify.require_active_provider_cli_paths(args, providers)
            args.compose_container_bin = Path(
                providers["container-compose"]["active"]["executables"]["container"])
            providers["apple-stock"]["active"]["executables"]["container-apiserver"] = str(
                root / "other/container-apiserver")
            with self.assertRaisesRegex(ValueError, "exact active runtime payload"):
                qualify.require_active_provider_cli_paths(args, providers)

    def test_active_provider_receipt_is_part_of_private_qualification_identity(self) -> None:
        providers = {}
        for lane, prefix in (("apple-stock", "a"), ("container-compose", "b")):
            providers[lane] = {
                "asset": {"sha256": prefix * 64, "commit": prefix * 40},
                "prepared": {"preparationSHA256": ("c" if prefix == "a" else "d") * 64,
                             "inventorySHA256": ("e" if prefix == "a" else "f") * 64},
                "active": {"root": f"/retained/active-runtimes/{lane}/payload",
                           "inventorySHA256": ("1" if prefix == "a" else "2") * 64,
                           "activation": {"receiptSHA256": ("3" if prefix == "a" else "4") * 64}}}
        result = qualify.active_provider_runtime_receipts(providers)
        self.assertEqual(result["container-compose"]["activationReceiptSHA256"], "4" * 64)
        self.assertEqual(result["apple-stock"]["archiveSHA256"], "a" * 64)
        providers["container-compose"]["active"]["activation"].pop("receiptSHA256")
        with self.assertRaisesRegex(ValueError, "receipt identity has missing"):
            qualify.active_provider_runtime_receipts(providers)

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
            package_root = prepared_root / "Payload"
            provider_root = base / "active-runtimes/apple-stock/payload"
            package_helpers = (
                package_root / "libexec/container/plugins/container-core-images/bin/container-core-images",
                package_root / "libexec/container/plugins/machine-apiserver/bin/machine-apiserver")
            core, machine = package_helpers
            active_helpers = tuple(provider_root / path.relative_to(package_root) for path in package_helpers)
            for path, data in zip(package_helpers, (b"locked core helper", b"locked API helper")):
                path.parent.mkdir(parents=True, mode=0o700)
                path.write_bytes(data)
                path.chmod(0o755)
            for source, path in zip(package_helpers, active_helpers):
                path.parent.mkdir(parents=True, mode=0o700)
                path.write_bytes(source.read_bytes())
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
            active = {"root": str(provider_root),
                      "executables": {"container": str(container),
                                      "container-apiserver": str(container.parent / "container-apiserver")},
                      "activation": {"receiptSHA256": "f" * 64},
                      "inventorySHA256": "1" * 64}
            (container.parent / "container-apiserver").write_bytes(b"provider API")
            (container.parent / "container-apiserver").chmod(0o755)
            arguments = argparse.Namespace(_guest_retained_root=retained, stock_container_bin=container,
                                           _active_provider_runtimes={"apple-stock": {"active": active}})
            with (mock.patch.object(released_engine, "provider_runtime_selection", return_value=asset),
                  mock.patch.object(prepare_releases, "layout", return_value=specification["layout"]),
                  mock.patch.object(prepare_releases, "require_retained", return_value={
                      "root": str(prepared_root), "preparationSHA256": preparation_sha,
                      "inventorySHA256": "e" * 64}),
                  mock.patch.object(prepare_releases, "validate_prepared", return_value={"inventory": inventory})):
                with mock.patch.object(qualify, "REPOSITORY", REPOSITORY):
                    helpers = qualify.admit_provider_helper_programs("apple-stock", arguments)

            self.assertEqual(helpers["com.apple.container.container-core-images"],
                             {"program": active_helpers[0], "sha256": inventory[
                                 "Payload/libexec/container/plugins/container-core-images/bin/container-core-images"]["sha256"]})
            self.assertEqual(arguments._provider_helper_evidence["apple-stock"], {
                "assetSHA256": asset["sha256"], "preparationSHA256": preparation_sha,
                "preparedReceiptSHA256": qualify.sha256(receipt_path),
                "inventorySHA256": "e" * 64,
                "activationReceiptSHA256": "f" * 64,
                "activeInventorySHA256": "1" * 64,
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
            active_helpers[0].write_bytes(b"changed helper bytes")
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
                             side_effect=lambda _repo, lane, retained, **_kwargs: (
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
        with tempfile.TemporaryDirectory(dir=NATIVE_SOCKET_TMPDIR) as temporary:
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


class E06ComponentTests(unittest.TestCase):
    FIXTURE = "E06-network-volume"
    OBSERVATIONS = {"network": "removed", "volume": "removed"}
    CHANGED_OBSERVATION = "volume"
    WRONG_VALUE = "retained"

    def test_filter_and_cli_only_execution_keep_e13_supported(self) -> None:
        for fixture in (self.FIXTURE, qualify.COMPONENT_FIXTURE):
            with self.subTest(fixture=fixture):
                environment = {"DEVCONTAINER_PARITY_FIXTURES": "ambient", "PATH": "/usr/bin"}
                selected = qualify.selected_fixture_environment(environment, fixture)
                self.assertEqual(selected["DEVCONTAINER_PARITY_FIXTURES"], fixture)
                self.assertEqual(environment["DEVCONTAINER_PARITY_FIXTURES"], "ambient")
                cli = subprocess.CompletedProcess(["cli"], 0, "", "")
                calls = []
                actual, vscode, passed = qualify.run_suite_pair(
                    lambda: cli, lambda: calls.append("vscode"),
                    lambda: True, lambda: calls.append("vscode-cleanup"),
                    component_fixture=fixture)
                self.assertIs(actual, cli)
                self.assertIsNone(vscode)
                self.assertTrue(passed)
                self.assertEqual(calls, [])
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            qualify.selected_fixture_environment({}, "E01-container-lifecycle")

    def test_make_dry_run_selects_fixture_or_target_default_without_changing_full_mode(self) -> None:
        environment = {key: value for key, value in os.environ.items()
                       if key not in {"NATIVE_PARITY_COMPONENT_FIXTURE", "MAKEFLAGS", "MAKEFILES", "MFLAGS"}}
        cases = (("native-parity-component", None, qualify.COMPONENT_FIXTURE),
                 ("native-parity-component", self.FIXTURE, self.FIXTURE),
                 ("native-parity-release", None, None))
        for target, selected, expected in cases:
            with self.subTest(target=target, selected=selected):
                command = ["make", "-n", "--no-print-directory", target]
                if selected is not None:
                    command.append(f"NATIVE_PARITY_COMPONENT_FIXTURE={selected}")
                result = subprocess.run(command, cwd=REPOSITORY, env=environment,
                                        capture_output=True, text=True, check=True, timeout=10)
                if expected is None:
                    self.assertNotIn("--component-fixture", result.stdout)
                else:
                    self.assertIn(f'--component-fixture "{expected}"', result.stdout)
                    other = self.FIXTURE if expected == qualify.COMPONENT_FIXTURE else qualify.COMPONENT_FIXTURE
                    self.assertNotIn(f'--component-fixture "{other}"', result.stdout)

    def test_selected_component_cleanup_rejects_wrong_extra_fixture_and_survivors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary).resolve()
            lane = evidence / "docker"
            lane.mkdir()
            for fixtures, differences, expected in (
                ([self.FIXTURE], [], True),
                ([qualify.COMPONENT_FIXTURE], [], False),
                ([self.FIXTURE, qualify.COMPONENT_FIXTURE], [], False),
                ([self.FIXTURE], ["project remains"], False),
            ):
                with self.subTest(fixtures=fixtures, differences=differences):
                    (lane / "results.json").write_text(json.dumps({
                        "fixtures": [{"id": fixture} for fixture in fixtures],
                        "cleanupDifferences": differences}))
                    self.assertEqual(qualify.cli_cleanup_is_complete(
                        evidence, "docker", self.FIXTURE), expected)
        with self.assertRaisesRegex(RuntimeError, "CLI cleanup is incomplete"):
            qualify.run_suite_pair(lambda: subprocess.CompletedProcess(["cli"], 0),
                                   lambda: self.fail("V01 must not run"), lambda: False,
                                   lambda: True, component_fixture=self.FIXTURE)

    def test_selected_component_comparison_uses_exact_observations_and_rejects_extra_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary).resolve()
            for lane in qualify.LANES:
                root = evidence / lane
                root.mkdir()
                (root / "results.json").write_text(json.dumps({
                    "backend": lane, "status": "passed", "durationSeconds": 1.0,
                    "cleanupDifferences": [], "fixtures": [{
                        "id": self.FIXTURE, "status": "passed", "durationSeconds": 1.0,
                        "observations": dict(self.OBSERVATIONS)}]}))
            comparison = qualify.compare_component_results(evidence, self.FIXTURE)
            self.assertTrue(qualify.component_comparison_is_passed(
                comparison, evidence, self.FIXTURE))
            self.assertFalse(qualify.component_comparison_is_passed(
                comparison, evidence, qualify.COMPONENT_FIXTURE))
            result = evidence / "container-compose/results.json"
            payload = json.loads(result.read_text())
            payload["fixtures"][0]["observations"][self.CHANGED_OBSERVATION] = self.WRONG_VALUE
            result.write_text(json.dumps(payload))
            self.assertEqual(qualify.compare_component_results(
                evidence, self.FIXTURE)["status"], "failed")
            payload["fixtures"][0]["observations"][self.CHANGED_OBSERVATION] = self.OBSERVATIONS[self.CHANGED_OBSERVATION]
            payload["fixtures"].append({"id": qualify.COMPONENT_FIXTURE, "status": "passed"})
            result.write_text(json.dumps(payload))
            self.assertEqual(qualify.compare_component_results(
                evidence, self.FIXTURE)["status"], "failed")


    def test_e14_component_comparison_preserves_all_seven_observations_and_false_values(self) -> None:
        fixture = "E14-compose-terminal-size"
        contract = json.loads((REPOSITORY / "Tests/Parity/fixtures" / fixture / "contract.json").read_text())
        expected = {key: str(value).lower() if isinstance(value, bool) else str(value)
                    for key, value in contract["expected"].items()}
        self.assertEqual(set(expected), {"inherited_size", "host_resize", "tty_selected", "stdin_roundtrip",
                                         "merged_streams", "exact_exit", "auto_remove"})
        self.assertEqual(expected["inherited_size"], "true")
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary).resolve()
            for lane in qualify.LANES:
                root = evidence / lane
                root.mkdir()
                (root / "results.json").write_text(json.dumps({
                    "backend": lane, "status": "passed", "durationSeconds": 1.0,
                    "cleanupDifferences": [], "fixtures": [{"id": fixture, "status": "passed",
                        "durationSeconds": 1.0, "observations": dict(expected)}]}))
            self.assertEqual(qualify.compare_component_results(evidence, fixture)["status"], "passed")
            result = evidence / "container-compose/results.json"
            payload = json.loads(result.read_text())
            payload["fixtures"][0]["observations"]["inherited_size"] = "false"
            result.write_text(json.dumps(payload))
            self.assertEqual(qualify.compare_component_results(evidence, fixture)["status"], "failed")

    def test_selected_component_receipt_is_dynamic_and_cannot_seal_full_qualification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary).resolve()
            args = argparse.Namespace(evidence=evidence, component_fixture=self.FIXTURE,
                                      source_commit="a" * 40, _source_tree="b" * 40,
                                      provenance_sha256="c" * 64, state_sha256="d" * 64,
                                      _component_package_proof={"archiveSHA256": "e" * 64})
            cleanup = {lane: {"status": "restored", "cliCleanupComplete": True,
                              "vscodeCleanupComplete": False, "vscodeStatus": "skipped"}
                       for lane in qualify.LANES}
            host = {"status": "restored", "hostGuardCleared": True,
                    "initialColima": "stopped", "finalColima": "stopped",
                    "initialServiceSetSHA256": "f" * 64, "finalServiceSetSHA256": "f" * 64,
                    "initialServiceCount": 8, "finalServiceCount": 8}
            comparison = {"status": "passed", "suite": "cli", "expectedFixtures": [self.FIXTURE],
                          "evidenceStatus": "passed", "functionalParityStatus": "passed",
                          "timingStatus": "passed", "requireZeroFunctionalDifferences": True}
            qualify.write_json(evidence / "component-comparison.json", comparison)
            with mock.patch.object(qualify, "seal_after_host_cleanup") as sealer:
                result = qualify.finalize_component_result(args, cleanup, comparison, host, {}, {}, [])
            sealer.assert_not_called()
            self.assertEqual(result["fixture"], self.FIXTURE)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["scope"], "component-only")
            self.assertFalse(result["releaseAuthority"])
            self.assertEqual(result["fixtureCounts"]["totalLaneFixtureResults"], 3)
            self.assertEqual(result["vscodeStatus"], "skipped")
            self.assertFalse((evidence / "qualification.json").exists())

    def test_selected_component_recheck_binds_initialized_source_and_campaign(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary).resolve() / "component"
            evidence.mkdir(mode=0o700)
            os.chmod(evidence, 0o700)
            inputs = {"sourceCommit": "a" * 40, "sourceTree": "b" * 40, "campaign": self.FIXTURE}
            qualify.write_json(evidence / "operator-inputs.json", inputs)
            os.chmod(evidence / "operator-inputs.json", 0o600)
            identity = qualify.capture_component_evidence_identity(evidence, inputs)
            args = argparse.Namespace(evidence=evidence, component_fixture=self.FIXTURE,
                                      source_commit=inputs["sourceCommit"],
                                      _source_tree=inputs["sourceTree"], campaign=inputs["campaign"])
            qualify.validate_evidence_root(args, identity)
            args.component_fixture = "E01-container-lifecycle"
            with self.assertRaisesRegex(ValueError, "supported component"):
                qualify.validate_evidence_root(args, identity)


class E07ComponentTests(E06ComponentTests):
    FIXTURE = "E07-init-attachment"
    OBSERVATIONS = {key: "true" for key in (
        "prestart_attach", "binary_duplex", "source_separation", "stdin_eof", "exact_exit",
        "history", "restart_history", "combined_history_live")}
    CHANGED_OBSERVATION = "stdin_eof"
    WRONG_VALUE = "false"


if __name__ == "__main__":
    unittest.main()
