#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Security-focused tests for the live parity lane environment."""

from __future__ import annotations

import signal
import argparse
import copy
import json
import os
import shutil
import sqlite3
import subprocess
import unittest
import urllib.error
from contextlib import closing
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from parity_lib import Fixture, ParityError
from run_lane import (
    LaneRunner,
    PARITY_HARNESS,
    finalized_selection,
    candidate_selection,
    WORKFLOW_RETAINED,
    validate_candidate_fixture_set,
    validate_d05_cache_selection,
    create_socket_root,
    install_cancellation_handlers,
    resolver_nameservers,
    run_checked,
    safe_environment,
)


class FinalizedSelectionTests(unittest.TestCase):
    def test_selection_requires_all_four_explicit_inputs(self) -> None:
        values = dict(finalized_directory=Path("/final"), finalization_provenance_sha256="a" * 64,
                      finalization_state=Path("/state"), expected_source_commit="b" * 40)
        self.assertEqual(finalized_selection(argparse.Namespace(**values)), values)
        values["finalization_state"] = None
        invalid_arguments = argparse.Namespace(**values)
        with self.assertRaisesRegex(ParityError, "all four"):
            finalized_selection(invalid_arguments)

    def test_lifecycle_backend_arguments_follow_lane_and_finalization(self) -> None:
        runner = LaneRunner.__new__(LaneRunner)
        runner.devcontainer_docker = "/qualified/docker"
        for lane, selection, expected in (
            ("apple-stock", {"release": True}, []),
            ("container-compose", {"release": True}, []),
            ("docker", {"release": True}, ["--docker-path", "/qualified/docker"]),
            ("apple-stock", None, ["--docker-path", "/qualified/docker"]),
            ("container-compose", None, ["--docker-path", "/qualified/docker"]),
            ("docker", None, ["--docker-path", "/qualified/docker"]),
        ):
            with self.subTest(lane=lane, finalized=selection is not None):
                runner.lane = lane
                runner.finalized_selection = selection
                self.assertEqual(runner.lifecycle_backend_arguments(), expected)


class UnsignedCandidateDiagnosticTests(unittest.TestCase):
    def test_actual_candidate_cli_arguments_do_not_look_like_partial_finalized_selection(self) -> None:
        with mock.patch("run_lane.sys.argv", [
                "run_lane.py", "apple-stock", "/tmp/evidence",
                "--candidate-invocation", "47387ce0-3819-4eca-b06e-11356ce4568d",
                "--expected-source-commit", "5f22bd379c408383daa252b5fc666077fe42e5d3",
                "--repository", "/tmp/product"]):
            arguments = __import__("run_lane").parse_args()
        self.assertIsNone(finalized_selection(arguments))
        self.assertEqual(candidate_selection(arguments), {
            "candidate_invocation": "47387ce0-3819-4eca-b06e-11356ce4568d",
            "expected_source_commit": "5f22bd379c408383daa252b5fc666077fe42e5d3"})

    def test_warm_d05_cli_flag_is_parsed_without_changing_candidate_selection(self) -> None:
        with mock.patch("run_lane.sys.argv", [
                "run_lane.py", "apple-stock", "/tmp/evidence",
                "--candidate-invocation", "47387ce0-3819-4eca-b06e-11356ce4568d",
                "--expected-source-commit", "5f22bd379c408383daa252b5fc666077fe42e5d3",
                "--repository", "/tmp/product", "--d05-cache-state", "warm"]):
            arguments = __import__("run_lane").parse_args()
        self.assertEqual(arguments.d05_cache_state, "warm")
        self.assertIsNone(finalized_selection(arguments))
        self.assertIsNotNone(candidate_selection(arguments))

    def test_warm_d05_selection_is_one_native_admitted_fixture_only(self) -> None:
        validate_d05_cache_selection("warm", "apple-stock", {"D05-features"}, True)
        for state, lane, selected, admitted in (
                ("warm", "docker", {"D05-features"}, True),
                ("warm", "apple-stock", {"D05-features", "E07-init-attachment"}, True),
                ("warm", "container-compose", {"D05-features"}, False),
                ("unsupported", "apple-stock", {"D05-features"}, True)):
            with self.subTest(state=state, lane=lane, selected=selected, admitted=admitted), self.assertRaises(
                    ParityError):
                validate_d05_cache_selection(state, lane, selected, admitted)

    def test_candidate_selection_is_explicit_and_excludes_finalized_inputs(self) -> None:
        selection = candidate_selection(argparse.Namespace(
            candidate_invocation="47387ce0-3819-4eca-b06e-11356ce4568d",
            expected_source_commit="5f22bd379c408383daa252b5fc666077fe42e5d3"))
        self.assertEqual(selection, {
            "candidate_invocation": "47387ce0-3819-4eca-b06e-11356ce4568d",
            "expected_source_commit": "5f22bd379c408383daa252b5fc666077fe42e5d3"})
        with self.assertRaisesRegex(ParityError, "mutually exclusive"):
            candidate_selection(argparse.Namespace(
                candidate_invocation="candidate",
                finalized_directory=Path("/final"),
                finalization_provenance_sha256="b" * 64,
                finalization_state=Path("/state"), expected_source_commit="a" * 40))
        with self.assertRaisesRegex(ParityError, "native-only"):
            candidate_selection(argparse.Namespace(
                candidate_invocation="candidate", expected_source_commit="a" * 40,
                lane="docker"))
        validate_candidate_fixture_set({"C03-compose-resources", "E07-init-attachment"})
        with self.assertRaisesRegex(ParityError, "only selected C03"):
            validate_candidate_fixture_set({"E01-engine-negotiation"})

    def test_candidate_execution_uses_only_admitted_candidate_paths(self) -> None:
        runner = LaneRunner.__new__(LaneRunner)
        runner.lane = "container-compose"
        runner.repository = Path("/product")
        runner.candidate_selection = {"candidate_invocation": "candidate",
                                      "expected_source_commit": "a" * 40}
        runner.finalized_selection = None
        runner.candidate = {"executables": {"devcontainer": "/candidate/devcontainer",
                                            "devcontainer-engine": "/candidate/engine",
                                            "devcontainer-compose": "/candidate/compose"}}
        runner.finalized = None
        runner.devcontainer_docker = "/ignored/docker"
        runner.provider_paths = {"DEVCONTAINER_COMPOSE_BIN": "/provider/compose"}
        self.assertEqual(runner.package_executable("devcontainer"), "/candidate/devcontainer")
        self.assertEqual(runner.devcontainers_command(), ["/candidate/devcontainer"])
        self.assertEqual(runner.lifecycle_backend_arguments(), [])

    def test_candidate_readmission_rejects_wrong_source_profile_and_inventory(self) -> None:
        runner = LaneRunner.__new__(LaneRunner)
        runner.lane = "apple-stock"
        runner.repository = Path("/product")
        runner.harness_repository = Path("/tooling")
        runner.candidate_selection = {"candidate_invocation": "candidate",
                                      "expected_source_commit": "a" * 40}
        runner.provider_paths = {}
        runner.provider_hashes = {}
        runner.harness_sha256 = "f" * 64
        bad_receipts = (
            {"schemaVersion": 2, "runtimeProfile": "stock", "commit": "b" * 40},
            {"schemaVersion": 2, "runtimeProfile": "enhanced", "commit": "a" * 40},
            {"schemaVersion": 1, "runtimeProfile": "stock", "commit": "a" * 40},
        )
        def git_output(command, **_kwargs):
            return "a" * 40 if command[-1] == "HEAD" else ""

        with (mock.patch("run_lane.parity_harness_sha256", return_value="f" * 64),
              mock.patch("run_lane.subprocess.check_output", side_effect=git_output)):
            for receipt in bad_receipts:
                candidate_module = SimpleNamespace(
                    Path=Path, retained_candidate=mock.Mock(return_value=(receipt, {})),
                    admit_candidate=mock.Mock(),
                    canonical=lambda value: json.dumps(value, sort_keys=True))
                with (self.subTest(receipt=receipt),
                      mock.patch("run_lane.load_candidate_admitter", return_value=candidate_module),
                      self.assertRaisesRegex(ParityError, "schema-2 stock package")):
                    runner.readmit_candidate(first=True)
                candidate_module.admit_candidate.assert_not_called()

            candidate_module = SimpleNamespace(
                Path=Path, retained_candidate=mock.Mock(return_value=(
                    {"schemaVersion": 2, "runtimeProfile": "stock", "commit": "a" * 40}, {})),
                admit_candidate=mock.Mock(side_effect=ValueError("candidate inventory is incomplete")),
                canonical=lambda value: json.dumps(value, sort_keys=True))
            with (mock.patch("run_lane.load_candidate_admitter", return_value=candidate_module),
                  self.assertRaisesRegex(ParityError, "inventory is incomplete")):
                runner.readmit_candidate(first=True)

    def test_candidate_readmission_uses_account_retained_authority_not_lane_home(self) -> None:
        runner = LaneRunner.__new__(LaneRunner)
        runner.lane = "apple-stock"
        runner.repository = Path("/product")
        runner.harness_repository = Path("/tooling")
        runner.candidate_selection = {"candidate_invocation": "candidate",
                                      "expected_source_commit": "a" * 40}
        runner.provider_paths = {}
        runner.provider_hashes = {}
        runner.harness_sha256 = "f" * 64
        receipt = {"schemaVersion": 2, "runtimeProfile": "stock", "commit": "a" * 40,
                   "referenceRuntime": {"lockSHA256": "1" * 64}}
        admission = {"scope": "local-candidate-integration-only",
                     "candidateInvocation": "candidate", "sourceCommit": "a" * 40,
                     "runtimeProfile": "stock", "assetSHA256": "b" * 64,
                     "preparationSHA256": "c" * 64, "inventorySHA256": "d" * 64,
                     "dependencyLockSHA256": "e" * 64, "executables": {"devcontainer": "/candidate/devcontainer"},
                     "terminalLaunchers": {"arm64": "1" * 64, "amd64": "2" * 64},
                     "goSDKLicenseSHA256": "3" * 64}
        module = SimpleNamespace(
            Path=Path, retained_candidate=mock.Mock(return_value=(receipt, {})),
            admit_candidate=mock.Mock(return_value=admission),
            canonical=lambda value: json.dumps(value, sort_keys=True))
        def git_output(command, **_kwargs):
            return "a" * 40 if command[-1] == "HEAD" else ""

        with (mock.patch("run_lane.parity_harness_sha256", return_value="f" * 64),
              mock.patch("run_lane.subprocess.check_output", side_effect=git_output),
              mock.patch("run_lane.load_candidate_admitter", return_value=module),
              mock.patch.object(Path, "home", return_value=Path("/wrong-transaction-home"))):
            runner.readmit_candidate(first=True)
        module.retained_candidate.assert_called_once_with(
            WORKFLOW_RETAINED / "bazel-evidence.sqlite", "candidate", "devcontainer")
        module.admit_candidate.assert_called_once_with(
            WORKFLOW_RETAINED, "candidate", "stock", "devcontainer")

    def test_candidate_readmission_rejects_changed_product_checkout_before_authority_read(self) -> None:
        runner = LaneRunner.__new__(LaneRunner)
        runner.lane = "apple-stock"
        runner.repository = Path("/product")
        runner.candidate_selection = {"candidate_invocation": "candidate",
                                      "expected_source_commit": "a" * 40}
        runner.provider_paths = {}
        runner.provider_hashes = {}
        git_output = mock.Mock(side_effect=["a" * 40, " M Tests/Parity/manifest.json"])
        with mock.patch("run_lane.subprocess.check_output", git_output):
            with self.assertRaisesRegex(ParityError, "not clean"):
                runner.admit_candidate()
        git_output.assert_called()

    def test_devcontainer_preserves_literal_backend_flag_after_separator(self) -> None:
        runner = LaneRunner.__new__(LaneRunner)
        runner.lane = "apple-stock"
        runner.repository = Path("/repository")
        runner.finalized_selection = {"release": True}
        runner.finalized = {"executables": {"devcontainer": "/signed/devcontainer"}}
        runner.environment = {}
        arguments = ["exec", "--workspace-folder", "/fixture", "--", "--docker-path", "/literal"]

        with mock.patch("run_lane.subprocess.run", return_value=mock.Mock(returncode=0)) as invoke:
            runner.devcontainer(arguments, 10)

        self.assertEqual(
            invoke.call_args.args[0],
            ["/signed/devcontainer", "exec", "--workspace-folder", "/fixture", "--", "--docker-path", "/literal"],
        )

    def test_release_uses_signed_binaries_and_private_reference(self) -> None:
        runner = LaneRunner.__new__(LaneRunner)
        runner.lane = "container-compose"
        runner.repository = Path("/repository")
        runner.finalized_selection = {"expected_source_commit": "b" * 40}
        runner.finalized = {"executables": {"devcontainer": "/signed/devcontainer",
                                              "devcontainer-engine": "/signed/engine",
                                              "devcontainer-compose": "/signed/compose"},
                            "referenceRuntime": {"paths": {"node": "/signed/node",
                                                           "cli/devcontainer.js": "/signed/cli.js"}}}
        runner.provider_paths = {"DEVCONTAINER_COMPOSE_BIN": "/qualified/compose"}
        self.assertEqual(runner.package_executable("devcontainer-engine"), "/signed/engine")
        self.assertEqual(runner.compose_command("project", Path("/fixture/compose.yml"))[0], "/signed/compose")
        self.assertEqual(runner.devcontainers_command(), ["/signed/devcontainer"])
        self.assertEqual(runner.provider_executable("DEVCONTAINER_COMPOSE_BIN", "ambient"), "/qualified/compose")
        runner.environment = {"PATH": "/usr/bin"}
        with mock.patch("run_lane.subprocess.run", return_value=mock.Mock(returncode=0)) as invoke:
            runner.devcontainer(["up", "--workspace-folder", "/fixture"], 10)
        self.assertEqual(invoke.call_args.args[0], ["/signed/devcontainer", "up", "--workspace-folder", "/fixture"])

    def test_readmission_rejects_changed_signed_identity(self) -> None:
        runner = LaneRunner.__new__(LaneRunner)
        runner.lane = "docker"
        runner.repository = Path("/repository")
        runner.finalized_selection = {"finalized_directory": Path("/final"),
                                     "finalization_provenance_sha256": "a" * 64,
                                     "finalization_state": Path("/state"),
                                     "expected_source_commit": "b" * 40}
        runner.provider_paths = {}
        runner.provider_hashes = {}
        runner.harness_sha256 = "f" * 64
        admission = {"scope": "finalized-native-package-runtime-input", "kind": "signed-notarized-native-package",
                     "sourceCommit": "b" * 40, "runtimeProfile": "stock", "candidateReceiptSHA256": "c" * 64,
                     "finalizationProvenanceSHA256": "a" * 64, "trustedStateSHA256": "d" * 64,
                     "archiveSHA256": "e" * 64, "archiveSize": 1, "preparationSHA256": "f" * 64,
                     "inventorySHA256": "1" * 64, "productionBinarySHA256": {},
                     "signatureInventorySHA256": "2" * 64, "referenceRuntime": {"files": {}},
                     "signatureEvidenceRoot": "/unique/first"}
        with (mock.patch("run_lane.load_finalized_admitter", return_value=lambda **_: admission),
              mock.patch("run_lane.parity_harness_sha256", return_value="f" * 64)):
            runner.readmit_finalized(first=True)
            admission["signatureEvidenceRoot"] = "/unique/second"
            runner.readmit_finalized()
            admission["archiveSHA256"] = "0" * 64
            with self.assertRaisesRegex(ParityError, "identity changed"):
                runner.readmit_finalized()

    def test_native_provider_bytes_must_survive_post_run_readmission(self) -> None:
        with TemporaryDirectory() as temporary:
            provider = Path(temporary).resolve() / "container"
            provider.write_bytes(b"qualified provider")
            provider.chmod(0o755)
            runner = LaneRunner.__new__(LaneRunner)
            runner.lane = "apple-stock"
            runner.repository = Path("/repository")
            runner.finalized_selection = {"finalized_directory": Path("/final"),
                                         "finalization_provenance_sha256": "a" * 64,
                                         "finalization_state": Path("/state"),
                                         "expected_source_commit": "b" * 40}
            runner.provider_paths = {}
            runner.provider_hashes = {}
            runner.harness_sha256 = "f" * 64
            with (
                mock.patch.dict("run_lane.os.environ", {"DEVCONTAINER_CONTAINER_BIN": str(provider)}),
                mock.patch("run_lane.parity_harness_sha256", return_value="f" * 64),
                mock.patch("run_lane.load_finalized_admitter", return_value=lambda **_: {
                    "scope": "finalized-native-package-runtime-input", "kind": "signed-notarized-native-package",
                    "sourceCommit": "b" * 40, "runtimeProfile": "stock", "candidateReceiptSHA256": "c" * 64,
                    "finalizationProvenanceSHA256": "a" * 64, "trustedStateSHA256": "d" * 64,
                    "archiveSHA256": "e" * 64, "archiveSize": 1, "preparationSHA256": "f" * 64,
                    "inventorySHA256": "1" * 64, "productionBinarySHA256": {},
                    "signatureInventorySHA256": "2" * 64, "referenceRuntime": {"files": {}},
                }),
            ):
                runner.admit_finalized()
                provider.write_bytes(b"changed provider")
                with self.assertRaisesRegex(ParityError, "qualified provider changed"):
                    runner.readmit_finalized()


class ComponentBuilderSelectionTests(unittest.TestCase):
    def _run_selection(self, lane: str, fixture_ids: tuple[str, ...], selected: str,
                       runtime_profile=None, cache_state="cold"):
        import sys

        repository = Path(__file__).resolve().parents[2]
        testing = repository / "Tools/testing"
        if str(testing) not in sys.path:
            sys.path.insert(0, str(testing))
        import owned_guest_fixture

        with TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            manifest = json.loads((repository / "Tests/Parity/manifest.json").read_text())
            fixtures = [SimpleNamespace(identifier=identifier, runner="engine",
                                        backends=("docker", "apple-stock", "container-compose"))
                        for identifier in fixture_ids]
            events = []

            class Bridge:
                def __init__(self, _runner, owned, **kwargs):
                    self.preparation_error = None
                    self.fixtures = owned
                    self.provider_required = kwargs.get("provider_required", False)
                    self.builder_required = kwargs.get("builder_required", False)

                def prepare_native_provider(self):
                    if self.provider_required:
                        events.append("provider-required")
                    events.append("provision")

                def prepare_native_builder(self):
                    events.append("builder-start")

                def prepare_native_builder_readiness(self):
                    events.append("builder-ready")

                def attach_endpoint(self):
                    events.append("attach")

                def prepare(self):
                    events.append("docker-image-prepare")

                def cleanup(self):
                    events.append("guest-cleanup")

            runner = LaneRunner.__new__(LaneRunner)
            runner.lane, runner.repository, runner.manifest = lane, repository, manifest
            runner.docker, runner.node_package_runner = "/pinned/docker", "/pinned/npx"
            runner.output, runner.finalized_selection = base / "evidence" / lane, {}
            runner.environment = {"DOCKER_HOST": "unix:///tmp/docker.sock"}
            runner.provider_hashes = {}
            runner.harness_sha256 = "f" * 64
            runner.finalized_identity = {"runtimeProfile": runtime_profile} if runtime_profile else None
            runner.d05_cache_state = cache_state
            runner.cleanup_differences = []
            runner._preserve_engine_on_uncertain_guest_cleanup = False
            builder = mock.Mock(side_effect=lambda: events.append("buildx-bootstrap"))

            def run_fixture(fixture):
                events.append("fixture:" + fixture.identifier)
                return {"id": fixture.identifier, "status": "passed", "durationSeconds": 0.0,
                        "observations": {}, "differences": [], "diagnostic": ""}

            common_patches = (
                mock.patch("run_lane.implemented_fixtures", return_value=fixtures),
                mock.patch.object(owned_guest_fixture, "_retained_root", return_value=base),
                mock.patch.object(owned_guest_fixture, "admit_guest_inputs", return_value={}),
                mock.patch.object(owned_guest_fixture, "OwnedGuestFixtureRunner", Bridge),
                mock.patch.object(runner, "admit_finalized"),
                mock.patch.object(runner, "configure_docker_oracle",
                                  side_effect=lambda: events.append("docker-oracle")),
                mock.patch.object(runner, "prepare_builder", builder),
                mock.patch.object(runner, "configure_devcontainer_client"),
                mock.patch.object(runner, "fingerprint", return_value={}),
                mock.patch.object(runner, "run_fixture", side_effect=run_fixture),
                mock.patch.object(runner, "stop_builder"),
                mock.patch.object(runner, "check_runtime_state_cleanup"),
                mock.patch.object(runner, "readmit_finalized"),
                mock.patch.object(runner, "start_engine", side_effect=lambda: events.append("engine")),
                mock.patch.object(runner, "stop_engine"),
                mock.patch("e04_build_readiness.docker_readiness",
                           side_effect=lambda *_args: events.append("docker-readiness")),
            )
            with mock.patch.dict("run_lane.os.environ",
                                 {"DEVCONTAINER_PARITY_FIXTURES": selected}, clear=True):
                with common_patches[0], common_patches[1], common_patches[2], common_patches[3], \
                        common_patches[4], common_patches[5], common_patches[6], common_patches[7], \
                        common_patches[8], common_patches[9], common_patches[10], common_patches[11], \
                        common_patches[12], common_patches[13], common_patches[14], common_patches[15]:
                    result = runner.run()
            return result, builder, events

    def test_isolated_native_e06_prepares_authenticated_guest_before_engine(self) -> None:
        for lane in ("apple-stock", "container-compose"):
            with self.subTest(lane=lane):
                result, _builder, events = self._run_selection(
                    lane, ("E06-network-volume",), "E06-network-volume", runtime_profile="stock")
                self.assertEqual(result, 0)
                self.assertEqual(events.count("provision"), 1)
                self.assertLess(events.index("provision"), events.index("engine"))
                self.assertLess(events.index("engine"), events.index("attach"))
                self.assertLess(events.index("attach"), events.index("fixture:E06-network-volume"))
                self.assertIn("guest-cleanup", events)

    def test_docker_e06_does_not_acquire_native_guest_preparation(self) -> None:
        result, _builder, events = self._run_selection(
            "docker", ("E06-network-volume",), "E06-network-volume")
        self.assertEqual(result, 0)
        self.assertNotIn("provision", events)
        self.assertNotIn("attach", events)
        self.assertNotIn("docker-image-prepare", events)

    def test_full_selection_keeps_preparation_once_for_existing_owned_route(self) -> None:
        result, _builder, events = self._run_selection(
            "apple-stock", ("E06-network-volume", "E07-init-attachment"), "")
        self.assertEqual(result, 0)
        self.assertEqual(events.count("provision"), 1)
        self.assertLess(events.index("provision"), events.index("fixture:E06-network-volume"))
        self.assertIn("fixture:E07-init-attachment", events)

    def test_selected_e13_component_skips_builder_on_docker_and_fork(self) -> None:
        for lane in ("docker", "container-compose"):
            with self.subTest(lane=lane):
                result, builder, events = self._run_selection(
                    lane, ("E13-compose-signals", "E04-image-build"), "E13-compose-signals")
                self.assertEqual(result, 0)
                builder.assert_not_called()
                if lane == "container-compose":
                    self.assertLess(events.index("provision"), events.index("engine"))
                    self.assertLess(events.index("engine"), events.index("attach"))
                else:
                    self.assertLess(events.index("docker-oracle"), events.index("attach"))
                self.assertIn("attach", events)
                self.assertLess(events.index("attach"), events.index("fixture:E13-compose-signals"))
                self.assertLess(events.index("fixture:E13-compose-signals"), events.index("guest-cleanup"))
                self.assertIn("guest-cleanup", events)


    def test_selected_e14_component_skips_builder_and_runs_exact_terminal_fixture(self) -> None:
        result, builder, events = self._run_selection(
            "container-compose", ("E14-compose-terminal-size", "E04-image-build"), "E14-compose-terminal-size")
        self.assertEqual(result, 0)
        builder.assert_not_called()
        self.assertLess(events.index("provision"), events.index("engine"))
        self.assertLess(events.index("engine"), events.index("attach"))
        self.assertLess(events.index("attach"), events.index("fixture:E14-compose-terminal-size"))
        self.assertLess(events.index("fixture:E14-compose-terminal-size"), events.index("guest-cleanup"))
        self.assertIn("guest-cleanup", events)

    def test_warm_d05_only_admits_kernel_and_init_preparation_before_engine(self) -> None:
        result, builder, events = self._run_selection(
            "apple-stock", ("D05-features",), "D05-features", "stock", cache_state="warm")
        self.assertEqual(result, 0)
        builder.assert_not_called()
        self.assertLess(events.index("provider-required"), events.index("provision"))
        self.assertLess(events.index("provision"), events.index("engine"))
        self.assertLess(events.index("engine"), events.index("attach"))
        self.assertLess(events.index("attach"), events.index("fixture:D05-features"))
        self.assertIn("guest-cleanup", events)

    def test_finalized_e04_component_starts_and_proves_native_builder_before_legacy_fixture(self) -> None:
        for lane in ("apple-stock", "container-compose"):
            with self.subTest(lane=lane):
                result, builder, events = self._run_selection(
                    lane, ("E04-image-build",), "E04-image-build", "stock")
                self.assertEqual(result, 0)
                builder.assert_not_called()
                self.assertLess(events.index("provision"), events.index("builder-start"))
                self.assertLess(events.index("builder-start"), events.index("engine"))
                self.assertLess(events.index("attach"), events.index("builder-ready"))
                self.assertLess(events.index("builder-ready"), events.index("fixture:E04-image-build"))
                self.assertIn("guest-cleanup", events)

    def test_docker_e04_readiness_runs_after_buildx_before_legacy_fixture(self) -> None:
        result, builder, events = self._run_selection(
            "docker", ("E04-image-build",), "E04-image-build")
        self.assertEqual(result, 0)
        builder.assert_called_once_with()
        self.assertLess(events.index("docker-oracle"), events.index("buildx-bootstrap"))
        self.assertLess(events.index("buildx-bootstrap"), events.index("docker-readiness"))
        self.assertLess(events.index("docker-readiness"), events.index("fixture:E04-image-build"))

    def test_unfiltered_build_matrix_still_prepares_builder(self) -> None:
        for lane in ("docker", "container-compose"):
            with self.subTest(lane=lane):
                result, builder, _events = self._run_selection(
                    lane, ("E13-compose-signals", "E04-image-build"), "")
                self.assertEqual(result, 0)
                builder.assert_called_once_with()

    def test_finalized_stock_native_lanes_use_native_api_builder(self) -> None:
        for lane in ("apple-stock", "container-compose", "docker"):
            with self.subTest(lane=lane):
                result, builder, events = self._run_selection(
                    lane, ("E13-compose-signals", "E04-image-build"), "", "stock")
                self.assertEqual(result, 0)
                self.assertIn("fixture:E04-image-build", events)
                if lane == "docker":
                    builder.assert_called_once_with()
                else:
                    builder.assert_not_called()


class SafeEnvironmentTests(unittest.TestCase):
    def test_environment_uses_an_explicit_non_secret_allowlist(self) -> None:
        environment = safe_environment(
            {
                "BASH_ENV": "/tmp/host-shell-hook",
                "CONTAINER_APP_ROOT": "/stable/runtime",
                "CONTAINER_COMPOSE_BUILD_INFO": "/tmp/build-info.json",
                "CONTAINER_COMPOSE_CONTAINER": "/tmp/container",
                "CONTAINER_INSTALL_ROOT": "/stable/provider",
                "CONTAINER_LOG_ROOT": "/tmp/runtime-logs",
                "CONTAINER_SERVICE_NAMESPACE": "io.github.example.runtime",
                "DEVCONTAINER_DOCKER_ORACLE_HOST": "unix:///tmp/docker.sock",
                "DEVCONTAINER_API_DEFINITION_SHA256": "d" * 64,
                "DEVCONTAINER_API_SERVER_SHA256": "e" * 64,
                "DEVCONTAINER_API_SERVICE_PID": "1234",
                "DEVCONTAINER_COMPOSE_PROVIDER_SHA256": "a" * 64,
                "DEVCONTAINER_BACKEND": "operator-choice",
                "DEVCONTAINER_CONFIG": "/operator/config.toml",
                "DOCKER_CONTEXT": "fixture",
                "GITHUB_TOKEN": "must-not-leak",
                "HOME": "/Users/operator",
                "LD_PRELOAD": "/tmp/injected.dylib",
                "PATH": "/usr/bin:/bin",
                "RUNNER_TRACKING_ID": "github_fixture",
                "SONAR_TOKEN": "must-not-leak",
            }
        )
        self.assertEqual(
            environment,
            {
                "CONTAINER_APP_ROOT": "/stable/runtime",
                "CONTAINER_COMPOSE_BUILD_INFO": "/tmp/build-info.json",
                "CONTAINER_COMPOSE_CONTAINER": "/tmp/container",
                "CONTAINER_INSTALL_ROOT": "/stable/provider",
                "CONTAINER_LOG_ROOT": "/tmp/runtime-logs",
                "CONTAINER_SERVICE_NAMESPACE": "io.github.example.runtime",
                "DEVCONTAINER_DOCKER_ORACLE_HOST": "unix:///tmp/docker.sock",
                "DEVCONTAINER_API_DEFINITION_SHA256": "d" * 64,
                "DEVCONTAINER_API_SERVER_SHA256": "e" * 64,
                "DEVCONTAINER_API_SERVICE_PID": "1234",
                "DEVCONTAINER_COMPOSE_PROVIDER_SHA256": "a" * 64,
                "DOCKER_CONTEXT": "fixture",
                "HOME": "/Users/operator",
                "PATH": "/usr/bin:/bin",
                "RUNNER_TRACKING_ID": "github_fixture",
            },
        )

    def test_runtime_selection_values_are_not_inherited_from_operator_environment(self) -> None:
        environment = safe_environment({
            "DEVCONTAINER_BACKEND": "operator-choice",
            "DEVCONTAINER_CONFIG": "/operator/config.toml",
            "DEVCONTAINER_STATE": "/operator/state.sqlite",
            "DEVCONTAINER_SOCKET": "/operator/docker.sock",
            "DEVCONTAINER_COMPOSE_PROVIDER": "docker",
        })
        self.assertNotIn("DEVCONTAINER_BACKEND", environment)
        self.assertNotIn("DEVCONTAINER_CONFIG", environment)
        self.assertNotIn("DEVCONTAINER_STATE", environment)
        self.assertNotIn("DEVCONTAINER_SOCKET", environment)
        self.assertNotIn("DEVCONTAINER_COMPOSE_PROVIDER", environment)


class NativeBackendSelectionTests(unittest.TestCase):
    def test_engine_setup_recomputes_backend_for_actual_cli_and_compose_children(self) -> None:
        for lane, backend in (("apple-stock", "stock"), ("container-compose", "container-compose")):
            with self.subTest(lane=lane), TemporaryDirectory() as temporary:
                root = Path(temporary)
                socket_root = root / "socket"
                socket_root.mkdir()
                (socket_root / "docker.sock").touch()
                runner = LaneRunner.__new__(LaneRunner)
                runner.lane, runner.repository = lane, root
                runner.runtime_root, runner.output = root / "runtime", root
                runner.finalized_selection = {}
                runner.docker = "/pinned/docker"
                runner.environment = safe_environment({"DEVCONTAINER_BACKEND": "operator-choice"})
                self.assertNotIn("DEVCONTAINER_BACKEND", runner.environment)
                runner.package_executable = mock.Mock(return_value="/pinned/engine")
                runner.provider_executable = mock.Mock(return_value="/pinned/provider")
                runner.devcontainers_command = mock.Mock(return_value=["/pinned/devcontainer"])
                process = mock.Mock()
                process.poll.return_value = None
                result = subprocess.CompletedProcess([], 0, "", "")
                with (mock.patch("run_lane.platform.system", return_value="Darwin"),
                      mock.patch("run_lane.platform.machine", return_value="arm64"),
                      mock.patch("run_lane.create_socket_root", return_value=socket_root),
                      mock.patch("run_lane.subprocess.Popen", return_value=process) as launch,
                      mock.patch("run_lane.subprocess.run", return_value=result) as execute):
                    try:
                        runner.start_engine()
                        self.assertEqual(launch.call_args.args[0][-1], backend)
                        runner.devcontainer(["up"], timeout=30)
                        self.assertEqual(execute.call_args.kwargs["env"]["DEVCONTAINER_BACKEND"], backend)
                        self.assertEqual(runner.compose_environment()["DEVCONTAINER_BACKEND"], backend)
                    finally:
                        runner.engine_log.close()


class EngineRoutePreflightTests(unittest.TestCase):
    def test_unknown_engine_route_fails_before_output_or_runtime_admission(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "docker"
            output.mkdir()
            sentinel = output / "keep.json"
            sentinel.write_text("unchanged")
            manifest = json.loads((Path(__file__).resolve().parents[2] /
                                   "Tests/Parity/manifest.json").read_text())
            manifest = copy.deepcopy(manifest)
            manifest["fixtures"].append({"id": "E99-unrouted", "status": "implemented",
                                         "runner": "engine", "backends": ["docker"]})
            runner = LaneRunner.__new__(LaneRunner)
            runner.lane = "docker"
            runner.manifest = manifest
            runner.docker = "/pinned/docker"
            runner.node_package_runner = "/pinned/node"
            runner.output = output
            with mock.patch.object(runner, "admit_finalized") as admit:
                with self.assertRaisesRegex(ParityError, "route preflight"):
                    runner.run()
            self.assertEqual(sentinel.read_text(), "unchanged")
            admit.assert_not_called()

    def test_owned_guest_fixture_dispatch_uses_the_lane_scoped_adapter(self) -> None:
        from types import SimpleNamespace

        runner = LaneRunner.__new__(LaneRunner)
        adapter = mock.Mock()
        expected = {"id": "E07-init-attachment", "status": "passed"}
        adapter.run.return_value = expected
        runner._owned_guest_runner = adapter
        fixture = SimpleNamespace(identifier="E07-init-attachment")
        raw = Path("/tmp/evidence/raw/E07-init-attachment")

        self.assertIs(runner.run_engine_fixture(fixture, raw), expected)
        adapter.run.assert_called_once_with(fixture, raw)

    def test_runtime_error_during_guest_provisioning_skips_engine_start(self) -> None:
        import qualify_finalized_package as qualifier

        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            evidence = root / "apple-stock"
            fixture = SimpleNamespace(identifier="E07-init-attachment", runner="engine",
                                      backends=("apple-stock",))
            manifest = json.loads((Path(__file__).resolve().parents[2] /
                                   "Tests/Parity/manifest.json").read_text())

            class FailingBridge:
                def __init__(self, *_args, **_kwargs):
                    self.preparation_error = None

                def attach_endpoint(self):
                    # No endpoint is attached after API-only provisioning fails.
                    pass

                def prepare_native_provider(self):
                    raise RuntimeError("guest provision command failed")

                def run(self, row, _raw):
                    return {"id": row.identifier, "status": "failed", "observations": {},
                            "durationSeconds": 0.0, "differences": [],
                            "diagnostic": self.preparation_error}

                def cleanup(self):
                    raise ParityError("owned guest preparation is incomplete")

            runner = LaneRunner.__new__(LaneRunner)
            runner.lane, runner.repository, runner.manifest = "apple-stock", Path(__file__).resolve().parents[2], manifest
            runner.docker, runner.node_package_runner = "/pinned/docker", "/pinned/npx"
            runner.output, runner.finalized_selection = evidence, None
            runner.finalized_identity, runner.cleanup_differences = None, []
            runner._preserve_engine_on_uncertain_guest_cleanup = False
            with (mock.patch("run_lane.implemented_fixtures", return_value=[fixture]),
                  mock.patch("owned_guest_fixture._retained_root", return_value=root / "retained"),
                  mock.patch("owned_guest_fixture.admit_guest_inputs", return_value={}),
                  mock.patch("owned_guest_fixture.OwnedGuestFixtureRunner", FailingBridge),
                  mock.patch.object(runner, "admit_finalized"),
                  mock.patch.object(runner, "start_engine") as start_engine,
                  mock.patch.object(runner, "configure_devcontainer_client"),
                  mock.patch.object(runner, "fingerprint", return_value={}),
                  mock.patch.object(runner, "stop_builder"),
                  mock.patch.object(runner, "check_runtime_state_cleanup"),
                  mock.patch.object(runner, "readmit_finalized"),
                  mock.patch.object(runner, "stop_engine") as stop_engine):
                result = runner.run()

            payload = json.loads((evidence / "results.json").read_text())
            self.assertEqual(result, 1)
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["fixtures"][0]["status"], "failed")
            self.assertTrue(any("guest input preparation failed" in row
                                for row in payload["cleanupDifferences"]))
            self.assertTrue(runner._preserve_engine_on_uncertain_guest_cleanup)
            start_engine.assert_not_called()
            stop_engine.assert_not_called()
            self.assertFalse(qualifier.cli_cleanup_is_complete(root, "apple-stock"))

    def test_isolated_e06_provisioning_failure_skips_engine_start(self) -> None:
        import qualify_finalized_package as qualifier

        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            evidence = root / "apple-stock"
            fixture = SimpleNamespace(identifier="E06-network-volume", runner="engine",
                                      backends=("apple-stock",))
            manifest = json.loads((Path(__file__).resolve().parents[2] /
                                   "Tests/Parity/manifest.json").read_text())

            class FailingBridge:
                def __init__(self, *_args, **_kwargs):
                    self.preparation_error = None

                def attach_endpoint(self):
                    # No endpoint is attached after API-only provisioning fails.
                    pass

                def prepare_native_provider(self):
                    raise RuntimeError("guest provision command failed")

                def run(self, row, _raw):
                    return {"id": row.identifier, "status": "failed", "observations": {},
                            "durationSeconds": 0.0, "differences": [],
                            "diagnostic": self.preparation_error}

                def cleanup(self):
                    raise ParityError("owned guest preparation is incomplete")

            runner = LaneRunner.__new__(LaneRunner)
            runner.lane, runner.repository, runner.manifest = "apple-stock", Path(__file__).resolve().parents[2], manifest
            runner.docker, runner.node_package_runner = "/pinned/docker", "/pinned/npx"
            runner.output, runner.finalized_selection = evidence, None
            runner.finalized_identity, runner.cleanup_differences = None, []
            runner._preserve_engine_on_uncertain_guest_cleanup = False
            with (mock.patch.dict("run_lane.os.environ",
                                  {"DEVCONTAINER_PARITY_FIXTURES": "E06-network-volume"}),
                  mock.patch("run_lane.implemented_fixtures", return_value=[fixture]),
                  mock.patch("owned_guest_fixture._retained_root", return_value=root / "retained"),
                  mock.patch("owned_guest_fixture.admit_guest_inputs", return_value={}),
                  mock.patch("owned_guest_fixture.OwnedGuestFixtureRunner", FailingBridge),
                  mock.patch.object(runner, "admit_finalized"),
                  mock.patch.object(runner, "start_engine") as start_engine,
                  mock.patch.object(runner, "configure_devcontainer_client"),
                  mock.patch.object(runner, "fingerprint", return_value={}),
                  mock.patch.object(runner, "stop_builder"),
                  mock.patch.object(runner, "check_runtime_state_cleanup"),
                  mock.patch.object(runner, "readmit_finalized"),
                  mock.patch.object(runner, "stop_engine") as stop_engine):
                result = runner.run()

            payload = json.loads((evidence / "results.json").read_text())
            self.assertEqual(result, 1)
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["fixtures"][0]["status"], "failed")
            self.assertTrue(any("guest input preparation failed" in row
                                for row in payload["cleanupDifferences"]))
            self.assertTrue(runner._preserve_engine_on_uncertain_guest_cleanup)
            start_engine.assert_not_called()
            stop_engine.assert_not_called()
            self.assertFalse(qualifier.cli_cleanup_is_complete(root, "apple-stock"))

    def test_native_guest_provisions_before_engine_and_attaches_only_after_ready(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            evidence = root / "apple-stock"
            fixture = SimpleNamespace(identifier="E07-init-attachment", runner="engine",
                                      backends=("apple-stock",))
            manifest = json.loads((Path(__file__).resolve().parents[2] /
                                   "Tests/Parity/manifest.json").read_text())
            events = []

            class Bridge:
                def __init__(self, *_args, **_kwargs):
                    self.preparation_error = None

                def prepare_native_provider(self):
                    events.append("provision")

                def attach_endpoint(self):
                    events.append("attach")

                def run(self, row, _raw):
                    return {"id": row.identifier, "status": "passed", "observations": {},
                            "durationSeconds": 0.0, "differences": [], "diagnostic": ""}

                def cleanup(self):
                    events.append("cleanup")

            runner = LaneRunner.__new__(LaneRunner)
            runner.lane, runner.repository, runner.manifest = "apple-stock", Path(__file__).resolve().parents[2], manifest
            runner.docker, runner.node_package_runner = "/pinned/docker", "/pinned/npx"
            runner.output, runner.finalized_selection = evidence, None
            runner.finalized_identity, runner.cleanup_differences = None, []
            runner._preserve_engine_on_uncertain_guest_cleanup = False
            with (mock.patch("run_lane.implemented_fixtures", return_value=[fixture]),
                  mock.patch("owned_guest_fixture._retained_root", return_value=root / "retained"),
                  mock.patch("owned_guest_fixture.admit_guest_inputs", return_value={}),
                  mock.patch("owned_guest_fixture.OwnedGuestFixtureRunner", Bridge),
                  mock.patch.object(runner, "admit_finalized"),
                  mock.patch.object(runner, "start_engine", side_effect=lambda: events.append("engine")),
                  mock.patch.object(runner, "configure_devcontainer_client"),
                  mock.patch.object(runner, "run_fixture", side_effect=lambda row: (
                      events.append("fixture") or {"id": row.identifier, "status": "passed",
                                                     "durationSeconds": 0.0, "observations": {},
                                                     "differences": [], "diagnostic": ""})),
                  mock.patch.object(runner, "fingerprint", return_value={}),
                  mock.patch.object(runner, "stop_builder"),
                  mock.patch.object(runner, "check_runtime_state_cleanup"),
                  mock.patch.object(runner, "readmit_finalized"),
                  mock.patch.object(runner, "stop_engine")):
                self.assertEqual(runner.run(), 0)

            self.assertLess(events.index("provision"), events.index("engine"))
            self.assertLess(events.index("engine"), events.index("attach"))
            self.assertLess(events.index("attach"), events.index("cleanup"))

    def test_qualifier_and_lane_runner_hash_the_same_harness_closure(self) -> None:
        import qualify_finalized_package as qualifier

        self.assertEqual(tuple(qualifier.PARITY_HARNESS), PARITY_HARNESS)


class RuntimePathTests(unittest.TestCase):
    def test_provider_explicit_binaries_are_first_on_child_path(self) -> None:
        environment = {
            "DEVCONTAINER_CONTAINER_BIN": "/stable/container/bin/container",
            "DEVCONTAINER_COMPOSE_BIN": "/stable/compose/bin/container-compose",
            "HOME": "/Users/operator",
            "PATH": "/current/bin:/usr/bin:/stable/container/bin",
        }
        with (
            mock.patch.dict("run_lane.os.environ", environment, clear=True),
            mock.patch(
                "run_lane.load_manifest",
                return_value={
                    "referencePins": {
                        "devcontainersCli": {
                            "version": "0.88.0",
                        },
                    },
                },
            ),
            mock.patch(
                "run_lane.shutil.which",
                side_effect=["/current/bin/docker", "/current/bin/npx"],
            ),
        ):
            runner = LaneRunner(
                "container-compose",
                Path("/repository"),
                Path("/evidence"),
            )

        self.assertEqual(
            runner.environment["PATH"],
            (
                "/stable/container/bin:/stable/compose/bin:"
                "/current/bin:/usr/bin"
            ),
        )
        self.assertEqual(
            runner.environment["CONTAINER_COMPOSE_CONTAINER"],
            "/stable/container/bin/container",
        )


class CancellationHandlerTests(unittest.TestCase):
    def test_workflow_termination_becomes_a_catchable_cleanup_error(self) -> None:
        with mock.patch("run_lane.signal.signal") as register:
            install_cancellation_handlers()

        self.assertEqual(
            [call.args[0] for call in register.call_args_list],
            [signal.SIGINT, signal.SIGTERM],
        )
        cancel = register.call_args_list[1].args[1]
        with self.assertRaisesRegex(
            ParityError,
            "CLI parity interrupted by SIGTERM",
        ):
            cancel(signal.SIGTERM, None)
        cancel(signal.SIGTERM, None)


class BoundedCommandTests(unittest.TestCase):
    def test_clean_swift_build_can_select_the_live_gate_timeout(self) -> None:
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch(
            "run_lane.subprocess.run",
            return_value=completed,
        ) as run:
            result = run_checked(
                ["swift", "build"],
                cwd=Path("/repository"),
                environment={"PATH": "/usr/bin:/bin"},
                timeout_seconds=1800,
            )

        self.assertIs(result, completed)
        self.assertEqual(run.call_args.kwargs["timeout"], 1800)

    def test_compatibility_socket_is_canonical_and_within_darwin_limit(self) -> None:
        root = create_socket_root()
        try:
            self.assertEqual(root, Path("/tmp").resolve(strict=True) / root.name)
            self.assertEqual(root.resolve(strict=True), root)
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertLess(len(os.fsencode(root / "docker.sock")), 104)
        finally:
            shutil.rmtree(root)


class FingerprintTests(unittest.TestCase):
    def test_direct_cli_reference_is_retained_with_runtime_evidence(self) -> None:
        runner = LaneRunner.__new__(LaneRunner)
        runner.lane = "docker"
        runner.docker = "/usr/bin/docker"
        runner.node_package_runner = "/usr/bin/npx"
        runner.cli_version = "0.88.0"
        runner.cli_reference = {
            "version": "0.88.0",
            "source": "https://github.com/devcontainers/cli",
            "commit": "a" * 40,
            "npmIntegrity": "sha512-" + "b" * 86 + "==",
        }
        runner.repository = Path("/repository")
        runner.environment = {"PATH": "/usr/bin:/bin"}
        completed = [
            mock.Mock(returncode=0, stdout='{"Client":{}}', stderr=""),
            mock.Mock(returncode=0, stdout="0.88.0\n", stderr=""),
        ]

        with (
            mock.patch("run_lane.platform.machine", return_value="arm64"),
            mock.patch(
                "run_lane.platform.platform",
                return_value="macOS-26-arm64",
            ),
            mock.patch(
                "run_lane.subprocess.run",
                side_effect=completed,
            ) as run,
        ):
            fingerprint = runner.fingerprint()

        self.assertEqual(
            fingerprint["devcontainersReference"],
            runner.cli_reference,
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["/usr/bin/npx", "--yes", "@devcontainers/cli@0.88.0", "--version"],
        )


class FixtureProbeTests(unittest.TestCase):
    def test_fixture_probe_uses_the_resolved_remote_workspace(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "probe.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            repository = root / "repository"
            repository.mkdir()
            runner = LaneRunner.__new__(LaneRunner)
            runner.output = root / "evidence"
            runner.repository = repository
            runner.devcontainer_docker = "/usr/bin/docker"
            runner.lane = "apple-stock"
            runner.finalized_selection = {"release": True}
            fixture = Fixture(
                directory=source,
                identifier="fixture",
                expected={"ready": "true"},
                backends=("docker",),
                runner="devcontainer",
            )
            calls: list[list[str]] = []
            up = mock.Mock(
                returncode=0,
                stdout=(
                    '{"containerId":"fixture",'
                    '"remoteWorkspaceFolder":"/workspaces/fixture"}'
                ),
                stderr="",
            )
            probe = mock.Mock(returncode=0, stdout="ready=true\n", stderr="")

            def devcontainer(arguments: list[str], timeout: int) -> mock.Mock:
                self.assertIn(timeout, {120, 1800})
                calls.append(arguments)
                return up if len(calls) == 1 else probe

            runner.devcontainer = devcontainer
            runner.additional_fixture_observations = mock.Mock(return_value={})
            runner.cleanup_fixture = mock.Mock(return_value="")
            with (
                mock.patch("run_lane.assert_contract", return_value=[]),
                mock.patch.object(
                    runner,
                    "lifecycle_backend_arguments",
                    wraps=runner.lifecycle_backend_arguments,
                ) as backend_arguments,
            ):
                result = runner.run_fixture(fixture)

        self.assertEqual(result["status"], "passed")
        backend_arguments.assert_called_once_with()
        self.assertNotIn("--docker-path", calls[0])
        self.assertEqual(
            calls[1][-6:],
            [
                "--",
                "/bin/sh",
                '-c',
                'cd "$1" && exec /bin/sh ./probe.sh',
                "probe",
                "/workspaces/fixture",
            ],
        )

    def test_warm_d05_build_is_untimed_and_cache_failure_does_not_change_functional_pass(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace_root = root / "workspace-root"
            workspace = workspace_root / "D05-features"
            devcontainer = workspace / ".devcontainer"
            devcontainer.mkdir(parents=True)
            for relative in ("contract.json", ".devcontainer/devcontainer.json",
                             ".devcontainer/devcontainer-lock.json", "probe.sh"):
                path = workspace / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")
            runner = LaneRunner.__new__(LaneRunner)
            runner.output = root / "evidence"
            runner.repository = root / "repository"
            runner.repository.mkdir()
            runner.devcontainer_docker = "/usr/bin/docker"
            runner.lane = "apple-stock"
            runner.finalized_selection = {"release": True}
            runner.candidate_selection = None
            runner.finalized_identity = {
                "scope": "finalized-native-package-runtime-input", "runtimeProfile": "stock",
                "sourceCommit": "a" * 40, "archiveSHA256": "b" * 64,
                "candidateReceiptSHA256": "c" * 64,
            }
            runner.candidate_identity = None
            runner.d05_cache_state = "warm"
            fixture = Fixture(directory=root / "source", identifier="D05-features",
                              expected={"ready": "true"}, backends=("docker",), runner="devcontainer")
            runner.create_fixture_workspace = mock.Mock(return_value=(workspace_root, workspace))
            up = mock.Mock(returncode=0, stdout='{"remoteWorkspaceFolder":"/workspace"}', stderr="")
            probe = mock.Mock(returncode=0, stdout="ready=true\n", stderr="")
            calls = []

            def invoke(arguments, timeout):
                calls.append(("cli", arguments[0], timeout))
                return up if arguments[0] == "up" else probe

            runner.devcontainer = invoke
            runner.remote_workspace_from_up = mock.Mock(return_value="/workspace")
            runner.additional_fixture_observations = mock.Mock(return_value={})
            runner.cleanup_fixture = mock.Mock(side_effect=lambda _fixture: calls.append(("cleanup",)) or "")
            runner.cleanup_fixture_workspace = mock.Mock(return_value="")
            clocks = iter((10.0, 20.0, 21.0, 22.0))

            def monotonic():
                calls.append(("clock",))
                return next(clocks)

            def monotonic_ns():
                calls.append(("clock-ns",))
                return 100

            def prepare(**kwargs):
                calls.append(("warmup",))
                return {"status": "warmup_completed_unverified"}

            def verify(**kwargs):
                calls.append(("verify",))
                (kwargs["evidence_dir"] / "cache-verification.json").write_text("{}\n")
                return {"status": "not_comparable", "performanceComparisonEligible": False,
                        "reason": "one stage was not cached", "warmupReceiptSHA256": "d" * 64}

            with (mock.patch("run_lane.prepare_d05_feature_cache", side_effect=prepare),
                  mock.patch("run_lane.verify_d05_feature_cache", side_effect=verify),
                  mock.patch("run_lane.time.monotonic", side_effect=monotonic),
                  mock.patch("run_lane.time.monotonic_ns", side_effect=monotonic_ns),
                  mock.patch("run_lane.assert_contract", return_value=[])):
                result = runner.run_fixture(fixture)

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["durationSeconds"], 11.0)
        self.assertFalse(runner.d05_feature_cache_diagnostic["performanceComparisonEligible"])
        self.assertLess(calls.index(("warmup",)), calls.index(("clock-ns",)))
        self.assertLess(calls.index(("clock-ns",)), calls.index(("cli", "up", 1800)))
        self.assertLess(calls.index(("cleanup",)), calls.index(("verify",)))

    def test_warmup_invocation_error_keeps_functional_result_and_marks_cache_ineligible(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace_root = root / "workspace-root"
            workspace = workspace_root / "D05-features"
            workspace.mkdir(parents=True)
            runner = LaneRunner.__new__(LaneRunner)
            runner.output = root / "evidence"
            runner.repository = root / "repository"
            runner.repository.mkdir()
            runner.devcontainer_docker = "/usr/bin/docker"
            runner.lane = "apple-stock"
            runner.finalized_selection = {"release": True}
            runner.candidate_selection = None
            runner.finalized_identity = {
                "scope": "finalized-native-package-runtime-input", "runtimeProfile": "stock",
                "sourceCommit": "a" * 40, "archiveSHA256": "b" * 64,
                "candidateReceiptSHA256": "c" * 64,
            }
            runner.candidate_identity = None
            runner.d05_cache_state = "warm"
            fixture = Fixture(directory=root / "source", identifier="D05-features",
                              expected={"ready": "true"}, backends=("docker",), runner="devcontainer")
            runner.create_fixture_workspace = mock.Mock(return_value=(workspace_root, workspace))
            up = mock.Mock(returncode=0, stdout='{"remoteWorkspaceFolder":"/workspace"}', stderr="")
            probe = mock.Mock(returncode=0, stdout="ready=true\n", stderr="")
            runner.devcontainer = mock.Mock(side_effect=[up, probe])
            runner.remote_workspace_from_up = mock.Mock(return_value="/workspace")
            runner.additional_fixture_observations = mock.Mock(return_value={})
            runner.cleanup_fixture = mock.Mock(return_value="")
            runner.cleanup_fixture_workspace = mock.Mock(return_value="")

            with (mock.patch("run_lane.prepare_d05_feature_cache", side_effect=OSError("warmup failed")),
                  mock.patch("run_lane.verify_d05_feature_cache") as verify,
                  mock.patch("run_lane.assert_contract", return_value=[])):
                result = runner.run_fixture(fixture)

        self.assertEqual(result["status"], "passed")
        self.assertFalse(runner.d05_feature_cache_diagnostic["performanceComparisonEligible"])
        self.assertEqual(runner.d05_feature_cache_diagnostic["reason"], "cache warmup failed: OSError")
        verify.assert_not_called()

    def test_frozen_reuse_and_rebuild_construction_uses_backend_helper(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            workspace = root / "fixture"
            devcontainer_dir = workspace / ".devcontainer"
            devcontainer_dir.mkdir(parents=True)
            (devcontainer_dir / "devcontainer-lock.json").write_text("{}", encoding="utf-8")
            raw = root / "raw"
            raw.mkdir()
            runner = LaneRunner.__new__(LaneRunner)
            runner.lane = "apple-stock"
            runner.finalized_selection = {"release": True}
            runner.devcontainer_docker = "/qualified/docker"
            runner.repository = repository
            runner.environment = {}
            runner.docker = "/qualified/docker"
            fixture = Fixture(
                directory=workspace,
                identifier="D07-reuse-cleanup",
                expected={},
                backends=("docker",),
                runner="devcontainer",
            )
            frozen = mock.Mock(returncode=1, stdout="", stderr="frozen rejection")
            reused = mock.Mock(returncode=0, stdout='{"containerId":"reused"}', stderr="")
            rebuilt = mock.Mock(returncode=0, stdout='{"containerId":"rebuilt"}', stderr="")
            runner.devcontainer = mock.Mock(side_effect=[frozen, reused, rebuilt])
            up = mock.Mock(returncode=0, stdout='{"containerId":"first"}', stderr="")
            subprocess_result = subprocess.CompletedProcess([], 0, stdout="1|1", stderr="")

            with (
                mock.patch.object(
                    runner,
                    "lifecycle_backend_arguments",
                    wraps=runner.lifecycle_backend_arguments,
                ) as backend_arguments,
                mock.patch("run_lane.subprocess.run", return_value=subprocess_result),
            ):
                runner.validate_feature_lock(fixture, raw)
                runner.validate_reuse_cleanup(fixture, raw, up)

        backend_arguments.assert_has_calls([mock.call(), mock.call(), mock.call()])
        self.assertEqual(backend_arguments.call_count, 3)
        for invocation in runner.devcontainer.call_args_list:
            self.assertNotIn("--docker-path", invocation.args[0])

    def test_remote_workspace_requires_an_absolute_path(self) -> None:
        runner = LaneRunner.__new__(LaneRunner)

        with self.assertRaisesRegex(
            ParityError,
            "absolute remoteWorkspaceFolder",
        ):
            runner.remote_workspace_from_up('{"remoteWorkspaceFolder":"relative"}')

    def test_fixture_reports_a_workspace_cleanup_failure(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = LaneRunner.__new__(LaneRunner)
            runner.output = root / "evidence"
            runner.repository = root / "repository"
            runner.repository.mkdir()
            runner.devcontainer_docker = "/usr/bin/docker"
            runner.lane = "docker"
            runner.finalized_selection = None
            workspace_root = root / "workspace-root"
            workspace = workspace_root / "fixture"
            workspace.mkdir(parents=True)
            fixture = Fixture(
                directory=root / "source",
                identifier="fixture",
                expected={"ready": "true"},
                backends=("docker",),
                runner="devcontainer",
            )
            up = mock.Mock(
                returncode=0,
                stdout=(
                    '{"containerId":"fixture",'
                    '"remoteWorkspaceFolder":"/workspaces/fixture"}'
                ),
                stderr="",
            )
            probe = mock.Mock(returncode=0, stdout="ready=true\n", stderr="")
            runner.create_fixture_workspace = mock.Mock(
                return_value=(workspace_root, workspace)
            )
            runner.devcontainer = mock.Mock(side_effect=[up, probe])
            runner.additional_fixture_observations = mock.Mock(return_value={})
            runner.cleanup_fixture = mock.Mock(return_value="")
            runner.cleanup_fixture_workspace = mock.Mock(
                return_value="ERROR: fixture workspace cleanup failed"
            )

            with mock.patch("run_lane.assert_contract", return_value=[]):
                result = runner.run_fixture(fixture)

        self.assertEqual(result["status"], "failed")
        self.assertIn("workspace cleanup failed", result["diagnostic"])

    def test_engine_fixture_skips_the_bind_mount_workspace_copy(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = LaneRunner.__new__(LaneRunner)
            runner.output = root / "evidence"
            fixture = Fixture(
                directory=root / "source",
                identifier="engine-fixture",
                expected={},
                backends=("docker",),
                runner="engine",
            )
            expected = {"id": "engine-fixture", "status": "passed"}
            runner.run_engine_fixture = mock.Mock(return_value=expected)

            result = runner.run_fixture(fixture)

        self.assertIs(result, expected)
        runner.run_engine_fixture.assert_called_once()


class FixtureWorkspaceTests(unittest.TestCase):
    def test_fixture_workspace_is_copied_under_the_repository_build_root(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            source = root / "source"
            source.mkdir()
            (source / "probe.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            runner = LaneRunner.__new__(LaneRunner)
            runner.repository = repository
            fixture = Fixture(
                directory=source,
                identifier="fixture",
                expected={},
                backends=("docker",),
                runner="devcontainer",
            )

            workspace_root, workspace = runner.create_fixture_workspace(fixture)

            self.assertEqual(workspace, workspace_root / "fixture")
            self.assertTrue((workspace / "probe.sh").is_file())
            self.assertEqual(
                (workspace_root / ".devcontainer-parity-workspace-root").read_text(
                    encoding="utf-8"
                ),
                "devcontainer parity workspace root v1\n",
            )
            self.assertEqual(
                workspace_root.parent,
                repository / ".build" / "parity-workspaces",
            )
            self.assertEqual(runner.cleanup_fixture_workspace(workspace_root), "")
            self.assertFalse(workspace_root.exists())

    def test_workspace_cleanup_preserves_a_root_without_its_marker(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            source = root / "source"
            source.mkdir()
            runner = LaneRunner.__new__(LaneRunner)
            runner.repository = repository
            fixture = Fixture(
                directory=source,
                identifier="fixture",
                expected={},
                backends=("docker",),
                runner="devcontainer",
            )
            workspace_root, _ = runner.create_fixture_workspace(fixture)
            (workspace_root / ".devcontainer-parity-workspace-root").unlink()

            cleanup = runner.cleanup_fixture_workspace(workspace_root)

            self.assertIn("marker is unsafe", cleanup)
            self.assertTrue(workspace_root.is_dir())

    def test_workspace_cleanup_rejects_a_missing_root(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            runner = LaneRunner.__new__(LaneRunner)
            runner.repository = repository

            cleanup = runner.cleanup_fixture_workspace(
                repository / ".build" / "parity-workspaces" / "missing"
            )

            self.assertIn("unsafe fixture workspace root", cleanup)

    def test_workspace_copy_failure_removes_its_owned_root(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            source = root / "source"
            source.mkdir()
            runner = LaneRunner.__new__(LaneRunner)
            runner.repository = repository
            fixture = Fixture(
                directory=source,
                identifier="fixture",
                expected={},
                backends=("docker",),
                runner="devcontainer",
            )

            with mock.patch(
                "run_lane.shutil.copytree",
                side_effect=OSError("copy failed"),
            ):
                with self.assertRaisesRegex(OSError, "copy failed"):
                    runner.create_fixture_workspace(fixture)

            self.assertFalse((repository / ".build" / "parity-workspaces").exists())

    def test_workspace_cleanup_rejects_escaped_and_modified_roots(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            source = root / "source"
            source.mkdir()
            runner = LaneRunner.__new__(LaneRunner)
            runner.repository = repository
            fixture = Fixture(
                directory=source,
                identifier="fixture",
                expected={},
                backends=("docker",),
                runner="devcontainer",
            )
            escaped = root / "escaped"
            escaped.mkdir()
            (escaped / ".devcontainer-parity-workspace-root").write_text(
                "devcontainer parity workspace root v1\n",
                encoding="utf-8",
            )
            workspace_root, _ = runner.create_fixture_workspace(fixture)
            (workspace_root / ".devcontainer-parity-workspace-root").write_text(
                "changed\n",
                encoding="utf-8",
            )

            escaped_cleanup = runner.cleanup_fixture_workspace(escaped)
            modified_cleanup = runner.cleanup_fixture_workspace(workspace_root)

            self.assertIn("escaped its parent", escaped_cleanup)
            self.assertIn("did not match", modified_cleanup)
            self.assertTrue(escaped.is_dir())
            self.assertTrue(workspace_root.is_dir())

    def test_workspace_cleanup_leaves_a_shared_parent_and_reports_removal_errors(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            source = root / "source"
            source.mkdir()
            runner = LaneRunner.__new__(LaneRunner)
            runner.repository = repository
            fixture = Fixture(
                directory=source,
                identifier="fixture",
                expected={},
                backends=("docker",),
                runner="devcontainer",
            )
            first_root, _ = runner.create_fixture_workspace(fixture)
            second_root, _ = runner.create_fixture_workspace(fixture)

            self.assertEqual(runner.cleanup_fixture_workspace(first_root), "")
            self.assertTrue(second_root.parent.is_dir())

            with mock.patch(
                "run_lane.shutil.rmtree",
                side_effect=OSError("permission denied"),
            ):
                failed_cleanup = runner.cleanup_fixture_workspace(second_root)

            self.assertIn("cleanup failed", failed_cleanup)
            self.assertTrue(second_root.is_dir())


class PortEvidenceTests(unittest.TestCase):
    def test_failed_host_connections_are_preserved_as_evidence(self) -> None:
        with TemporaryDirectory() as temporary:
            runner = LaneRunner.__new__(LaneRunner)
            runner.repository = Path("/repository")
            runner.environment = {"PATH": "/usr/bin:/bin"}
            runner.docker = "/usr/bin/docker"
            completed = [
                mock.Mock(returncode=17, stdout="", stderr="collision"),
                mock.Mock(returncode=0, stdout="", stderr=""),
            ]

            with (
                mock.patch(
                    "run_lane.urllib.request.urlopen",
                    side_effect=urllib.error.URLError("No route to host"),
                ),
                mock.patch("run_lane.time.sleep"),
                mock.patch(
                    "run_lane.subprocess.run",
                    side_effect=completed,
                ),
            ):
                observations = runner.validate_ports(Path(temporary))

            evidence = (
                Path(temporary) / "host-connectivity.log"
            ).read_text(encoding="utf-8")

        self.assertEqual(
            observations,
            {
                "collision_rejected": "true",
                "host_connectivity": "false",
            },
        )
        self.assertIn("attempt 1: URLError: <urlopen error No route", evidence)
        self.assertIn("attempt 50: URLError: <urlopen error No route", evidence)


class CleanupFixtureTests(unittest.TestCase):
    def test_failed_compose_down_is_reported_and_all_project_containers_removed(
        self,
    ) -> None:
        runner = LaneRunner.__new__(LaneRunner)
        runner.repository = Path("/repository")
        runner.environment = {"PATH": "/usr/bin:/bin"}
        runner.docker = "/usr/bin/docker"
        runner.lane = "apple-stock"
        fixture = SimpleNamespace(directory=Path("/fixtures/C02"))
        completed = [
            mock.Mock(returncode=0, stdout="primary\n", stderr=""),
            mock.Mock(returncode=0, stdout="parity-project\n", stderr=""),
            mock.Mock(
                returncode=17,
                stdout="",
                stderr="network still has active endpoints\n",
            ),
            mock.Mock(returncode=0, stdout="primary\ndependency\n", stderr=""),
            mock.Mock(
                returncode=0,
                stdout="primary\ndependency\n",
                stderr="",
            ),
        ]

        with (
            mock.patch.object(Path, "is_file", return_value=True),
            mock.patch.object(
                Path,
                "read_text",
                return_value='{"dockerComposeFile":"../compose.yaml"}',
            ),
            mock.patch("run_lane.subprocess.run", side_effect=completed) as run,
        ):
            output = runner.cleanup_fixture(fixture)

        self.assertIn("ERROR: compose down exited 17", output)
        self.assertIn("network still has active endpoints", output)
        self.assertEqual(
            run.call_args_list[-1].args[0],
            ["/usr/bin/docker", "rm", "-f", "primary", "dependency"],
        )


class BuilderCleanupTests(unittest.TestCase):
    def test_container_compose_client_routes_compose_subcommand_to_wrapper(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            docker = root / "docker"
            docker.write_text(
                "#!/bin/sh\nprintf 'docker:%s\\n' \"$*\"\n",
                encoding="utf-8",
            )
            docker.chmod(0o700)
            repository = root / "repository"
            compose = repository / ".build" / "debug" / "devcontainer-compose"
            compose.parent.mkdir(parents=True)
            compose.write_text(
                "#!/bin/sh\nprintf 'compose:%s\\n' \"$*\"\n",
                encoding="utf-8",
            )
            compose.chmod(0o700)
            runner = LaneRunner.__new__(LaneRunner)
            runner.lane = "container-compose"
            runner.repository = repository
            runner.docker = str(docker)
            runner.devcontainer_docker = runner.docker
            runner.socket_root = root
            runner.environment = {}
            runner.finalized_identity = {"runtimeProfile": "stock"}

            runner.configure_devcontainer_client()
            buildx = subprocess.run([runner.devcontainer_docker, "buildx", "version"], capture_output=True, check=False)
            compose_version = subprocess.run(
                [runner.devcontainer_docker, "compose", "version", "--short"],
                capture_output=True,
                check=False,
                text=True,
            )
            docker_version = subprocess.run(
                [runner.devcontainer_docker, "version"],
                capture_output=True,
                check=False,
                text=True,
            )
            wrapper_source = Path(runner.devcontainer_docker).read_text(
                encoding="utf-8"
            )

        self.assertEqual(buildx.returncode, 1)
        self.assertEqual(runner.environment["DOCKER_BUILDKIT"], "0")
        self.assertEqual(compose_version.returncode, 0)
        self.assertEqual(compose_version.stdout.strip(), "compose:version --short")
        self.assertEqual(docker_version.returncode, 0)
        self.assertEqual(docker_version.stdout.strip(), "docker:version")
        self.assertIn('exec "$compose" "$@"', wrapper_source)
        self.assertEqual(
            runner.environment["DEVCONTAINER_DOCKER_BIN"],
            runner.devcontainer_docker,
        )

    def test_stock_client_reports_buildx_unavailable(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            docker = root / "docker"
            docker.write_text(
                "#!/bin/sh\nprintf 'forwarded:%s\\n' \"$*\"\n",
                encoding="utf-8",
            )
            docker.chmod(0o700)
            runner = LaneRunner.__new__(LaneRunner)
            runner.lane = "apple-stock"
            runner.docker = str(docker)
            runner.devcontainer_docker = runner.docker
            runner.socket_root = root
            runner.environment = {}

            runner.configure_devcontainer_client()
            buildx = subprocess.run(
                [runner.docker, "buildx", "version"],
                capture_output=True,
                check=False,
                text=True,
            )
            forwarded = subprocess.run(
                [runner.docker, "version"],
                capture_output=True,
                check=False,
                text=True,
            )
            legacy_build = subprocess.run(
                [
                    runner.docker,
                    "build",
                    "--progress",
                    "plain",
                    "--load",
                    ".",
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            wrapper_source = Path(runner.docker).read_text(encoding="utf-8")

        self.assertNotEqual(buildx.returncode, 0)
        self.assertIn("unknown command", buildx.stderr)
        self.assertEqual(forwarded.returncode, 0)
        self.assertEqual(forwarded.stdout.strip(), "forwarded:version")
        self.assertEqual(legacy_build.returncode, 0)
        self.assertEqual(legacy_build.stdout.strip(), "forwarded:build .")
        self.assertTrue(wrapper_source.startswith("#!/bin/sh\n"))
        self.assertTrue(wrapper_source.endswith('exec "$docker" "$@"\n'))
        self.assertEqual(runner.environment["DOCKER_BUILDKIT"], "0")
        self.assertEqual(
            runner.environment["DEVCONTAINER_DOCKER_BIN"],
            runner.devcontainer_docker,
        )
        self.assertEqual(runner.docker, runner.devcontainer_docker)

    def test_resolver_nameservers_rejects_invalid_and_duplicate_entries(
        self,
    ) -> None:
        self.assertEqual(
            resolver_nameservers(
                """
                nameserver 192.0.2.53
                nameserver 2001:db8::53
                nameserver fe80::1%en0
                nameserver 192.0.2.53
                nameserver invalid.example
                search example.test
                """
            ),
            ["192.0.2.53", "2001:db8::53", "fe80::1%en0"],
        )

    def test_provider_builder_disables_restart_and_uses_host_dns(self) -> None:
        with TemporaryDirectory() as temporary:
            runner = LaneRunner.__new__(LaneRunner)
            runner.lane = "container-compose"
            runner.repository = Path("/repository")
            runner.environment = {"PATH": "/usr/bin:/bin"}
            runner.docker = "/usr/bin/docker"
            runner.output = Path(temporary)
            completed = [
                mock.Mock(returncode=0, stdout="", stderr=""),
                mock.Mock(returncode=0, stdout="", stderr=""),
            ]

            with (
                mock.patch.object(
                    runner,
                    "docker_container_inventory",
                    side_effect=[set(), {"builder-container-id"}],
                ),
                mock.patch(
                    "run_lane.subprocess.run",
                    side_effect=completed,
                ) as run,
                mock.patch(
                    "run_lane.Path.read_text",
                    return_value="nameserver 192.0.2.53\n",
                ),
                mock.patch("run_lane.os.getpid", return_value=123),
            ):
                runner.prepare_builder()

        self.assertEqual(
            run.call_args_list[0].args[0],
            [
                "/usr/bin/docker",
                "buildx",
                "create",
                "--name",
                "devcontainer-parity-container-compose-123",
                "--driver",
                "docker-container",
                "--driver-opt",
                "restart-policy=no",
                "--buildkitd-config",
                str(Path(temporary) / "buildkitd.toml"),
            ],
        )
        self.assertEqual(
            runner.environment["BUILDX_BUILDER"],
            "devcontainer-parity-container-compose-123",
        )
        self.assertEqual(
            runner.builder_container_ids,
            {"builder-container-id"},
        )

    def test_docker_builder_uses_daemon_integrated_buildkit(self) -> None:
        with TemporaryDirectory() as temporary:
            runner = LaneRunner.__new__(LaneRunner)
            runner.lane = "docker"
            runner.repository = Path("/repository")
            runner.environment = {"PATH": "/usr/bin:/bin"}
            runner.docker = "/usr/bin/docker"
            runner.output = Path(temporary)
            runner.builder_name = None

            with mock.patch(
                "run_lane.subprocess.run",
                return_value=mock.Mock(returncode=0, stdout="ready", stderr=""),
            ) as run:
                runner.prepare_builder()

        self.assertEqual(
            run.call_args.args[0],
            [
                "/usr/bin/docker",
                "buildx",
                "inspect",
                "--bootstrap",
                "default",
            ],
        )
        self.assertEqual(runner.environment["BUILDX_BUILDER"], "default")
        self.assertIsNone(runner.builder_name)

    def test_builder_cleanup_reports_and_removes_exact_leaked_container(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            runner = LaneRunner.__new__(LaneRunner)
            runner.repository = Path("/repository")
            runner.environment = {"PATH": "/usr/bin:/bin"}
            runner.docker = "/usr/bin/docker"
            runner.builder_name = "devcontainer-parity-fixture"
            runner.builder_container_ids = {"builder-container-id"}
            runner.cleanup_differences = []
            runner.output = Path(temporary)
            completed = [
                mock.Mock(returncode=0, stdout="", stderr=""),
                mock.Mock(
                    returncode=0,
                    stdout="builder-container-id\n",
                    stderr="",
                ),
            ]

            with (
                mock.patch(
                    "run_lane.subprocess.run",
                    side_effect=completed,
                ) as run,
                mock.patch(
                    "run_lane.time.monotonic",
                    side_effect=[0.0, 16.0],
                ),
            ):
                runner.stop_builder()

        self.assertEqual(
            run.call_args_list[-1].args[0],
            ["/usr/bin/docker", "rm", "-f", "builder-container-id"],
        )
        self.assertEqual(
            runner.cleanup_differences,
            [
                "isolated buildx builder leaked container(s): "
                "builder-container-id"
            ],
        )
        self.assertIsNone(runner.builder_name)
        self.assertEqual(runner.builder_container_ids, set())

    def test_runtime_state_cleanup_reports_durable_leaks(self) -> None:
        with TemporaryDirectory() as temporary:
            runner = LaneRunner.__new__(LaneRunner)
            runner.lane = "container-compose"
            runner.runtime_root = Path(temporary)
            runner.cleanup_differences = []
            state = runner.runtime_root / "state.sqlite"
            with closing(sqlite3.connect(state)) as database, database:
                database.execute("CREATE TABLE projects (key TEXT)")
                database.execute(
                    "CREATE TABLE runtime_containers (runtime_id TEXT)"
                )
                database.execute("INSERT INTO projects VALUES ('fixture')")
                database.execute(
                    "INSERT INTO runtime_containers VALUES ('fixture')"
                )

            runner.check_runtime_state_cleanup()

        self.assertEqual(
            runner.cleanup_differences,
            [
                "lane state leaked 1 project claim(s) and "
                "1 runtime container record(s)"
            ],
        )


if __name__ == "__main__":
    unittest.main()
