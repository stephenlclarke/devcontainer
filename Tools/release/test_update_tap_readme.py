"""Tests for managed Homebrew tap documentation updates."""

from __future__ import annotations

import importlib.util
import sys
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


class TapReadmeUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module("update-tap-readme.py", "update_tap_readme")

    def test_section_is_appended_and_replaced_idempotently(self) -> None:
        original = "# Homebrew Tap\n\nExisting content.\n"
        section = "## Dev Containers For Apple container\n\nFirst version."
        updated = self.module.update_readme(original, section)
        self.assertEqual(updated.count(self.module.START_MARKER), 1)
        self.assertIn("First version.", updated)

        replacement = "## Dev Containers For Apple container\n\nReplacement."
        replaced = self.module.update_readme(updated, replacement)
        self.assertEqual(replaced.count(self.module.START_MARKER), 1)
        self.assertNotIn("First version.", replaced)
        self.assertEqual(
            self.module.update_readme(replaced, replacement),
            replaced,
        )

    def test_source_extraction_omits_design_only_preamble(self) -> None:
        source = (
            "# Homebrew Tap Documentation\n\n"
            "> Maintained source.\n\n"
            "## Dev Containers For Apple container\n\n"
            "Install details.\n"
        )
        section = self.module.documentation_section(source)
        self.assertTrue(section.startswith("## Dev Containers"))
        self.assertNotIn("Maintained source", section)

    def test_malformed_markers_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "malformed"):
            self.module.update_readme(
                "# Tap\n\n<!-- devcontainer-docs:start -->\n",
                "## Dev Containers For Apple container",
            )


if __name__ == "__main__":
    unittest.main()
