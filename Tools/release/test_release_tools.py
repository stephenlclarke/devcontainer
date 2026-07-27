"""Tests for deterministic release metadata, SBOM, and Homebrew rendering."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
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
                "0123456789abcdef0123456789abcdef01234567",
                "--lane",
                "stable",
                "--architecture",
                "arm64",
                "--output",
                str(output),
            )

            value = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(value["version"], "1.2.3")
            self.assertEqual(
                value["commit"],
                "0123456789abcdef0123456789abcdef01234567",
            )
            self.assertEqual(value["source"], "stephenlclarke/devcontainer")
            self.assertEqual(value["lane"], "stable")
            self.assertEqual(value["buildType"], "release")
            self.assertEqual(value["architecture"], "arm64")
            self.assertEqual(value["containerDistribution"], "apple")
            self.assertEqual(value["provider"], "none")

    def test_archive_is_byte_reproducible_and_metadata_is_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "devcontainer-1.2.3"
            executable = source / "bin" / "devcontainer"
            metadata = source / "share" / "devcontainer" / "build-info.json"
            executable.parent.mkdir(parents=True)
            metadata.parent.mkdir(parents=True)
            executable.write_bytes(b"executable")
            metadata.write_text('{"version":"1.2.3"}\n', encoding="utf-8")
            executable.chmod(0o755)
            metadata.chmod(0o644)
            first = root / "first.tar.gz"
            second = root / "second.tar.gz"
            epoch = "1785100000"

            self.run_tool(
                "create-reproducible-archive.py",
                "--source",
                str(source),
                "--output",
                str(first),
                "--epoch",
                epoch,
            )
            os.utime(executable, (1_000_000_000, 1_000_000_000))
            os.utime(metadata, (1_100_000_000, 1_100_000_000))
            self.run_tool(
                "create-reproducible-archive.py",
                "--source",
                str(source),
                "--output",
                str(second),
                "--epoch",
                epoch,
            )

            self.assertEqual(first.read_bytes(), second.read_bytes())
            with tarfile.open(first, "r:gz") as archive:
                members = archive.getmembers()
                self.assertEqual(
                    [member.name for member in members],
                    sorted(member.name for member in members),
                )
                self.assertTrue(
                    all(member.mtime == int(epoch) for member in members)
                )
                self.assertTrue(all(member.uid == 0 for member in members))
                self.assertTrue(all(member.gid == 0 for member in members))
                executable_member = archive.getmember(
                    "devcontainer-1.2.3/bin/devcontainer"
                )
                self.assertEqual(executable_member.mode, 0o755)

    def test_archive_rejects_symbolic_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "devcontainer-1.2.3"
            source.mkdir()
            (source / "target").write_text("value", encoding="utf-8")
            (source / "link").symlink_to("target")

            arguments = (
                "create-reproducible-archive.py",
                "--source",
                str(source),
                "--output",
                str(root / "archive.tar.gz"),
                "--epoch",
                "1",
            )
            with self.assertRaises(subprocess.CalledProcessError):
                self.run_tool(*arguments)

    def test_sbom_contains_the_root_and_every_resolved_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "sbom.spdx.json"
            self.run_tool(
                "write-sbom.py",
                "--version",
                "1.2.3",
                "--commit",
                "0123456789abcdef0123456789abcdef01234567",
                "--source-date-epoch",
                "1785100000",
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
            self.assertEqual(
                document["creationInfo"]["created"],
                "2026-07-26T21:06:40Z",
            )
            self.assertEqual(
                document["packages"][0]["sourceInfo"],
                (
                    "Exact Git commit "
                    "0123456789abcdef0123456789abcdef01234567"
                ),
            )
            dependency_packages = [
                package
                for package in document["packages"]
                if package["name"] != "devcontainer"
            ]
            self.assertTrue(
                all(
                    package["licenseDeclared"] != "NOASSERTION"
                    and package["licenseConcluded"] != "NOASSERTION"
                    and package["sourceInfo"].startswith("Exact Git revision ")
                    for package in dependency_packages
                )
            )

    def test_third_party_notices_include_exact_reviewed_legal_texts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            resolved = root / "Package.resolved"
            licenses = root / "dependency-licenses.json"
            checkouts = root / "checkouts"
            checkout = checkouts / "fixture"
            output = root / "THIRD-PARTY-NOTICES.txt"
            checkout.mkdir(parents=True)
            resolved.write_text(
                json.dumps(
                    {
                        "pins": [
                            {
                                "identity": "fixture",
                                "location": "https://example.com/fixture.git",
                                "state": {
                                    "revision": "a" * 40,
                                    "version": "1.2.3",
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            licenses.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "licenses": {"fixture": "MIT"},
                    }
                ),
                encoding="utf-8",
            )
            (checkout / "LICENSE.txt").write_text(
                "MIT License\nPermission is hereby granted, free of charge\n",
                encoding="utf-8",
            )
            (checkout / "NOTICE.txt").write_text(
                "Fixture attribution\n",
                encoding="utf-8",
            )

            self.run_tool(
                "write-third-party-notices.py",
                "--resolved",
                str(resolved),
                "--license-manifest",
                str(licenses),
                "--checkouts",
                str(checkouts),
                "--output",
                str(output),
            )

            rendered = output.read_text(encoding="utf-8")
            self.assertIn("Dependency: fixture", rendered)
            self.assertIn("Version: 1.2.3", rendered)
            self.assertIn("Revision: " + "a" * 40, rendered)
            self.assertIn("Declared license: MIT", rendered)
            self.assertIn("----- LICENSE.txt -----", rendered)
            self.assertIn("----- NOTICE.txt -----", rendered)
            self.assertIn("Fixture attribution", rendered)

    def test_dependency_license_ledger_must_exactly_match_lockfile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            resolved = root / "Package.resolved"
            licenses = root / "dependency-licenses.json"
            output = root / "sbom.json"
            resolved.write_text(
                json.dumps(
                    {
                        "pins": [
                            {
                                "identity": "fixture",
                                "location": "https://example.com/fixture.git",
                                "state": {
                                    "revision": "a" * 40,
                                    "version": "1.2.3",
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            licenses.write_text(
                '{"schemaVersion":1,"licenses":{}}\n',
                encoding="utf-8",
            )

            arguments = (
                "write-sbom.py",
                "--version",
                "1.2.3",
                "--commit",
                "0123456789abcdef0123456789abcdef01234567",
                "--source-date-epoch",
                "1785100000",
                "--resolved",
                str(resolved),
                "--license-manifest",
                str(licenses),
                "--output",
                str(output),
            )
            with self.assertRaises(subprocess.CalledProcessError):
                self.run_tool(*arguments)

    def test_homebrew_formula_embeds_version_and_archive_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            archive = temporary_root / "devcontainer.tar.gz"
            template = temporary_root / "formula.rb.in"
            output = temporary_root / "devcontainer.rb"
            archive.write_bytes(b"release-archive")
            template.write_text(
                "class @FORMULA_CLASS@\n"
                'url "@URL@"\n'
                'version "@FORMULA_VERSION@"\n'
                'product "@PRODUCT_VERSION@"\n'
                'conflict "@CONFLICTS_WITH@"\n'
                'sha256 "@SHA256@"\n',
                encoding="utf-8",
            )

            self.run_tool(
                "render-homebrew-formula.py",
                "--product-version",
                "1.2.3",
                "--formula-version",
                "current.418.0123456789ab",
                "--formula-class",
                "DevcontainerCurrent",
                "--url",
                (
                    "https://github.com/stephenlclarke/devcontainer/releases/"
                    "download/current/"
                    "devcontainer-current-0123456789ab-arm64.tar.gz"
                ),
                "--conflicts-with",
                "devcontainer",
                "--archive",
                str(archive),
                "--template",
                str(template),
                "--output",
                str(output),
            )

            rendered = output.read_text(encoding="utf-8")
            self.assertIn("class DevcontainerCurrent", rendered)
            self.assertIn(
                (
                    'url "https://github.com/stephenlclarke/devcontainer/'
                    "releases/download/current/"
                    'devcontainer-current-0123456789ab-arm64.tar.gz"'
                ),
                rendered,
            )
            self.assertIn('version "current.418.0123456789ab"', rendered)
            self.assertIn('product "1.2.3"', rendered)
            self.assertIn('conflict "devcontainer"', rendered)
            self.assertIn(
                f'sha256 "{hashlib.sha256(archive.read_bytes()).hexdigest()}"',
                rendered,
            )

    def test_homebrew_renderer_rejects_cross_channel_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            archive = temporary_root / "devcontainer.tar.gz"
            template = temporary_root / "formula.rb.in"
            output = temporary_root / "devcontainer.rb"
            archive.write_bytes(b"release-archive")
            template.write_text("@FORMULA_CLASS@\n", encoding="utf-8")

            arguments = (
                "render-homebrew-formula.py",
                "--product-version",
                "1.2.3",
                "--formula-version",
                "current.418.0123456789ab",
                "--formula-class",
                "Devcontainer",
                "--url",
                (
                    "https://github.com/stephenlclarke/devcontainer/"
                    "releases/download/current/"
                    "devcontainer-current-0123456789ab-arm64.tar.gz"
                ),
                "--conflicts-with",
                "devcontainer-current",
                "--archive",
                str(archive),
                "--template",
                str(template),
                "--output",
                str(output),
            )
            with self.assertRaises(subprocess.CalledProcessError):
                self.run_tool(*arguments)


if __name__ == "__main__":
    unittest.main()
