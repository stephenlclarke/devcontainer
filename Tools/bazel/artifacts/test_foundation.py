#!/usr/bin/env python3
##===----------------------------------------------------------------------===##
## Copyright © 2026 container-compose project authors.
##
## Licensed under the Apache License, Version 2.0 (the "License");
## you may not use this file except in compliance with the License.
## You may obtain a copy of the License at
##
##   https://www.apache.org/licenses/LICENSE-2.0
##
## Unless required by applicable law or agreed to in writing, software
## distributed under the License is distributed on an "AS IS" BASIS,
## WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
## See the License for the specific language governing permissions and
## limitations under the License.
##===----------------------------------------------------------------------===##

"""Focused sealing and no-source-fallback checks for the foundation layer."""

import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch

from artifacts import foundation
from artifacts.foundation import (archive_bytes, compiled_c_header_sources, digest, group_pins,
                        header_only_c_targets, inspect, package_overlay,
                        recipe_identity, transformed_build)


BUILD = '''load("@build_bazel_rules_swift//swift:swift.bzl", "swift_library", "swift_library_group")
load("@rules_cc//cc:defs.bzl", "cc_library")
swift_library(
    name = "Logging.rspm.__impl",
    module_name = "Logging",
    srcs = ["Sources/Logging.swift"],
)
cc_library(
    name = "CThing.rspm_c",
    srcs = ["Sources/thing.c", "Sources/thing.h"],
    textual_hdrs = ["Sources/Shims.c"],
)
cc_library(
    name = "HeaderOnly",
    hdrs = ["Sources/thing.h"],
)
cc_library(
    name = "InactiveC",
    srcs = ["Sources/thing.c"],
)
'''

LEGACY_LOCK_FIXTURES = {
    "argument-parser.lock.json": "06c5ef150dc92876484ff849980e69ad967d4fb0b66c4024f9b7e4b7832b68ce",
    "container-sdk-enhanced.lock.json": "c2784522152856079bd0d8099a8af151ef1e562f4d4902150346efa95079ff47",
    "container-sdk-stock.lock.json": "4d0470004fd40bb8d440109e792e993563840c0889a3a622dc045b88f99ffaeb",
    "containerization-enhanced.lock.json": "75010ab0b5e69f67e3d5aa4aa9e152bc7b0a3f26477af4fc501d8afc024da57f",
    "containerization-stock.lock.json": "0918dab08b02c8b9aa5c13db8c2c165c245fee9ce579a7de4ac87b1e68c1600f",
    "engine-api-enhanced.lock.json": "007dcded692ff84c64b88cfef2211e62e3bf321985689908440b332b29edfcee",
    "engine-api-stock.lock.json": "a5cb5830748d5d2f858597b6c1ffce60eefbb45482eef69ad35369b705ab8d3c",
    "foundation-enhanced.lock.json": "5383f27b335a66de065102515bf7ff3644b8ad784f409c15a8d3b5be6c908956",
    "foundation-stock.lock.json": "986d309ad2ced14a5656a6d9291e174a7e5afc12ea8d7db649fed809eebf6495",
}


class FoundationTests(unittest.TestCase):
    def _layer_fixture(self, directory: str, source_root: Path | None = None) -> Path:
        """Copy package inputs and immutable archived locks used by the verifier."""
        source_root = source_root or Path(__file__).resolve().parents[3]
        source_root = Path(source_root)
        lock_fixture_root = Path(__file__).resolve().parent / "fixtures/legacy-00a6549"
        root = Path(directory)
        files = (
            "Package.swift", "Package.resolved", "Package.stock.resolved", ".bazelrc",
            ".bazelversion", "MODULE.bazel", "MODULE.bazel.lock", "BUILD.bazel",
            "Tools/bazel/run.sh", "Tools/bazel/input_identity.py", "Tools/bazel/source_graph.py",
            "Tools/bazel/dependencies.bzl", "Tools/bazel/rules-swift-sandbox-output.patch",
            "Tools/bazel/rules-license-empty-provider.patch",
            "Tools/bazel/artifacts/foundation.py", "Tools/bazel/artifacts/foundation_import.bzl",
            "Tools/bazel/artifacts/compiled_outputs.bzl", "Tools/bazel/artifacts/BUILD.bazel",
        )
        for relative in files:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_root / relative, destination)
        for name, expected_sha in LEGACY_LOCK_FIXTURES.items():
            source = lock_fixture_root / name
            self.assertEqual(digest(source.read_bytes()), expected_sha, name)
            destination = root / "Tools/bazel/artifacts" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

        old_pin = "4bf4750989138800d65abbbe7f9ff8d7b286bd16"
        manifest_path = root / "Package.swift"
        manifest = manifest_path.read_bytes()
        resolved_path = root / "Package.resolved"
        resolved = resolved_path.read_bytes()
        baseline_manifest_sha = "f77f14603ace0bed2ec3cd60e1a0b7a2d043a33cd181c42a4f22d398c569100a"
        baseline_lock_sha = "f9ba00f315a3f74a5dd5440c76dd092e98a4ac41bda1339227e9b4967bb9a562"
        selected_pin = foundation.source_pins(root, "enhanced")["container"]
        self.assertEqual(json.loads(resolved)["originHash"], foundation.digest(manifest))
        manifest_pattern = re.compile(rb'(enhancedRevision:\s*")' + selected_pin.encode() + rb'(")')
        manifest, count = manifest_pattern.subn(
            lambda match: match.group(1) + old_pin.encode() + match.group(2), manifest)
        self.assertEqual(count, 1)
        self.assertEqual(foundation.digest(manifest), baseline_manifest_sha)
        origin_pattern = re.compile(rb'("originHash"\s*:\s*")' + foundation.digest(
            manifest_path.read_bytes()).encode() + rb'(")')
        resolved, count = origin_pattern.subn(
            lambda match: match.group(1) + baseline_manifest_sha.encode() + match.group(2), resolved)
        self.assertEqual(count, 1)
        revision_pattern = re.compile(rb'("revision"\s*:\s*")' + selected_pin.encode() + rb'(")')
        resolved, count = revision_pattern.subn(
            lambda match: match.group(1) + old_pin.encode() + match.group(2), resolved)
        self.assertEqual(count, 1)
        self.assertEqual(foundation.digest(resolved), baseline_lock_sha)
        manifest_path.write_bytes(manifest)
        resolved_path.write_bytes(resolved)
        return root

    def _q_pin_fixture(self, directory: str) -> Path:
        """Seed a synthetic Q revision from the immutable archived 00a6549 inputs."""
        root = self._layer_fixture(directory)
        old_pin = "4bf4750989138800d65abbbe7f9ff8d7b286bd16"
        new_pin = "f" * 40
        manifest_path = root / "Package.swift"
        manifest = manifest_path.read_bytes()
        self.assertEqual(manifest.count(old_pin.encode()), 1)
        manifest = manifest.replace(old_pin.encode(), new_pin.encode())
        manifest_path.write_bytes(manifest)
        resolved_path = root / "Package.resolved"
        resolved = resolved_path.read_bytes()
        self.assertEqual(resolved.count(old_pin.encode()), 1)
        resolved = resolved.replace(old_pin.encode(), new_pin.encode())
        baseline_origin = json.loads(resolved)["originHash"]
        self.assertEqual(baseline_origin,
                         "f77f14603ace0bed2ec3cd60e1a0b7a2d043a33cd181c42a4f22d398c569100a")
        resolved, count = re.subn(
            rb'("originHash"\s*:\s*")' + baseline_origin.encode() + rb'(")',
            lambda match: match.group(1) + foundation.digest(manifest).encode() + match.group(2),
            resolved)
        self.assertEqual(count, 1)
        resolved_path.write_bytes(resolved)
        return root

    def _verify_archived_consumer(self, root: Path, profile: str, group: str) -> None:
        lock_path = root / f"Tools/bazel/artifacts/{group}-{profile}.lock.json"
        lock = json.loads(lock_path.read_text())
        with tempfile.TemporaryDirectory() as tool_directory:
            tools = Path(tool_directory)
            swiftc = tools / "swiftc"
            swiftc.write_text("fixture compiler")
            sdk = tools / "MacOSX.sdk"
            sdk.mkdir()
            (sdk / "SDKSettings.json").write_text("fixture SDK")

            def command(*arguments: str, **_kwargs: object) -> str:
                if arguments[:2] == ("/usr/bin/xcrun", "-f"):
                    return str(swiftc)
                if arguments[:2] == ("/usr/bin/xcrun", "--sdk") and arguments[-1] == "--show-sdk-path":
                    return str(sdk)
                if arguments[:2] == ("/usr/bin/xcrun", "--sdk") and arguments[-1] == "--show-sdk-version":
                    return lock["toolchain"]["sdkVersion"]
                if arguments[:2] == ("/usr/bin/xcodebuild", "-version"):
                    return lock["toolchain"]["xcodeVersion"]
                raise AssertionError(f"unexpected toolchain query: {arguments}")

            real_file_digest = foundation.file_digest

            def file_digest(path: Path) -> str:
                if path == swiftc:
                    return lock["toolchain"]["swiftcSHA256"]
                if path == sdk / "SDKSettings.json":
                    return lock["toolchain"]["sdkSettingsSHA256"]
                return real_file_digest(path)

            with (patch.object(foundation.platform, "system", return_value="Darwin"),
                  patch.object(foundation.platform, "machine", return_value="arm64"),
                  patch.object(foundation, "command", side_effect=command),
                  patch.object(foundation, "file_digest", side_effect=file_digest)):
                foundation.verify_consumer(lock_path, root, None, {}, profile, group)

    def test_exact_enhanced_container_pin_delta_reuses_only_unchanged_lower_groups(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._q_pin_fixture(directory)
            for group in ("foundation", "containerization", "engine-api"):
                lock = json.loads((root / f"Tools/bazel/artifacts/{group}-enhanced.lock.json").read_text())
                with self.subTest(group=group):
                    self._verify_archived_consumer(root, "enhanced", group)
            sdk = json.loads((root / "Tools/bazel/artifacts/container-sdk-enhanced.lock.json").read_text())
            self.assertFalse(foundation._legacy_recipe_compatible(root, sdk, "enhanced", "container-sdk"))
            with self.assertRaisesRegex(ValueError, "source pins or lower layer"):
                foundation.verify_consumer(root / "Tools/bazel/artifacts/container-sdk-enhanced.lock.json",
                                           root, None, {}, "enhanced", "container-sdk")

    def test_all_archived_groups_accept_producer_only_delta_at_exact_baseline_pins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._layer_fixture(directory)
            for profile in ("stock", "enhanced"):
                for group in ("foundation", "containerization", "engine-api", "container-sdk"):
                    lock = json.loads((root / f"Tools/bazel/artifacts/{group}-{profile}.lock.json").read_text())
                    with self.subTest(profile=profile, group=group):
                        self._verify_archived_consumer(root, profile, group)

    def test_all_stock_archived_groups_accept_only_the_exact_enhanced_q_pin_delta(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._q_pin_fixture(directory)
            for group in ("foundation", "containerization", "engine-api", "container-sdk"):
                lock = json.loads((root / f"Tools/bazel/artifacts/{group}-stock.lock.json").read_text())
                with self.subTest(group=group):
                        self._verify_archived_consumer(root, "stock", group)

    def test_new_q_pin_and_replaced_sdk_lock_reconstruct_original_archive_locks(self) -> None:
        """A live Q/SDK lock advance must not rewrite the archived lock fixture."""
        source_root = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "current-source"
            source_files = (
                "Package.swift", "Package.resolved", "Package.stock.resolved", ".bazelrc",
                ".bazelversion", "MODULE.bazel", "MODULE.bazel.lock", "BUILD.bazel",
                "Tools/bazel/run.sh", "Tools/bazel/input_identity.py", "Tools/bazel/source_graph.py",
                "Tools/bazel/dependencies.bzl", "Tools/bazel/rules-swift-sandbox-output.patch",
                "Tools/bazel/rules-license-empty-provider.patch",
                "Tools/bazel/artifacts/foundation.py", "Tools/bazel/artifacts/foundation_import.bzl",
                "Tools/bazel/artifacts/compiled_outputs.bzl", "Tools/bazel/artifacts/BUILD.bazel",
                "Tools/bazel/artifacts/container-sdk-enhanced.lock.json",
            )
            for relative in source_files:
                destination = current / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_root / relative, destination)

            manifest_path = current / "Package.swift"
            manifest = manifest_path.read_bytes()
            old_pin = foundation.source_pins(current, "enhanced")["container"]
            self.assertEqual(json.loads((current / "Package.resolved").read_text())["originHash"],
                             foundation.digest(manifest))
            new_q = "e" * 40
            self.assertNotEqual(new_q, old_pin)
            self.assertEqual(manifest.count(old_pin.encode()), 1)
            manifest = manifest.replace(old_pin.encode(), new_q.encode())
            manifest_path.write_bytes(manifest)
            resolved_path = current / "Package.resolved"
            resolved = resolved_path.read_bytes()
            self.assertEqual(resolved.count(old_pin.encode()), 1)
            resolved = resolved.replace(old_pin.encode(), new_q.encode())
            resolved, count = re.subn(
                rb'("originHash"\s*:\s*")[0-9a-f]{64}("\s*,)',
                lambda match: match.group(1) + foundation.digest(manifest).encode() + match.group(2),
                resolved,
            )
            self.assertEqual(count, 1)
            resolved_path.write_bytes(resolved)
            self.assertEqual(json.loads(resolved)["originHash"], foundation.digest(manifest))

            replaced_sdk_lock = current / "Tools/bazel/artifacts/container-sdk-enhanced.lock.json"
            candidate_sdk = json.loads(replaced_sdk_lock.read_text())
            candidate_sdk["targetCommit"] = new_q
            candidate_sdk.setdefault("sourcePins", {})["container"] = new_q
            candidate_sdk["archiveSHA256"] = "0" * 64
            replaced_sdk_lock.write_text(json.dumps(candidate_sdk, sort_keys=True) + "\n")
            self.assertEqual(foundation.source_pins(current, "enhanced")["container"], new_q)

            fixture = self._layer_fixture(str(Path(directory) / "fixture"), current)
            for name, expected_sha in LEGACY_LOCK_FIXTURES.items():
                with self.subTest(lock=name):
                    self.assertEqual(digest((fixture / "Tools/bazel/artifacts" / name).read_bytes()),
                                     expected_sha)
            archived_sdk = json.loads((fixture / "Tools/bazel/artifacts/container-sdk-enhanced.lock.json").read_text())
            archived_pin = "4bf4750989138800d65abbbe7f9ff8d7b286bd16"
            self.assertEqual(archived_sdk["sourcePins"]["container"], archived_pin)
            self.assertNotEqual(archived_sdk, candidate_sdk)
            self.assertEqual(foundation.digest((fixture / "Package.swift").read_bytes()),
                             "f77f14603ace0bed2ec3cd60e1a0b7a2d043a33cd181c42a4f22d398c569100a")
            self.assertEqual(foundation.digest((fixture / "Package.resolved").read_bytes()),
                             "f9ba00f315a3f74a5dd5440c76dd092e98a4ac41bda1339227e9b4967bb9a562")

    def test_stock_source_graph_reuses_only_its_archived_exact_stock_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._q_pin_fixture(directory)
            lock = json.loads((root / "Tools/bazel/artifacts/container-sdk-stock.lock.json").read_text())
            self.assertTrue(foundation._legacy_recipe_compatible(root, lock, "stock", "container-sdk"))
            foundation.verify_source_graph(root, "stock", lock["sourceGraph"])
            changed = json.loads(json.dumps(lock["sourceGraph"]))
            changed["receipt"]["loadedContainerLockSHA256"] = "0" * 64
            changed["receiptSHA256"] = foundation.digest(
                (json.dumps(changed["receipt"], sort_keys=True) + "\n").encode())
            with self.assertRaisesRegex(ValueError, "source graph"):
                foundation.verify_source_graph(root, "stock", changed)

            changed = json.loads(json.dumps(lock["sourceGraph"]))
            changed["receipt"]["unexpectedField"] = "not in the archived receipt"
            changed["receiptSHA256"] = foundation.digest(
                (json.dumps(changed["receipt"], sort_keys=True) + "\n").encode())
            with self.assertRaisesRegex(ValueError, "source graph"):
                foundation.verify_source_graph(root, "stock", changed)

    def test_q_pin_compatibility_requires_original_compact_lock_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._q_pin_fixture(directory)
            path = root / "Tools/bazel/artifacts/foundation-stock.lock.json"
            original = path.read_bytes()
            lock = json.loads(original)
            self.assertTrue(foundation._legacy_recipe_compatible(root, lock, "stock", "foundation"))
            path.write_bytes(original + b"\n")
            self.assertFalse(foundation._legacy_recipe_compatible(root, lock, "stock", "foundation"))
            path.write_bytes(original)
            changed = json.loads(original)
            changed["archiveSHA256"] = "0" * 64
            path.write_text(json.dumps(changed, sort_keys=True) + "\n")
            self.assertFalse(foundation._legacy_recipe_compatible(root, changed, "stock", "foundation"))

    def test_q_pin_compatibility_rejects_other_manifest_and_stock_lock_changes(self) -> None:
        for relative, mutate in (
            ("Package.swift", lambda data: data.replace(b".macOS(.v15)", b".macOS(.v14)")),
            ("Package.stock.resolved", lambda data: data + b"\n"),
        ):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = self._q_pin_fixture(directory)
                path = root / relative
                path.write_bytes(mutate(path.read_bytes()))
                self.assertFalse(foundation._legacy_upper_pin_delta(root, "enhanced"))

    def test_q_pin_compatibility_rejects_duplicate_pin_and_untrue_origin_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._q_pin_fixture(directory)
            resolved = root / "Package.resolved"
            data = resolved.read_text()
            container = next(row for row in json.loads(data)["pins"]
                             if row["identity"] == "container")
            # Use JSON structure to append a second pin with the same identity.
            parsed = json.loads(data)
            parsed["pins"].append(container)
            resolved.write_text(json.dumps(parsed, indent=2) + "\n")
            self.assertFalse(foundation._legacy_upper_pin_delta(root, "enhanced"))
            resolved.write_text(data.replace('"originHash" : "', '"originHash" : "0', 1))
            self.assertFalse(foundation._legacy_upper_pin_delta(root, "enhanced"))

    def test_q_pin_compatibility_rejects_lower_recipe_production_and_header_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._q_pin_fixture(directory)
            lock_path = root / "Tools/bazel/artifacts/containerization-enhanced.lock.json"
            lock = json.loads(lock_path.read_text())

            foundation_lock = root / "Tools/bazel/artifacts/foundation-enhanced.lock.json"
            changed_lower = json.loads(foundation_lock.read_text())
            changed_lower["archiveSHA256"] = "0" * 64
            foundation_lock.write_text(json.dumps(changed_lower))
            self.assertFalse(foundation._legacy_recipe_compatible(root, lock, "enhanced", "containerization"))
            archived_foundation_lock = (Path(__file__).resolve().parent / "fixtures/legacy-00a6549" /
                                        "foundation-enhanced.lock.json")
            foundation_lock.write_bytes(archived_foundation_lock.read_bytes())

            changed_recipe = json.loads(json.dumps(lock))
            changed_recipe["recipeSHA256"]["rootBuild"] = "0" * 64
            self.assertFalse(foundation._legacy_recipe_compatible(root, changed_recipe, "enhanced", "containerization"))

            dependency = root / "Tools/bazel/dependencies.bzl"
            original_dependency = dependency.read_bytes()
            dependency.write_bytes(original_dependency + b"\n# changed input\n")
            self.assertFalse(foundation._legacy_recipe_compatible(root, lock, "enhanced", "containerization"))
            dependency.write_bytes(original_dependency)

            source = root / "Tools/bazel/artifacts/foundation.py"
            original_source = source.read_text()
            source.write_text(original_source.replace(
                'def verify_consumer(lock_path: Path, root: Path, mirror: Path | None,\n'
                '                    environment: dict[str, str], profile: str = "enhanced",\n'
                '                    group: str = "foundation") -> dict:',
                'def verify_consumer(lock_path: Path, root: Path, mirror: Path | None,\n'
                '                    environment: dict[str, str], profile: str = "enhanced",\n'
                '                    group: str = "container-sdk") -> dict:', 1))
            self.assertFalse(foundation._legacy_producer_ast_unchanged(root))
            source.write_text(original_source)

            source.write_text(original_source.replace('return "swiftpkg_"', 'return "changed_"', 1))
            self.assertFalse(foundation._legacy_producer_ast_unchanged(root))


    def test_generated_build_replaces_compilers_and_keeps_other_rules(self) -> None:
        result = transformed_build(BUILD)
        self.assertIn('swift_library = "foundation_swift_library"', result)
        self.assertIn('cc_library = "foundation_cc_library"', result)
        self.assertIn('"swift_library_group"', result)
        self.assertNotIn('load("@rules_cc//cc:defs.bzl")', result)


    def test_overlay_contains_binaries_headers_and_metadata_without_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "BUILD.bazel").write_text(BUILD)
            (source / "Sources").mkdir()
            (source / "Sources/Logging.swift").write_text("print(0)\n")
            (source / "Sources/thing.c").write_text("int x;\n")
            (source / "Sources/thing.h").write_text("extern int x;\n")
            (source / "Sources/Shims.c").write_text("#define SHIM 1\n")
            (source / "LICENSE").write_text("license\n")
            outputs = {"binary/Logging.swiftmodule": b"module",
                       "binary/Logging.swiftdoc": b"docs",
                       "binary/libLogging.rspm.__impl.a": b"swift archive",
                       "binary/libCThing.rspm_c.lo": b"c archive"}
            identity = {"swift": {"Logging.rspm.__impl": {
                "swiftmodule": "binary/Logging.swiftmodule", "swiftdoc": "binary/Logging.swiftdoc",
                "archive": "binary/libLogging.rspm.__impl.a"}},
                "cc": {"CThing.rspm_c": "binary/libCThing.rspm_c.lo"}}
            files = package_overlay("swiftpkg_example", source, outputs, identity)
            self.assertIn("Sources/thing.h", files)
            self.assertIn("Sources/Shims.c", files)
            self.assertNotIn("Sources/thing.c", files)
            self.assertNotIn("Sources/Logging.swift", files)
            self.assertIn(b"CThing.rspm_c", files["prebuilt.bzl"])
            self.assertIn(b'C_HEADER_ONLY = ["HeaderOnly"]', files["prebuilt.bzl"])
            self.assertIn(b'"CThing.rspm_c": ["Sources/thing.h"]', files["prebuilt.bzl"])
            self.assertNotIn(b'"CThing.rspm_c": ["Sources/thing.c"', files["prebuilt.bzl"])


    def test_unknown_c_sources_never_become_header_only(self) -> None:
        build = '''cc_library(
    name = "Generated",
    srcs = GENERATED_SOURCES,
)
cc_library(
    name = "Headers",
    srcs = ["include/public.h"],
)
'''
        self.assertEqual(header_only_c_targets(build), {"Headers"})
        with self.assertRaisesRegex(ValueError, "unsupported source expression"):
            compiled_c_header_sources(build, {"Generated"})


    def test_group_pins_do_not_mix_sdk_and_foundation(self) -> None:
        pins = {"swift-log": "a" * 40, "containerization": "b" * 40,
                "container-engine-api": "c" * 40, "container": "d" * 40,
                "swift-argument-parser": "e" * 40}
        self.assertEqual(group_pins(pins, "foundation"), {"swift-log": "a" * 40})
        self.assertEqual(group_pins(pins, "container-sdk"), {"container": "d" * 40})


    def test_archive_is_deterministic_and_rejects_member_change(self) -> None:
        files = {"foundation/swiftpkg_example/BUILD.bazel": b"filegroup(name = 'x')\n",
                 "foundation/swiftpkg_example/binary/libExample.a": b"example archive"}
        manifest = {"schema": 1, "group": "foundation", "profile": "enhanced",
                    "configuredRoot": "//:products",
                    "selectedTargets": ["@swiftpkg_example//:Example"],
                    "compiledOutputBEP": {"eventsSHA256": "a" * 64, "files": {
                        "bazel-out/config/bin/external/+dependencies+swiftpkg_example/libExample.a": {
                            "sha256": digest(b"example archive"), "size": len(b"example archive")}}},
                    "packages": {"example": {"repository": "swiftpkg_example", "swiftTargets": ["Example"], "cTargets": []}},
                    "files": {name: digest(data) for name, data in files.items()}}
        sealed = archive_bytes(files, manifest)
        self.assertEqual(sealed, archive_bytes(files, manifest))
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "layer.tar.gz"
            archive.write_bytes(sealed)
            self.assertEqual(inspect(archive)["archiveSHA256"], digest(sealed))
            archive.write_bytes(archive_bytes({"foundation/swiftpkg_example/BUILD.bazel": b"changed"}, manifest))
            with self.assertRaises(ValueError):
                inspect(archive)


    def test_archive_rejects_noncanonical_member_aliases(self) -> None:
        canonical = "foundation/swiftpkg_example/BUILD.bazel"
        aliases = (
            "foundation/swiftpkg_example/./BUILD.bazel",
            "foundation/swiftpkg_example//BUILD.bazel",
            "foundation/swiftpkg_example/../swiftpkg_example/BUILD.bazel",
            "/foundation/swiftpkg_example/BUILD.bazel",
            "foundation/swiftpkg_example/BUILD.bazel/",
        )
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "layer.tar.gz"
            for alias in aliases:
                files = {canonical: b"original", alias: b"alias"}
                manifest = {"schema": 1, "group": "foundation", "profile": "enhanced",
                            "configuredRoot": "//:products",
                            "selectedTargets": ["@swiftpkg_example//:Example"],
                            "packages": {"example": {"repository": "swiftpkg_example", "swiftTargets": ["Example"], "cTargets": []}},
                            "files": {name: digest(data) for name, data in files.items()}}
                archive.write_bytes(archive_bytes(files, manifest))
                with self.subTest(alias=alias), self.assertRaisesRegex(ValueError, "canonical"):
                    inspect(archive)


    def test_recipe_identity_changes_with_applied_source_patch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Tools/bazel/artifacts").mkdir(parents=True)
            for name in ("foundation.py", "foundation_import.bzl", "compiled_outputs.bzl", "BUILD.bazel"):
                (root / "Tools/bazel/artifacts" / name).write_text("recipe\n")
            for name in ("run.sh", "input_identity.py", "dependencies.bzl", "source_graph.py", "rules-license-empty-provider.patch"):
                (root / "Tools/bazel" / name).write_text("recipe\n")
            (root / "BUILD.bazel").write_text("binary\n")
            (root / "Package.swift").write_text("package\n")
            (root / ".bazelrc").write_text("common --enable_bzlmod\nbuild --macos_minimum_os=15.0\n"
                                           "build:release --compilation_mode=opt\n")
            (root / "MODULE.bazel").write_text("rules\n")
            (root / "MODULE.bazel.lock").write_text(json.dumps({
                "lockFileVersion": 21, "registryFileHashes": {},
                "selectedYankedVersions": {}, "moduleExtensions": {}}))
            patch = root / "Tools/bazel/rules-swift-sandbox-output.patch"
            patch.write_text("before\n")
            first = recipe_identity(root)
            patch.write_text("after\n")
            self.assertNotEqual(first, recipe_identity(root))
            patch.write_text("before\n")
            (root / "Tools/bazel/artifacts/compiled_outputs.bzl").write_text("changed aspect\n")
            self.assertNotEqual(first, recipe_identity(root))
            (root / "Tools/bazel/artifacts/compiled_outputs.bzl").write_text("recipe\n")
            (root / ".bazelrc").write_text("common --enable_bzlmod\nbuild --macos_minimum_os=16.0\n"
                                           "build:release --compilation_mode=opt\n")
            self.assertNotEqual(first, recipe_identity(root))


if __name__ == "__main__":
    unittest.main()
