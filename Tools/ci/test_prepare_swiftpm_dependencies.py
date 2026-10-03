"""Offline tests for enhanced SwiftPM patch preflight and application."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent / "prepare-swiftpm-dependencies.py"
SPEC = importlib.util.spec_from_file_location("prepare_swiftpm_dependencies", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class PrepareSwiftPMDependenciesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="swiftpm-patch-test-")
        self.root = Path(self.temporary.name)
        (self.root / "Tools/bazel").mkdir(parents=True)
        self.rows = []
        self.checkouts = []
        repositories = self.root / ".build/repositories"
        repositories.mkdir(parents=True)
        for identity, filename, origin in (
            ("zstd", "Package.swift", "https://example.invalid/zstd.git"),
            ("containerization", "Runtime.swift", "https://example.invalid/containerization.git"),
            ("container-engine-api", "Responder.swift", "https://example.invalid/engine-api.git"),
        ):
            seed = self.root / ".seed" / identity
            seed.mkdir(parents=True)
            (seed / filename).write_text("original\n", encoding="utf-8")
            (seed / ".gitignore").write_text("*.ignored.swift\n", encoding="utf-8")
            git(seed, "init", "-q")
            git(seed, "config", "user.name", "Fixture")
            git(seed, "config", "user.email", "fixture@example.invalid")
            git(seed, "add", filename)
            git(seed, "add", ".gitignore")
            git(seed, "commit", "-q", "-m", "base")
            revision = git(seed, "rev-parse", "HEAD")
            mirror = repositories / (identity + "-fixture")
            subprocess.run(["git", "clone", "--bare", str(seed), str(mirror)], check=True,
                           capture_output=True)
            git(mirror, "remote", "set-url", "origin", origin)
            checkout = self.root / ".build/checkouts" / identity
            checkout.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", str(mirror), str(checkout)], check=True,
                           capture_output=True)
            (checkout / filename).write_text("patched\n", encoding="utf-8")
            if identity == "zstd":
                added = checkout / "include/zstd.h"
                added.parent.mkdir()
                added.write_text("#include ../zstd.h\n", encoding="utf-8")
                added.chmod(0o755)
                git(checkout, "add", "-N", "include/zstd.h")
            patch_bytes = subprocess.run(["git", "diff", "--binary", "HEAD"], cwd=checkout,
                                         check=True, capture_output=True).stdout
            patch_name = identity + ".patch"
            patch_path = self.root / "Tools/bazel" / patch_name
            patch_path.write_bytes(patch_bytes)
            self.rows.append({"identity": identity, "revision": revision,
                              "location": origin, "patch": patch_name,
                              "sha256": hashlib.sha256(patch_bytes).hexdigest()})
            self.checkouts.append(checkout)
            git(checkout, "reset", "--hard", "-q")

        self._write_lock("Package.resolved", self.rows)
        stock = [
            {"identity": "container", "revision": "9a8917ca2da5cd6ba059b9ba5ca5a74892e9bb7d",
             "location": "https://github.com/apple/container.git"},
            {"identity": "containerization", "revision": "9eacc197d7c3663eb29cbab6d51244ede6d1cd7d",
             "location": "https://github.com/apple/containerization.git"},
            {"identity": "container-engine-api", "revision": "40436017e1e93012b8dab7cfc3c79783538065c3",
             "location": "https://github.com/stephenlclarke/container-engine-api.git"},
        ]
        self._write_lock("Package.stock.resolved", stock)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_lock(self, name: str, rows: list[dict]) -> None:
        (self.root / name).write_text(json.dumps({"pins": [
            {"identity": row["identity"], "kind": "remoteSourceControl",
             "location": row["location"], "state": {"revision": row["revision"]}}
            for row in rows
        ]}), encoding="utf-8")

    def _prepare(self, *, env: dict[str, str] | None = None) -> dict:
        return MODULE.prepare(self.root, Path(".build"), environment=env or {}, patch_rows=self.rows)

    def test_applies_all_reviewed_patches_then_accepts_exact_repeat(self) -> None:
        result = self._prepare()
        self.assertEqual(result["status"], "prepared")
        self.assertEqual([item["status"] for item in result["patches"]], ["applied"] * 3)
        again = self._prepare()
        self.assertEqual(again, result)
        self.assertTrue((self.checkouts[0] / "include/zstd.h").is_file())

    def test_stock_profile_is_an_unmodified_noop(self) -> None:
        stock_lock = (self.root / "Package.stock.resolved").read_bytes()
        (self.root / "Package.resolved").write_bytes(stock_lock)
        before = [MODULE.status_paths(MODULE.PatchSpec(
            row["identity"], row["revision"], row["location"], checkout,
            self.root / "Tools/bazel" / row["patch"], row["sha256"]))
            for row, checkout in zip(self.rows, self.checkouts)]
        result = MODULE.prepare(self.root, Path(".build"), "stock", environment={},
                                patch_rows=self.rows)
        after = [MODULE.status_paths(MODULE.PatchSpec(
            row["identity"], row["revision"], row["location"], checkout,
            self.root / "Tools/bazel" / row["patch"], row["sha256"]))
            for row, checkout in zip(self.rows, self.checkouts)]
        self.assertEqual(result["status"], "unmodified")
        self.assertEqual(after, before)
        self.assertEqual((self.root / "Package.resolved").read_bytes(), stock_lock)

    def test_stock_profile_rejects_enhanced_active_lock(self) -> None:
        with self.assertRaisesRegex(ValueError, "active Package.resolved differs"):
            MODULE.prepare(self.root, Path(".build"), "stock", environment={},
                           patch_rows=self.rows)

    def test_bad_last_patch_is_rejected_before_any_checkout_changes(self) -> None:
        before = [MODULE.status_paths(MODULE.PatchSpec(
            row["identity"], row["revision"], row["location"], checkout,
            self.root / "Tools/bazel" / row["patch"], row["sha256"]))
            for row, checkout in zip(self.rows, self.checkouts)]
        bad_rows = [*self.rows[:-1], {**self.rows[-1], "sha256": "0" * 64}]
        with self.assertRaisesRegex(ValueError, "patch bytes changed"):
            MODULE.prepare(self.root, Path(".build"), environment={}, patch_rows=bad_rows)
        after = [MODULE.status_paths(MODULE.PatchSpec(
            row["identity"], row["revision"], row["location"], checkout,
            self.root / "Tools/bazel" / row["patch"], row["sha256"]))
            for row, checkout in zip(self.rows, self.checkouts)]
        self.assertEqual(after, before)

    def test_unrelated_tracked_change_is_rejected(self) -> None:
        target = self.checkouts[1] / "operator.swift"
        target.write_text("operator edit\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unrelated or partial"):
            self._prepare()

    def test_exact_patch_plus_extra_change_in_same_file_is_rejected(self) -> None:
        self._prepare()
        target = self.checkouts[2] / "Responder.swift"
        target.write_text(target.read_text(encoding="utf-8") + "foreign\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "exact reviewed patch output"):
            self._prepare()

    def test_mode_only_change_to_patched_file_is_rejected(self) -> None:
        self._prepare()
        target = self.checkouts[2] / "Responder.swift"
        target.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "exact reviewed patch output"):
            self._prepare()

    def test_mode_only_change_to_patch_added_file_is_rejected(self) -> None:
        self._prepare()
        target = self.checkouts[0] / "include/zstd.h"
        self.assertEqual(target.stat().st_mode & 0o777, 0o755)
        target.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "exact reviewed patch output"):
            self._prepare()

    def test_extra_untracked_file_is_rejected(self) -> None:
        self._prepare()
        (self.checkouts[1] / "surprise.swift").write_text("x\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unrelated or partial"):
            self._prepare()

    def test_checkout_origin_mismatch_is_rejected(self) -> None:
        git(self.checkouts[0], "remote", "set-url", "origin", "https://example.invalid/other.git")
        with self.assertRaisesRegex(ValueError, "not its scratch-local package mirror"):
            self._prepare()

    def test_package_mirror_must_bind_expected_upstream(self) -> None:
        mirror = self.root / ".build/repositories/zstd-fixture"
        git(mirror, "remote", "set-url", "origin", "https://example.invalid/other.git")
        with self.assertRaisesRegex(ValueError, "mirror origin differs"):
            self._prepare()

    def test_ignored_extra_checkout_file_is_rejected(self) -> None:
        self._prepare()
        (self.checkouts[1] / "Unexpected.ignored.swift").write_text("source\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unrelated or partial"):
            self._prepare()

    def test_checkout_revision_mismatch_is_rejected(self) -> None:
        git(self.checkouts[0], "checkout", "--orphan", "other")
        git(self.checkouts[0], "rm", "-rf", ".")
        (self.checkouts[0] / "Package.swift").write_text("different\n", encoding="utf-8")
        git(self.checkouts[0], "add", "Package.swift")
        git(self.checkouts[0], "commit", "-q", "-m", "other")
        with self.assertRaisesRegex(ValueError, "revision differs"):
            self._prepare()

    def test_source_path_override_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "rejects package override"):
            self._prepare(env={"CONTAINER_ENGINE_API_PACKAGE_PATH": "/tmp/custom-api"})

    def test_unrelated_environment_hints_are_not_package_overrides(self) -> None:
        result = self._prepare(env={"CONTAINERIZATION_SOURCE": "documentation-only"})
        self.assertEqual(result["status"], "prepared")

    def test_default_profile_is_enhanced(self) -> None:
        result = self._prepare()
        self.assertEqual(result["profile"], "enhanced")

    def test_wrong_profile_name_fails_before_writes(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be enhanced or stock"):
            MODULE.prepare(self.root, Path(".build"), "custom", environment={},
                           patch_rows=self.rows)

    def test_patch_paths_reject_parent_traversal(self) -> None:
        patch = self.root / "bad.patch"
        patch.write_text("diff --git a/../escape b/../escape\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unsafe path"):
            MODULE.patch_paths(patch)

    def test_partial_scratch_tree_fails_before_resolve(self) -> None:
        scratch = self.root / "partial-scratch"
        (scratch / "checkouts/zstd").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "partial SwiftPM patch checkout set"):
            MODULE.prepare(self.root, scratch, environment={}, patch_rows=self.rows)

    def test_missing_custom_scratch_resolves_once_without_changing_lock(self) -> None:
        scratch = self.root / "coverage-scratch"
        resolver = self._fake_resolver(scratch, mutate_lock=False)
        original_lock = (self.root / "Package.resolved").read_bytes()
        result = MODULE.prepare(self.root, scratch, swift=(sys.executable, str(resolver)),
                                environment={"DEVCONTAINER_RUNTIME_PROFILE": "enhanced"},
                                patch_rows=self.rows)
        self.assertEqual(result["status"], "prepared")
        self.assertEqual((self.root / "Package.resolved").read_bytes(), original_lock)
        self.assertEqual((scratch / "resolve-count").read_text(), "1")
        self.assertEqual((scratch / "checkouts/zstd/include/zstd.h").read_text(),
                         "#include ../zstd.h\n")

    def test_resolver_lock_mutation_fails_before_patching(self) -> None:
        scratch = self.root / "coverage-scratch"
        resolver = self._fake_resolver(scratch, mutate_lock=True)
        original_checkout = (self.checkouts[0] / "Package.swift").read_bytes()
        with self.assertRaisesRegex(ValueError, "changed the selected lock bytes"):
            MODULE.prepare(self.root, scratch, swift=(sys.executable, str(resolver)),
                           environment={"DEVCONTAINER_RUNTIME_PROFILE": "enhanced"},
                           patch_rows=self.rows)
        self.assertEqual((self.checkouts[0] / "Package.swift").read_bytes(), original_checkout)
        self.assertEqual((scratch / "checkouts/zstd/Package.swift").read_text(), "original\n")
        self.assertFalse((scratch / "checkouts/zstd/include/zstd.h").exists())

    def _fake_resolver(self, scratch: Path, *, mutate_lock: bool) -> Path:
        resolver = self.root / ("fake-resolve-mutate.py" if mutate_lock else "fake-resolve.py")
        source = self.root / ".build/checkouts"
        mirror_source = self.root / ".build/repositories"
        code = (
            "from pathlib import Path\nimport os, shutil, subprocess\n"
            f"src = Path({str(source)!r})\n"
            f"dst = Path({str(scratch / 'checkouts')!r})\n"
            f"mirror_src = Path({str(mirror_source)!r})\n"
            f"mirror_dst = Path({str(scratch / 'repositories')!r})\n"
            "mirror_dst.mkdir(parents=True, exist_ok=True)\n"
            "for child in mirror_src.iterdir():\n"
            "    if child.is_dir() and not (mirror_dst / child.name).exists():\n"
            "        shutil.copytree(child, mirror_dst / child.name, symlinks=True)\n"
            "dst.mkdir(parents=True, exist_ok=True)\n"
            "for child in src.iterdir():\n"
            "    if child.is_dir() and not (dst / child.name).exists():\n"
            "        shutil.copytree(child, dst / child.name, symlinks=True)\n"
            "        subprocess.run(['git', 'remote', 'set-url', 'origin', str(mirror_dst / (child.name + '-fixture'))], cwd=dst / child.name, check=True, capture_output=True)\n"
            f"Path({str(scratch / 'resolve-count')!r}).write_text('1')\n"
        )
        if mutate_lock:
            code += f"Path({str(self.root / 'Package.resolved')!r}).write_text('mutated')\n"
        resolver.write_text(code, encoding="utf-8")
        return resolver


if __name__ == "__main__":
    unittest.main()
