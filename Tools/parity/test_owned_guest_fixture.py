#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# SPDX-License-Identifier: Apache-2.0

"""Inert failure behavior for the owned released-guest bridge."""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from owned_guest_fixture import OwnedGuestFixtureRunner, _active_provider_home, admit_guest_inputs
from parity_lib import ParityError


REPOSITORY = Path(__file__).resolve().parents[2]


class OwnedGuestFailureTests(unittest.TestCase):
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
                            b"compose-stdout\nsignal:USR1\nsignal:USR1\nsignal:TERM\n",
                        )
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

            self.assertEqual(result["status"], "passed")
            self.assertEqual(len(instances), 1)
            self.assertTrue(instances[0].operated and instances[0].cleaned)
            self.assertEqual(result["signalStream"]["counts"], {"SIGUSR1": 2, "SIGTERM": 1})
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

    def test_compose_selection_exports_authenticated_lane_paths_and_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            provider_root = root / "provider"
            bin_root = provider_root / "bin"
            bin_root.mkdir(parents=True, mode=0o700)
            container = bin_root / "container"
            wrapper = root / "devcontainer-compose-wrapper"
            provider = root / "container-compose-provider"
            state = root / "state.sqlite"
            for executable in (container, wrapper, provider):
                executable.write_bytes(b"#!/bin/sh\nexit 0\n")
                executable.chmod(0o755)
            state.touch(mode=0o600)
            provider_sha = hashlib.sha256(provider.read_bytes()).hexdigest()
            for lane, backend in (("apple-stock", "stock"), ("container-compose", "container-compose")):
                bridge = OwnedGuestFixtureRunner.__new__(OwnedGuestFixtureRunner)
                bridge.lane, bridge.socket = lane, root / "docker.sock"
                bridge.container, bridge.compose, bridge.compose_provider = str(container), wrapper, provider
                bridge.runner = SimpleNamespace(environment={
                    "DEVCONTAINER_STATE": str(state),
                    "DEVCONTAINER_COMPOSE_PROVIDER_SHA256": provider_sha,
                })
                selection = bridge._compose_wrapper_selection()
                self.assertEqual(selection["DEVCONTAINER_BACKEND"], backend)
                self.assertEqual(selection["DEVCONTAINER_COMPOSE_PROVIDER"], "container-compose")
                self.assertEqual(selection["DEVCONTAINER_COMPOSE_BIN"], str(provider))
                self.assertEqual(selection["DEVCONTAINER_CONTAINER_BIN"], str(container))
                self.assertEqual(selection["DEVCONTAINER_STATE"], str(state))
                self.assertEqual(selection["DEVCONTAINER_SOCKET"], str(bridge.socket))
                self.assertNotEqual(selection["DEVCONTAINER_COMPOSE_BIN"], str(bridge.compose))

    def test_docker_inputs_select_only_the_locked_alpine_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
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

    def test_docker_compose_requires_the_exact_manifest_hash_and_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
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
    def test_preparation_targets_the_active_private_home_not_fixture_scratch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
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
            bridge.retained = base / "retained"
            bridge.retained.mkdir(mode=0o700)

            staging_root, journal, selected_owner = bridge._case_paths_for_preparation()

            self.assertEqual(staging_root, home)
            self.assertEqual(selected_owner, owner)
            self.assertTrue((home / "owner.json").is_file())
            self.assertFalse((campaign / "owned-guest-runtime").exists())
            self.assertEqual(json.loads(journal.owner), owner)

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


if __name__ == "__main__":
    unittest.main()
