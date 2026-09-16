"""Native candidate layout, determinism, provenance and failure tests."""

import json
import os
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest

from candidate_archive import PRODUCTS, package, sha256


ARCHIVE_TOOL = Path(__file__).parents[1] / "release/create-reproducible-archive.py"


class CandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.manifest = {"binaries": {}, "files": {}, "profile": "stock", "commit": "unspecified", "epoch": "0"}
        for name in PRODUCTS:
            path = self.root / name
            path.write_bytes(struct.pack("<II", 0xFEEDFACF, 0x0100000C) + b"fixture-not-executable")
            self.manifest["binaries"][name] = str(path)
        for name in ["LICENSE", "NOTICE.md", "devcontainer-plugin-config.toml"]:
            path = self.root / name
            path.write_text("fixture " + name)
            self.manifest["files"][name] = str(path)
        for key, content in [("makefile", "DEVCONTAINER_VERSION ?= 1.2.3\n"), ("resolved", '{"pins":[]}'),
                             ("licenses", json.dumps([{"licenses": [{"license_text": str(self.root / "LICENSE")}]}]))]:
            path = self.root / key
            path.write_text(content)
            self.manifest[key] = str(path)
        self.archive = self.root / "candidate.tar.gz"
        self.receipt = self.root / "candidate.json"

    def build(self) -> None:
        package(self.manifest, self.archive, self.receipt, ARCHIVE_TOOL)

    def test_layout_receipt_and_repeated_bytes_are_deterministic(self) -> None:
        self.build()
        first = sha256(self.archive)
        self.build()
        self.assertEqual(first, sha256(self.archive))
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt["archiveSHA256"], first)
        self.assertFalse(receipt["distributionReady"])
        self.assertEqual(receipt["runtimeProfile"], "stock")
        self.assertEqual(set(receipt["products"]), PRODUCTS)
        with tarfile.open(self.archive) as archive:
            for name in PRODUCTS:
                self.assertEqual(archive.getmember(f"devcontainer-1.2.3/bin/{name}").mode, 0o755)
            self.assertEqual(archive.getmember("devcontainer-1.2.3/share/devcontainer/LICENSE").mode, 0o644)
            plugin = "devcontainer-1.2.3/libexec/container/plugins/devcontainer/"
            self.assertTrue(archive.getmember(plugin + "config.toml").isfile())
            self.assertEqual(archive.extractfile(plugin + "bin/devcontainer").read(), Path(self.manifest["binaries"]["devcontainer"]).read_bytes())
        self.assertFalse(list(self.root.glob("candidate-stage-*")))

    def test_source_or_profile_change_changes_candidate_identity(self) -> None:
        self.build()
        previous = sha256(self.archive)
        self.manifest["profile"] = "enhanced"
        self.manifest["commit"] = "a" * 40
        self.build()
        self.assertNotEqual(previous, sha256(self.archive))
        self.assertEqual(json.loads(self.receipt.read_text())["commit"], "a" * 40)

    def test_rejects_missing_product_wrong_architecture_and_empty_licenses(self) -> None:
        binary = self.manifest["binaries"].pop("devcontainer")
        with self.assertRaisesRegex(ValueError, "three native products"):
            self.build()
        self.manifest["binaries"]["devcontainer"] = binary
        original = Path(binary).read_bytes()
        Path(binary).write_text("not a native binary")
        with self.assertRaisesRegex(ValueError, "arm64 Mach-O"):
            self.build()
        Path(binary).write_bytes(original)
        Path(self.manifest["licenses"]).write_text("[]")
        with self.assertRaisesRegex(ValueError, "license evidence"):
            self.build()
        self.assertFalse(list(self.root.glob("candidate-stage-*")))

    def test_rejects_bad_version_commit_profile_epoch_and_resource_paths(self) -> None:
        for key, value in [("commit", "main"), ("profile", "unknown"), ("epoch", "-1")]:
            with self.subTest(key=key):
                before = self.manifest[key]
                self.manifest[key] = value
                with self.assertRaises(ValueError):
                    self.build()
                self.manifest[key] = before
        self.manifest["files"]["../escape"] = str(self.root / "LICENSE")
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            self.build()
        del self.manifest["files"]["../escape"]
        Path(self.manifest["makefile"]).write_text("not a version")
        with self.assertRaisesRegex(ValueError, "semantic version"):
            self.build()
