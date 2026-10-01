"""Apple completed-build recovery never adopts or signals the legacy Engine."""

import os
from pathlib import Path
from unittest.mock import Mock, patch
import tempfile
import unittest

from case_evidence import CaseStore, canonical, digest, validate_identity
from guest_fixture import OWNER_LABEL
from host_runtime import HostGuard
from recover_apple_build import admit_original, builder_state, require_outputs_absent, recover_resources
from recover_apple_build import require_previous_commands_closed
from service_journal import ServiceJournal


class AppleBuildRecoveryTests(unittest.TestCase):
    def images(self):
        images = Mock(intent={"image": "config", "base": "repository@manifest"})
        images.recovery_plan.return_value = []
        images.key.side_effect = lambda failing, event: str(failing) + event
        images.records.return_value = {"Falseremoved": b"{}", "Trueremoved": b"{}"}
        images.client.call.return_value = (200, canonical([{"Id": "config"}]))
        images.inspect.return_value = {"Id": "config", "RepoDigests": ["repository@manifest"], "Config": {"Labels": {}}}
        return images

    def test_only_absent_receipted_outputs_and_exact_unlabelled_base_qualify(self):
        images = self.images()
        require_outputs_absent(images)
        self.assertEqual(images.client.call.call_args.args, ("GET", "/images/json?all=true"))
        images.recovery_plan.return_value = [{"id": "still-present"}]
        with self.assertRaisesRegex(ValueError, "still exists"):
            require_outputs_absent(images)
        images.recovery_plan.return_value = []
        images.records.return_value = {}
        with self.assertRaisesRegex(ValueError, "receipt"):
            require_outputs_absent(images)

    def test_extra_images_invalid_inventory_and_changed_base_preserve_quarantine(self):
        for status, payload in ((500, []), (200, []), (200, {}), (200, [{}, {}]),
                                (200, [None]), (200, [{"Id": "foreign"}])):
            images = self.images()
            images.client.call.return_value = (status, canonical(payload))
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                require_outputs_absent(images)
        for image in (None, {}, {"Id": "config", "RepoDigests": []},
                      {"Id": "config", "RepoDigests": ["repository@manifest"],
                       "Config": {"Labels": {OWNER_LABEL: "other-case"}}}):
            images = self.images()
            images.inspect.return_value = image
            with self.subTest(image=image), self.assertRaisesRegex(ValueError, "base image"):
                require_outputs_absent(images)

    def test_builder_closure_is_exact_and_reappearance_is_refused(self):
        builder = Mock(intent={"original": "inputs"})
        builder.journal.records.return_value = {}
        with self.assertRaisesRegex(ValueError, "captured creation"):
            builder_state(builder)
        original = {"e04-builder-intent.json": canonical(builder.intent),
                    "e04-builder-start-completed.json": canonical({"completed": True}),
                    "e04-builder-created.json": canonical({"configurationSHA256": "a" * 64, "id": "buildkit"})}
        for name in original:
            builder.journal.records.return_value = {key: value for key, value in original.items() if key != name}
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "captured creation"):
                builder_state(builder)
        builder.journal.records.return_value = original
        builder.verify.return_value = ({"status": {"state": "running"}}, {})
        self.assertEqual(builder_state(builder), "running")
        builder.journal.records.return_value = {**original, "e04-builder-removed.json": b"{}"}
        with self.assertRaisesRegex(ValueError, "receipt"):
            builder_state(builder)
        builder.journal.records.return_value = {**original, "e04-builder-removed.json": canonical({
            "intentSHA256": digest(canonical(builder.intent)), "absent": True})}
        builder.inventory.return_value = []
        self.assertEqual(builder_state(builder), "absent")
        builder.inventory.return_value = [{}]
        with self.assertRaisesRegex(ValueError, "reappeared"):
            builder_state(builder)

    def test_previous_inventory_requires_stopped_helper_and_sealed_diagnostics(self):
        name = "guest-builder-list-5"
        records = {name + "-intent.json": canonical({"arguments": ["list"]}),
                   name + "-process.json": canonical({"pid": 42}),
                   name + "-stopped.json": canonical({"verifiedStopped": True}),
                   name + ".log": b"[]", name + "-log.json": canonical({
                       "bytes": 2, "sha256": digest(b"[]"), "truncated": False})}
        with patch("recover_apple_build.process_inventory", return_value={}) as inventory:
            require_previous_commands_closed(records)
            for missing in ("-process.json", "-stopped.json", ".log", "-log.json"):
                with self.subTest(missing=missing), self.assertRaises(ValueError):
                    require_previous_commands_closed({key: value for key, value in records.items() if key != name + missing})
            for item in ({"pid": 42, "group": 42}, {"pid": 43, "group": 42}):
                inventory.return_value = {item["pid"]: item}
                with self.subTest(item=item), self.assertRaisesRegex(ValueError, "helper"):
                    require_previous_commands_closed(records)

    def test_admission_binds_original_candidate_releases_and_guest_inputs(self):
        releases = [{"candidateInvocation": "original"}, {"runtime": "original"}]
        inputs = {"builder": "original"}
        runtime = {"releases": releases, "guestInputs": inputs}
        owner = {"identity": {"runtimeSHA256": digest(canonical(runtime)), "lane": "apple-stock"}}
        admission = {"runtime": runtime, "releaseLock": {}, "guestLocks": [{}, {}], "builderLock": {}}
        with patch("recover_apple_build.admit", return_value=releases) as admit, \
                patch("recover_apple_build.admit_guest", return_value=inputs) as guest:
            self.assertEqual(admit_original(Path("/retained"), owner, {"admission.json": admission}), (releases, inputs))
            self.assertEqual(admit.call_args.args[-1], "original")
            guest.return_value = {"changed": True}
            with self.assertRaisesRegex(ValueError, "inputs changed"):
                admit_original(Path("/retained"), owner, {"admission.json": admission})
            guest.return_value = inputs
            admit.return_value = []
            with self.assertRaisesRegex(ValueError, "inputs changed"):
                admit_original(Path("/retained"), owner, {"admission.json": admission})
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            admit_original(Path("/retained"), owner, {})

    def test_stable_build_recovery_uses_activation_not_historical_path(self):
        releases = [{"candidateInvocation": "original"}, {"activation": {"receiptSHA256": "exact"}}]
        inputs = {"builder": "original"}
        runtime = {"releases": releases, "guestInputs": inputs}
        owner = {"identity": {"runtimeSHA256": digest(canonical(runtime)), "lane": "apple-stock"}}
        admission = {"runtime": runtime, "releaseLock": {}, "guestLocks": [{}, {}], "builderLock": {}}
        with patch("recover_apple_build.admit") as legacy, \
                patch("recover_apple_build.admit_runtime", return_value=releases) as active, \
                patch("recover_apple_build.admit_guest", return_value=inputs):
            self.assertEqual(admit_original(Path("/retained"), owner, {"admission.json": admission}), (releases, inputs))
            active.assert_called_once_with({}, "apple-stock", Path("/retained"), "original")
            legacy.assert_not_called()
            active.return_value = [releases[0], {"activation": {"receiptSHA256": "changed"}}]
            with self.assertRaisesRegex(ValueError, "inputs changed"):
                admit_original(Path("/retained"), owner, {"admission.json": admission})


class AppleBuildResourceTransactionTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(scratch.cleanup)
        self.base = Path(scratch.name).resolve()
        self.retained = self.base / "retained"
        self.retained.mkdir(mode=0o700)
        (self.retained / "private-runtime").mkdir(mode=0o700)
        self.root = self.base / "case-fixture"
        self.root.mkdir(mode=0o700)
        self.identity = {"campaign": "recovery", "fixture": "E04-image-build", "lane": "apple-stock",
                         **{key: "a" * 64 for key in ("harnessSHA256", "runtimeSHA256", "releaseSetSHA256", "contractSHA256")}}
        self.owner = {"root": str(self.root), "identity": self.identity}
        self.key = validate_identity(self.identity)
        (self.root / "owner.json").write_bytes(canonical(self.owner))
        self.guard = HostGuard(self.retained / "runtime-admission.json")
        self.guard.begin(self.owner)
        self.store = CaseStore(self.retained / "runtime-cases.sqlite")
        self.store.path.chmod(0o600)
        self.store.begin(self.identity)
        for name, value in {"owner.json": self.owner, "process-intent.json": {"program": "/engine"},
                            "process.json": {"pid": 42, "root": str(self.root)}}.items():
            self.store.attach(self.identity, name, canonical(value))
        self.result = {"status": "failed", "observations": {}, "errors": ["cleanup: ValueError"],
                       "durationsNS": {"setup": 1, "operation": 1, "cleanup": 1},
                       "cleanup": {"status": "unknown", "remainingOwnedResources": []}}
        self.store.finish(self.identity, self.result)
        self.journal = ServiceJournal(self.retained / "private-runtime" / (digest(str(self.root).encode()) + ".sqlite"),
                                      self.owner, create=True)
        releases = [{}, {"executables": {"container": "/prepared/container", "container-apiserver": "/prepared/api"}}]
        inputs = {"workload": {"image": {"config": "sha256:" + "c" * 64,
                                        "repository": "repository", "manifest": "sha256:" + "d" * 64}},
                  "builder": {}}
        self.admit = patch("recover_apple_build.admit_original", return_value=(releases, inputs)).start()
        self.runtime = patch("recover_apple_build.ControlledRuntime").start().return_value
        self.guest = patch("recover_apple_build.ReleasedGuest").start().return_value
        self.builder = patch("recover_apple_build.ReleasedBuilder").start().return_value
        patch("recover_apple_build.BuildImages").start()
        self.absent = patch("recover_apple_build.require_outputs_absent").start()
        patch("recover_apple_build.builder_state", return_value="running").start()
        self.signal = patch("os.kill").start()
        self.addCleanup(patch.stopall)

    def recover(self, apply):
        return recover_resources(self.retained, self.owner, self.guard, apply=apply)

    def test_report_then_apply_never_signals_client_restores_services_or_rewrites_failure(self):
        self.assertEqual(self.recover(False)["status"], "ready-to-clean-apple-build-resources")
        self.builder.cleanup.assert_not_called()
        self.assertNotIn("e04-images-removed.json", self.journal.records())
        self.assertEqual(self.recover(True)["status"], "apple-build-resources-removed")
        self.builder.cleanup.assert_called_once()
        self.assertEqual(self.journal.records()["e04-images-removed.json"], canonical({"absent": True}))
        self.signal.assert_not_called()
        self.runtime.restore.assert_not_called()
        self.assertTrue(self.guard.path.exists())
        self.assertTrue(self.root.exists())
        self.assertEqual(self.store.begin(self.identity), self.result)

    def test_changed_inputs_failed_inspection_and_builder_failure_preserve_host(self):
        self.absent.side_effect = ValueError("unknown completion")
        with self.assertRaisesRegex(ValueError, "unknown completion"):
            self.recover(True)
        self.builder.cleanup.assert_not_called()
        self.absent.side_effect = None
        original = self.admit.return_value
        self.admit.side_effect = [original, ([], {})]
        with self.assertRaisesRegex(ValueError, "ownership or inputs"):
            self.recover(True)
        self.builder.cleanup.assert_not_called()
        self.admit.side_effect = None
        self.builder.cleanup.side_effect = ValueError("builder changed")
        with self.assertRaisesRegex(ValueError, "builder changed"):
            self.recover(True)
        self.runtime.restore.assert_not_called()
        self.signal.assert_not_called()
        self.assertTrue(self.guard.path.exists())

    def test_wrong_fixture_and_replaced_marker_fail_before_runtime_admission(self):
        self.owner["identity"]["fixture"] = "E01-engine-negotiation"
        with self.assertRaisesRegex(ValueError, "Apple E04"):
            self.recover(False)
        self.owner["identity"]["fixture"] = "E04-image-build"
        (self.root / "owner.json").write_bytes(b"{}")
        with self.assertRaisesRegex(ValueError, "ownership changed"):
            self.recover(True)
        self.admit.assert_not_called()
