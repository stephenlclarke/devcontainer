# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Check direct and transitive SwiftPM source identity before Bazel acceptance."""

import json
from pathlib import Path
import shutil
import tempfile
import unittest

import source_graph


def dependency(identity: str, revision: str) -> dict:
    return {"sourceControl": [{"identity": identity,
            "location": {"remote": [{"urlString": source_graph.EXPECTED_URLS[identity]}]},
            "requirement": {"revision": [revision]}}]}


class SourceGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.container = self.root / "container"
        self.container.mkdir()
        (self.container / "Package.swift").write_text("// selected source fixture\n")
        for name in ("Package.swift", "Package.resolved", "Package.stock.resolved"):
            shutil.copyfile(source_graph.ROOT / name, self.root / name)
        rows = source_graph.pins(self.root / "Package.resolved")
        (self.container / "Package.resolved").write_text(json.dumps({"pins": [
            rows[name] for name in ("containerization", "container-engine-api")]}))
        self.revisions = {name: rows[name]["state"]["revision"] for name in source_graph.EXPECTED_URLS}
        self.direct = {"dependencies": [dependency(name, revision)
                                        for name, revision in self.revisions.items()]}
        self.transitive = {"dependencies": [dependency(name, self.revisions[name])
                                            for name in ("containerization", "container-engine-api")]}

    def verify(self) -> dict:
        return source_graph.verify_graph(self.root, self.container, self.direct, self.transitive)

    def test_exact_selected_graph(self) -> None:
        receipt = self.verify()
        self.assertEqual(receipt["profile"], "enhanced")
        self.assertEqual(receipt["source"], self.revisions)
        self.assertEqual(receipt["loadedContainerManifestSHA256"],
                         source_graph.digest(self.container / "Package.swift"))

    def test_stock_graph_uses_exact_apple_versions_and_its_own_lock(self) -> None:
        stock = source_graph.pins(self.root / "Package.stock.resolved")
        (self.container / "Package.resolved").write_text(json.dumps({"pins": [stock["containerization"]]}))
        direct = {"dependencies": [
            {"sourceControl": [{"identity": name,
                "location": {"remote": [{"urlString": source_graph.STOCK_URLS[name]}]},
                "requirement": {"exact": [stock[name]["state"]["version"]]}}]}
            for name in ("container", "containerization")
        ] + [dependency("container-engine-api", stock["container-engine-api"]["state"]["revision"])]}
        transitive = {"dependencies": [direct["dependencies"][1]]}
        receipt = source_graph.verify_graph(self.root, self.container, direct, transitive, "stock")
        self.assertEqual(receipt["profile"], "stock")
        self.assertEqual(receipt["source"]["container"], stock["container"]["state"]["revision"])
        direct["dependencies"][1]["sourceControl"][0]["requirement"]["exact"] = ["0.44.0"]
        with self.assertRaisesRegex(ValueError, "stock manifest"):
            source_graph.verify_graph(self.root, self.container, direct, transitive, "stock")

    def test_nested_lock_mismatch_rejects(self) -> None:
        rows = json.loads((self.container / "Package.resolved").read_text())
        rows["pins"][0]["state"]["revision"] = "f" * 40
        (self.container / "Package.resolved").write_text(json.dumps(rows))
        with self.assertRaisesRegex(ValueError, "loaded Container lock"):
            self.verify()

    def test_transitive_api_and_containerization_conflict_reject(self) -> None:
        for index in range(2):
            altered = json.loads(json.dumps(self.transitive))
            altered["dependencies"][index]["sourceControl"][0]["requirement"]["revision"] = ["f" * 40]
            with self.assertRaisesRegex(ValueError, "different transitive graph"):
                source_graph.verify_graph(self.root, self.container, self.direct, altered)

    def test_direct_manifest_and_lock_must_match(self) -> None:
        altered = json.loads(json.dumps(self.direct))
        altered["dependencies"][0]["sourceControl"][0]["requirement"]["revision"] = ["f" * 40]
        with self.assertRaisesRegex(ValueError, "manifest differs"):
            source_graph.verify_graph(self.root, self.container, altered, self.transitive)
        rows = json.loads((self.root / "Package.resolved").read_text())
        for row in rows["pins"]:
            if row["identity"] == "container-engine-api":
                row["state"]["revision"] = "f" * 40
        (self.root / "Package.resolved").write_text(json.dumps(rows))
        with self.assertRaisesRegex(ValueError, "loaded Container lock"):
            self.verify()

    def test_duplicate_or_unreviewed_dependency_rejects(self) -> None:
        duplicated = json.loads(json.dumps(self.transitive))
        duplicated["dependencies"].append(duplicated["dependencies"][0])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            source_graph.verify_graph(self.root, self.container, self.direct, duplicated)
        altered = json.loads(json.dumps(self.transitive))
        altered["dependencies"][0]["sourceControl"][0]["location"]["remote"][0]["urlString"] = "https://example.invalid/fake"
        with self.assertRaisesRegex(ValueError, "unreviewed"):
            source_graph.verify_graph(self.root, self.container, self.direct, altered)

    def test_duplicate_lock_or_different_stock_argument_parser_rejects(self) -> None:
        lock = json.loads((self.root / "Package.resolved").read_text())
        lock["pins"].append(lock["pins"][0])
        (self.root / "Package.resolved").write_text(json.dumps(lock))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.verify()
        shutil.copyfile(source_graph.ROOT / "Package.resolved", self.root / "Package.resolved")
        stock = json.loads((self.root / "Package.stock.resolved").read_text())
        for row in stock["pins"]:
            if row["identity"] == "swift-argument-parser":
                row["state"]["revision"] = "f" * 40
        (self.root / "Package.stock.resolved").write_text(json.dumps(stock))
        with self.assertRaisesRegex(ValueError, "disagree"):
            self.verify()

    def test_enhanced_only_swiftpm_patches_are_exported_and_applied(self) -> None:
        bazel_dir = source_graph.ROOT / "Tools/bazel"
        dependencies = (bazel_dir / "dependencies.bzl").read_text()
        build = (bazel_dir / "BUILD.bazel").read_text()
        zstd_patch = (bazel_dir / "zstd-public-module.patch").read_text()
        ext4_patch = (bazel_dir / "containerization-ext4-unaligned.patch").read_text()

        self.assertIn('profile == "enhanced" and pin["identity"] == "zstd"', dependencies)
        self.assertIn('profile == "enhanced" and pin["identity"] == "containerization"', dependencies)
        self.assertIn('patches = ["//Tools/bazel:zstd-public-module.patch"]', dependencies)
        self.assertIn('patches = ["//Tools/bazel:containerization-ext4-unaligned.patch"]', dependencies)
        self.assertIn('patch_args = ["-p1"]', dependencies)
        self.assertIn('"zstd-public-module.patch"', build)
        self.assertIn('"containerization-ext4-unaligned.patch"', build)
        self.assertIn('+            publicHeadersPath: "include",', zstd_patch)
        self.assertIn('+#include "../zstd.h"', zstd_patch)
        self.assertIn('+            return self.loadUnaligned(as: T.self)', ext4_patch)
        self.assertIn('+                ptr.loadUnaligned(as: T.self)', ext4_patch)


if __name__ == "__main__":
    unittest.main()
