# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Require actual linked and compiled inputs from every admitted binary group."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from artifacts import prove_layers
from artifacts.argument_parser import file_digest


class CompiledConsumerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.execroot = self.root / "execroot"
        self.execroot.mkdir()
        self.ap = self.root / "argument-parser"
        self.ap.mkdir()
        self.manifests = {}
        self.argument = {"files": {}}
        self.fragments = [{"id": 1, "label": "external"}]
        self.artifacts = []
        self.archive_ids = []
        self.compiled_ids = []
        self.next_id = 2
        for group in ("foundation", "containerization", "engine-api", "container-sdk"):
            repository = "swiftpkg_" + group.replace("-", "_")
            files = {}
            archive = f"binary/lib{group}.a"
            module = f"binary/{group}.swiftmodule"
            header = "include/shared.h"
            for relative in (archive, module, header):
                path = self.execroot / "external" / ("+dependencies+" + repository) / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((group + "/" + relative).encode())
                files[group + "/" + repository + "/" + relative] = file_digest(path)
                identity = self.artifact("external/+dependencies+" + repository + "/" + relative)
                (self.archive_ids if relative == archive else self.compiled_ids).append(identity)
            self.manifests[group] = {"packages": {group: {"repository": repository}},
                                     "files": files}
        for name in ("libArgumentParser.rspm.__impl.a", "libArgumentParserToolInfo.rspm.__impl.a"):
            (self.ap / name).write_bytes(name.encode())
            self.argument["files"][name] = file_digest(self.ap / name)
            path = self.execroot / "external/+dependencies+swiftpkg_swift_argument_parser" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
            self.archive_ids.append(self.artifact("external/+dependencies+swiftpkg_swift_argument_parser/" + name))
        for name in ("BUILD.bazel", "REPO.bazel"):
            (self.ap / name).write_text("# reviewed import\n")
        self.graph = {"targets": [{"id": i, "label": target}
                                  for i, target in enumerate(prove_layers.PRODUCTS, 1)] +
                                 [{"id": 5, "label": "@@+dependencies+swiftpkg_foundation//:Logging"}],
                      "pathFragments": self.fragments, "artifacts": self.artifacts,
                      "depSetOfFiles": [{"id": 1, "directArtifactIds": self.archive_ids},
                                        {"id": 2, "directArtifactIds": self.compiled_ids}],
                      "actions": [{"targetId": i, "mnemonic": "CppLink", "inputDepSetIds": [1]}
                                  for i in range(1, 5)] +
                                 [{"targetId": 1, "mnemonic": "SwiftCompile", "inputDepSetIds": [2]},
                                  {"targetId": 5, "mnemonic": "FileWrite", "inputDepSetIds": []}]}

    def artifact(self, relative: str) -> int:
        parent = None
        for part in relative.split("/"):
            existing = next((row for row in self.fragments
                             if row["label"] == part and row.get("parentId") == parent), None)
            if existing is None:
                existing = {"id": self.next_id, "label": part}
                if parent is not None:
                    existing["parentId"] = parent
                self.fragments.append(existing)
                self.next_id += 1
            parent = existing["id"]
        artifact = self.next_id
        self.next_id += 1
        self.artifacts.append({"id": artifact, "pathFragmentId": parent})
        return artifact

    def verify(self, graph: dict | None = None) -> dict:
        return prove_layers.verify_actions(graph or self.graph, self.manifests,
                                           self.argument, self.ap, self.execroot)

    def test_all_four_links_use_sealed_archives_modules_and_headers(self) -> None:
        result = self.verify()
        self.assertEqual(set(result["products"]), set(prove_layers.PRODUCTS))
        self.assertEqual({row["group"] for row in result["linkedArchives"].values()},
                         set(self.manifests) | {"argument-parser"})
        self.assertTrue(any(path.endswith(".swiftmodule") for path in result["matchedInputs"]))
        self.assertTrue(any(path.endswith(".h") for path in result["matchedInputs"]))

    def test_changed_actual_archive_or_module_bytes_reject(self) -> None:
        for name in ("binary/libfoundation.a", "binary/foundation.swiftmodule"):
            path = self.execroot / "external/+dependencies+swiftpkg_foundation" / name
            previous = path.read_bytes()
            path.write_bytes(b"changed actual consumed input")
            with self.assertRaisesRegex(ValueError, "different bytes"):
                self.verify()
            path.write_bytes(previous)

    def test_imported_source_action_and_unsealed_source_input_reject(self) -> None:
        graph = copy.deepcopy(self.graph)
        graph["actions"].append({"targetId": 5, "mnemonic": "SwiftCompile", "inputDepSetIds": []})
        with self.assertRaisesRegex(ValueError, "still compiles"):
            self.verify(graph)
        graph = copy.deepcopy(self.graph)
        source = self.artifact("external/+dependencies+swiftpkg_foundation/Source.swift")
        graph["pathFragments"] = self.fragments
        graph["artifacts"] = self.artifacts
        graph["depSetOfFiles"][1]["directArtifactIds"].append(source)
        with self.assertRaisesRegex(ValueError, "unsealed"):
            self.verify(graph)

    def test_missing_lower_archive_or_wrong_prefix_reject(self) -> None:
        graph = copy.deepcopy(self.graph)
        graph["depSetOfFiles"][0]["directArtifactIds"] = [
            identity for identity in self.archive_ids if identity not in self.archive_ids[:1]]
        with self.assertRaisesRegex(ValueError, "omit a released"):
            self.verify(graph)
        graph = copy.deepcopy(self.graph)
        marker = next(row for row in graph["pathFragments"]
                      if row["label"] == "+dependencies+swiftpkg_swift_argument_parser")
        marker["label"] = "+dependencies+forged_swift_argument_parser"
        with self.assertRaisesRegex(ValueError, "ArgumentParser archive"):
            self.verify(graph)

    def test_four_product_links_are_required(self) -> None:
        graph = copy.deepcopy(self.graph)
        graph["actions"] = [row for row in graph["actions"] if row["targetId"] != 4]
        with self.assertRaisesRegex(ValueError, "four-product closure"):
            self.verify(graph)


class InvocationAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.build = self.root / "build"
        self.query = self.root / "query"
        for directory, operation, target in ((self.build, "build", prove_layers.ROOT_TARGET),
                                             (self.query, "aquery", "deps(" + prove_layers.ROOT_TARGET + ")")):
            directory.mkdir()
            snapshot = {"commit": "a" * 40, "dirty": False, "schema": 1, "files": {},
                        "tooling": {}}
            for name in ("inputs-before.json", "inputs-after.json"):
                (directory / name).write_text(json.dumps(snapshot))
            (directory / "outcome.json").write_text(json.dumps({"bazel_exit_code": 0,
                                                                "validation_exit_code": 0}))
            events = [{"unstructuredCommandLine": {"args": [operation, "--config=release",
                        "--config=enhanced", "--config=prebuilt-container-sdk", target]}},
                      {"optionsParsed": {"explicitCmdLine": ["--config=enhanced", "--config=release",
                        "--config=prebuilt-container-sdk", "--define=DEVCONTAINER_COMMIT=" + "a" * 40],
                        "cmdLine": ["--compilation_mode=opt",
                        "--repo_env=DEVCONTAINER_RUNTIME_PROFILE=enhanced",
                        *["--repo_env=" + name + "=prebuilt" for name in
                          ("DEVCONTAINER_ARGUMENT_PARSER_LAYER", "DEVCONTAINER_FOUNDATION_LAYER",
                           "DEVCONTAINER_CONTAINERIZATION_LAYER", "DEVCONTAINER_ENGINE_API_LAYER",
                           "DEVCONTAINER_CONTAINER_SDK_LAYER")]]}},
                      {"finished": {"overallSuccess": True}}]
            if operation == "build":
                events.append({"id": {"targetCompleted": {"label": target}},
                               "completed": {"success": True,
                                             "outputGroup": [{"name": "default", "fileSets": [{"id": "1"}]}]}})
            else:
                # Bazel automatically adds --nobuild to normal aquery only.
                events[1]["optionsParsed"]["cmdLine"].append("--nobuild")
            (directory / "events.json").write_text("\n".join(map(json.dumps, events)) + "\n")

    def admitted(self, profile: str = "enhanced") -> dict:
        snapshot = json.loads((self.build / "inputs-before.json").read_text())
        with patch.object(prove_layers, "source_identity", return_value=snapshot), \
                patch.object(prove_layers, "tooling_identity", return_value={}):
            return prove_layers.admitted_invocations(self.build, self.query, profile, self.root)

    def test_same_successful_build_and_query_snapshot(self) -> None:
        result = self.admitted()
        self.assertEqual(result["source"], "a" * 40)
        self.assertFalse(result["dirty"])

    def test_changed_profile_failed_outcome_and_missing_root_reject(self) -> None:
        with self.assertRaisesRegex(ValueError, "contradictory"):
            self.admitted("stock")
        (self.build / "outcome.json").write_text('{"bazel_exit_code":0,"validation_exit_code":2}')
        with self.assertRaisesRegex(ValueError, "successful"):
            self.admitted()
        (self.build / "outcome.json").write_text('{"bazel_exit_code":0,"validation_exit_code":0}')
        events = [json.loads(line) for line in (self.build / "events.json").read_text().splitlines()]
        events[-1]["completed"]["success"] = False
        (self.build / "events.json").write_text("\n".join(map(json.dumps, events)) + "\n")
        with self.assertRaisesRegex(ValueError, "default executable"):
            self.admitted()

    def test_output_group_compiler_vector_and_current_snapshot_reject(self) -> None:
        events = [json.loads(line) for line in (self.build / "events.json").read_text().splitlines()]
        events[-1]["completed"]["outputGroup"] = []
        (self.build / "events.json").write_text("\n".join(map(json.dumps, events)) + "\n")
        with self.assertRaisesRegex(ValueError, "default executable"):
            self.admitted()
        events[-1]["completed"]["outputGroup"] = [{"name": "default", "fileSets": [{"id": "1"}]}]
        events[1]["optionsParsed"]["explicitCmdLine"].append("--config=stock")
        (self.build / "events.json").write_text("\n".join(map(json.dumps, events)) + "\n")
        with self.assertRaisesRegex(ValueError, "contradictory"):
            self.admitted()
        events[1]["optionsParsed"]["explicitCmdLine"].pop()
        events[1]["optionsParsed"]["explicitCmdLine"].append("--output_groups=empty")
        (self.build / "events.json").write_text("\n".join(map(json.dumps, events)) + "\n")
        with self.assertRaisesRegex(ValueError, "contradictory"):
            self.admitted()
        events[1]["optionsParsed"]["explicitCmdLine"].pop()
        (self.build / "events.json").write_text("\n".join(map(json.dumps, events)) + "\n")
        changed = {"commit": "b" * 40, "dirty": False, "schema": 1, "files": {}}
        with patch.object(prove_layers, "source_identity", return_value=changed), \
                patch.object(prove_layers, "tooling_identity", return_value={}):
            with self.assertRaisesRegex(ValueError, "different source"):
                prove_layers.admitted_invocations(self.build, self.query, "enhanced", self.root)

    def test_raw_graph_must_equal_leased_aquery_stdout(self) -> None:
        raw = self.root / "stdout.log"
        captured = self.query / "aquery.stdout.log"
        raw.write_text('{"actions":[]}\n')
        captured.write_bytes(raw.read_bytes())
        self.assertEqual(prove_layers.verified_aquery_stdout(self.query, raw), file_digest(raw))
        raw.write_text('{"actions":[{"forged":true}]}\n')
        with self.assertRaisesRegex(ValueError, "leased aquery"):
            prove_layers.verified_aquery_stdout(self.query, raw)

    def test_build_and_query_override_vectors_must_match(self) -> None:
        events = [json.loads(line) for line in (self.query / "events.json").read_text().splitlines()]
        events[1]["optionsParsed"]["explicitCmdLine"].append(
            "--override_repository=+dependencies+swiftpkg_container=/tmp/other")
        (self.query / "events.json").write_text("\n".join(map(json.dumps, events)) + "\n")
        with self.assertRaisesRegex(ValueError, "different source/tooling"):
            self.admitted()

    def test_effective_wrong_profile_or_compiler_mode_reject(self) -> None:
        events = [json.loads(line) for line in (self.query / "events.json").read_text().splitlines()]
        events[1]["optionsParsed"]["cmdLine"].append("--compilation_mode=dbg")
        (self.query / "events.json").write_text("\n".join(map(json.dumps, events)) + "\n")
        with self.assertRaisesRegex(ValueError, "ineffective"):
            self.admitted()
        events[1]["optionsParsed"]["cmdLine"].pop()
        events[1]["optionsParsed"]["cmdLine"].append(
            "--repo_env=DEVCONTAINER_RUNTIME_PROFILE=stock")
        (self.query / "events.json").write_text("\n".join(map(json.dumps, events)) + "\n")
        with self.assertRaisesRegex(ValueError, "contradictory"):
            self.admitted()

    def test_effective_minimum_os_must_match_build_and_query(self) -> None:
        events = [json.loads(line) for line in (self.query / "events.json").read_text().splitlines()]
        events[1]["optionsParsed"]["cmdLine"].append("--macos_minimum_os=14.0")
        (self.query / "events.json").write_text("\n".join(map(json.dumps, events)) + "\n")
        with self.assertRaisesRegex(ValueError, "different source/tooling"):
            self.admitted()

    def test_nobuild_is_only_bazels_implicit_aquery_option(self) -> None:
        self.admitted()
        events = [json.loads(line) for line in (self.build / "events.json").read_text().splitlines()]
        events[1]["optionsParsed"]["cmdLine"].append("--nobuild")
        (self.build / "events.json").write_text("\n".join(map(json.dumps, events)) + "\n")
        with self.assertRaisesRegex(ValueError, "ineffective"):
            self.admitted()
        events[1]["optionsParsed"]["cmdLine"].pop()
        events[1]["optionsParsed"]["explicitCmdLine"].append("--nobuild")
        (self.build / "events.json").write_text("\n".join(map(json.dumps, events)) + "\n")
        with self.assertRaisesRegex(ValueError, "contradictory"):
            self.admitted()


if __name__ == "__main__":
    unittest.main()
