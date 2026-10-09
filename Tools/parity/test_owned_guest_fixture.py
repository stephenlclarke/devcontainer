#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# SPDX-License-Identifier: Apache-2.0

"""Inert failure behavior for the owned released-guest bridge."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import pwd
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from owned_guest_fixture import (ApiRuntimeView, OwnedGuestFixtureRunner,
                                 _active_provider_home, _safe_attachment_diagnostic_trace,
                                 admit_guest_inputs)
from parity_lib import ParityError


REPOSITORY = Path(__file__).resolve().parents[2]
ACCOUNT_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)


class OwnedGuestFailureTests(unittest.TestCase):
    def test_eight_attachment_generations_reject_non_candidate_and_multi_fixture_scope(self) -> None:
        runner = SimpleNamespace(lane="apple-stock", repository=REPOSITORY,
                                 candidate_selection=None)
        with self.assertRaisesRegex(ParityError, "native candidate diagnostic"):
            OwnedGuestFixtureRunner(runner, [], fixture_selection=("E07-init-attachment",),
                                    attachment_generations=8)
        runner.candidate_selection = {"candidate_invocation": "candidate"}
        with self.assertRaisesRegex(ParityError, "native candidate diagnostic"):
            OwnedGuestFixtureRunner(runner, [], fixture_selection=("D05-features", "E07-init-attachment"),
                                    attachment_generations=8)

    def test_released_guest_rejects_eight_generations_without_native_candidate_identity(self) -> None:
        guest_runtime = importlib.import_module("guest_runtime")
        owner = {"identity": {"fixture": "E07-init-attachment"}}
        with self.assertRaisesRegex(ValueError, "owned native candidate E07"):
            guest_runtime.ReleasedGuest({}, "E07-init-attachment", Path("/tmp/case"), owner,
                                        object(), "/provider/container", Path("/tmp/engine.sock"),
                                        attachment_generations=8)
        candidate_owner = {"identity": {
            "candidateInvocation": "candidate", "candidateReceiptSHA256": "a" * 64,
            "archiveSHA256": "b" * 64,
        }}
        with self.assertRaisesRegex(ValueError, "owned native candidate E07"):
            guest_runtime.ReleasedGuest({}, "E07-init-attachment", Path("/tmp/case"), candidate_owner,
                                        object(), "", Path("/tmp/engine.sock"), attachment_generations=8)

    def test_failed_e07_projection_retains_generation_numbers_and_bounded_stage_rows(self) -> None:
        events = [{"stage": "duplex", "durationNS": generation, "generation": generation,
                   "stream": {"inputAcceptedBytes": generation, "payload": "omit"}}
                  for generation in range(1, 9)]
        trace = _safe_attachment_diagnostic_trace(events)
        self.assertEqual([item["generation"] for item in trace], list(range(1, 9)))
        self.assertTrue(all(item["stream"] == {"inputAcceptedBytes": index}
                            for index, item in enumerate(trace, 1)))

    def test_multi_fixture_candidate_guard_is_reused_for_later_e07_case_and_cleanup(self) -> None:
        import guest_runtime
        import owned_guest_fixture

        with tempfile.TemporaryDirectory(dir=ACCOUNT_HOME) as temporary:
            base = Path(temporary).resolve()
            campaign = base / "campaign"
            campaign.mkdir(mode=0o700)
            home = campaign / "apple-stock-home"
            home.mkdir(mode=0o700)
            (home / "container").mkdir(mode=0o700)
            retained = base / "workflow"
            retained.mkdir(mode=0o700)
            guard_path = retained / "runtime-admission.json"
            selection = ("C03-compose-resources", "D05-features", "E07-init-attachment")
            candidate = {
                "scope": "local-candidate-integration-only",
                "candidateInvocation": "47387ce0-3819-4eca-b06e-11356ce4568d",
                "sourceCommit": "a" * 40, "runtimeProfile": "stock",
                "assetSHA256": "b" * 64, "candidateReceiptSHA256": "c" * 64,
            }
            guard_identity = {
                "campaign": "candidate-campaign", "sourceCommit": candidate["sourceCommit"],
                "scope": "unsigned-native-candidate-diagnostic",
                "candidateInvocation": candidate["candidateInvocation"],
                "candidateReceiptSHA256": candidate["candidateReceiptSHA256"],
                "archiveSHA256": candidate["assetSHA256"], "runtimeProfile": "stock",
                "diagnosticFixtures": sorted(selection),
            }
            guard_path.write_text(json.dumps({"identity": guard_identity, "root": str(campaign)},
                                             sort_keys=True))
            guard_path.chmod(0o600)
            environment = {"HOME": str(home), "CONTAINER_APP_ROOT": str(home / "container"),
                           "DEVCONTAINER_PARITY_RETAINED_ROOT": str(retained),
                           "DEVCONTAINER_PARITY_GUARD": str(guard_path)}
            runner = SimpleNamespace(lane="apple-stock", output=campaign / "apple-stock",
                                     environment=environment, finalized_identity=None,
                                     candidate_identity=candidate,
                                     cleanup_differences=[],
                                     _preserve_engine_on_uncertain_guest_cleanup=False)
            owner_home = {"identity": {"campaign": "candidate-campaign", "lane": "apple-stock",
                                        "sourceCommit": candidate["sourceCommit"]}, "root": str(home)}
            (home / "owner.json").write_text(json.dumps(owner_home, sort_keys=True) + "\n")
            (home / "owner.json").chmod(0o600)

            fixture = SimpleNamespace(identifier="E07-init-attachment", expected={})
            case_root = campaign / "owned-guest-runtime" / "apple-stock" / fixture.identifier
            case_root.mkdir(mode=0o700, parents=True)
            case_identity = {"campaign": campaign.name, "lane": "apple-stock",
                             "fixture": fixture.identifier,
                             "endpointSHA256": hashlib.sha256(b"/tmp/engine.sock").hexdigest(),
                             **owned_guest_fixture._admitted_package_guest_identity(runner)}
            case_owner = {"identity": case_identity, "root": str(case_root)}
            (case_root / "owner.json").write_bytes(
                json.dumps(case_owner, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
            raw = base / "raw"
            raw.mkdir(mode=0o700)

            class Journal:
                def __init__(self):
                    self.entries = {}

                def put(self, name, payload):
                    self.entries[name] = payload

                def receipt(self):
                    return {"status": "restored"}

            journal = Journal()
            selections = []

            class Runtime:
                def __init__(self, _runner, _journal, _socket, _compose, fixture_selection):
                    self.journal = _journal
                    self.fixture_selection = fixture_selection

                def verify(self):
                    selections.append(self.fixture_selection)
                    owned_guest_fixture._active_provider_home(
                        runner, fixture_selection=self.fixture_selection)

            class Guest:
                def __init__(self, *_args, **_kwargs):
                    pass

                def operation(self):
                    return {"ready": "true"}

                def cleanup(self):
                    return {"status": "passed", "remainingOwnedResources": []}

            bridge = OwnedGuestFixtureRunner.__new__(OwnedGuestFixtureRunner)
            bridge.runner, bridge.repository, bridge.lane = runner, REPOSITORY, "apple-stock"
            bridge.fixtures = [fixture]
            bridge.fixture_selection = selection
            bridge.retained = retained
            bridge.inputs = {"workload": {"image": {}}}
            bridge.socket, bridge.compose, bridge.container = Path("/tmp/engine.sock"), None, "/provider/container"
            bridge.preparation_error = None
            bridge.preparation = (home, journal, SimpleNamespace(), owner_home)
            bridge._case_paths = mock.Mock(return_value=(case_root, journal, case_owner))
            bridge._provision_event_sequence = 0
            bridge.image_preexisting = False
            bridge.loaded_image_id = None

            with (mock.patch.object(owned_guest_fixture, "LaneRuntimeView", Runtime),
                  mock.patch.object(guest_runtime, "ReleasedGuest", Guest),
                  mock.patch.object(owned_guest_fixture, "assert_contract", return_value=[]),
                  mock.patch.object(owned_guest_fixture, "ACCOUNT_HOME", base)):
                owned_guest_fixture._active_provider_home(runner, fixture_selection=selection)
                result = bridge.run(fixture, raw)

            self.assertEqual(result["status"], "passed")
            self.assertEqual(selections, [selection, selection])
            self.assertEqual(case_identity["sourceCommit"], candidate["sourceCommit"])
            self.assertEqual(case_identity["archiveSHA256"], candidate["assetSHA256"])
            self.assertEqual(case_identity["candidateInvocation"], candidate["candidateInvocation"])
            self.assertEqual(case_identity["candidateReceiptSHA256"], candidate["candidateReceiptSHA256"])

            runner.attachment_generations = 8
            guard_identity["diagnosticFixtures"] = ["E07-init-attachment"]
            guard_identity["attachmentGenerations"] = 8
            guard_path.write_text(json.dumps({"identity": guard_identity, "root": str(campaign)},
                                             sort_keys=True))
            guard_path.chmod(0o600)
            self.assertEqual(owned_guest_fixture._active_provider_home(
                runner, fixture_selection=("E07-init-attachment",))[0], home)
            guard_identity.pop("attachmentGenerations")
            guard_path.write_text(json.dumps({"identity": guard_identity, "root": str(campaign)},
                                             sort_keys=True))
            guard_path.chmod(0o600)
            with self.assertRaisesRegex(ParityError, "campaign guard differs"):
                owned_guest_fixture._active_provider_home(
                    runner, fixture_selection=("E07-init-attachment",))

    def test_failed_e07_retains_only_bounded_stage_and_stream_diagnostics(self) -> None:
        import guest_runtime
        import owned_guest_fixture

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, raw = base / "case", base / "raw"
            root.mkdir(mode=0o700)
            raw.mkdir(mode=0o700)
            owner = {"identity": {"campaign": "campaign", "lane": "apple-stock",
                                  "fixture": "E07-init-attachment", "sourceCommit": "a" * 40,
                                  "archiveSHA256": "b" * 64, "endpointSHA256": "c" * 64},
                     "root": str(root)}
            (root / "owner.json").write_bytes(
                json.dumps(owner, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())

            class Journal:
                def __init__(self):
                    self.entries = {}

                def put(self, name, payload):
                    self.entries[name] = payload

                def records(self):
                    return dict(self.entries)

                def receipt(self):
                    return {"status": "restored"}

            class Runtime:
                def __init__(self, *args, **_kwargs):
                    self.journal = args[1]

                def verify(self):
                    pass

            journal = Journal()

            class Guest:
                def __init__(self, *_args, **kwargs):
                    self.observe = kwargs["observe"]

                def operation(self):
                    self.observe({
                        "method": "POST", "route": "/v1.54/containers/secret-id/attach",
                        "stage": "observer-startup-history", "durationNS": 30_000_000_000,
                        "error": "TimeoutError", "status": 101,
                        "stream": {"outputWireBytes": 0, "outputEOF": False,
                                   "privatePayload": "must not escape"},
                        "responseBody": "must not escape",
                    })
                    raise TimeoutError("whole-connection deadline")

                def cleanup(self):
                    return {"status": "passed", "remainingOwnedResources": []}

            fixture = SimpleNamespace(identifier="E07-init-attachment", expected={})
            runner = SimpleNamespace(lane="apple-stock", cleanup_differences=[],
                                     _preserve_engine_on_uncertain_guest_cleanup=False,
                                     finalized_identity={"sourceCommit": "a" * 40})
            bridge = OwnedGuestFixtureRunner.__new__(OwnedGuestFixtureRunner)
            bridge.runner, bridge.repository, bridge.lane = runner, REPOSITORY, runner.lane
            bridge.fixtures, bridge.retained, bridge.inputs = [fixture], base, {"workload": {"image": {}}}
            bridge.socket, bridge.compose, bridge.container = base / "docker.sock", None, "/provider/container"
            bridge.preparation_error, bridge.preparation = None, (root, journal, Runtime(None, journal), owner)
            bridge._case_paths = mock.Mock(return_value=(root, journal, owner))
            bridge._provision_event_sequence = 0

            with (mock.patch.object(owned_guest_fixture, "LaneRuntimeView", Runtime),
                  mock.patch.object(guest_runtime, "ReleasedGuest", Guest)):
                result = bridge.run(fixture, raw)

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["diagnosticTrace"], [{
                "stage": "observer-startup-history", "durationNS": 30_000_000_000,
                "status": 101, "errorType": "TimeoutError",
                "stream": {"outputWireBytes": 0, "outputEOF": False},
            }])
            encoded = json.dumps(result["diagnosticTrace"])
            self.assertNotIn("secret-id", encoded)
            self.assertNotIn("privatePayload", encoded)
            self.assertNotIn("responseBody", encoded)

    def test_incomplete_lane_preparation_reports_each_fixture_failed(self) -> None:
        runner = OwnedGuestFixtureRunner.__new__(OwnedGuestFixtureRunner)
        runner.preparation_error = "fixture preflight failed"
        result = runner.run(SimpleNamespace(identifier="E07-init-attachment"), Path("/unused/raw"))
        self.assertEqual(result["id"], "E07-init-attachment")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["observations"], {})
        self.assertIn("fixture preflight failed", result["diagnostic"])

    def test_incomplete_preparation_keeps_runtime_quarantined(self) -> None:
        runner = OwnedGuestFixtureRunner.__new__(OwnedGuestFixtureRunner)
        runner.preparation_error = "partial guest input load"
        runner.lane = "apple-stock"
        with self.assertRaisesRegex(ParityError, "preserve the selected runtime"):
            runner.cleanup()

    def test_guest_operation_and_cleanup_use_same_instance_and_receipt_precedes_root_removal(self) -> None:
        self._run_e13_guest_bridge({"config": {"Tty": True, "OpenStdin": False}}, True)

    def test_e13_missing_or_invalid_inspection_fails_without_mode_claim(self) -> None:
        for inspection in (None, [], {"config": {}},
                           {"config": {"Tty": 1, "OpenStdin": False}},
                           {"config": {"Tty": True, "OpenStdin": 0}}):
            with self.subTest(inspection=inspection):
                self._run_e13_guest_bridge(inspection, False)

    def _run_e13_guest_bridge(self, inspection, expected_pass) -> None:
        import shutil
        import guest_runtime
        import owned_guest_fixture

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, raw = base / "case", base / "raw"
            root.mkdir(mode=0o700)
            raw.mkdir(mode=0o700)
            owner = {"identity": {"campaign": "campaign", "lane": "apple-stock",
                                  "fixture": "E13-compose-signals", "sourceCommit": "a" * 40,
                                  "archiveSHA256": "b" * 64, "endpointSHA256": "c" * 64},
                     "root": str(root)}
            marker = json.dumps(owner, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            (root / "owner.json").write_bytes(marker)
            class Journal:
                def __init__(self, owner):
                    self.owner = owner
                    self.entries = {}

                def put(self, name, payload):
                    if name in self.entries and self.entries[name] != payload:
                        raise ValueError("immutable journal entry changed")
                    self.entries[name] = payload

                def records(self):
                    return dict(self.entries)

                def receipt(self):
                    return {"status": "restored"}

            class Runtime:
                def __init__(self, *args, **_kwargs):
                    # This test isolates guest ownership from the runtime constructor.
                    self.journal = args[1] if len(args) > 1 else args[0]

                def verify(self):
                    # Host endpoint validation is outside this deterministic cleanup test.
                    pass

            instances = []
            class Guest:
                def __init__(self, *_args, **_kwargs):
                    instances.append(self)
                    self.runtime = _args[4]
                    self.fixture = _args[1]

                def operation(self):
                    self.operated = True
                    return {"usr1_forwarded": "true", "guest_continues": "true",
                            "term_forwarded": "true", "exact_exit": "true", "auto_remove": "true"}

                def cleanup(self):
                    self.cleaned = True
                    if self.fixture == "E13-compose-signals":
                        self.runtime.journal.put(
                            "guest-compose-foreground.log",
                            b"compose-stdout\nsignal:USR1\nsignal:TERM\n",
                        )
                        if inspection is not None:
                            self.runtime.journal.put("compose-foreground-inspection.json",
                                                     json.dumps(inspection).encode())
                    return {"status": "passed", "remainingOwnedResources": []}

            fixture = SimpleNamespace(identifier="E13-compose-signals", expected={})
            runner = SimpleNamespace(lane="apple-stock", cleanup_differences=[],
                                     _preserve_engine_on_uncertain_guest_cleanup=False,
                                     finalized_identity={"sourceCommit": "a" * 40})
            bridge = OwnedGuestFixtureRunner.__new__(OwnedGuestFixtureRunner)
            bridge.runner, bridge.repository, bridge.lane = runner, REPOSITORY, runner.lane
            bridge.fixtures, bridge.retained, bridge.inputs = [fixture], base, {"workload": {"image": {}}}
            bridge.socket, bridge.compose, bridge.container = base / "docker.sock", None, "/provider/container"
            journal = Journal(owner)
            runtime = Runtime(journal)
            bridge.preparation_error, bridge.preparation = None, (root, journal, runtime, owner)
            bridge._case_paths = mock.Mock(return_value=(root, bridge.preparation[1], owner))
            bridge._provision_event_sequence = 0
            receipt_path = raw / "owned-guest-journal-receipt.json"
            real_rmtree = shutil.rmtree

            def check_receipt_then_remove(path):
                self.assertTrue(receipt_path.is_file())
                real_rmtree(path)

            with (mock.patch.object(owned_guest_fixture, "LaneRuntimeView", Runtime),
                  mock.patch.object(guest_runtime, "ReleasedGuest", Guest),
                  mock.patch.object(owned_guest_fixture, "assert_contract", return_value=[]),
                  mock.patch.object(bridge, "_compose_wrapper_selection", return_value={
                      "DEVCONTAINER_BACKEND": "stock", "DEVCONTAINER_COMPOSE_PROVIDER": "container-compose",
                      "DEVCONTAINER_COMPOSE_BIN": "/admitted/container-compose",
                      "DEVCONTAINER_CONTAINER_BIN": "/admitted/container", "DEVCONTAINER_STATE": "/state",
                      "DEVCONTAINER_SOCKET": "/socket"}),
                  mock.patch.object(owned_guest_fixture.shutil, "rmtree", side_effect=check_receipt_then_remove)):
                result = bridge.run(fixture, raw)

            self.assertEqual(result["status"], "passed" if expected_pass else "failed")
            self.assertEqual(len(instances), 1)
            self.assertTrue(instances[0].operated and instances[0].cleaned)
            if expected_pass:
                self.assertEqual(result["signalStream"]["counts"], {"SIGUSR1": 1, "SIGTERM": 1})
                self.assertEqual(result["signalContract"], {"modeVersion": 2, "tty": True, "openStdin": False})
            else:
                self.assertNotIn("signalContract", result)
                self.assertIn("E13", result["diagnostic"])
            self.assertTrue(receipt_path.is_file())
            self.assertFalse(root.exists())

    def test_cleanup_runtime_error_retains_root_and_marks_engine_uncertain(self) -> None:
        import guest_runtime
        import owned_guest_fixture

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, raw = base / "case", base / "raw"
            root.mkdir(mode=0o700)
            raw.mkdir(mode=0o700)
            owner = {"identity": {"campaign": "campaign", "lane": "apple-stock",
                                  "fixture": "E07-init-attachment", "sourceCommit": "a" * 40,
                                  "archiveSHA256": "b" * 64, "endpointSHA256": "c" * 64},
                     "root": str(root)}
            (root / "owner.json").write_text(json.dumps(owner, sort_keys=True, separators=(",", ":")))
            class Journal:
                def __init__(self, owner):
                    self.owner = owner
                    self.entries = {}

                def put(self, name, payload):
                    if name in self.entries and self.entries[name] != payload:
                        raise ValueError("immutable journal entry changed")
                    self.entries[name] = payload

                def receipt(self):
                    return {"status": "restored"}

            class Runtime:
                def __init__(self, *_args, **_kwargs):
                    # This test isolates guest ownership from the runtime constructor.
                    pass

                def verify(self):
                    # Host endpoint validation is outside this deterministic cleanup test.
                    pass

            class Guest:
                def __init__(self, *_args, **kwargs):
                    self.observe = kwargs["observe"]

                def operation(self):
                    self.observe({"event": "owned probe reached cleanup"})
                    return {"attached": "true"}

                def cleanup(self):
                    raise RuntimeError("owned cleanup timed out")

            fixture = SimpleNamespace(identifier="E07-init-attachment", expected={})
            runner = SimpleNamespace(lane="apple-stock", cleanup_differences=[],
                                     _preserve_engine_on_uncertain_guest_cleanup=False,
                                     finalized_identity={"sourceCommit": "a" * 40})
            bridge = OwnedGuestFixtureRunner.__new__(OwnedGuestFixtureRunner)
            bridge.runner, bridge.repository, bridge.lane = runner, REPOSITORY, runner.lane
            bridge.fixtures, bridge.retained, bridge.inputs = [fixture], base, {"workload": {"image": {}}}
            bridge.socket, bridge.compose, bridge.container = base / "docker.sock", None, "/provider/container"
            bridge.preparation_error, bridge.preparation = None, (root, Journal(owner), Runtime(), owner)
            bridge._case_paths = mock.Mock(return_value=(root, bridge.preparation[1], owner))
            bridge._provision_event_sequence = 0
            with (mock.patch.object(owned_guest_fixture, "LaneRuntimeView", Runtime),
                  mock.patch.object(guest_runtime, "ReleasedGuest", Guest),
                  mock.patch.object(owned_guest_fixture, "assert_contract", return_value=[])):
                result = bridge.run(fixture, raw)

            self.assertEqual(result["status"], "failed")
            self.assertTrue(root.is_dir())
            self.assertTrue(runner._preserve_engine_on_uncertain_guest_cleanup)
            self.assertTrue(any("cleanup is uncertain" in item for item in runner.cleanup_differences))
            self.assertIn("probe-events-cleanup-failed.json", bridge.preparation[1].entries)
            self.assertIn(b"owned probe reached cleanup", bridge.preparation[1].entries[
                "probe-events-cleanup-failed.json"])


class OwnedGuestAdmissionTests(unittest.TestCase):
    def test_native_compose_provider_is_distinct_from_the_signed_wrapper_and_locked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            provider = root / "container-compose"
            provider.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            provider.chmod(0o755)
            provider_sha = hashlib.sha256(provider.read_bytes()).hexdigest()
            pins = json.loads((REPOSITORY / "Tests/Parity/manifest.json").read_text())["referencePins"]
            environment = {"DEVCONTAINER_COMPOSE_BIN": str(provider),
                           "DEVCONTAINER_COMPOSE_PROVIDER_SHA256": provider_sha}
            bridge = OwnedGuestFixtureRunner.__new__(OwnedGuestFixtureRunner)
            bridge.runner = SimpleNamespace(environment=environment, manifest={"referencePins": pins})
            bridge.repository = REPOSITORY
            reported = {"version": pins["containerCompose"]["stableVersion"],
                        "commit": pins["containerCompose"]["stableCommit"]}
            with mock.patch("owned_guest_fixture.subprocess.run", return_value=subprocess.CompletedProcess(
                    [str(provider)], 0, json.dumps(reported), "")) as invoke:
                admitted = bridge._admit_external_compose_provider()
            self.assertEqual(admitted, provider)
            self.assertNotEqual(admitted, Path("/signed/devcontainer-compose"))
            invoke.assert_called_once()
            self.assertEqual(invoke.call_args.args[0], [str(provider), "version", "--format", "json"])

            environment["DEVCONTAINER_COMPOSE_PROVIDER_SHA256"] = "0" * 64
            with mock.patch("owned_guest_fixture.subprocess.run") as invoke:
                with self.assertRaisesRegex(ParityError, "admitted executable"):
                    bridge._admit_external_compose_provider()
            invoke.assert_not_called()

    def test_stock_compose_tools_require_locked_docker_and_standalone_compose(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            docker = root / "docker"
            compose = root / "docker-compose"
            for executable in (docker, compose):
                executable.write_bytes(b"#!/bin/sh\nexit 0\n")
                executable.chmod(0o755)
            pins = json.loads((REPOSITORY / "Tests/Parity/manifest.json").read_text())[
                "referencePins"]["docker"]
            environment = {"DEVCONTAINER_DOCKER_BIN": str(docker),
                           "DEVCONTAINER_DOCKER_COMPOSE_BIN": str(compose)}
            bridge = OwnedGuestFixtureRunner.__new__(OwnedGuestFixtureRunner)
            bridge.runner = SimpleNamespace(environment=environment,
                                            manifest={"referencePins": {"docker": pins}})
            bridge.repository = REPOSITORY
            versions = [
                subprocess.CompletedProcess([str(docker), "--version"], 0,
                                            f"Docker version {pins['cliVersion']}, build test\n", ""),
                subprocess.CompletedProcess([str(compose), "version", "--short"], 0,
                                            pins["composeVersion"] + "\n", ""),
            ]
            def locked_digest(path):
                return pins["cliSHA256"] if Path(path) == docker else pins["composeSHA256"]

            with mock.patch("owned_guest_fixture.sha256", side_effect=locked_digest), \
                    mock.patch("owned_guest_fixture.subprocess.run", side_effect=versions) as invoke:
                selected_docker, selected_compose = bridge._admit_stock_compose_tools()
            self.assertEqual((selected_docker, selected_compose), (docker, compose))
            self.assertEqual(invoke.call_args_list[0].args[0], [str(docker), "--version"])
            self.assertEqual(invoke.call_args_list[1].args[0], [str(compose), "version", "--short"])

            with mock.patch("owned_guest_fixture.sha256", return_value="0" * 64), \
                    mock.patch("owned_guest_fixture.subprocess.run") as invoke:
                with self.assertRaisesRegex(ParityError, "manifest-pinned bytes"):
                    bridge._admit_stock_compose_tools()
            invoke.assert_not_called()

            wrong_versions = [
                subprocess.CompletedProcess([str(docker), "--version"], 0,
                                            f"Docker version {pins['cliVersion']}, build test\n", ""),
                subprocess.CompletedProcess([str(compose), "version", "--short"], 0, "5.3.0\n", ""),
            ]
            with mock.patch("owned_guest_fixture.sha256", side_effect=locked_digest), \
                    mock.patch("owned_guest_fixture.subprocess.run", side_effect=wrong_versions):
                with self.assertRaisesRegex(ParityError, "manifest-pinned version"):
                    bridge._admit_stock_compose_tools()

    def test_compose_selection_exports_authenticated_lane_paths_and_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            provider_root = root / "provider"
            bin_root = provider_root / "bin"
            bin_root.mkdir(parents=True, mode=0o700)
            container = bin_root / "container"
            wrapper = root / "devcontainer-compose-wrapper"
            provider = root / "container-compose-provider"
            docker = root / "docker"
            docker_adapter = root / "docker-no-buildx"
            docker_compose = root / "docker-compose"
            state = root / "state.sqlite"
            for executable in (container, wrapper, provider, docker, docker_adapter, docker_compose):
                executable.write_bytes(b"#!/bin/sh\nexit 0\n")
                executable.chmod(0o755)
            state.touch(mode=0o600)
            provider_sha = hashlib.sha256(provider.read_bytes()).hexdigest()
            pins = json.loads((REPOSITORY / "Tests/Parity/manifest.json").read_text())["referencePins"]
            bridge = OwnedGuestFixtureRunner.__new__(OwnedGuestFixtureRunner)
            bridge.lane, bridge.socket = "apple-stock", root / "docker.sock"
            bridge.container, bridge.compose = str(container), wrapper
            bridge.compose_provider = None
            bridge.docker_cli, bridge.docker_compose = docker, docker_compose
            bridge.runner = SimpleNamespace(environment={
                "DEVCONTAINER_STATE": str(state),
                # LaneRunner intentionally installs this adapter before fixtures run.
                "DEVCONTAINER_DOCKER_BIN": str(docker_adapter),
                "DEVCONTAINER_DOCKER_COMPOSE_BIN": str(docker_compose),
            }, manifest={"referencePins": pins})
            def locked_stock_digest(path):
                return pins["docker"]["cliSHA256"] if Path(path) == docker else pins["docker"]["composeSHA256"]
            with mock.patch("owned_guest_fixture.sha256", side_effect=locked_stock_digest):
                selection = bridge._compose_wrapper_selection()
            self.assertEqual(selection["DEVCONTAINER_BACKEND"], "stock")
            self.assertEqual(selection["DEVCONTAINER_COMPOSE_PROVIDER"], "docker")
            self.assertEqual(selection["DEVCONTAINER_DOCKER_BIN"], str(docker))
            self.assertEqual(selection["DEVCONTAINER_DOCKER_COMPOSE_BIN"], str(docker_compose))
            self.assertNotIn("DEVCONTAINER_COMPOSE_BIN", selection)
            self.assertEqual(selection["DEVCONTAINER_CONTAINER_BIN"], str(container))
            self.assertEqual(selection["DEVCONTAINER_STATE"], str(state))
            self.assertEqual(selection["DEVCONTAINER_SOCKET"], str(bridge.socket))

            bridge.lane = "container-compose"
            bridge.compose_provider = provider
            bridge.docker_cli = bridge.docker_compose = None
            bridge.runner.environment = {"DEVCONTAINER_STATE": str(state),
                                         "DEVCONTAINER_COMPOSE_PROVIDER_SHA256": provider_sha}
            selection = bridge._compose_wrapper_selection()
            self.assertEqual(selection["DEVCONTAINER_BACKEND"], "container-compose")
            self.assertEqual(selection["DEVCONTAINER_COMPOSE_PROVIDER"], "container-compose")
            self.assertEqual(selection["DEVCONTAINER_COMPOSE_BIN"], str(provider))
            self.assertNotEqual(selection["DEVCONTAINER_COMPOSE_BIN"], str(bridge.compose))

    def test_docker_inputs_select_only_the_locked_alpine_archive(self) -> None:
        with tempfile.TemporaryDirectory(dir=ACCOUNT_HOME) as temporary:
            retained = Path(temporary).resolve()
            image_store = retained / "guest-images"
            image_store.mkdir(mode=0o700)
            admitted = {"image": {"manifest": "sha256:" + "a" * 64},
                        "sha256": "b" * 64, "path": str(image_store / "asset.tar")}
            bazel_path = str(REPOSITORY / "Tools/bazel")
            if bazel_path not in sys.path:
                sys.path.insert(0, bazel_path)
            prepare_guest_images = importlib.import_module("prepare_guest_images")
            with mock.patch.object(prepare_guest_images, "require_image", return_value=admitted) as require:
                actual = admit_guest_inputs(REPOSITORY, "docker", retained)
            lock = json.loads((REPOSITORY / "Tools/bazel/guest-images.lock.json").read_text())
            alpine = next(image for image in lock["images"] if image["name"] == "alpine-workload")
            require.assert_called_once_with(alpine, image_store)
            self.assertEqual(actual, {"workload": admitted})

    def test_native_guest_admission_threads_only_lock_admitted_provider_images(self) -> None:
        with tempfile.TemporaryDirectory(dir=ACCOUNT_HOME) as temporary:
            retained = Path(temporary).resolve()
            provider_images = {"guest": {"reference": "fixture", "archiveSHA256": "a" * 64,
                                          "source": "b" * 40},
                              "builder": {"reference": "fixture-builder", "archiveSHA256": "c" * 64,
                                          "source": "d" * 40}}
            if str(REPOSITORY / "Tools/bazel") not in sys.path:
                sys.path.insert(0, str(REPOSITORY / "Tools/bazel"))
            guest_runtime = importlib.import_module("guest_runtime")
            with mock.patch("released_engine.provider_image_references", return_value=provider_images) as refs, \
                    mock.patch.object(guest_runtime, "admit_guest", return_value={"admitted": True}) as admit:
                actual = admit_guest_inputs(REPOSITORY, "container-compose", retained)
            self.assertEqual(actual, {"admitted": True})
            refs.assert_called_once()
            self.assertEqual(refs.call_args.args[1:], ("container-compose", retained))
            self.assertEqual(admit.call_args.kwargs["provider_image_references"], provider_images)

    def test_docker_compose_requires_the_exact_manifest_hash_and_version(self) -> None:
        with tempfile.TemporaryDirectory(dir=ACCOUNT_HOME) as temporary:
            root = Path(temporary).resolve()
            retained = root / "retained"
            retained.mkdir(mode=0o700)
            executable = root / "docker-compose"
            executable.write_bytes(b"pinned compose bytes")
            executable.chmod(0o755)
            expected_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
            fixture = SimpleNamespace(identifier="E09-compose-foreground")
            runner = SimpleNamespace(
                lane="docker",
                repository=REPOSITORY,
                environment={"DEVCONTAINER_PARITY_RETAINED_ROOT": str(retained),
                             "DEVCONTAINER_DOCKER_COMPOSE_BIN": str(executable)},
                manifest={"referencePins": {"docker": {"composeSHA256": expected_hash,
                                                           "composeVersion": "5.3.1"}}},
            )
            with mock.patch("owned_guest_fixture.subprocess.run",
                            return_value=subprocess.CompletedProcess([], 0, "5.3.1\n", "")) as invoke:
                admitted = OwnedGuestFixtureRunner(runner, [fixture], admitted_inputs={})
            self.assertEqual(admitted.compose, executable)
            invoke.assert_called_once()

            runner.manifest["referencePins"]["docker"]["composeSHA256"] = "0" * 64
            with mock.patch("owned_guest_fixture.subprocess.run") as invoke:
                with self.assertRaisesRegex(ParityError, "manifest-pinned bytes"):
                    OwnedGuestFixtureRunner(runner, [fixture], admitted_inputs={})
            invoke.assert_not_called()


class ActiveProviderHomeTests(unittest.TestCase):
    def test_candidate_diagnostic_guard_binds_candidate_package_and_exact_fixture_selection(self) -> None:
        with tempfile.TemporaryDirectory(dir=ACCOUNT_HOME) as temporary:
            base = Path(temporary).resolve()
            campaign = base / "campaign"
            campaign.mkdir(mode=0o700)
            home = campaign / "apple-stock-home"
            home.mkdir(mode=0o700)
            (home / "container").mkdir(mode=0o700)
            retained = base / "workflow"
            retained.mkdir(mode=0o700)
            guard_path = retained / "runtime-admission.json"
            selection = ("C03-compose-resources", "D05-features", "E07-init-attachment")
            candidate = {
                "scope": "local-candidate-integration-only",
                "candidateInvocation": "47387ce0-3819-4eca-b06e-11356ce4568d",
                "sourceCommit": "a" * 40, "runtimeProfile": "stock",
                "assetSHA256": "b" * 64, "candidateReceiptSHA256": "c" * 64,
            }
            guard_identity = {
                "campaign": "candidate-campaign", "sourceCommit": candidate["sourceCommit"],
                "scope": "unsigned-native-candidate-diagnostic",
                "candidateInvocation": candidate["candidateInvocation"],
                "candidateReceiptSHA256": candidate["candidateReceiptSHA256"],
                "archiveSHA256": candidate["assetSHA256"], "runtimeProfile": "stock",
                "diagnosticFixtures": sorted(selection),
            }
            guard_path.write_text(json.dumps({"identity": guard_identity, "root": str(campaign)},
                                             sort_keys=True))
            guard_path.chmod(0o600)
            owner = {"identity": {"campaign": "candidate-campaign", "lane": "apple-stock",
                                  "sourceCommit": candidate["sourceCommit"]}, "root": str(home)}
            marker = home / "owner.json"
            marker.write_text(json.dumps(owner, sort_keys=True) + "\n")
            marker.chmod(0o600)
            runner = SimpleNamespace(
                environment={"HOME": str(home), "CONTAINER_APP_ROOT": str(home / "container"),
                             "DEVCONTAINER_PARITY_RETAINED_ROOT": str(retained),
                             "DEVCONTAINER_PARITY_GUARD": str(guard_path)},
                lane="apple-stock", output=campaign / "apple-stock",
                finalized_identity=None, candidate_identity=candidate,
            )

            with mock.patch("owned_guest_fixture.ACCOUNT_HOME", base):
                self.assertEqual(_active_provider_home(runner, fixture_selection=selection), (home, owner))
                for key, changed in (("candidateInvocation", "different-invocation"),
                                     ("candidateReceiptSHA256", "d" * 64),
                                     ("assetSHA256", "e" * 64), ("sourceCommit", "f" * 40),
                                     ("runtimeProfile", "enhanced")):
                    original = candidate[key]
                    candidate[key] = changed
                    with self.subTest(key=key), self.assertRaisesRegex(ParityError, "campaign guard differs"):
                        _active_provider_home(runner, fixture_selection=selection)
                    candidate[key] = original
                with self.assertRaisesRegex(ParityError, "campaign guard differs"):
                    _active_provider_home(runner, fixture_selection=("C03-compose-resources", "unknown"))

    def test_finalized_native_diagnostic_guard_binds_its_finite_fixture_selection(self) -> None:
        with tempfile.TemporaryDirectory(dir=ACCOUNT_HOME) as temporary:
            base = Path(temporary).resolve()
            campaign = base / "campaign"
            campaign.mkdir(mode=0o700)
            home = campaign / "apple-stock-home"
            home.mkdir(mode=0o700)
            (home / "container").mkdir(mode=0o700)
            retained = base / "workflow"
            retained.mkdir(mode=0o700)
            guard_path = retained / "runtime-admission.json"
            selection = ("E07-init-attachment",)
            guard = {"identity": {"campaign": "finalized-campaign", "sourceCommit": "a" * 40,
                                  "scope": "finalized-native-parity-diagnostic",
                                  "diagnosticFixtures": list(selection)},
                     "root": str(campaign)}
            guard_path.write_text(json.dumps(guard, sort_keys=True))
            guard_path.chmod(0o600)
            owner = {"identity": {"campaign": "finalized-campaign", "lane": "apple-stock",
                                  "sourceCommit": "a" * 40}, "root": str(home)}
            marker = home / "owner.json"
            marker.write_text(json.dumps(owner, sort_keys=True) + "\n")
            marker.chmod(0o600)
            runner = SimpleNamespace(
                environment={"HOME": str(home), "CONTAINER_APP_ROOT": str(home / "container"),
                             "DEVCONTAINER_PARITY_RETAINED_ROOT": str(retained),
                             "DEVCONTAINER_PARITY_GUARD": str(guard_path)},
                lane="apple-stock", output=campaign / "apple-stock",
                finalized_identity={"sourceCommit": "a" * 40}, candidate_identity=None,
            )

            with mock.patch("owned_guest_fixture.ACCOUNT_HOME", base):
                self.assertEqual(_active_provider_home(runner, fixture_selection=selection), (home, owner))
                with self.assertRaisesRegex(ParityError, "campaign guard differs"):
                    _active_provider_home(runner, fixture_selection=("C03-compose-resources",))

    def test_component_guard_is_bound_only_to_each_exact_supported_fixture_selection(self) -> None:
        with tempfile.TemporaryDirectory(dir=ACCOUNT_HOME) as temporary:
            base = Path(temporary).resolve()
            campaign = base / "campaign"
            campaign.mkdir(mode=0o700)
            home = campaign / "apple-stock-home"
            home.mkdir(mode=0o700)
            (home / "container").mkdir(mode=0o700)
            guard_root = base / "workflow"
            guard_root.mkdir(mode=0o700)
            guard_path = guard_root / "runtime-admission.json"
            guard = {"identity": {"campaign": "campaign-id", "sourceCommit": "a" * 40,
                                   "scope": "finalized-native-parity-component"},
                     "root": str(campaign)}
            guard_path.write_text(json.dumps(guard, sort_keys=True))
            guard_path.chmod(0o600)
            owner = {"identity": {"campaign": "campaign-id", "lane": "apple-stock",
                                  "sourceCommit": "a" * 40}, "root": str(home)}
            marker = home / "owner.json"
            marker.write_text(json.dumps(owner, sort_keys=True) + "\n")
            marker.chmod(0o600)
            runner = SimpleNamespace(
                environment={"HOME": str(home), "CONTAINER_APP_ROOT": str(home / "container"),
                             "DEVCONTAINER_PARITY_RETAINED_ROOT": str(guard_root),
                             "DEVCONTAINER_PARITY_GUARD": str(guard_path)},
                lane="apple-stock", output=campaign / "apple-stock",
                finalized_identity={"sourceCommit": "a" * 40},
            )

            for selection in (("E06-network-volume",), ("E07-init-attachment",),
                              ("E13-compose-signals",), ("E14-compose-terminal-size",),
                              ("E04-image-build",)):
                with self.subTest(selection=selection):
                    self.assertEqual(_active_provider_home(runner, fixture_selection=selection), (home, owner))
            for selection in (None, (), ("unknown-component",), ("E10-compose-redirected",),
                              ("E06-network-volume", "E13-compose-signals"),
                              ("E06-network-volume", "E06-network-volume"),
                              ("E07-init-attachment", "E07-init-attachment"),
                              ("E14-compose-terminal-size", "E14-compose-terminal-size"),
                              ("E04-image-build", "E04-image-build"),
                              ("E04-image-build", "E13-compose-signals"),
                              ("E07-init-attachment", "E14-compose-terminal-size"),
                              ("E13-compose-signals", "E14-compose-terminal-size"),
                              ("E06-network-volume", "E07-init-attachment",
                               "E13-compose-signals", "E14-compose-terminal-size")):
                with self.subTest(selection=selection), self.assertRaisesRegex(
                        ParityError, "campaign guard differs"):
                    _active_provider_home(runner, fixture_selection=selection)

            owner["identity"]["sourceCommit"] = "b" * 40
            marker.write_text(json.dumps(owner, sort_keys=True) + "\n")
            with self.assertRaisesRegex(ParityError, "ownership marker differs"):
                _active_provider_home(runner, fixture_selection=("E06-network-volume",))
            owner["identity"]["sourceCommit"] = "a" * 40
            marker.write_text(json.dumps(owner, sort_keys=True) + "\n")

            guard["identity"]["scope"] = "unrecognized-component-scope"
            guard_path.write_text(json.dumps(guard, sort_keys=True))
            with self.assertRaisesRegex(ParityError, "campaign guard differs"):
                _active_provider_home(runner, fixture_selection=("E13-compose-signals",))

    def test_preparation_targets_the_active_private_home_not_fixture_scratch(self) -> None:
        with tempfile.TemporaryDirectory(dir=ACCOUNT_HOME) as temporary:
            base = Path(temporary).resolve()
            campaign = base / "campaign"
            campaign.mkdir(mode=0o700)
            home = campaign / "apple-stock-home"
            home.mkdir(mode=0o700)
            (home / "container").mkdir(mode=0o700)
            campaign_identity = "campaign-id"
            guard_root = base / "workflow"
            guard_root.mkdir(mode=0o700)
            guard_path = guard_root / "runtime-admission.json"
            guard_path.write_text(json.dumps({"identity": {"campaign": campaign_identity,
                                                            "sourceCommit": "a" * 40,
                                                            "scope": "finalized-native-parity"},
                                               "root": str(campaign)}, sort_keys=True))
            guard_path.chmod(0o600)
            identity = {"campaign": campaign_identity, "lane": "apple-stock",
                        "sourceCommit": "a" * 40}
            owner = {"identity": identity, "root": str(home)}
            marker = home / "owner.json"
            marker.write_text(json.dumps(owner, sort_keys=True) + "\n")
            marker.chmod(0o600)
            runner = SimpleNamespace(
                environment={"HOME": str(home), "CONTAINER_APP_ROOT": str(home / "container"),
                             "DEVCONTAINER_PARITY_RETAINED_ROOT": str(guard_root),
                             "DEVCONTAINER_PARITY_GUARD": str(guard_path)},
                lane="apple-stock", output=campaign / "apple-stock",
                finalized_identity={"sourceCommit": "a" * 40},
            )
            bridge = OwnedGuestFixtureRunner.__new__(OwnedGuestFixtureRunner)
            bridge.runner, bridge.lane, bridge.repository = runner, "apple-stock", REPOSITORY
            bridge.fixtures = [SimpleNamespace(identifier="E09-compose-foreground")]
            bridge.fixture_selection = ("E09-compose-foreground",)
            bridge.retained = base / "retained"
            bridge.retained.mkdir(mode=0o700)

            staging_root, journal, selected_owner = bridge._case_paths_for_preparation()

            self.assertEqual(staging_root, home)
            self.assertEqual(selected_owner, owner)
            self.assertTrue((home / "owner.json").is_file())
            self.assertFalse((campaign / "owned-guest-runtime").exists())
            self.assertEqual(json.loads(journal.owner), owner)


class NativeProvisionBeforeEngineTests(unittest.TestCase):
    def test_d05_warm_provider_preparation_uses_only_kernel_and_init_prerequisites(self) -> None:
        import owned_guest_fixture

        sys.path.insert(0, str(REPOSITORY / "Tools/testing"))
        import guest_runtime

        inputs = {"kernel": {"sha256": "a" * 64},
                  "initialization": {"archiveSHA256": "b" * 64},
                  "workload": {"archiveSHA256": "c" * 64}}
        events = []
        owner = {"identity": {"campaign": "campaign", "lane": "apple-stock"}}
        root, journal = Path("/private/provider-home"), mock.Mock()

        class Runtime:
            def __init__(self, *_args):
                events.append("api-view")

            def verify(self):
                events.append("api-verify")

        guests = []

        class Guest:
            def __init__(self, *args, **_kwargs):
                self.fixture = args[1]
                self.socket = args[6]
                guests.append(self)

            def provision(self):
                events.append("kernel-and-init-provision")

        bridge = OwnedGuestFixtureRunner.__new__(OwnedGuestFixtureRunner)
        bridge.runner = SimpleNamespace(lane="apple-stock")
        bridge.lane, bridge.repository = "apple-stock", REPOSITORY
        bridge.fixtures, bridge.retained, bridge.inputs = [], Path("/retained"), inputs
        bridge.fixture_selection = ("D05-features",)
        bridge.builder_required, bridge.provider_required = False, True
        bridge.container, bridge.socket, bridge.compose = "/provider/bin/container", None, None
        bridge.preparation, bridge.preparation_error = None, None
        bridge._provision_event_sequence = 0
        bridge._case_paths_for_preparation = mock.Mock(return_value=(root, journal, owner))

        with (mock.patch.object(owned_guest_fixture, "ApiRuntimeView", Runtime),
              mock.patch.object(owned_guest_fixture, "admit_guest_inputs", return_value=inputs) as admit,
              mock.patch.object(owned_guest_fixture, "guest_input_identity", side_effect=lambda value: value),
              mock.patch.object(guest_runtime, "ReleasedGuest", Guest)):
            bridge.prepare_native_provider()

        self.assertEqual(guests[0].fixture, "E07-init-attachment")
        self.assertIsNone(guests[0].socket)
        self.assertNotIn("builder", inputs)
        self.assertEqual(admit.call_args_list, [mock.call(REPOSITORY, "apple-stock", Path("/retained"), builder=False)] * 2)
        self.assertEqual(events.count("kernel-and-init-provision"), 1)
        self.assertLess(events.index("api-verify"), events.index("kernel-and-init-provision"))

    def test_c03_provider_prerequisite_uses_e07_provisioning_without_running_e07(self) -> None:
        import owned_guest_fixture

        sys.path.insert(0, str(REPOSITORY / "Tools/testing"))
        import guest_runtime

        inputs = {"kernel": {"sha256": "a" * 64},
                  "initialization": {"archiveSHA256": "b" * 64},
                  "workload": {"archiveSHA256": "c" * 64}}
        events = []
        owner = {"identity": {"campaign": "campaign", "lane": "apple-stock"}}
        root, journal = Path("/private/provider-home"), mock.Mock()

        class Runtime:
            def __init__(self, *_args):
                events.append("api-view")

            def verify(self):
                events.append("api-verify")

        guests = []

        class Guest:
            def __init__(self, *args, **_kwargs):
                self.fixture = args[1]
                self.socket = args[6]
                guests.append(self)

            def provision(self):
                events.append("kernel-and-init-provision")

        bridge = OwnedGuestFixtureRunner.__new__(OwnedGuestFixtureRunner)
        bridge.runner = SimpleNamespace(lane="apple-stock")
        bridge.lane, bridge.repository = "apple-stock", REPOSITORY
        bridge.fixtures, bridge.retained, bridge.inputs = [], Path("/retained"), inputs
        bridge.fixture_selection = ("C03-compose-resources",)
        bridge.builder_required, bridge.provider_required = False, True
        bridge.container, bridge.socket, bridge.compose = "/provider/bin/container", None, None
        bridge.preparation, bridge.preparation_error = None, None
        bridge._provision_event_sequence = 0
        bridge._case_paths_for_preparation = mock.Mock(return_value=(root, journal, owner))

        with (mock.patch.object(owned_guest_fixture, "ApiRuntimeView", Runtime),
              mock.patch.object(owned_guest_fixture, "admit_guest_inputs", return_value=inputs) as admit,
              mock.patch.object(owned_guest_fixture, "guest_input_identity", side_effect=lambda value: value),
              mock.patch.object(guest_runtime, "ReleasedGuest", Guest)):
            bridge.prepare_native_provider()

        self.assertEqual([guest.fixture for guest in guests], ["E07-init-attachment"])
        self.assertIsNone(guests[0].socket)
        self.assertNotIn("builder", inputs)
        self.assertEqual(admit.call_args_list,
                         [mock.call(REPOSITORY, "apple-stock", Path("/retained"), builder=False)] * 2)
        self.assertEqual(events.count("kernel-and-init-provision"), 1)
        self.assertLess(events.index("api-verify"), events.index("kernel-and-init-provision"))

    def test_native_provision_uses_api_view_without_a_placeholder_socket_once(self) -> None:
        for identifier in ("E07-init-attachment", "E06-network-volume", "E04-image-build"):
            with self.subTest(fixture=identifier):
                import owned_guest_fixture

                sys.path.insert(0, str(REPOSITORY / "Tools/testing"))
                import guest_runtime

                fixture = SimpleNamespace(identifier=identifier)
                inputs = {"kernel": {"sha256": "a" * 64},
                          "initialization": {"archiveSHA256": "b" * 64},
                          "workload": {"archiveSHA256": "c" * 64}}
                events = []
                owner = {"identity": {"campaign": "campaign", "lane": "apple-stock"}}
                root = Path("/private/provider-home")
                journal = mock.Mock()

                class Runtime:
                    def __init__(self, *_args):
                        events.append("api-view")

                    def verify(self):
                        events.append("api-verify")

                guests = []

                class Guest:
                    def __init__(self, *args, **_kwargs):
                        self.fixture = args[1]
                        self.socket = args[6]
                        guests.append(self)
                        events.append("guest-created")

                    def provision(self):
                        events.append("provision")

                bridge = OwnedGuestFixtureRunner.__new__(OwnedGuestFixtureRunner)
                bridge.runner = SimpleNamespace(lane="apple-stock")
                bridge.lane, bridge.repository = "apple-stock", REPOSITORY
                bridge.fixtures, bridge.retained, bridge.inputs = [fixture], Path("/retained"), inputs
                bridge.fixture_selection = (identifier,)
                bridge.builder_required = identifier == "E04-image-build"
                if bridge.builder_required:
                    bridge.fixtures = []
                bridge.container, bridge.socket, bridge.compose = "/provider/bin/container", None, None
                bridge.preparation, bridge.preparation_error = None, None
                bridge._provision_event_sequence = 0
                bridge._case_paths_for_preparation = mock.Mock(return_value=(root, journal, owner))

                with (mock.patch.object(owned_guest_fixture, "ApiRuntimeView", Runtime),
                      mock.patch.object(owned_guest_fixture, "admit_guest_inputs", return_value=inputs) as admit,
                      mock.patch.object(owned_guest_fixture, "guest_input_identity", side_effect=lambda value: value),
                      mock.patch.object(guest_runtime, "ReleasedGuest", Guest)):
                    bridge.prepare_native_provider()
                    with self.assertRaisesRegex(ParityError, "only once"):
                        bridge.prepare_native_provider()

                self.assertIsNone(bridge.socket)
                self.assertIsNone(guests[0].socket)
                self.assertEqual(guests[0].fixture, "E07-init-attachment" if bridge.builder_required else identifier)
                self.assertEqual(admit.call_count, 2)
                self.assertEqual(events.count("provision"), 1)
                self.assertLess(events.index("api-verify"), events.index("provision"))
                self.assertEqual(bridge.preparation[0], root)

    def test_api_runtime_view_rechecks_home_api_pid_and_locked_server_bytes(self) -> None:
        import owned_guest_fixture

        sys.path.insert(0, str(REPOSITORY / "Tools/testing"))
        import runtime_services

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            campaign = base / "campaign"
            campaign.mkdir(mode=0o700)
            home = campaign / "provider-home"
            home.mkdir(mode=0o700)
            (home / "container").mkdir(mode=0o700)
            retained = base / "retained"
            retained.mkdir(mode=0o700)
            guard_path = retained / "runtime-admission.json"
            guard_path.write_text(json.dumps({
                "identity": {"campaign": "campaign-id", "sourceCommit": "a" * 40,
                             "scope": "finalized-native-parity"},
                "root": str(campaign),
            }, sort_keys=True))
            guard_path.chmod(0o600)
            owner = {"identity": {"campaign": "campaign-id", "lane": "apple-stock",
                                  "sourceCommit": "a" * 40}, "root": str(home)}
            marker = home / "owner.json"
            marker.write_text(json.dumps(owner, sort_keys=True) + "\n")
            marker.chmod(0o600)
            provider_bin = base / "provider/bin"
            provider_bin.mkdir(parents=True)
            container = provider_bin / "container"
            container.write_bytes(b"container")
            api = provider_bin / "container-apiserver"
            api.write_bytes(b"locked api")
            api.chmod(0o755)
            environment = {
                "HOME": str(home), "CONTAINER_APP_ROOT": str(home / "container"),
                "DEVCONTAINER_PARITY_RETAINED_ROOT": str(retained),
                "DEVCONTAINER_PARITY_GUARD": str(guard_path),
                "DEVCONTAINER_CONTAINER_BIN": str(container),
                "DEVCONTAINER_API_SERVICE_PID": "4123",
                "DEVCONTAINER_API_SERVER_SHA256": owned_guest_fixture.sha256(api),
                "DEVCONTAINER_API_DEFINITION_SHA256": "d" * 64,
            }
            runner = SimpleNamespace(
                lane="apple-stock", output=campaign / "apple-stock",
                finalized_identity={"sourceCommit": "a" * 40},
                finalized_selection=None, environment=environment,
                provider_executable=lambda _name, _fallback: str(container),
            )
            view = ApiRuntimeView(runner, home, owner, mock.Mock(), ("E07-init-attachment",))
            with (mock.patch.object(owned_guest_fixture, "ACCOUNT_HOME", base),
                  mock.patch.object(runtime_services, "verify_selected_api") as verify):
                view.verify()
                verify.assert_called_once_with(home, api, 4123, "d" * 64)

                environment["DEVCONTAINER_API_SERVICE_PID"] = "0"
                with self.assertRaisesRegex(ParityError, "API executable or identity"):
                    view.verify()
                environment["DEVCONTAINER_API_SERVICE_PID"] = "4123"

                environment["HOME"] = str(base / "substituted-home")
                with self.assertRaises(OSError):
                    view.verify()
                environment["HOME"] = str(home)

                api.write_bytes(b"changed api")
                with self.assertRaisesRegex(ParityError, "API executable or identity"):
                    view.verify()

    def test_api_runtime_view_fails_when_selected_service_identity_fails(self) -> None:
        import owned_guest_fixture

        with mock.patch.object(owned_guest_fixture, "_verify_native_api",
                               side_effect=ParityError("API PID changed")):
            runner = SimpleNamespace(finalized_selection=None)
            view = ApiRuntimeView(runner, Path("/private/home"), {}, mock.Mock(), ())
            with self.assertRaisesRegex(ParityError, "API PID changed"):
                view.verify()

    def test_real_released_guest_provision_uses_api_view_journal_without_socket(self) -> None:
        import shutil
        import guest_runtime
        import owned_guest_fixture
        from service_journal import ServiceJournal

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            retained = base / "retained"
            retained.mkdir(mode=0o700)
            root = base / "provider-home"
            root.mkdir(mode=0o700)
            (root / "container").mkdir(mode=0o700)
            provider_bin = base / "provider" / "bin"
            provider_bin.mkdir(parents=True)
            kernel = base / "kernel"
            kernel.write_bytes(b"admitted-kernel")
            initialization = base / "initialization.oci"
            initialization.write_bytes(b"admitted-init")
            workload = base / "workload.oci"
            workload.write_bytes(b"admitted-workload")
            inputs = {
                "kernel": {"files": {"kernel": str(kernel)}, "sha256": "a" * 64},
                "initialization": {"path": str(initialization), "sha256": "b" * 64,
                                   "image": {"manifest": "sha256:" + "b" * 64,
                                             "config": "sha256:" + "c" * 64}},
                "workload": {"path": str(workload), "sha256": "c" * 64,
                             "image": {"manifest": "sha256:" + "c" * 64,
                                       "config": "sha256:" + "d" * 64}},
            }
            fixture = SimpleNamespace(identifier="E07-init-attachment")
            owner = {"identity": {"campaign": "campaign", "lane": "apple-stock",
                                  "fixture": "owned-guest-preparation"}, "root": str(root)}
            journal = ServiceJournal(retained / "preparation.sqlite", owner, create=True)
            runner = SimpleNamespace(lane="apple-stock", finalized_selection=None)
            bridge = OwnedGuestFixtureRunner.__new__(OwnedGuestFixtureRunner)
            bridge.runner, bridge.lane = runner, "apple-stock"
            bridge.repository, bridge.fixtures = REPOSITORY, [fixture]
            bridge.fixture_selection, bridge.builder_required = (fixture.identifier,), False
            bridge.retained, bridge.inputs = retained, inputs
            bridge.container, bridge.socket, bridge.compose = str(provider_bin / "container"), None, None
            bridge.preparation, bridge.preparation_error = None, None
            bridge._provision_event_sequence = 0
            bridge._case_paths_for_preparation = mock.Mock(return_value=(root, journal, owner))
            guest_instances = []
            original_init = guest_runtime.ReleasedGuest.__init__

            def capture_guest(instance, *args, **kwargs):
                original_init(instance, *args, **kwargs)
                guest_instances.append(instance)

            class CommandEffectsOnly:
                def __init__(self):
                    self.process = SimpleNamespace(pid=123456, wait=lambda timeout=None: 0)

                def start(self, argv, cwd, output, *, provider_install=None):
                    command = argv[1:]
                    output.write(b"mocked admitted provider command\n")
                    if command[:3] == ["system", "kernel", "set"]:
                        kernels = root / "container" / "kernels"
                        kernels.mkdir(mode=0o700, exist_ok=True)
                        shutil.copyfile(kernel, kernels / "vmlinux")
                        (kernels / "default.kernel-arm64").symlink_to("vmlinux")

                def stop(self):
                    # This fake starts no process, so there is nothing to stop.
                    return None

            with (mock.patch.object(owned_guest_fixture, "_verify_native_api",
                                    return_value=(root, owner)),
                  mock.patch.object(owned_guest_fixture, "admit_guest_inputs", return_value=inputs),
                  mock.patch.object(owned_guest_fixture, "guest_input_identity",
                                    side_effect=lambda value: value),
                  mock.patch.object(guest_runtime.ReleasedGuest, "__init__", new=capture_guest),
                  mock.patch.object(guest_runtime, "OwnedProcess", CommandEffectsOnly)):
                bridge.prepare_native_provider()

            self.assertEqual(len(guest_instances), 1)
            guest = guest_instances[0]
            self.assertIsNone(guest.socket)
            self.assertIs(guest.runtime.journal, journal)
            self.assertIs(bridge.preparation[2].journal, journal)
            self.assertIn("guest-inputs.json", journal.records())
            self.assertIn("guest-provisioned.json", journal.records())
            self.assertEqual(sum(name.endswith("-intent.json") for name in journal.records()), 3)
            self.assertIsNone(bridge.socket)
            self.assertTrue((root / "container/kernels/default.kernel-arm64").is_symlink())


class RemainingActiveProviderHomeTests(unittest.TestCase):
    def test_preparation_rejects_a_mismatched_active_home_owner_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            campaign = base / "campaign"
            campaign.mkdir(mode=0o700)
            home = campaign / "home"
            home.mkdir(mode=0o700)
            (home / "container").mkdir(mode=0o700)
            guard_root = base / "workflow"
            guard_root.mkdir(mode=0o700)
            guard_path = guard_root / "runtime-admission.json"
            guard_path.write_text(json.dumps({"identity": {"campaign": "campaign-id",
                                                            "sourceCommit": "a" * 40,
                                                            "scope": "finalized-native-parity"},
                                               "root": str(campaign)}, sort_keys=True))
            guard_path.chmod(0o600)
            owner = {"identity": {"campaign": "other", "lane": "apple-stock",
                                  "sourceCommit": "a" * 40}, "root": str(home)}
            marker = home / "owner.json"
            marker.write_text(json.dumps(owner, sort_keys=True) + "\n")
            marker.chmod(0o600)
            runner = SimpleNamespace(
                environment={"HOME": str(home), "CONTAINER_APP_ROOT": str(home / "container"),
                             "DEVCONTAINER_PARITY_RETAINED_ROOT": str(guard_root),
                             "DEVCONTAINER_PARITY_GUARD": str(guard_path)},
                lane="apple-stock", output=campaign / "apple-stock",
                finalized_identity={"sourceCommit": "a" * 40},
            )
            with mock.patch("owned_guest_fixture.ACCOUNT_HOME", base):
                with self.assertRaisesRegex(ParityError, "differs from this lane"):
                    _active_provider_home(runner)

    def test_docker_preparation_keeps_its_campaign_scoped_ssd_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            evidence = base / "full84-run"
            evidence.mkdir(mode=0o700)
            retained = base / "retained"
            retained.mkdir(mode=0o700)
            runner = SimpleNamespace(
                output=evidence / "docker", lane="docker", retained_root=retained,
                repository=REPOSITORY,
                environment={"HOME": "/unrelated-account-home"},
                finalized_identity={"sourceCommit": "a" * 40, "archiveSHA256": "b" * 64},
            )
            bridge = OwnedGuestFixtureRunner.__new__(OwnedGuestFixtureRunner)
            bridge.runner, bridge.lane, bridge.retained = runner, "docker", retained
            bridge.repository = REPOSITORY

            staging_root, journal, owner = bridge._case_paths_for_preparation()

            self.assertEqual(staging_root, evidence / "owned-guest-runtime/docker/preparation")
            self.assertNotEqual(staging_root, Path(runner.environment["HOME"]))
            self.assertEqual(owner["identity"]["lane"], "docker")
            self.assertEqual(json.loads(journal.owner), owner)


class NativeE04BuilderOwnershipTests(unittest.TestCase):
    def test_readiness_removal_failure_preserves_exact_image_and_builder_evidence(self) -> None:
        import engine_probe

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            journal, runtime, guest = mock.Mock(), mock.Mock(), mock.Mock()
            bridge = OwnedGuestFixtureRunner.__new__(OwnedGuestFixtureRunner)
            bridge.socket = root / "docker.sock"
            bridge.preparation = (root, journal, runtime, {})
            bridge.builder_preparation_guest = guest
            identity = "sha256:" + "a" * 64

            def request(_socket, _method, path):
                from urllib.parse import unquote

                tag = unquote(path.removeprefix("/v1.53/images/").removesuffix("/json"))
                statuses = (404, 200, 200, 500)
                index = request.count
                request.count += 1
                return statuses[index], json.dumps({"Id": identity, "RepoTags": [tag]}).encode()

            request.count = 0
            with mock.patch.object(engine_probe, "request", side_effect=request):
                with self.assertRaisesRegex(ParityError, "remains after exact cleanup"):
                    bridge.prepare_native_builder_readiness()

            names = [call.args[0] for call in journal.put.call_args_list]
            self.assertIn("e04-readiness-build-intent.json", names)
            self.assertIn("e04-readiness-image-created.json", names)
            self.assertIn("e04-readiness-image-remove-intent.json", names)
            self.assertNotIn("e04-readiness-image-removed.json", names)
            self.assertIs(bridge.builder_preparation_guest, guest)
            self.assertEqual(guest.command.call_count, 2)
            self.assertNotIn("--load", guest.command.call_args_list[0].args[1])

    def test_builder_cleanup_failure_keeps_owner_and_does_not_write_success_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            bridge = OwnedGuestFixtureRunner.__new__(OwnedGuestFixtureRunner)
            bridge.preparation_error = None
            bridge.lane = "apple-stock"
            bridge.loaded_image_id, bridge.image_preexisting = None, False
            guest, journal, runtime = mock.Mock(), mock.Mock(), mock.Mock()
            guest.builder.cleanup.side_effect = ValueError("uncertain builder deletion")
            bridge.builder_preparation_guest = guest
            bridge.preparation = (root, journal, runtime, {})
            bridge.runner = SimpleNamespace(output=root)

            with self.assertRaisesRegex(ValueError, "uncertain builder deletion"):
                bridge.cleanup()

            guest.builder.cleanup.assert_called_once_with()
            self.assertIs(bridge.builder_preparation_guest, guest)
            journal.receipt.assert_not_called()
            self.assertFalse((root / "native-e04-builder-journal-receipt.json").exists())


if __name__ == "__main__":
    unittest.main()
