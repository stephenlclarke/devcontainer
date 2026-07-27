"""Tests for stable and Current release version calculations."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


TOOLS = Path(__file__).resolve().parent


def load_module(filename: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load module: {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ReleaseVersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module("release-version.py", "release_version")

    def test_compose_compatible_increment_selectors(self) -> None:
        base = self.module.SemanticVersion.parse("1.4.2")
        self.assertEqual(str(self.module.resolve_selector("--+", base)), "1.4.3")
        self.assertEqual(str(self.module.resolve_selector("-+-", base)), "1.5.0")
        self.assertEqual(str(self.module.resolve_selector("+--", base)), "2.0.0")

    def test_tags_are_strictly_filtered_and_sorted_numerically(self) -> None:
        versions = self.module.semantic_tags(
            ["1.9.0", "1.10.0", "v2.0.0", "1.2", "2.0.0-rc.1", "0.9.0"]
        )
        self.assertEqual([str(version) for version in versions], [
            "0.9.0",
            "1.9.0",
            "1.10.0",
        ])

    def test_selector_is_relative_to_latest_semantic_tag(self) -> None:
        candidate = self.module.release_version(
            "--+",
            self.module.SemanticVersion.parse("1.10.0"),
            ["1.9.9", "1.10.2", "not-a-release"],
        )
        self.assertEqual(str(candidate), "1.10.3")

    def test_initial_explicit_release_may_equal_checked_in_version(self) -> None:
        candidate = self.module.release_version(
            "0.1.0",
            self.module.SemanticVersion.parse("0.1.0"),
            [],
        )
        self.assertEqual(str(candidate), "0.1.0")

    def test_stale_and_invalid_candidates_are_rejected(self) -> None:
        checked_in = self.module.SemanticVersion.parse("1.3.0")
        with self.assertRaisesRegex(ValueError, "newer than latest"):
            self.module.release_version("1.2.0", checked_in, ["1.2.0"])
        with self.assertRaisesRegex(ValueError, "older than checked-in"):
            self.module.release_version("1.2.9", checked_in, [])
        with self.assertRaisesRegex(ValueError, "invalid version selector"):
            self.module.release_version("++-", checked_in, [])

    def test_write_changes_only_the_authoritative_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            makefile = Path(temporary_directory) / "Makefile"
            makefile.write_text(
                "DEVCONTAINER_VERSION ?= 0.1.0\n"
                "EXAMPLE = DEVCONTAINER_VERSION ?= 9.9.9\n",
                encoding="utf-8",
            )
            self.module.write_makefile_version(
                makefile,
                self.module.SemanticVersion.parse("0.2.0"),
            )
            self.assertEqual(
                makefile.read_text(encoding="utf-8"),
                "DEVCONTAINER_VERSION ?= 0.2.0\n"
                "EXAMPLE = DEVCONTAINER_VERSION ?= 9.9.9\n",
            )

    def test_cli_resolves_tags_from_a_git_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            (repository / "Makefile").write_text(
                "DEVCONTAINER_VERSION ?= 1.2.0\n",
                encoding="utf-8",
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "-c",
                    "user.name=Release Test",
                    "-c",
                    "user.email=release-test@example.invalid",
                    "commit",
                    "-q",
                    "--allow-empty",
                    "-m",
                    "test: seed repository",
                ],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repository), "tag", "1.2.4"],
                check=True,
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(TOOLS / "release-version.py"),
                    "--repository",
                    str(repository),
                    "--selector=--+",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.stdout.strip(), "1.2.5")


class CurrentFormulaVersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module(
            "current-formula-version.py",
            "current_formula_version",
        )

    def test_version_uses_run_number_and_lowercase_sha_prefix(self) -> None:
        self.assertEqual(
            self.module.current_formula_version(
                "418",
                "0123456789ABCDEF0123456789ABCDEF01234567",
            ),
            "current.418.0123456789ab",
        )

    def test_invalid_inputs_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.module.current_formula_version("0", "0" * 40)
        with self.assertRaises(ValueError):
            self.module.current_formula_version("418", "0123456789ab")
        with self.assertRaises(ValueError):
            self.module.current_formula_version("run-418", "0" * 40)


if __name__ == "__main__":
    unittest.main()
