"""Regression tests for bounded repository cleanup."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


TOOLS = Path(__file__).resolve().parent


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("safe_clean", TOOLS / "safe-clean.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load safe-clean.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SafeCleanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_removes_only_declared_generated_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            retained = root / "Sources" / "Example.swift"
            retained.parent.mkdir()
            retained.write_text("struct Example {}\n", encoding="utf-8")

            for relative in self.module.GENERATED_PATHS:
                target = root / relative
                if target.suffix:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("generated\n", encoding="utf-8")
                else:
                    target.mkdir(parents=True, exist_ok=True)
                    (target / "generated").write_text("generated\n", encoding="utf-8")

            cache = root / "Tools" / "example" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "example.pyc").write_bytes(b"generated")

            self.assertEqual(self.module.main(root), 0)

            for relative in self.module.GENERATED_PATHS:
                self.assertFalse((root / relative).exists())
            self.assertFalse(cache.exists())
            self.assertEqual(
                retained.read_text(encoding="utf-8"),
                "struct Example {}\n",
            )

    def test_refuses_symlink_that_resolves_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "repository"
            outside = Path(temporary_directory) / "outside"
            root.mkdir()
            outside.mkdir()
            (root / ".build").symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(RuntimeError, "refusing unsafe clean target"):
                self.module.main(root)
            self.assertTrue(outside.is_dir())


if __name__ == "__main__":
    unittest.main()
