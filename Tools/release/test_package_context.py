"""Tests for immutable package and formula identities."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType


TOOLS = Path(__file__).resolve().parent
COMMIT = "0123456789ABCDEF0123456789ABCDEF01234567"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def load_module(filename: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load module: {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class PackageContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module("package-context.py", "package_context")

    def test_stable_identity_is_semantic_and_immutable(self) -> None:
        context = self.module.package_context("1.2.3", "stable", COMMIT)
        self.assertEqual(context.asset, "devcontainer-release-arm64.tar.gz")
        self.assertEqual(context.formulaVersion, "1.2.3")
        self.assertEqual(context.releaseTag, "1.2.3")
        self.assertEqual(context.commit, COMMIT.lower())

    def test_current_identity_is_commit_addressed_and_monotonic(self) -> None:
        context = self.module.package_context("1.2.3", "current", COMMIT, "418")
        self.assertEqual(
            context.asset,
            "devcontainer-current-0123456789ab-arm64.tar.gz",
        )
        self.assertEqual(context.formulaVersion, "current.418.0123456789ab")
        self.assertEqual(context.releaseTag, "current")

    def test_development_identity_supports_an_uncommitted_source_tree(self) -> None:
        context = self.module.package_context(
            "1.2.3",
            "development",
            "unspecified",
        )
        self.assertEqual(context.asset, "devcontainer-1.2.3-macos-arm64.tar.gz")
        self.assertEqual(context.releaseTag, "")

    def test_invalid_or_incomplete_release_inputs_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.module.package_context("v1.2.3", "stable", COMMIT)
        with self.assertRaises(ValueError):
            self.module.package_context("1.2.3", "stable", "0123456789ab")
        with self.assertRaisesRegex(ValueError, "workflow run number"):
            self.module.package_context("1.2.3", "current", COMMIT)
        with self.assertRaisesRegex(ValueError, "package lane"):
            self.module.package_context("1.2.3", "nightly", COMMIT)


if __name__ == "__main__":
    unittest.main()
