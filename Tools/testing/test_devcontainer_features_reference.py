"""D05 exact Feature inputs, native routing, rejection evidence and cleanup."""

import copy
import json
from pathlib import Path
import sys
from unittest.mock import Mock, patch
import unittest

from case_evidence import canonical
from devcontainer_candidate import CandidateCommands, DevcontainerFeaturesCandidate
from devcontainer_features_reference import DevcontainerFeaturesReference, FIXTURE, IMAGE, NEGATIVE, fixture_inputs
from host_runtime import deadline
from guest_runtime import guest_diagnostic_plan, require_guest_cleanup
import test_devcontainer_reference as reference_tests


PROBE = b"feature_git=true\nfeature_jq=true\nlockfile=true\n"


class FeaturesReferenceTests(reference_tests.ReferenceTests):
    def reopen(self):
        self.inputs["devcontainerFixture"] = fixture_inputs(Path(__file__).parents[2])
        fixture = DevcontainerFeaturesReference(self.vm, self.inputs, self.owner)
        self.server.images = {
            IMAGE: {"Id": self.inputs["workload"]["image"]["manifest"], "RootFS": {"Layers": ["sha256:" + "1" * 64]}},
            "generated:test": {"Id": "sha256:" + "e" * 64,
                               "RootFS": {"Layers": ["sha256:" + "1" * 64, "sha256:" + "2" * 64]},
                               "Config": {"Labels": {"devcontainer.metadata": json.dumps(fixture.expected_metadata())}}},
        }
        return fixture

    def guest(self):
        value = super().guest()
        value["Config"]["Image"] = "generated:test"
        value["Image"] = "sha256:" + "e" * 64
        return value

    def command(self, name, arguments, **kwargs):
        if name != NEGATIVE:
            result = super().command(name, arguments, **kwargs)
            return PROBE if name == "devcontainer-exec" else result
        self.commands.append((name, arguments, kwargs["timeout"]))
        self.assertFalse(self.fixture.lock_path.exists())
        self.assertIn("--frozen-lockfile", arguments)
        self.assertNotIn("--remove-existing-container", arguments)
        self.vm.journal.put(name + "-intent.json", canonical({"arguments": arguments}))
        self.vm.journal.put(name + "-exit.json", canonical({"code": getattr(self, "exit_code", 1)}))
        result = getattr(self, "negative_result", {"outcome": "error", "message": "Lockfile does not exist.",
                                                  "description": "An error occurred setting up the container."})
        (self.root / (name + ".log")).write_bytes(canonical(result))
        message = getattr(self, "negative_message", "Error: Lockfile does not exist.\n    at FQ")
        prefix = canonical({"type": "text", "text": "Resolving Remote"}) + b"\n(node:123) [DEP0169] DeprecationWarning\n"
        (self.root / (name + self.fixture.negative_stderr_suffix)).write_bytes(prefix + message.encode())
        if getattr(self, "exit_code", 1) == 0:
            return canonical(result)
        raise RuntimeError("expected CLI exit")

    def test_full_contract_uses_exact_pins_isolated_paths_and_verified_cleanup(self):
        self.assertEqual(self.start(), dict(feature_git="true", feature_jq="true", lockfile="true", frozen_lock="true"))
        self.assertEqual(self.fixture.workspace.name, FIXTURE)
        self.assertEqual(self.commands[0][1][-1], IMAGE)
        self.assertEqual([entry[0] for entry in self.commands],
                         ["devcontainer-image-pull", NEGATIVE, "devcontainer-up", "devcontainer-exec"])
        self.assertEqual([entry[2] for entry in self.commands], [120, 120, 900, 60])
        self.assertIn("--frozen-lockfile", self.commands[2][1])
        self.assertIn("DOCKER_BUILDKIT=0", self.commands[2][1])
        self.assertEqual(self.fixture.lock_path.read_text(), self.inputs["devcontainerFixture"]["lockfile"])
        self.assertIn("devcontainer-frozen-rejected.json", self.vm.journal.records())
        self.fixture.cleanup()
        self.assertIsNone(self.server.guest)

    def test_unrelated_failure_is_not_frozen_lock_success_and_restores_original(self):
        self.negative_message = "Error: request failed: DNS unavailable"
        self.fixture.setup()
        with self.assertRaisesRegex(ValueError, "expected reason"):
            self.fixture.operation()
        self.assertEqual(self.fixture.lock_path.read_text(), self.inputs["devcontainerFixture"]["lockfile"])
        self.assertNotIn("devcontainer-frozen-rejected.json", self.vm.journal.records())
        self.assertNotIn("devcontainer-up-intent.json", self.vm.journal.records())
        self.fixture.cleanup()

    def test_real_cli_mixed_stderr_still_requires_exact_structured_failure(self):
        self.start()
        self.fixture.cleanup()

    def test_diagnostic_line_alone_cannot_hide_different_structured_error(self):
        self.negative_result = {"outcome": "error", "message": "Network unavailable"}
        with self.assertRaisesRegex(ValueError, "expected reason"):
            self.start()
        self.fixture.cleanup()

    def test_accepted_missing_lock_fails_even_if_output_claims_error(self):
        self.exit_code = 0
        self.fixture.setup()
        with self.assertRaisesRegex(ValueError, "was accepted"):
            self.fixture.operation()
        self.assertTrue(self.fixture.lock_path.is_file())
        self.fixture.cleanup()

    def test_error_diagnostic_cannot_hide_a_success_or_signal_exit(self):
        self.negative_result = {"outcome": "success", "containerId": reference_tests.ID}
        with self.assertRaisesRegex(ValueError, "expected reason"):
            self.start()
        self.fixture.cleanup()

    def test_signal_exit_is_not_missing_lock_rejection(self):
        self.exit_code = -15
        with self.assertRaisesRegex(ValueError, "expected reason"):
            self.start()
        self.fixture.cleanup()

    def test_negative_completion_is_required_for_recovery(self):
        self.start()
        records = self.vm.journal.records()
        del records[NEGATIVE + "-exit.json"]
        with patch.object(self.vm.journal, "records", return_value=records), self.assertRaisesRegex(ValueError, "completion"):
            self.fixture.cleanup()
        self.assertIsNotNone(self.server.guest)
        self.fixture.cleanup()

    def test_generated_metadata_and_base_ancestry_are_exact(self):
        self.start()
        original = copy.deepcopy(self.server.images)
        for mutate in (
            lambda images: images["generated:test"]["Config"]["Labels"].update({"devcontainer.metadata": "[]"}),
            lambda images: images[IMAGE].update(Id="sha256:" + "f" * 64),
            lambda images: images["generated:test"]["RootFS"].update(Layers=["sha256:" + "5" * 64]),
        ):
            self.server.images = copy.deepcopy(original)
            mutate(self.server.images)
            with self.assertRaisesRegex(ValueError, "image identity"):
                self.fixture.cleanup()
        self.server.images = original
        self.fixture.cleanup()

    def test_missing_lock_does_not_overwrite_unexpected_replacement(self):
        command = self.vm.command
        def replaced(*args, **kwargs):
            try:
                return command(*args, **kwargs)
            finally:
                if args[0] == NEGATIVE:
                    self.fixture.lock_path.write_text("foreign replacement")
        self.vm.command = replaced
        with self.assertRaisesRegex(ValueError, "unexpected lockfile"):
            self.start()
        self.assertEqual(self.fixture.lock_path.read_text(), "foreign replacement")
        self.assertEqual(self.fixture.lock_path.with_suffix(".json.parity-missing").read_text(),
                         self.inputs["devcontainerFixture"]["lockfile"])

    def test_fixture_reader_rejects_changed_feature_and_lock(self):
        root = self.root / "repository/Tests/Parity/fixtures" / FIXTURE
        (root / ".devcontainer").mkdir(parents=True)
        inputs = self.inputs["devcontainerFixture"]
        (root / "probe.sh").write_text(inputs["probe"])
        (root / ".devcontainer/devcontainer.json").write_text(inputs["configuration"])
        lock = root / ".devcontainer/devcontainer-lock.json"
        lock.write_text('{"features": {}}')
        with self.assertRaisesRegex(ValueError, "lock pin"):
            fixture_inputs(self.root / "repository")
        lock.write_text(inputs["lockfile"])
        self.assertEqual(fixture_inputs(self.root / "repository"), inputs)

    def test_operation_has_one_whole_phase_deadline(self):
        self.fixture.setup()
        def bound(seconds):
            self.assertEqual(seconds, 1080)
            return deadline(3)
        with patch("devcontainer_features_reference.deadline", side_effect=bound):
            self.fixture.operation()
        self.fixture.cleanup()


class FeaturesCandidateTests(FeaturesReferenceTests):
    def reopen(self):
        super().reopen()
        self.inputs["devcontainerCandidate"] = {"executables": {"devcontainer": "/prepared/devcontainer"}}
        self.vm.container = "/prepared/container"
        self.vm.prepare_cleanup = lambda: None  # Fake commands have no processes.
        self.vm.close = lambda: None
        return DevcontainerFeaturesCandidate(self.vm, self.inputs, self.owner, before_build=lambda: None)

    def test_full_contract_uses_exact_pins_isolated_paths_and_verified_cleanup(self):
        self.assertEqual(self.start(), dict(feature_git="true", feature_jq="true", lockfile="true", frozen_lock="true"))
        self.assertEqual(self.commands[0][1][-1], IMAGE)
        for entry in self.commands[1:3]:
            self.assertEqual(entry[1].count("--frozen-lockfile"), 1)
            self.assertEqual(entry[1][2], "/prepared/devcontainer")
            self.assertIn("--buildkit", entry[1])
        self.fixture.cleanup()
        self.assertIsNone(self.server.guest)

    def test_slow_cleanup_response_obeys_whole_phase_deadline(self):
        self.start()
        self.server.drip = True
        with deadline(0.08), self.assertRaises(TimeoutError):
            self.fixture.cleanup()
        self.assertIsNotNone(self.server.guest)

    def test_real_native_command_rejection_uses_retained_native_stderr(self):
        runner = CandidateCommands(self.root, self.vm.socket, Mock(journal=self.vm.journal), "/usr/bin/true")
        script = ('import json,sys; print(json.dumps({"outcome":"error","message":"Lockfile does not exist."})); '
                  'print("Error: Lockfile does not exist.",file=sys.stderr); '
                  'sys.exit(1)')
        with self.assertRaises(RuntimeError):
            runner.command(NEGATIVE, [sys.executable, "-I", "-c", script], timeout=3, separate_output=True)
        self.fixture.vm = runner
        self.fixture.verify_rejection()
        runner.close()
        require_guest_cleanup(self.vm.journal.records())

    def test_native_negative_requires_stop_receipt_and_both_diagnostic_streams(self):
        records = {NEGATIVE + "-intent.json": b"{}"}
        with self.assertRaisesRegex(ValueError, "process"):
            require_guest_cleanup(records)
        records[NEGATIVE + "-stopped.json"] = canonical({"verifiedStopped": True})
        with self.assertRaisesRegex(ValueError, "diagnostics"):
            require_guest_cleanup(records)
        for stem in (NEGATIVE, NEGATIVE + "-stderr"):
            (self.root / (stem + ".log")).write_bytes(b"retained failure")
        plan = guest_diagnostic_plan(self.root, records)
        self.assertEqual(set(plan), {NEGATIVE + suffix for suffix in
                                   (".log", "-log.json", "-stderr.log", "-stderr-log.json")})
        records.update(plan)
        require_guest_cleanup(records)
        del records[NEGATIVE + "-stderr.log"]
        with self.assertRaisesRegex(ValueError, "diagnostics"):
            require_guest_cleanup(records)


if __name__ == "__main__":
    unittest.main()
