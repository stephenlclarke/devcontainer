"""Regression tests for narrowly scoped Swift coverage cleanup."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent / "clean-swift-coverage.py"
SPEC = importlib.util.spec_from_file_location("clean_swift_coverage", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class CleanSwiftCoverageTests(unittest.TestCase):
    def test_cleans_only_the_selected_build_coverage_outputs(self) -> None:
        with tempfile.TemporaryDirectory(dir=MODULE.ROOT) as temporary:
            repository = Path(temporary).resolve()
            scratch = repository / ".build"
            checkout = scratch / "checkouts/containerization/vminitd/.devcontainer"
            binary = scratch / "out/Products/Debug"
            codecov = binary / "codecov"
            checkout.mkdir(parents=True)
            codecov.mkdir(parents=True)
            dependency_config = checkout / "devcontainer.json"
            patched_source = scratch / "checkouts/containerization/Sources/UnsafeLittleEndianBytes.swift"
            dependency_config.write_text('{"name":"tracked dependency config"}\n', encoding="utf-8")
            patched_source.parent.mkdir(parents=True)
            patched_source.write_text("// reviewed ext4 patch\n", encoding="utf-8")
            stale_profiles = [codecov / "default.profdata", codecov / "devcontainer.json",
                              codecov / "test.profraw"]
            for profile in stale_profiles:
                profile.write_bytes(b"generated")
            unrelated = codecov / "keep.json"
            unrelated.write_text("keep\n", encoding="utf-8")
            nested = codecov / "nested"
            nested.mkdir()
            nested_profile = nested / "preserve.profraw"
            nested_profile.write_bytes(b"not a flat output")

            removed = MODULE.clean_profiles(scratch, binary)

            self.assertEqual(removed, ("default.profdata", "devcontainer.json", "test.profraw"))
            self.assertTrue(dependency_config.is_file())
            self.assertTrue(patched_source.is_file())
            self.assertTrue(unrelated.is_file())
            self.assertTrue(nested_profile.is_file())
            for profile in stale_profiles:
                self.assertFalse(profile.exists(), profile.name)

    def test_refuses_product_directory_outside_selected_scratch(self) -> None:
        with tempfile.TemporaryDirectory(dir=MODULE.ROOT) as temporary:
            repository = Path(temporary).resolve()
            scratch = repository / ".build"
            scratch.mkdir()
            outside = repository / "other-build"
            (outside / "codecov").mkdir(parents=True)
            profile = outside / "codecov/default.profdata"
            profile.write_bytes(b"keep")
            with self.assertRaisesRegex(ValueError, "selected scratch"):
                MODULE.clean_profiles(scratch, outside)
            self.assertTrue(profile.is_file())

    def test_missing_fresh_binary_directory_is_a_noop(self) -> None:
        with tempfile.TemporaryDirectory(dir=MODULE.ROOT) as temporary:
            repository = Path(temporary).resolve()
            scratch = repository / ".build"
            scratch.mkdir()
            binary = scratch / "arm64-apple-macosx/debug"
            self.assertEqual(MODULE.clean_profiles(scratch, binary), ())
            self.assertFalse(binary.exists())

    def test_rejects_symlinked_codecov_directory(self) -> None:
        with tempfile.TemporaryDirectory(dir=MODULE.ROOT) as temporary:
            repository = Path(temporary).resolve()
            scratch = repository / ".build"
            binary = scratch / "bin"
            binary.mkdir(parents=True)
            outside = repository / "outside"
            outside.mkdir()
            (outside / "default.profdata").write_bytes(b"keep")
            (binary / "codecov").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "must not be a symlink"):
                MODULE.clean_profiles(scratch, binary)
            self.assertTrue((outside / "default.profdata").is_file())

    def test_rejects_symlinked_coverage_file_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory(dir=MODULE.ROOT) as temporary:
            repository = Path(temporary).resolve()
            scratch = repository / ".build"
            codecov = scratch / "bin/codecov"
            codecov.mkdir(parents=True)
            outside = repository / "outside.profdata"
            outside.write_bytes(b"keep")
            (codecov / "default.profdata").symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "regular file"):
                MODULE.clean_profiles(scratch, codecov.parent)
            self.assertTrue(outside.is_file())


if __name__ == "__main__":
    unittest.main()
