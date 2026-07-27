"""Tests for deterministic release metadata, SBOM, and Homebrew rendering."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPOSITORY_ROOT / "Tools" / "release"


class ReleaseToolTests(unittest.TestCase):
    def run_tool(self, name: str, *arguments: str, cwd: Path | None = None) -> None:
        subprocess.run(
            [sys.executable, str(TOOLS / name), *arguments],
            cwd=cwd or REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_build_info_is_deterministic_and_identifies_the_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "build-info.json"
            self.run_tool(
                "write-build-info.py",
                "--version",
                "1.2.3",
                "--commit",
                "abc123",
                "--output",
                str(output),
            )

            value = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(value["version"], "1.2.3")
            self.assertEqual(value["commit"], "abc123")
            self.assertEqual(value["source"], "stephenlclarke/devcontainer")
            self.assertEqual(value["lane"], "stable")

    def test_sbom_contains_the_root_and_every_resolved_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "sbom.spdx.json"
            self.run_tool(
                "write-sbom.py",
                "--version",
                "1.2.3",
                "--output",
                str(output),
            )

            document = json.loads(output.read_text(encoding="utf-8"))
            resolved = json.loads(
                (REPOSITORY_ROOT / "Package.resolved").read_text(encoding="utf-8")
            )
            pins = resolved.get("pins", resolved.get("object", {}).get("pins", []))
            package_names = {package["name"] for package in document["packages"]}
            self.assertIn("devcontainer", package_names)
            self.assertTrue(
                {pin["identity"] for pin in pins}.issubset(package_names)
            )
            self.assertEqual(len(document["relationships"]), len(pins))

    def test_homebrew_formula_embeds_version_and_archive_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            archive = temporary_root / "devcontainer.tar.gz"
            template = temporary_root / "formula.rb.in"
            output = temporary_root / "devcontainer.rb"
            archive.write_bytes(b"release-archive")
            template.write_text(
                'version = "@VERSION@"\nsha256 = "@SHA256@"\n',
                encoding="utf-8",
            )

            self.run_tool(
                "render-homebrew-formula.py",
                "--version",
                "1.2.3",
                "--archive",
                str(archive),
                "--template",
                str(template),
                "--output",
                str(output),
            )

            rendered = output.read_text(encoding="utf-8")
            self.assertIn('version = "1.2.3"', rendered)
            self.assertIn(
                f'sha256 = "{hashlib.sha256(archive.read_bytes()).hexdigest()}"',
                rendered,
            )


if __name__ == "__main__":
    unittest.main()
