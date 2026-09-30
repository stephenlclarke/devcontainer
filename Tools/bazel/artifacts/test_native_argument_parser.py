# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Fail-closed importer and production-option boundaries without Bazel or network."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from artifacts import import_argument_parser as native
from artifacts import prove_argument_parser as proof


class NativeArgumentParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.selected = self.root / "selected"
        self.selected.mkdir()
        self.manifest = {"files": {}, "toolchain": {"generatedBuildSHA256": native.sha256(native.BUILD)}}
        for name in native.FILES:
            path = self.selected / name
            path.write_bytes(name.encode())
            path.chmod(0o644)
            self.manifest["files"][name] = native.sha256(path)
        for name, data in (("BUILD.bazel", native.BUILD.read_bytes()),
                           ("REPO.bazel", native.REPO.read_bytes()),
                           ("layer.json", json.dumps(self.manifest).encode()),
                           ("import-receipt.json", b"{}")):
            path = self.selected / name
            path.write_bytes(data)
            path.chmod(0o644)

    def test_exact_inventory_and_mode(self) -> None:
        native.inventory(self.selected, self.manifest)
        (self.selected / "unexpected.swift").write_text("source fallback")
        with self.assertRaisesRegex(ValueError, "inventory"):
            native.inventory(self.selected, self.manifest)
        (self.selected / "unexpected.swift").unlink()
        (self.selected / native.FILES[0]).chmod(0o755)
        with self.assertRaisesRegex(ValueError, "mode"):
            native.inventory(self.selected, self.manifest)

    def test_canonical_loaded_repository_must_resolve_to_selected(self) -> None:
        output = self.root / "output"
        (output / "external").mkdir(parents=True)
        loaded = output / "external" / native.REPOSITORY
        loaded.symlink_to(self.selected, target_is_directory=True)
        native.verify_origin(output, self.selected)
        loaded.unlink()
        forged = self.root / "forged"
        forged.mkdir()
        (forged / "BUILD.bazel").write_bytes(native.BUILD.read_bytes())
        loaded.symlink_to(forged, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "canonical"):
            native.verify_origin(output, self.selected)

    def test_wrong_build_and_module_bytes_reject(self) -> None:
        (self.selected / "BUILD.bazel").write_text("# source fallback\n")
        with self.assertRaisesRegex(ValueError, "BUILD"):
            native.inventory(self.selected, self.manifest)
        (self.selected / "BUILD.bazel").write_bytes(native.BUILD.read_bytes())
        (self.selected / native.FILES[0]).write_text("changed")
        with self.assertRaisesRegex(ValueError, "bytes"):
            native.inventory(self.selected, self.manifest)

    def test_both_profile_pins_must_match_published_source(self) -> None:
        revision = "a" * 40
        row = {"identity": "swift-argument-parser", "kind": "remoteSourceControl",
               "state": {"revision": revision}}
        for filename in ("Package.resolved", "Package.stock.resolved"):
            (self.root / filename).write_text(json.dumps({"pins": [row]}))
        native.verify_profile_pins(self.root, revision)
        (self.root / "Package.stock.resolved").write_text(json.dumps({"pins": [
            {**row, "state": {"revision": "b" * 40}}]}))
        with self.assertRaisesRegex(ValueError, "Package.stock.resolved"):
            native.verify_profile_pins(self.root, revision)

    def test_prebuilt_configuration_requires_release_and_rejects_overrides(self) -> None:
        script = native.ROOT / "Tools/bazel/run.sh"
        for args, success in [
            ("--config=release --config=prebuilt-argument-parser", True),
            ("--config=prebuilt-argument-parser", False),
            ("--config=release --config=prebuilt-argument-parser --copt=-DOTHER", False),
        ]:
            result = subprocess.run(["/bin/bash", "-c", 'source "$1"; shift; prebuilt_argument_parser "$@"',
                                     "_", str(script), *args.split()], capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, success, args)

    def test_configured_link_requires_both_exact_archives_and_no_source_actions(self) -> None:
        fragments = [{"id": 1, "label": "external"},
                     {"id": 2, "label": native.REPOSITORY, "parentId": 1}]
        artifacts = []
        files = {}
        for offset, name in enumerate(proof.ARCHIVES, 3):
            fragments.append({"id": offset, "label": name, "parentId": 2})
            artifacts.append({"id": offset, "pathFragmentId": offset})
            (self.selected / name).write_bytes(name.encode())
            files[name] = native.sha256(self.selected / name)
        graph = {"targets": [{"id": 1, "label": proof.TARGET},
                             {"id": 2, "label": "@@" + native.REPOSITORY + "//:ArgumentParser"}],
                 "pathFragments": fragments, "artifacts": artifacts,
                 "depSetOfFiles": [{"id": 1, "directArtifactIds": [3, 4]}],
                 "actions": [{"targetId": 1, "mnemonic": "CppLink", "inputDepSetIds": [1]}]}
        self.assertEqual(len(proof.verify_actions(graph, self.selected, {"files": files})["archiveInputs"]), 2)
        graph["pathFragments"].append({"id": 5, "label": "foreign", "parentId": 1})
        graph["pathFragments"][1]["parentId"] = 5
        with self.assertRaisesRegex(ValueError, "sealed"):
            proof.verify_actions(graph, self.selected, {"files": files})
        graph["pathFragments"][1]["parentId"] = 1
        graph["actions"].append({"targetId": 2, "mnemonic": "SwiftCompile", "inputDepSetIds": []})
        with self.assertRaisesRegex(ValueError, "source action"):
            proof.verify_actions(graph, self.selected, {"files": files})
        graph["actions"].pop()
        graph["depSetOfFiles"][0]["directArtifactIds"] = [3]
        with self.assertRaisesRegex(ValueError, "sealed"):
            proof.verify_actions(graph, self.selected, {"files": files})


if __name__ == "__main__":
    unittest.main()
