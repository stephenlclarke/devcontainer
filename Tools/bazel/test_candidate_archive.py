"""Native candidate layout, determinism, provenance and failure tests."""

import hashlib
import json
import os
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest

from candidate_archive import CLI_FILES, PRODUCTS, package, sha256


ARCHIVE_TOOL = Path(__file__).parents[1] / "release/create-reproducible-archive.py"


class CandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.manifest = {"binaries": {}, "files": {}, "profile": "stock", "commit": "b" * 40, "epoch": "0"}
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
        lock = self.root / "runtime-lock.json"
        lock.write_text(json.dumps({"schemaVersion": 1, "node": {"version": "24.21.0"}, "cli": {"version": "0.88.0"}}))
        cli = {}
        for name in CLI_FILES:
            path = self.root / "cli" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"version":"0.88.0"}' if name == "package.json" else "fixture " + name)
            cli[name] = str(path)
        self.manifest["reference"] = {"node": self.manifest["binaries"]["devcontainer"],
                                      "nodeLicense": str(self.root / "LICENSE"), "lock": str(lock), "cli": cli}

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
        self.assertEqual(receipt["schemaVersion"], 2)
        self.assertEqual(receipt["referenceRuntime"]["nodeVersion"], "24.21.0")
        with tarfile.open(self.archive) as archive:
            for name in PRODUCTS:
                self.assertEqual(archive.getmember(f"devcontainer-1.2.3/bin/{name}").mode, 0o755)
            self.assertEqual(archive.getmember("devcontainer-1.2.3/share/devcontainer/LICENSE").mode, 0o644)
            plugin = "devcontainer-1.2.3/libexec/container/plugins/devcontainer/"
            self.assertTrue(archive.getmember(plugin + "config.toml").isfile())
            self.assertEqual(archive.extractfile(plugin + "bin/devcontainer").read(), Path(self.manifest["binaries"]["devcontainer"]).read_bytes())
            reference = "devcontainer-1.2.3/libexec/devcontainer/reference/"
            for name, expected in receipt["referenceRuntime"]["files"].items():
                member = archive.getmember(reference + name)
                self.assertTrue(member.isfile())
                self.assertEqual(member.mode, 0o755 if name == "node" else 0o644)
                self.assertEqual(hashlib.sha256(archive.extractfile(member).read()).hexdigest(), expected)
        self.assertFalse(list(self.root.glob("candidate-stage-*")))

    def test_source_or_profile_change_changes_candidate_identity(self) -> None:
        self.build()
        previous = sha256(self.archive)
        self.manifest["profile"] = "enhanced"
        self.manifest["commit"] = "a" * 40
        self.build()
        self.assertNotEqual(previous, sha256(self.archive))
        self.assertEqual(json.loads(self.receipt.read_text())["commit"], "a" * 40)

    def test_empty_product_licenses_preserve_other_product_notices(self) -> None:
        licensed = {"license_text": str(self.root / "LICENSE")}
        Path(self.manifest["licenses"]).write_text(json.dumps([
            {"top_level_target": "//:devcontainer", "licenses": [licensed]},
            {"top_level_target": "//:devcontainer-compose", "licenses": []},
            {"top_level_target": "//:devcontainer-engine", "licenses": [licensed]},
        ]))
        self.build()
        with tarfile.open(self.archive) as archive:
            notices = archive.extractfile("devcontainer-1.2.3/share/devcontainer/THIRD-PARTY-NOTICES.txt").read().decode()
        self.assertEqual(notices.count("fixture LICENSE"), 1)

    def test_rejects_missing_product_wrong_architecture_and_empty_licenses(self) -> None:
        binary = self.manifest["binaries"].pop("devcontainer")
        with self.assertRaisesRegex(ValueError, "four native products"):
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
        for key, value in [("commit", "main"), ("commit", "unspecified"), ("profile", "unknown"), ("epoch", "-1")]:
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

    def test_private_runtime_rejects_missing_files_versions_and_wrong_node(self) -> None:
        runtime = self.manifest["reference"]
        missing = runtime["cli"].pop("LICENSE.txt")
        with self.assertRaisesRegex(ValueError, "inventory"):
            self.build()
        runtime["cli"]["LICENSE.txt"] = missing
        Path(missing).write_text("")
        with self.assertRaisesRegex(ValueError, "empty"):
            self.build()
        Path(missing).write_text("license")
        package = Path(runtime["cli"]["package.json"])
        package.write_text('{"version":"0.1.0"}')
        with self.assertRaisesRegex(ValueError, "version differs"):
            self.build()
        package.write_text('{"version":"0.88.0"}')
        runtime["node"] = missing
        with self.assertRaisesRegex(ValueError, "Node.*arm64"):
            self.build()
