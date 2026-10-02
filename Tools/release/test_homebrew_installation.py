"""Regression tests for the reversible Homebrew formula test boundary."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
import pwd
from pathlib import Path
import plistlib
import shutil
import tempfile
import unittest

import homebrew_installation as installation


SOURCE = "a" * 40
VERSION = "2.0.0"
ARCHIVE_SHA = "b" * 64


def internal_fixture_directory() -> tempfile.TemporaryDirectory:
    """Keep retained-role fixture data internal despite an external TMPDIR."""
    home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    return tempfile.TemporaryDirectory(prefix="homebrew-installation-", dir=home)


def tree_sha(path: Path) -> str:
    rows = {}
    for item in sorted(path.rglob("*")):
        relative = item.relative_to(path).as_posix()
        if item.is_symlink():
            rows[relative] = {"link": os.readlink(item)}
        elif item.is_file():
            rows[relative] = {"mode": item.stat().st_mode & 0o777,
                              "sha256": hashlib.sha256(item.read_bytes()).hexdigest()}
        else:
            rows[relative] = {"mode": item.stat().st_mode & 0o777, "directory": True}
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


class FakeGuard:
    def __init__(self):
        self.owner = None

    def check(self):
        if self.owner is not None:
            raise ValueError("quarantined")

    def begin(self, owner):
        if self.owner is not None:
            raise ValueError("already guarded")
        self.owner = owner

    def clear(self, owner):
        if self.owner != owner:
            raise ValueError("guard owner mismatch")
        self.owner = None


@contextmanager
def fake_lease(_path, guard):
    guard.check()
    yield


class FakeServices:
    def __init__(self):
        self.prior = []
        self.absent = list(installation.SERVICE_LABELS.values())
        self.captured_processes = []
        self.stopped = False
        self.restored = False

    def capture(self, _prefix, _kegs):
        return self.prior

    def stop(self):
        self.stopped = True

    def restore(self):
        self.restored = True

    def verify(self):
        if self.stopped and not self.restored:
            raise installation.InstallationError("service was not restored")


class FakeLaunchd:
    def __init__(self, item):
        self.item = item
        self.loaded = True
        self.bootouts = 0
        self.bootstraps = 0

    def labels(self):
        return {self.item["label"]} if self.loaded else set()

    def inspect(self, label):
        return self.item if self.loaded and label == self.item["label"] else None

    def process_id(self, _label):
        return 42

    def bootout(self, _label):
        self.loaded = False
        self.bootouts += 1

    def bootstrap(self, _path):
        self.loaded = True
        self.bootstraps += 1


class SurvivingProcesses:
    class ProcessSurvivors(ValueError):
        pass

    def __init__(self, process):
        self.process = process

    def capture_owned_processes(self, _launchd, prior):
        return [dict(self.process, labels=[item["label"] for item in prior])]

    def process_inventory(self):
        return {self.process["pid"]: self.process}

    def wait_stopped(self, probe, *, seconds):
        probe()

    def require_captured_processes_stopped(self, _launchd, _services, captured):
        if captured and self.process["started"] == captured[0]["started"]:
            raise self.ProcessSurvivors("captured service process survived bootout")


class FakeBrew:
    def __init__(self, prefix: Path, lane: str, candidate_version: str):
        self.prefix = prefix
        self.cellar = prefix / "Cellar"
        self.lane = lane
        self.candidate_formula = "devcontainer-current" if lane == "current" else "devcontainer"
        self.candidate_version = candidate_version
        self.taps = set()
        self.calls = []
        self.failure = None
        self.alter_link = False

    def __call__(self, *args, timeout=900):
        self.calls.append(tuple(args))
        if args == ("--prefix",):
            return str(self.prefix)
        if args == ("--cellar",):
            return str(self.cellar)
        if args[:1] == ("--prefix",) and len(args) == 2:
            return str(self.prefix / "opt" / self.candidate_formula)
        if args == ("tap",):
            return "\n".join(sorted(self.taps))
        if args[:3] == ("list", "--formula", "--versions"):
            rows = []
            for formula in sorted(installation.FORMULAE):
                root = self.cellar / formula
                for keg in sorted(root.iterdir()) if root.exists() else []:
                    if (keg / "INSTALL_RECEIPT.json").is_file():
                        rows.append(f"{formula} {keg.name}")
            return "\n".join(rows)
        if args[:2] == ("uninstall", "--force"):
            formula = args[2]
            root = self.cellar / formula
            if root.exists():
                shutil.rmtree(root)
            opt = self.prefix / "opt" / formula
            if opt.is_symlink():
                opt.unlink()
            for parent in (self.prefix / "bin", self.prefix / "share" / "man"):
                if parent.exists():
                    for path in parent.rglob("*"):
                        if path.is_symlink():
                            try:
                                target = path.resolve(strict=False)
                            except (OSError, RuntimeError):
                                continue
                            if target.is_relative_to(root) or os.readlink(path).startswith(f"../opt/{formula}/"):
                                path.unlink()
            return ""
        if args[:2] == ("tap-new", "--no-git"):
            tap = args[2]
            root = self.tap_root(tap)
            (root / "Formula").mkdir(parents=True)
            self.taps.add(tap)
            return ""
        if args[0] == "--repository":
            return str(self.tap_root(args[1]))
        if args[:1] in (("trust",), ("audit",), ("fetch",), ("untrust",)):
            return ""
        if args[:1] == ("install",):
            keg = self.cellar / self.candidate_formula / self.candidate_version
            (keg / "bin").mkdir(parents=True)
            if self.failure == "interrupt":
                (keg / "partial").write_text("interrupted")
                raise KeyboardInterrupt()
            if self.failure == "install":
                (keg / "partial").write_text("partial")
                raise installation.InstallationError("injected install failure")
            for name in ("devcontainer", "devcontainer-engine"):
                executable = keg / "bin" / name
                executable.write_text("fixture executable")
                executable.chmod(0o755)
            (keg / "INSTALL_RECEIPT.json").write_text("{}")
            opt = self.prefix / "opt" / self.candidate_formula
            opt.parent.mkdir(parents=True, exist_ok=True)
            opt.symlink_to(Path("../Cellar") / self.candidate_formula / self.candidate_version)
            bin_root = self.prefix / "bin"
            bin_root.mkdir(exist_ok=True)
            link = bin_root / "devcontainer"
            if self.alter_link:
                link.symlink_to("/tmp/foreign-target")
            else:
                link.symlink_to("../opt/" + self.candidate_formula + "/bin/devcontainer")
            return ""
        if args[:1] == ("test",):
            if self.failure == "test":
                raise installation.InstallationError("injected test failure")
            return ""
        if args[:1] == ("untap",):
            tap = args[1]
            root = self.tap_root(tap)
            if root.exists():
                shutil.rmtree(root)
            self.taps.discard(tap)
            return ""
        raise AssertionError(f"unhandled fake brew call: {args}")

    def tap_root(self, tap):
        slug = "homebrew-" + tap.split("/", 1)[1]
        return self.prefix / "Homebrew/Library/Taps/stephenlclarke" / slug


class HomebrewInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = internal_fixture_directory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.prefix = self.root / "homebrew"
        self.cellar = self.prefix / "Cellar"
        self.retained = self.root / "retained"
        self.retained.mkdir(mode=0o700)
        scratch_root = tempfile.TemporaryDirectory()
        self.addCleanup(scratch_root.cleanup)
        self.ssd = Path(scratch_root.name).resolve()
        self.scratch = self.ssd / "homebrew-installation-123"
        self.formula_path = self.root / "devcontainer.rb"
        self.context_path = self.root / "package-context.json"
        self.receipt = self.root / "receipt.json"
        self._write_context_and_formula("stable")
        self._install_old("devcontainer", "1.0.1")
        self.unrelated = self.prefix / "bin/unrelated-tool"
        self.unrelated.parent.mkdir(parents=True, exist_ok=True)
        self.unrelated.write_text("keep me")
        self.before = tree_sha(self.cellar)
        self.brew = FakeBrew(self.prefix, "stable", VERSION)
        self.services = FakeServices()
        self.guard = FakeGuard()

    def _write_context_and_formula(self, lane):
        if lane == "stable":
            context = {"asset": "devcontainer-release-arm64.tar.gz", "commit": SOURCE,
                       "formulaVersion": VERSION, "lane": "stable", "productVersion": VERSION,
                       "releaseTag": VERSION}
            declarations = ("", "")
            url = f"https://github.com/stephenlclarke/devcontainer/releases/download/{VERSION}/{context['asset']}"
            class_name = "Devcontainer"
        else:
            context = {"asset": f"devcontainer-current-{SOURCE[:12]}-arm64.tar.gz", "commit": SOURCE,
                       "formulaVersion": f"current.25.{SOURCE[:12]}", "lane": "current",
                       "productVersion": VERSION, "releaseTag": "current"}
            declarations = ('  version "current.25.' + SOURCE[:12] + '"\n',
                             '  conflicts_with "devcontainer", because: "both install devcontainer commands"\n\n')
            url = f"https://github.com/stephenlclarke/devcontainer/releases/download/current/{context['asset']}"
            class_name = "DevcontainerCurrent"
        self.context_path.write_text(json.dumps(context))
        template = (Path(__file__).with_name("devcontainer.rb.in")).read_text()
        formula = template.replace("@FORMULA_CLASS@", class_name)
        formula = formula.replace("@PRODUCT_VERSION@", VERSION).replace("@URL@", url)
        formula = formula.replace("@SHA256@", ARCHIVE_SHA)
        formula = formula.replace("@VERSION_DECLARATION@", declarations[0])
        formula = formula.replace("@CONFLICT_DECLARATION@", declarations[1])
        self.formula_path.write_text(formula)

    def _install_old(self, name, version):
        keg = self.cellar / name / version
        (keg / "bin").mkdir(parents=True, exist_ok=True)
        (keg / "bin/devcontainer").write_text("old executable")
        (keg / "bin/devcontainer-engine").write_text("old engine")
        (keg / "INSTALL_RECEIPT.json").write_text("{}")
        (keg / "bin/devcontainer").chmod(0o755)
        (keg / "bin/devcontainer-engine").chmod(0o755)
        opt = self.prefix / "opt" / name
        opt.parent.mkdir(parents=True, exist_ok=True)
        opt.symlink_to(Path("../Cellar") / name / version)
        bin_root = self.prefix / "bin"
        bin_root.mkdir(exist_ok=True)
        if name == "devcontainer":
            (bin_root / "devcontainer").symlink_to("../opt/devcontainer/bin/devcontainer")

    def transaction(self, **changes):
        options = {"formula_path": self.formula_path, "test_tap": "stephenlclarke/devcontainer-release-ci-123",
                   "lane": "stable", "expected_source_sha": SOURCE,
                   "finalized_context": self.context_path, "expected_version": VERSION,
                   "ssd_scratch": self.scratch, "retained_root": self.retained,
                   "receipt_output": self.receipt, "runner": self.brew,
                   "service": self.services, "storage_check": lambda *_: None,
                   "guard": self.guard, "lease_factory": fake_lease,
                   "cancellation_factory": nullcontext,
                   "version_check": lambda _path: json.dumps({"commit": SOURCE, "lane": "stable",
                                                                "version": VERSION})}
        options.update(changes)
        return installation.InstallationTransaction(**options)

    def test_success_restores_keg_links_and_preserves_unrelated_files(self):
        receipt = self.transaction().run()
        self.assertEqual(receipt["status"], "passed-restored")
        self.assertTrue(receipt["baselineRestored"])
        self.assertEqual(receipt["beforeInventorySHA256"], receipt["afterInventorySHA256"])
        self.assertEqual(tree_sha(self.cellar), self.before)
        self.assertEqual(self.unrelated.read_text(), "keep me")
        self.assertIsNone(self.guard.owner)
        self.assertFalse(self.scratch.exists())
        self.assertTrue((self.retained / receipt["backupId"] / "manifest.json").is_file())

    def test_partial_install_failure_restores_original_keg(self):
        self.brew.failure = "install"
        transaction = self.transaction()
        with self.assertRaises(installation.InstallationError):
            transaction.run()
        result = json.loads(self.receipt.read_text())
        self.assertEqual(result["status"], "failed-restored")
        self.assertTrue(result["baselineRestored"])
        self.assertEqual(tree_sha(self.cellar), self.before)
        self.assertIsNone(self.guard.owner)

    def test_formula_test_failure_restores_original_keg_and_links(self):
        self.brew.failure = "test"
        transaction = self.transaction()
        with self.assertRaises(installation.InstallationError):
            transaction.run()
        result = json.loads(self.receipt.read_text())
        self.assertEqual(result["failureCode"], "validation-or-installation-failed")
        self.assertEqual(result["originalFailureCode"], "validation-or-installation-failed")
        self.assertEqual(tree_sha(self.cellar), self.before)
        self.assertIsNone(self.guard.owner)

    def test_interrupt_during_install_restores_and_clears_owned_quarantine(self):
        self.brew.failure = "interrupt"
        transaction = self.transaction()
        with self.assertRaises(KeyboardInterrupt):
            transaction.run()
        result = json.loads(self.receipt.read_text())
        self.assertEqual(result["status"], "interrupted-restored")
        self.assertEqual(result["failureCode"], "cancelled")
        self.assertEqual(tree_sha(self.cellar), self.before)
        self.assertIsNone(self.guard.owner)

    def test_interrupt_before_first_mutation_clears_guard_after_unchanged_check(self):
        @contextmanager
        def interrupt_before_mutation():
            raise KeyboardInterrupt()
            yield

        transaction = self.transaction(cancellation_factory=interrupt_before_mutation)
        with self.assertRaises(KeyboardInterrupt):
            transaction.run()
        result = json.loads(self.receipt.read_text())
        self.assertEqual(result["status"], "interrupted-restored")
        self.assertEqual(tree_sha(self.cellar), self.before)
        self.assertIsNone(self.guard.owner)
        self.assertFalse(self.scratch.exists())
        self.assertFalse(any(call[0] == "uninstall" for call in self.brew.calls))

    def test_foreign_changed_link_fails_closed_and_keeps_backup_and_guard(self):
        self.brew.failure = "test"
        self.brew.alter_link = True
        transaction = self.transaction()
        with self.assertRaisesRegex(installation.InstallationError, "test transaction failed"):
            transaction.run()
        result = json.loads(self.receipt.read_text())
        self.assertEqual(result["status"], "restoration-failed")
        self.assertFalse(result["baselineRestored"])
        self.assertEqual(os.readlink(self.prefix / "bin/devcontainer"), "/tmp/foreign-target")
        self.assertTrue((self.retained / result["backupId"] / "manifest.json").is_file())
        self.assertIsNotNone(self.guard.owner)
        self.assertTrue(self.scratch.exists())
        self.assertEqual(self.unrelated.read_text(), "keep me")

    def test_ssd_rejection_happens_before_copy_or_homebrew_mutation(self):
        def reject_storage(*_):
            raise installation.InstallationError("SSD identity differs")

        transaction = self.transaction(storage_check=reject_storage)
        with self.assertRaises(installation.InstallationError):
            transaction.run()
        self.assertFalse(self.scratch.exists())
        self.assertFalse(any(self.retained.iterdir()))
        self.assertFalse(any(call[0] == "uninstall" for call in self.brew.calls))
        self.assertEqual(tree_sha(self.cellar), self.before)

    def test_wrong_enrolled_ssd_uuid_rejects_before_copy(self):
        disk_info = plistlib.dumps({"MountPoint": "/Volumes/SSD", "Internal": False,
                                    "VolumeUUID": "00000000-0000-0000-0000-000000000001"})
        transaction = self.transaction(storage_check=lambda *_: installation.validate_ssd(
            "00000000-0000-0000-0000-000000000002", disk_info))
        with self.assertRaises(installation.InstallationError):
            transaction.run()
        self.assertFalse(self.scratch.exists())
        self.assertFalse(any(self.retained.iterdir()))
        self.assertFalse(self.brew.calls)

    def test_symlinked_existing_keg_is_rejected_before_guard_or_uninstall(self):
        root = self.cellar / "devcontainer"
        shutil.rmtree(root)
        foreign = self.root / "foreign-keg"
        foreign.mkdir()
        root.symlink_to(foreign)
        transaction = self.transaction()
        with self.assertRaisesRegex(installation.InstallationError, "owned root"):
            transaction.run()
        self.assertIsNone(self.guard.owner)
        self.assertFalse(any(call[0] == "uninstall" for call in self.brew.calls))

    def test_context_source_or_lane_mismatch_rejects_before_storage_and_brew(self):
        context = json.loads(self.context_path.read_text())
        context["commit"] = "c" * 40
        self.context_path.write_text(json.dumps(context))
        transaction = self.transaction()
        with self.assertRaisesRegex(installation.InstallationError, "package context differs"):
            transaction.run()
        self.assertFalse(self.scratch.exists())
        self.assertFalse(self.brew.calls)

    def test_current_formula_context_and_exact_template_are_accepted(self):
        self._write_context_and_formula("current")
        context = installation.validate_context(
            self.context_path, self.formula_path, "current", SOURCE, VERSION,
            Path(__file__).with_name("devcontainer.rb.in"))
        self.assertEqual(context["formulaVersion"], f"current.25.{SOURCE[:12]}")

    def test_modified_formula_code_is_rejected_even_when_digest_line_is_valid(self):
        self.formula_path.write_text(self.formula_path.read_text() + "\n  system \"touch", encoding="utf-8")
        transaction = self.transaction()
        with self.assertRaisesRegex(installation.InstallationError, "maintained release template"):
            transaction.preflight()

    def test_tap_namespace_is_checked_before_any_homebrew_call(self):
        transaction = self.transaction(test_tap="someone/other-tap")
        with self.assertRaisesRegex(installation.InstallationError, "temporary tap name"):
            transaction.run()
        self.assertFalse(self.brew.calls)

    def test_launchd_survivor_blocks_replacement_and_is_never_killed(self):
        home = self.root / "home"
        agents = home / "Library/LaunchAgents"
        agents.mkdir(parents=True)
        keg = self.cellar / "devcontainer/1.0.1"
        executable = keg / "bin/devcontainer-engine"
        label = "homebrew.mxcl.devcontainer"
        definition = agents / f"{label}.plist"
        definition.write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": [str(executable)]}))
        job = {"label": label, "path": str(definition), "program": str(executable)}
        launchd = FakeLaunchd(job)
        process_runtime = SurvivingProcesses({"pid": 42, "parent": 1, "group": 42,
                                              "started": "Mon Sep 1 00:00:00 2026",
                                              "program": str(executable)})
        services = installation.LaunchdServices(launchd, home=home, process_runtime=process_runtime)
        services.capture(self.prefix, {"devcontainer": [keg]})
        with self.assertRaises(installation.InstallationError):
            services.stop()
        self.assertEqual(launchd.bootouts, 1)
        self.assertEqual(launchd.bootstraps, 0)
        self.assertFalse(launchd.loaded)

    def test_surviving_engine_prevents_any_formula_uninstall(self):
        home = self.root / "home"
        agents = home / "Library/LaunchAgents"
        agents.mkdir(parents=True)
        keg = self.cellar / "devcontainer/1.0.1"
        executable = keg / "bin/devcontainer-engine"
        label = "homebrew.mxcl.devcontainer"
        definition = agents / f"{label}.plist"
        definition.write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": [str(executable)]}))
        job = {"label": label, "path": str(definition), "program": str(executable)}
        launchd = FakeLaunchd(job)
        process_runtime = SurvivingProcesses({"pid": 42, "parent": 1, "group": 42,
                                              "started": "Mon Sep 1 00:00:00 2026",
                                              "program": str(executable)})
        service = installation.LaunchdServices(launchd, home=home, process_runtime=process_runtime)
        transaction = self.transaction(service=service)
        with self.assertRaises(installation.InstallationError):
            transaction.run()
        result = json.loads(self.receipt.read_text())
        self.assertEqual(result["status"], "restoration-failed")
        self.assertEqual(launchd.bootouts, 1)
        self.assertEqual(launchd.bootstraps, 0)
        self.assertFalse(any(call[:2] == ("uninstall", "--force") for call in self.brew.calls))
        self.assertEqual(tree_sha(self.cellar), self.before)
        self.assertIsNotNone(self.guard.owner)

    def test_wrong_product_version_is_rejected_and_baseline_is_restored(self):
        self.brew.version_mismatch = True
        transaction = self.transaction(version_check=lambda _path: json.dumps(
            {"commit": SOURCE, "lane": "stable", "version": "9.9.9"}))
        with self.assertRaises(installation.InstallationError):
            transaction.run()
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt["status"], "failed-restored")
        self.assertEqual(tree_sha(self.cellar), self.before)


if __name__ == "__main__":
    unittest.main()
