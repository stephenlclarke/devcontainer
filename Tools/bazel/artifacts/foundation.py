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

"""Seal one configured SwiftPM package group as a compiled dependency layer.

This producer consumes one admitted source-mode Bazel build and its configured
output list. It refuses missing or conflicting binary variants. A development
proof from a dirty checkout is marked as such and cannot be published.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import tarfile
from urllib.parse import unquote, urlparse
from contextlib import contextmanager

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from package_checks.cli_process import session_members, terminate_session

if __package__:
    from .argument_parser import command, digest, file_digest
else:
    from argument_parser import command, digest, file_digest

UPPER_PACKAGES = {"container", "containerization", "container-engine-api"}
BUILD_ONLY_PACKAGES = {"swift-docc-plugin", "swift-docc-symbolkit"}
PUBLISHED_LOWER = "swift-argument-parser"
GROUPS = ("foundation", "containerization", "engine-api", "container-sdk")
GROUP_PACKAGES = {"containerization": {"containerization"},
                  "engine-api": {"container-engine-api"}, "container-sdk": {"container"}}
GROUP_REPOSITORIES = {"foundation": "stephenlclarke/devcontainer",
                      "containerization": "stephenlclarke/containerization",
                      "engine-api": "stephenlclarke/container-engine-api",
                      "container-sdk": "stephenlclarke/container"}
LOWERS = {"foundation": ("argument-parser",),
          "containerization": ("argument-parser", "foundation"),
          "engine-api": ("argument-parser", "foundation"),
          "container-sdk": ("argument-parser", "foundation", "containerization", "engine-api")}
IMPLEMENTATION_EXTENSIONS = {".swift", ".c", ".cc", ".cpp", ".cxx", ".m", ".mm", ".s", ".S", ".asm"}
OUTPUT_SUFFIXES = (".swiftmodule", ".swiftdoc", ".a", ".lo")
REPOSITORY_PREFIX = "+dependencies+swiftpkg_"
SHA = re.compile(r"[0-9a-f]{64}\Z")


def source_records(root: Path, profile: str = "enhanced") -> dict[str, dict]:
    if profile not in {"enhanced", "stock"}:
        raise ValueError("foundation profile must be enhanced or stock")
    selected = "Package.resolved" if profile == "enhanced" else "Package.stock.resolved"
    rows = json.loads((root / selected).read_text())["pins"]
    records = {row["identity"]: {"revision": row["state"]["revision"],
                                  "location": row["location"]} for row in rows}
    if (len(records) != len(rows) or
            any(not re.fullmatch(r"[0-9a-f]{40}", item["revision"]) or
                not isinstance(item["location"], str) or not item["location"]
                for item in records.values())):
        raise ValueError("resolved dependency source pins are incomplete")
    return records


def source_pins(root: Path, profile: str = "enhanced") -> dict[str, str]:
    return {name: item["revision"] for name, item in source_records(root, profile).items()}


def _legacy_upper_pin_delta(root: Path, profile: str) -> bool:
    """Accept only the archived inputs with the reviewed enhanced runtime pins."""
    if profile not in {"enhanced", "stock"}:
        return False
    baseline_manifest = "f77f14603ace0bed2ec3cd60e1a0b7a2d043a33cd181c42a4f22d398c569100a"
    baseline_lock = "f9ba00f315a3f74a5dd5440c76dd092e98a4ac41bda1339227e9b4967bb9a562"
    baseline_stock_lock = "f7186c61b9e1571021ae2b1f58ef05f901043bfa872829e49546f766e41da37d"
    baseline_pins = {
        "container": "4bf4750989138800d65abbbe7f9ff8d7b286bd16",
        "containerization": "b404e03bb914904107a6a9305ba1f0e44c79a59c",
        "container-engine-api": "40436017e1e93012b8dab7cfc3c79783538065c3",
        "swift-nio-ssl": "3e13ce5f6dd5b7e89fff9ab55ab7caed39fe7285",
    }
    reviewed_pins = {
        "container": "f86fea2236fab118c0e0c6f8be5eb7672df894e2",
        "containerization": "6db16197bbad8196a78132f86529daa89125aafb",
        "container-engine-api": "48e44d74d738ca3d24351ba02c4869be1a3e6998",
        "swift-nio-ssl": "322f3c2a4a21df31c84ca416bf65ee5e9059e440",
    }
    manifest_path = root / "Package.swift"
    lock_path = root / "Package.resolved"
    stock_lock_path = root / "Package.stock.resolved"
    try:
        # This finite SDK-only transition binds the complete new direct snapshots.
        sdk_inputs = {
            root / "Package.swift": "096e5d9aa6d7b6f7987de62965bec52e870045a7ffd5262be423c7d0967eacee",
            root / "Package.resolved": "f0fd634dcfe88f316f53e9f9f42883adeb402e127ebf6e6be26fd13cc62b5ec9",
            root / "Package.stock.resolved": "24d5a40e2b1535c8d509488b7f7b4f8812863de151c97056b6f7be493e5518fb",
            root / "Tools/bazel/source_graph.py": "df1e509f43957d0e72319bca273eea277ba9c0c8c2e3292ac13767c63607f536",
        }
        if all(file_digest(path) == expected for path, expected in sdk_inputs.items()):
            stock = source_records(root, "stock")
            enhanced = source_pins(root, "enhanced")
            return (stock["container"]["revision"] == "aad0c75555d8ccce45aea01d7e1558eb7dee408e"
                    and stock["container"]["location"] == "https://github.com/stephenlclarke/container.git"
                    and enhanced["container"] == "906014c854a09df4283316289bc755a925f81fe3")
        # Bind the tested Engine API sources and enhanced Container nested pin
        # to exact reviewed files; every other manifest and lock byte stays fixed.
        engine_refresh_inputs = {
            manifest_path: "983e37299d3315b8ab836e56444f049ea6acfb67863d9dafbea3f7c1570b33d3",
            lock_path: "8a75925150ca36efd92d11767d4d2e4ee68f420361667228ba9abc9ed07c3325",
            stock_lock_path: "c5dc4990e57aae68e649864f7914605a4d374563950b460191eacec46c2c084d",
        }
        if all(file_digest(path) == expected for path, expected in engine_refresh_inputs.items()):
            enhanced = source_pins(root, "enhanced")
            stock = source_pins(root, "stock")
            return (enhanced["container-engine-api"] == "6e8c932fc8755a4b922fd239426e9029be0554e0"
                    and stock["container-engine-api"] == "36de2d66d4a1f7eb48c08d94cf1444f93d5f9c77"
                    and enhanced["container"] == "906014c854a09df4283316289bc755a925f81fe3")
        manifest = manifest_path.read_bytes()
        lock = lock_path.read_bytes()
        current_manifest_sha = digest(manifest)
        if file_digest(stock_lock_path) != baseline_stock_lock:
            return False
        selected = source_records(root, "enhanced")
        locations = {
            "container": "https://github.com/stephenlclarke/container.git",
            "containerization": "https://github.com/stephenlclarke/containerization.git",
            "container-engine-api": "https://github.com/stephenlclarke/container-engine-api.git",
            "swift-nio-ssl": "https://github.com/apple/swift-nio-ssl.git",
        }
        if any(not re.fullmatch(r"[0-9a-f]{40}", selected.get(name, {}).get("revision", ""))
               for name in locations):
            return False
        if (all(selected[name]["revision"] == pin for name, pin in baseline_pins.items())
                and all(selected[name]["location"] == (
                    "https://github.com/stephenlclarke/swift-nio-ssl.git"
                    if name == "swift-nio-ssl" else location)
                         for name, location in locations.items())
                and current_manifest_sha == baseline_manifest
                and file_digest(lock_path) == baseline_lock):
            return True
        if (any(selected[name]["revision"] != pin for name, pin in reviewed_pins.items())
                or any(selected[name]["location"] != location for name, location in locations.items())):
            return False

        normalized_manifest = manifest
        for name in ("container", "containerization"):
            current_pin = reviewed_pins[name].encode()
            baseline_pin = baseline_pins[name].encode()
            manifest_pattern = re.compile(rb'(enhancedRevision:\s*")' + current_pin + rb'(")')
            normalized_manifest, count = manifest_pattern.subn(
                lambda match, pin=baseline_pin: match.group(1) + pin + match.group(2),
                normalized_manifest)
            if count != 1:
                return False
        # Normalize only the exact enhanced Engine API conditional. Stock keeps
        # the historical revision and every other manifest byte stays pinned.
        current_engine_block = (
            b'revision: enhancedRuntime\n                ? "' + reviewed_pins["container-engine-api"].encode()
            + b'"\n                : "' + baseline_pins["container-engine-api"].encode() + b'"'
        )
        baseline_engine_block = b'revision: "' + baseline_pins["container-engine-api"].encode() + b'"'
        if normalized_manifest.count(current_engine_block) != 1:
            return False
        normalized_manifest = normalized_manifest.replace(current_engine_block, baseline_engine_block, 1)
        if digest(normalized_manifest) != baseline_manifest:
            return False

        parsed = json.loads(lock)
        rows = {row.get("identity"): row for row in parsed.get("pins", [])}
        if (len(rows) != len(parsed.get("pins", []))
                or any(rows.get(name, {}).get("location") != locations[name]
                       or rows.get(name, {}).get("state", {}).get("revision") != reviewed_pins[name]
                       for name in reviewed_pins)
                or parsed.get("originHash") != current_manifest_sha):
            return False
        origin_pattern = re.compile(rb'("originHash"\s*:\s*")[0-9a-f]{64}(")')
        normalized_lock, origin_count = origin_pattern.subn(
            lambda match: match.group(1) + baseline_manifest.encode() + match.group(2), lock)
        revision_counts = []
        for name, current_pin in reviewed_pins.items():
            revision_pattern = re.compile(rb'("revision"\s*:\s*")' + current_pin.encode() + rb'(")')
            normalized_lock, revision_count = revision_pattern.subn(
                lambda match, pin=baseline_pins[name].encode(): match.group(1) + pin + match.group(2),
                normalized_lock)
            revision_counts.append(revision_count)
        old_nio_location = b"https://github.com/stephenlclarke/swift-nio-ssl.git"
        current_nio_location = b"https://github.com/apple/swift-nio-ssl.git"
        normalized_lock, nio_location_count = re.subn(
            rb'("location"\s*:\s*")' + re.escape(current_nio_location) + rb'(")',
            lambda match: match.group(1) + old_nio_location + match.group(2), normalized_lock)
        return (origin_count == 1 and revision_counts == [1, 1, 1, 1]
                and nio_location_count == 1
                and digest(normalized_lock) == baseline_lock)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def _legacy_producer_ast_unchanged(root: Path) -> bool:
    """Pin every production node outside the five narrowly reviewed verifier nodes."""
    import ast
    import copy

    expected_module = "a4a341a1bc56dbe435497e16be7b31c8b3d48e8d13ab039a457b4582e992a7e8"
    expected_heads = {
        "_legacy_upper_pin_delta": "69158214437f3aaa765dfdf2ca3f582de6761c42db65ef5f1ea5700702fe1586",
        "_legacy_producer_ast_unchanged": "1076a2c55d65304bdbbccfb893a53f2fffa76a466877989fd65fb28f637be3a8",
        "_legacy_recipe_compatible": "390cffd6b160a83ef96cdd881774d0690ab5885d25247ff7b1968de48e0ffc50",
        "verify_consumer": "e691381bb64617c7fc5c7ca2e7c155f3daccbd3bb508174bb806734b27420512",
        "verify_source_graph": "6beeacddf79ad572b66c260451f1401990e52bb0ef37224b47ed56ea0c64e01d",
    }
    try:
        source = (root / "Tools/bazel/artifacts/foundation.py").read_text()
        tree = ast.parse(source)
        if any(getattr(node, "type_params", ()) for node in ast.walk(tree)):
            return False

        def version_neutral_dump(node: ast.AST) -> str:
            # Python 3.12 added empty FunctionDef.type_params to ast.dump;
            # the producer's maintained /usr/bin/python3 may be Python 3.9.
            # Remove that empty field from a deep copy only; all other AST
            # values, including string constants, remain part of the dump.
            normalized = copy.deepcopy(node)
            for current in ast.walk(normalized):
                if hasattr(current, "type_params"):
                    if current.type_params:
                        raise ValueError("generic production function is outside the pinned AST contract")
                    delattr(current, "type_params")
            return ast.dump(normalized, include_attributes=False)

        permitted = {"_legacy_upper_pin_delta", "_legacy_producer_ast_unchanged",
                     "_legacy_recipe_compatible", "verify_consumer", "verify_source_graph"}
        counts = {name: 0 for name in permitted}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in counts:
                counts[node.name] += 1
        if (counts["verify_consumer"] != 1 or counts["verify_source_graph"] != 1
                or counts["_legacy_upper_pin_delta"] != 1
                or counts["_legacy_producer_ast_unchanged"] != 1
                or counts["_legacy_recipe_compatible"] != 1):
            return False
        excluded = permitted
        retained = [node for node in tree.body
                    if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and node.name in excluded)]
        recipe_functions = [node for node in retained
                            if isinstance(node, ast.FunctionDef) and node.name == "recipe_identity"]
        patch_bindings = ast.parse('''
if profile == "enhanced" and group == "foundation":
    result["zstdPatch"] = file_digest(root / "Tools/bazel/zstd-public-module.patch")
if profile == "enhanced" and group == "containerization":
    result["ext4Patch"] = file_digest(root / "Tools/bazel/containerization-ext4-unaligned.patch")
if profile == "enhanced" and group == "engine-api":
    result["gatewayRecoveryPatch"] = file_digest(root / "Tools/bazel/gateway-recovery-capability.patch")
''').body
        if len(recipe_functions) != 1 or len(recipe_functions[0].body) < 4:
            return False
        recipe_body = recipe_functions[0].body
        if (version_neutral_dump(ast.Module(body=recipe_body[-4:-1], type_ignores=[])) !=
                version_neutral_dump(ast.Module(body=patch_bindings, type_ignores=[]))):
            return False
        # These are the only source-code additions allowed for the new patches
        # recipe inputs. All other recipe_identity nodes remain fingerprinted.
        del recipe_body[-4:-1]
        fingerprint = digest(version_neutral_dump(ast.Module(body=retained, type_ignores=[])).encode())
        if fingerprint != expected_module:
            return False
        for name, expected in expected_heads.items():
            node = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == name)
            header = ast.FunctionDef(name=node.name, args=node.args, body=[],
                                     decorator_list=node.decorator_list, returns=node.returns,
                                     type_comment=node.type_comment)
            if hasattr(node, "type_params"):
                if node.type_params:
                    return False
                header.type_params = node.type_params
            if digest(version_neutral_dump(header).encode()) != expected:
                return False
        return True
    except (OSError, SyntaxError, StopIteration, ValueError):
        return False


def _legacy_recipe_compatible(root: Path, lock: dict, profile: str, group: str) -> bool:
    """Permit only exact archived layers across reviewed upper-build changes."""
    # Old stock SDK is intentionally absent; only exact reviewed assets survive.
    if (file_digest(root / "Package.swift") == "096e5d9aa6d7b6f7987de62965bec52e870045a7ffd5262be423c7d0967eacee"
            and _legacy_upper_pin_delta(root, profile)):
        try:
            admitted = {('stock', 'foundation'): '986d309ad2ced14a5656a6d9291e174a7e5afc12ea8d7db649fed809eebf6495', ('stock', 'containerization'): '0918dab08b02c8b9aa5c13db8c2c165c245fee9ce579a7de4ac87b1e68c1600f', ('stock', 'engine-api'): '55e6fa9fc30c643fb315b8682f54ee71e335d4021b23aa43e0c1401bfb52a49a', ('enhanced', 'foundation'): '73dbf0ee81fa900bdff830610aae417befa67d3c221556837b0ebae342e8508b', ('enhanced', 'containerization'): '130525901ff3aa0d28a806128dee81c58bbd731ab64cfaab3364564be8b79289', ('enhanced', 'engine-api'): '3d8d17831d07be278a3ae2a6e9d42fdc62ddd60c69aa104c847d7fa59bda901f', ('enhanced', 'container-sdk'): 'a87c20734a05c410b8ba1c15fdc9ceadd8d1c0b3c0721d3d2b9b91a2318a9597'}
            # Preserve the exact upper Go/shared snapshot as well as Swift recipes.
            shared_inputs = {
                root / 'Tools/bazel/BUILD.bazel': '8dfb392d17aaf4d396fa862006f9709616accd03c650ef654516f9485807fe8c',
                root / 'Tools/bazel/rules-go-sdk-notices.patch': '3778368f7027873876084a62022705bbd8253341452e7b295cf5cb67742e4f7f',
                root / 'Tools/bazel/rules-go-sanitizer-isolation.patch': '77507777768a248f2da6e7181c6168ffeb6b26145c5e092a0cc0bc19925f1c49',
                root / 'Tools/terminal-launcher/go.mod': 'fff26f4375940d4ba8b2493bd5d491daf117b2e4d3e3a728fd2a1ae87246036c',
            }
            if any(file_digest(path) != expected for path, expected in shared_inputs.items()):
                return False
            key = (profile, group)
            if key not in admitted or not _legacy_producer_ast_unchanged(root):
                return False
            fixture = layer_lock_path(root, group, profile)
            if file_digest(fixture) != admitted[key] or json.loads(fixture.read_text()) != lock:
                return False
            recipes = {('stock', 'foundation'): 'c227e2feb830816649be00b6c60361c27dd82d105a26892652ebfc18410e7f3f', ('stock', 'containerization'): 'c227e2feb830816649be00b6c60361c27dd82d105a26892652ebfc18410e7f3f', ('stock', 'engine-api'): 'c227e2feb830816649be00b6c60361c27dd82d105a26892652ebfc18410e7f3f', ('enhanced', 'foundation'): '5fe29d66546e6438d7de661dba71e5397eec93b423c42a417483c3276ff354c7', ('enhanced', 'containerization'): '37fef3cd1fae6af30c0dc9548ea42f0ee7c819d676fbceb4002f8b52131758da', ('enhanced', 'engine-api'): '1886783afaf1ed2601e1fb1a64dc82ee772e6cfd78d338657c2af17294c8a782', ('enhanced', 'container-sdk'): '0628578e548dfe10c885a3d5fc44d6a2d603da50b25f21613d5530f6fa4e06bf'}
            recipe_path = root / "Tools/bazel/artifacts/fixtures/stock-xpc-clock-transition" / f"current-recipe-{group}-{profile}.json"
            if file_digest(recipe_path) != recipes[key]:
                return False
            before = json.loads(recipe_path.read_text())
            normalized = recipe_identity(root, profile, group)
            # All other current recipe fields must match the pre-transition snapshot.
            for field in ("producer", "swiftPackageManifest", "sourceGraphValidator"):
                normalized[field] = before[field]
            return (normalized == before
                    and lock.get("sourcePins") == group_pins(source_pins(root, profile), group)
                    and lock.get("lower") == lower_records(root, profile, group))
        except (OSError, ValueError, KeyError, TypeError):
            return False
    baseline_producer = "6443d5f36ddb098d7db91abadf070baaab94a49cb089a5a7a899a3874af1a014"
    baseline_manifest = "f77f14603ace0bed2ec3cd60e1a0b7a2d043a33cd181c42a4f22d398c569100a"
    baseline_launcher = "aee681d8038c67e57f0d06e194240147deb5c61ed7f00f87b5ad9099624ed987"
    reviewed_launcher = "fb47bcdd1f485735828a7f8197b343bf246240540acfce77f8074c7f5517c923"
    archived_locks = {
        ("enhanced", "foundation"): "5383f27b335a66de065102515bf7ff3644b8ad784f409c15a8d3b5be6c908956",
        ("enhanced", "containerization"): "75010ab0b5e69f67e3d5aa4aa9e152bc7b0a3f26477af4fc501d8afc024da57f",
        ("enhanced", "engine-api"): "007dcded692ff84c64b88cfef2211e62e3bf321985689908440b332b29edfcee",
        ("enhanced", "container-sdk"): "c2784522152856079bd0d8099a8af151ef1e562f4d4902150346efa95079ff47",
        ("stock", "foundation"): "986d309ad2ced14a5656a6d9291e174a7e5afc12ea8d7db649fed809eebf6495",
        ("stock", "containerization"): "0918dab08b02c8b9aa5c13db8c2c165c245fee9ce579a7de4ac87b1e68c1600f",
        ("stock", "engine-api"): "a5cb5830748d5d2f858597b6c1ffce60eefbb45482eef69ad35369b705ab8d3c",
        ("stock", "container-sdk"): "4d0470004fd40bb8d440109e792e993563840c0889a3a622dc045b88f99ffaeb",
    }
    current_enhanced_locks = {
        "foundation": "73dbf0ee81fa900bdff830610aae417befa67d3c221556837b0ebae342e8508b",
        "containerization": "130525901ff3aa0d28a806128dee81c58bbd731ab64cfaab3364564be8b79289",
        "engine-api": "33f3c3f739b2a4d4906c0c2c566676e5a2b26d3c5223ea8eb31c0210c8f5d8fd",
        "container-sdk": "fd8ce243165c3fe57e887b5befe948f258f0bd69ccba0fa1001adaef4ccdfc72",
    }
    terminal_addition_inputs = {
        root / "BUILD.bazel": "1fabba720bbaf04748d2848b68d171dc0ebaa82787fa356f6173bd03a1c7fc1c",
        root / "MODULE.bazel": "06c92af722c4fb6af0efd0e575bf6ab3b6f53daabb8b5488286483fd4e1c6c1e",
        root / "Tools/bazel/BUILD.bazel": "8dfb392d17aaf4d396fa862006f9709616accd03c650ef654516f9485807fe8c",
        root / "Tools/bazel/rules-go-sdk-notices.patch":
            "3778368f7027873876084a62022705bbd8253341452e7b295cf5cb67742e4f7f",
        root / "Tools/bazel/rules-go-sanitizer-isolation.patch":
            "77507777768a248f2da6e7181c6168ffeb6b26145c5e092a0cc0bc19925f1c49",
        root / "Tools/terminal-launcher/go.mod":
            "fff26f4375940d4ba8b2493bd5d491daf117b2e4d3e3a728fd2a1ae87246036c",
    }
    if (profile == "enhanced" and group not in {"foundation", "containerization", "engine-api", "container-sdk"}
            or profile == "stock" and group not in {"foundation", "containerization", "engine-api", "container-sdk"}
            or profile not in {"enhanced", "stock"}
            or not _legacy_upper_pin_delta(root, profile)
            or not _legacy_producer_ast_unchanged(root)):
        return False
    try:
        engine_refresh = (file_digest(root / "Package.swift") ==
                          "983e37299d3315b8ab836e56444f049ea6acfb67863d9dafbea3f7c1570b33d3")
        if engine_refresh and group not in {"foundation", "containerization"}:
            return False
        canonical_lock = layer_lock_path(root, group, profile)
        lock_sha = file_digest(canonical_lock)
        archived_match = profile == "stock" and lock_sha == archived_locks[(profile, group)]
        current_enhanced_match = (profile == "enhanced"
                                  and lock_sha == current_enhanced_locks[group])
        if ((not archived_match and not current_enhanced_match)
                or json.loads(canonical_lock.read_text()) != lock):
            return False
        if profile == "stock":
            # The archived stock recipe predates this exact guarded dispatcher.
            if (file_digest(root / "Tools/bazel/run.sh") != reviewed_launcher
                    or lock.get("recipeSHA256", {}).get("launcher") != baseline_launcher):
                return False
        # The terminal launcher adds a Go SDK and upper-only targets. Admit the
        # old Swift layer bytes only for this exact reviewed shared-file and
        # module-rule snapshot; lower package/source identities are checked
        # independently below.
        if any(file_digest(path) != expected for path, expected in terminal_addition_inputs.items()):
            return False
        current_recipe = recipe_identity(root, profile, group)
        if (current_recipe.get("rootBuild") != "1fabba720bbaf04748d2848b68d171dc0ebaa82787fa356f6173bd03a1c7fc1c"
                or current_recipe.get("moduleGraph") != "06c92af722c4fb6af0efd0e575bf6ab3b6f53daabb8b5488286483fd4e1c6c1e"
                or current_recipe.get("moduleRules") != "594a401a02f4d1e25683d030ac6f50b3eefa9ed6e6756eaa13d057d161b057e5"):
            return False
        if profile == "stock":
            # The exact extension adds enhancement-only patch application. Keep
            # old stock archives reusable only for this reviewed extension and
            # its exact three exported patch inputs; the stock source path remains
            # unpatched. Normalize no other recipe input.
            reviewed_inputs = {
                root / "Tools/bazel/dependencies.bzl":
                    "4a8cfa7026cdaec28ab6fd97ba480024a747c2c797eff42b822e4efd71f3fea1",
                root / "Tools/bazel/zstd-public-module.patch":
                    "4750e8650eaa5205db05a5d792478633b6d30154cea31fdba628b5b97cc15927",
                root / "Tools/bazel/containerization-ext4-unaligned.patch":
                    "960284f67cca0ba416da98f624934454e092d204b4525daf9902e0a0bbe7038d",
                root / "Tools/bazel/gateway-recovery-capability.patch":
                    "be69369a63c8c372b79ef83931125790881d057719846ca5499539c99df8bfb7",
                root / "Tools/bazel/BUILD.bazel":
                    "8dfb392d17aaf4d396fa862006f9709616accd03c650ef654516f9485807fe8c",
            }
            if any(file_digest(path) != expected for path, expected in reviewed_inputs.items()):
                return False
        if (lock.get("sourcePins") != group_pins(source_pins(root, profile), group)
                or lock.get("lower") != lower_records(root, profile, group)):
            return False
        expected = recipe_identity(root, profile, group)
        if profile == "stock":
            expected["producer"] = baseline_producer
            expected["launcher"] = baseline_launcher
            expected["swiftPackageManifest"] = baseline_manifest
            for name in ("rootBuild", "moduleGraph", "moduleRules"):
                expected[name] = lock["recipeSHA256"][name]
            expected["dependencyExtension"] = "8dbc2f830e0be2d1ddb6729f941a62997eef3bb475b590e5b77f9bc2d3abcc99"
        else:
            # Current enhanced layers differ only in the exact shared terminal
            # build snapshot above; every other current recipe field stays bound.
            # The pinned production AST excludes only reviewed verifier bodies.
            expected["producer"] = "29872c70f5aa696a2be65998de9bd484a7629a4176808877d9fe9e6f0fcec114"
            if engine_refresh:
                # Only the four exact lower locks survive this Engine/SDK
                # source refresh; their original manifest remains recipe-bound.
                expected["swiftPackageManifest"] = "f7ad97c42070afded8f7c3b8f98a6960b40b88d1f5e46d289f032d381a520eb0"
            for name in ("rootBuild", "moduleGraph", "moduleRules"):
                expected[name] = lock["recipeSHA256"][name]
        return lock.get("recipeSHA256") == expected
    except (OSError, ValueError, KeyError, TypeError):
        return False


def verify_source_graph(root: Path, profile: str, graph: object) -> None:
    """Bind the loaded Container source graph to the selected direct graph."""
    receipt = graph.get("receipt", {}) if isinstance(graph, dict) else {}
    selected = source_pins(root, profile)
    expected = {name: selected[name] for name in
                ("container", "containerization", "container-engine-api")}
    ordinary = (receipt.get("schema") == 1 and receipt.get("profile") == profile and
                receipt.get("source") == expected and
                receipt.get("argumentParserSource") == selected["swift-argument-parser"] and
                receipt.get("devcontainerManifestSHA256") == file_digest(root / "Package.swift") and
                receipt.get("devcontainerLockSHA256") == file_digest(root / "Package.resolved") and
                receipt.get("stockLockSHA256") == file_digest(root / "Package.stock.resolved") and
                bool(SHA.fullmatch(receipt.get("loadedContainerManifestSHA256", ""))) and
                bool(SHA.fullmatch(receipt.get("loadedContainerLockSHA256", ""))) and
                graph.get("receiptSHA256") == digest((json.dumps(receipt, sort_keys=True) + "\n").encode()))
    if ordinary:
        return
    if (profile == "enhanced" and file_digest(root / "Package.swift") == "096e5d9aa6d7b6f7987de62965bec52e870045a7ffd5262be423c7d0967eacee"
            and _legacy_upper_pin_delta(root, profile) and _legacy_producer_ast_unchanged(root)):
        fixture = layer_lock_path(root, "container-sdk", "enhanced")
        if (file_digest(fixture) == "a87c20734a05c410b8ba1c15fdc9ceadd8d1c0b3c0721d3d2b9b91a2318a9597"
                and graph == json.loads(fixture.read_text())["sourceGraph"]
                and digest((json.dumps(graph, sort_keys=True) + "\n").encode()) == "65050e680f529e5dc948dcad5399e5738efd4eae371997198cf84062e70209e0"
                and receipt.get("source") == expected
                and receipt.get("argumentParserSource") == selected["swift-argument-parser"]):
            return
    # The existing stock SDK archive was produced from the 00a6549 Devcontainer
    # manifest and enhanced lock. Permit only that archived identity when the
    # reviewed enhanced dependency selections are the only normalized deltas; all loaded stock graph
    # and stock-lock hashes remain exact.
    baseline_manifest = "f77f14603ace0bed2ec3cd60e1a0b7a2d043a33cd181c42a4f22d398c569100a"
    baseline_lock = "f9ba00f315a3f74a5dd5440c76dd092e98a4ac41bda1339227e9b4967bb9a562"
    if (profile == "stock" and _legacy_upper_pin_delta(root, profile)
            and receipt.get("schema") == 1 and receipt.get("profile") == profile
            and receipt.get("source") == expected
            and receipt.get("argumentParserSource") == selected["swift-argument-parser"]
            and receipt.get("devcontainerManifestSHA256") == baseline_manifest
            and receipt.get("devcontainerLockSHA256") == baseline_lock
            and receipt.get("stockLockSHA256") == file_digest(root / "Package.stock.resolved")
            and receipt.get("loadedContainerManifestSHA256") == "98abcd5d2cfb4a9f7e49f459bb61c7d3b1c84927d31a12760a5072be5f67a2bc"
            and receipt.get("loadedContainerLockSHA256") == "f81dd6ad3129356d93ad3e6a537db838ecfde65eca0e1a7b271c45d6ed4f2d03"
            and graph.get("receiptSHA256") == "e6f410dfdb2b903064b4fb0c2542bba34bb401750e2243ab01fbc52945e4813f"
            and graph.get("receiptSHA256") == digest((json.dumps(receipt, sort_keys=True) + "\n").encode())):
        return
    raise ValueError("ContainerSDK source graph differs from the selected profile and loaded source")


def foundation_pins(pins: dict[str, str]) -> dict[str, str]:
    return {name: revision for name, revision in pins.items()
            if name not in UPPER_PACKAGES | BUILD_ONLY_PACKAGES | {PUBLISHED_LOWER}}


def group_pins(pins: dict[str, str], group: str) -> dict[str, str]:
    if group == "foundation":
        return foundation_pins(pins)
    if group not in GROUP_PACKAGES:
        raise ValueError("unsupported Swift package layer group")
    return {name: pins[name] for name in GROUP_PACKAGES[group]}


def recipe_identity(root: Path, profile: str = "enhanced", group: str = "foundation") -> dict[str, str]:
    semantic_build_lines = []
    operational = ("build --jobs=", "build --local_resources=", "build --experimental_disk_cache_gc_",
                   "build --symlink_prefix=", "build --action_env=TMPDIR")
    for raw in (root / ".bazelrc").read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(("test ", "coverage ")):
            continue
        if line.startswith(operational) or line.startswith(("build:asan ", "build:tsan ", "common:prebuilt-")):
            continue
        if line.startswith(("common ", "build ", f"common:{profile} ", f"build:{profile} ", "build:release ")):
            semantic_build_lines.append(line)
    if not semantic_build_lines or not any("--macos_minimum_os=" in line for line in semantic_build_lines):
        raise ValueError("Bazel release configuration identity is incomplete")
    module_lock = json.loads((root / "MODULE.bazel.lock").read_text())
    # Bazel's local facts and extension evaluation metadata can change when a
    # later data-only release lock is added. Registry hashes and resolved
    # external module extensions still bind the compiler/build-rule graph.
    module_rules = {key: module_lock[key] for key in
                    ("lockFileVersion", "registryFileHashes", "selectedYankedVersions")}
    module_rules["moduleExtensions"] = {key: value for key, value in module_lock["moduleExtensions"].items()
                                        if "%local_git_ext" not in key}
    result = {"producer": file_digest(root / "Tools/bazel/artifacts/foundation.py"),
              "importer": file_digest(root / "Tools/bazel/artifacts/foundation_import.bzl"),
              "configuredOutputs": file_digest(root / "Tools/bazel/artifacts/compiled_outputs.bzl"),
              "artifactBuild": file_digest(root / "Tools/bazel/artifacts/BUILD.bazel"),
              "launcher": file_digest(root / "Tools/bazel/run.sh"),
              "sourceIdentity": file_digest(root / "Tools/bazel/input_identity.py"),
              "sourceGraphValidator": file_digest(root / "Tools/bazel/source_graph.py"),
              "rootBuild": file_digest(root / "BUILD.bazel"),
              "swiftPackageManifest": file_digest(root / "Package.swift"),
              "bazelConfiguration": digest(("\n".join(semantic_build_lines) + "\n").encode()),
              "moduleGraph": file_digest(root / "MODULE.bazel"),
              "moduleRules": digest(json.dumps(module_rules, sort_keys=True, separators=(",", ":")).encode()),
              "dependencyExtension": file_digest(root / "Tools/bazel/dependencies.bzl")}
    result["swiftSandboxPatch"] = file_digest(root / "Tools/bazel/rules-swift-sandbox-output.patch")
    result["rulesLicensePatch"] = file_digest(root / "Tools/bazel/rules-license-empty-provider.patch")
    if profile == "enhanced" and group == "foundation":
        result["zstdPatch"] = file_digest(root / "Tools/bazel/zstd-public-module.patch")
    if profile == "enhanced" and group == "containerization":
        result["ext4Patch"] = file_digest(root / "Tools/bazel/containerization-ext4-unaligned.patch")
    if profile == "enhanced" and group == "engine-api":
        result["gatewayRecoveryPatch"] = file_digest(root / "Tools/bazel/gateway-recovery-capability.patch")
    return result


def repo_name(identity: str) -> str:
    return "swiftpkg_" + identity.replace("-", "_").replace(".", "_")


def layer_lock_path(root: Path, group: str, profile: str) -> Path:
    if group not in GROUPS or profile not in {"enhanced", "stock"}:
        raise ValueError("unknown package layer lock")
    return root / "Tools/bazel/artifacts" / f"{group}-{profile}.lock.json"


def lower_records(root: Path, profile: str, group: str,
                  argument_parser_lock: Path | None = None) -> dict[str, dict]:
    selected = source_pins(root, profile)
    records = {}
    for lower in LOWERS[group]:
        if lower == "argument-parser":
            path = argument_parser_lock or root / "Tools/bazel/artifacts/argument-parser.lock.json"
        else:
            path = layer_lock_path(root, lower, profile)
        lock = json.loads(path.read_text())
        archive = lock.get("archiveSHA256", "")
        if not SHA.fullmatch(archive):
            raise ValueError(f"lower {lower} archive has no exact digest")
        if lower == "argument-parser":
            if lock.get("manifest", {}).get("sourceCommit") != selected[PUBLISHED_LOWER]:
                raise ValueError("published ArgumentParser pin differs from profile lock")
        elif lock.get("group") != lower or lock.get("profile") != profile:
            raise ValueError(f"lower {lower} has wrong group or profile")
        if (lock.get("developmentProof") is True or not isinstance(lock.get("tag"), str)
                or not SHA.fullmatch(lock.get("evidenceSHA256", ""))):
            raise ValueError(f"lower {lower} must be a published, qualified binary layer")
        records[lower] = {"archiveSHA256": archive,
                          "tag": lock.get("tag"),
                          "evidenceSHA256": lock.get("evidenceSHA256")}
    return records


def transformed_build(original: str) -> str:
    swift = 'load("@build_bazel_rules_swift//swift:swift.bzl"'
    cc = 'load("@rules_cc//cc:defs.bzl"'
    lines = []
    for line in original.splitlines():
        if line.startswith(swift):
            line = line.replace('"swift_library", ', '').replace(', "swift_library"', '')
            line = line.replace(', "swift_library")', ')')
        if line.startswith(cc):
            line = line.replace('"cc_library", ', '').replace(', "cc_library"', '')
            line = line.replace(', "cc_library")', ')')
        if line.startswith((swift, cc)) and ('"swift_library"' in line or '"cc_library"' in line):
            raise ValueError("could not replace a source compiler rule")
        if line.startswith((swift, cc)) and line.endswith('.bzl")'):
            continue
        lines.append(line)
    transformed = ('load(":prebuilt.bzl", swift_library = "foundation_swift_library", '
                   'cc_library = "foundation_cc_library")\n' + "\n".join(lines) + "\n")
    if re.search(r'^load\([^\n]*"(?:swift_library|cc_library)"', transformed[transformed.index("\n") + 1:], re.M):
        raise ValueError("a generated package still loads a source compiler rule")
    return transformed


def rule_modules(build: str) -> dict[str, str]:
    modules = {}
    for block in re.findall(r"^swift_library\(\n.*?^\)\n", build, re.M | re.S):
        name = re.search(r'^    name = "([^"]+)"', block, re.M)
        module = re.search(r'^    module_name = "([^"]+)"', block, re.M)
        if name and module:
            modules[name.group(1)] = module.group(1)
    return modules


def header_only_c_targets(build: str) -> set[str]:
    targets = set()
    for block in re.findall(r"^cc_library\(\n.*?^\)\n", build, re.M | re.S):
        name = re.search(r'^    name = "([^"]+)"', block, re.M)
        if not name:
            continue
        srcs = re.search(r'^    srcs = (.*?)(?=^    [a-z_]+ = |^\)\n)', block, re.M | re.S)
        if not srcs:
            targets.add(name.group(1))
            continue
        value = srcs.group(1).strip().removesuffix(",").strip()
        if not re.fullmatch(r'\[\s*(?:"[^"]+"\s*,?\s*)*\]', value, re.S):
            continue
        paths = re.findall(r'"([^"]+)"', value)
        if all(Path(path).suffix in {".h", ".hpp", ".modulemap", ".inc", ".def"}
               for path in paths):
            targets.add(name.group(1))
    return targets


def compiled_c_header_sources(build: str, compiled: set[str]) -> dict[str, list[str]]:
    """Retain public headers originally declared in C srcs, never C implementations."""
    result = {}
    found = set()
    for block in re.findall(r"^cc_library\(\n.*?^\)\n", build, re.M | re.S):
        name = re.search(r'^    name = "([^"]+)"', block, re.M)
        if not name or name.group(1) not in compiled:
            continue
        target = name.group(1)
        found.add(target)
        srcs = re.search(r'^    srcs = (.*?)(?=^    [a-z_]+ = |^\)\n)', block, re.M | re.S)
        if srcs is None:
            result[target] = []
            continue
        value = srcs.group(1).strip().removesuffix(",").strip()
        if not re.fullmatch(r'\[\s*(?:"[^"]+"\s*,?\s*)*\]', value, re.S):
            raise ValueError(f"compiled C target has unsupported source expression: {target}")
        result[target] = [path for path in re.findall(r'"([^"]+)"', value)
                          if Path(path).suffix in {".h", ".hpp", ".inc", ".modulemap", ".def"}]
    if found != compiled:
        raise ValueError("compiled C target is absent from generated BUILD: " + ", ".join(sorted(compiled - found)))
    return result


def artifact_map(paths: list[str], execution_root: Path, output_base: Path,
                 allowed: dict[str, str], build_outputs: dict[str, dict]) -> tuple[dict[str, dict[str, bytes]], dict[str, dict]]:
    files: dict[str, dict[str, bytes]] = {name: {} for name in allowed}
    identities: dict[str, dict] = {name: {"swift": {}, "cc": {}} for name in allowed}
    selected: set[str] = set()
    output_base = output_base.resolve(strict=True)
    for line in paths:
        relative = Path(line.strip())
        if not relative.parts or relative.parts[0] != "bazel-out" or ".." in relative.parts:
            continue
        marker = next((part for part in relative.parts if part.startswith(REPOSITORY_PREFIX)), None)
        if marker is None:
            continue
        repository = marker.removeprefix("+dependencies+")
        if repository not in allowed:
            continue
        name = relative.name
        if not name.endswith(OUTPUT_SUFFIXES):
            continue
        if not (execution_root / relative).exists() and name.endswith(".swiftdoc"):
            continue
        candidate = (execution_root / relative).resolve(strict=True)
        if not candidate.is_file() or not candidate.is_relative_to(output_base):
            raise ValueError(f"configured output escaped Bazel output base: {line}")
        content = candidate.read_bytes()
        recorded = build_outputs.get(relative.as_posix())
        if recorded is None or recorded != {"sha256": digest(content), "size": len(content)}:
            raise ValueError(f"configured binary differs from successful aspect output: {line}")
        selected.add(relative.as_posix())
        # Bazel's `.lo` output is an ar archive, but cc_import accepts only
        # `.a`/`.lib` labels. Keep its bytes and expose a conventional suffix.
        key = "binary/" + (name.removesuffix(".lo") + ".a" if name.endswith(".lo") else name)
        existing = files[repository].get(key)
        if existing is not None and existing != content:
            raise ValueError(f"one package has conflicting configured binary variants: {repository}/{name}")
        files[repository][key] = content
    if selected != set(build_outputs):
        raise ValueError("configured query differs from complete compiled aspect output group")
    for repository, members in files.items():
        archives = {name.removeprefix("binary/lib").removesuffix(".a"): name for name in members
                    if name.startswith("binary/lib") and name.endswith(".a")}
        identities[repository]["cc"] = {target: archive for target, archive in archives.items()
                                         if not target.endswith(".rspm.__impl")}
        identities[repository]["swift"] = {
            target: {"archive": archive,
                     "swiftmodule": "binary/" + module + ".swiftmodule",
                     "swiftdoc": "binary/" + module + ".swiftdoc"}
            for target, archive in archives.items() if target.endswith(".rspm.__impl")
            for module in [target.removesuffix(".rspm.__impl")]
        }
    return files, identities


def source_files_used_as_headers(build: str) -> set[str]:
    paths = set()
    for value in re.findall(r"\b(?:textual_hdrs|hdrs)\s*=\s*\[(.*?)\]", build, re.S):
        paths.update(re.findall(r'"([^"]+)"', value))
    return paths


def package_files(external: Path, build: str) -> dict[str, bytes]:
    members = {}
    textual_headers = source_files_used_as_headers(build)
    for path in sorted(external.rglob("*")):
        relative = path.relative_to(external)
        # Test/example/benchmark trees are never copied into the binary
        # overlay. Ignore their links before inspecting packaged metadata.
        if any(part.startswith(".git") for part in relative.parts):
            continue
        if relative.parts[0] in {"Tests", "Benchmarks", "Examples"}:
            continue
        if (relative.parts[0] in {"Plugins", "tests"} and
                relative.parts[0] not in build and not re.search(r"\bglob\s*\(", build)):
            # A generated SwiftPM package may retain build-tool plugins or
            # upstream lowercase test fixtures that no selected BUILD rule
            # references. Keep referenced or globbed trees under strict checks.
            continue
        if path.suffix in IMPLEMENTATION_EXTENSIONS and str(relative) not in textual_headers:
            # Implementations are replaced by the compiled output. Inspect
            # only files that are actually copied into the sealed overlay.
            continue
        if path.is_symlink():
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(external.resolve(strict=True)):
                raise ValueError(f"package metadata symlink leaves source tree: {path}")
            if path.is_dir():
                raise ValueError(f"package metadata contains a symlinked directory: {path}")
        if path.is_dir():
            continue
        if path.is_symlink() and not path.is_file():
            raise ValueError(f"package metadata symlink is not a file: {path}")
        if not path.is_file():
            raise ValueError(f"unexpected package input type: {path}")
        if relative.name == "BUILD.bazel":
            continue
        members[str(relative)] = path.read_bytes()
    return members


def package_overlay(repository: str, source: Path, outputs: dict[str, bytes],
                    identity: dict) -> dict[str, bytes]:
    original = (source / "BUILD.bazel").read_text()
    modules = rule_modules(original)
    for target in list(identity["swift"]):
        if target not in modules:
            identity["cc"][target] = identity["swift"].pop(target)["archive"]
    for target, artifact in identity["swift"].items():
        module = modules.get(target)
        if not module:
            raise ValueError(f"compiled Swift target absent from generated BUILD: {repository}/{target}")
        artifact["swiftmodule"] = "binary/" + module + ".swiftmodule"
        artifact["swiftdoc"] = "binary/" + module + ".swiftdoc"
        if artifact["swiftdoc"] not in outputs:
            del artifact["swiftdoc"]
        if any(member not in outputs for member in artifact.values()):
            raise ValueError(f"compiled Swift target lacks an interface/archive: {repository}/{target}")
    if not identity["swift"] and not identity["cc"]:
        raise ValueError(f"package has no compiled binary output: {repository}")
    source_files = package_files(source, original)
    c_src_headers = compiled_c_header_sources(original, set(identity["cc"]))
    # Binary members supersede any source-tree file with the same name.
    if set(source_files) & set(outputs):
        raise ValueError(f"binary output collides with package metadata: {repository}")
    prebuilt = ('load("@//Tools/bazel/artifacts:foundation_import.bzl", '
                '"import_swift_library", "import_cc_library")\n'
                'SWIFT = ' + json.dumps(identity["swift"], sort_keys=True) + '\n'
                'C = ' + json.dumps(identity["cc"], sort_keys=True) + '\n'
                'C_HEADER_ONLY = ' + json.dumps(sorted(header_only_c_targets(original))) + '\n'
                'C_SRC_HEADERS = ' + json.dumps(c_src_headers, sort_keys=True) + '\n'
                'def foundation_swift_library(**kwargs):\n'
                '    import_swift_library(SWIFT, **kwargs)\n'
                'def foundation_cc_library(**kwargs):\n'
                '    import_cc_library(C, C_HEADER_ONLY, C_SRC_HEADERS, **kwargs)\n')
    return {**source_files, **outputs,
            "BUILD.bazel": transformed_build(original).encode(),
            "prebuilt.bzl": prebuilt.encode()}


def archive_bytes(members: dict[str, bytes], manifest: dict) -> bytes:
    group = manifest["group"]
    contents = {**members, group + "/layer.json":
                (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()}
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0, filename="") as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for name, content in sorted(contents.items()):
                info = tarfile.TarInfo(name)
                info.size, info.mtime, info.uid, info.gid = len(content), 0, 0, 0
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(content))
    return out.getvalue()


def inspect(path: Path, expected_sha: str | None = None) -> dict:
    raw = path.read_bytes()
    archive_sha = digest(raw)
    if expected_sha and archive_sha != expected_sha:
        raise ValueError("foundational archive checksum differs from lock")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        names = tar.getnames()
        if any(any(component in {"", ".", ".."} for component in name.split("/")) for name in names):
            raise ValueError("foundational archive member is not a canonical POSIX path")
        if len(names) != len(set(names)) or any(not m.isfile() or m.issym() or m.islnk() for m in tar):
            raise ValueError("foundational archive has duplicate or non-file members")
        roots = {name.split("/", 1)[0] for name in names}
        if len(roots) != 1 or not roots.issubset(GROUPS) or any(".." in Path(n).parts for n in names):
            raise ValueError("foundational archive member escapes its package")
        contents = {name: tar.extractfile(name).read() for name in names}
    group = roots.pop()
    manifest = json.loads(contents.pop(group + "/layer.json"))
    if (manifest.get("schema") != 1 or manifest.get("group") != group
            or manifest.get("profile") not in {"enhanced", "stock"}):
        raise ValueError("foundational archive has the wrong profile")
    packages = manifest.get("packages", {})
    selected = sorted(f"@{row['repository']}//:{target}"
                      for row in packages.values()
                      for target in row.get("swiftTargets", []) + row.get("cTargets", []))
    if (manifest.get("configuredRoot") != PRODUCT_ROOT or not selected or
            manifest.get("selectedTargets") != selected or len(selected) != len(set(selected))):
        raise ValueError("archive lacks the exact configured product target set")
    if set(contents) != set(manifest.get("files", {})):
        raise ValueError("foundational archive manifest is incomplete")
    output_proof = manifest.get("compiledOutputBEP", {})
    if (not isinstance(output_proof, dict) or
            not SHA.fullmatch(output_proof.get("eventsSHA256", "")) or
            not isinstance(output_proof.get("files"), dict) or not output_proof["files"] or
            any(not isinstance(path, str) or not path.startswith("bazel-out/") or
                any(part in {"", ".", ".."} for part in path.split("/")) or
                not isinstance(row, dict) or not SHA.fullmatch(row.get("sha256", "")) or
                type(row.get("size")) is not int or row["size"] < 0
                for path, row in output_proof["files"].items())):
        raise ValueError("archive lacks exact compiled build output evidence")
    produced: dict[str, dict] = {}
    for path, record in output_proof["files"].items():
        marker = "/external/+dependencies+"
        if marker not in "/" + path:
            raise ValueError("compiled BEP output has no package repository")
        repository, separator, relative = ("/" + path).split(marker, 1)[1].partition("/")
        if not separator or not repository.startswith("swiftpkg_") or "/" in relative:
            raise ValueError("compiled BEP output has an unsupported package path")
        binary = relative.removesuffix(".lo") + ".a" if relative.endswith(".lo") else relative
        member = group + "/" + repository + "/binary/" + binary
        if member in produced and produced[member] != record:
            raise ValueError("compiled BEP output has conflicting binary variants")
        produced[member] = record
    archive_binaries = {name for name in manifest.get("files", {}) if "/binary/" in name}
    if (set(produced) != archive_binaries or
            any(record != {"sha256": manifest["files"][member], "size": len(contents[member])}
                for member, record in produced.items())):
        raise ValueError("sealed binaries differ from the successful compiled BEP outputs")
    if group == "container-sdk":
        graph = manifest.get("sourceGraph", {})
        receipt = graph.get("receipt", {}) if isinstance(graph, dict) else {}
        encoded = (json.dumps(receipt, sort_keys=True) + "\n").encode()
        if (receipt.get("schema") != 1 or receipt.get("profile") != manifest["profile"] or
                graph.get("receiptSHA256") != digest(encoded) or
                not SHA.fullmatch(receipt.get("loadedContainerManifestSHA256", "")) or
                not SHA.fullmatch(receipt.get("loadedContainerLockSHA256", ""))):
            raise ValueError("ContainerSDK lacks its verified source graph")
    for name, sha in manifest["files"].items():
        if not SHA.fullmatch(sha) or digest(contents[name]) != sha:
            raise ValueError(f"foundational archive member changed: {name}")
    return {"archiveSHA256": archive_sha, "manifest": manifest}


def proof_lock(receipt_path: Path, root: Path, output: Path) -> dict:
    receipt = json.loads(receipt_path.read_text())
    observed = inspect(Path(receipt["archive"]), receipt["archiveSHA256"])
    if observed["manifest"] != receipt["manifest"] or not observed["manifest"]["developmentProof"]:
        raise ValueError("development lock requires an actual proof archive")
    if output.exists():
        raise ValueError("proof lock already exists")
    profile = observed["manifest"]["profile"]
    manifest = observed["manifest"]
    group = manifest["group"]
    lock = {"schema": 1, "developmentProof": True, "group": group,
            "profile": profile, "archiveSHA256": observed["archiveSHA256"],
            "sourcePins": group_pins(source_pins(root, profile), group),
            "configuredRoot": manifest["configuredRoot"],
            "selectedTargets": manifest["selectedTargets"],
            "compiledOutputBEP": manifest["compiledOutputBEP"],
            "sourceGraph": manifest.get("sourceGraph"),
            "reachedSources": {name: {key: row[key] for key in ("sourceCommit", "sourceLocation", "repository")}
                               for name, row in manifest["packages"].items()},
            "lower": manifest["lower"], "toolchain": manifest["toolchain"],
            "recipeSHA256": manifest["recipeSHA256"]}
    output.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    return lock


def verify_consumer(lock_path: Path, root: Path, mirror: Path | None,
                    environment: dict[str, str], profile: str = "enhanced",
                    group: str = "foundation") -> dict:
    lock = json.loads(lock_path.read_text())
    if (lock.get("schema") != 1 or not SHA.fullmatch(lock.get("archiveSHA256", ""))
            or lock.get("group") != group or lock.get("profile") != profile
            or lock.get("configuredRoot") != PRODUCT_ROOT
            or not isinstance(lock.get("selectedTargets"), list) or not lock["selectedTargets"]
            or not isinstance(lock.get("compiledOutputBEP"), dict)
            or (group != "container-sdk" and
                lock.get("sourceGraph") is not None)
            or lock.get("sourcePins") != group_pins(source_pins(root, profile), group)
            or lock.get("lower") != lower_records(root, profile, group)):
        raise ValueError("foundational bundle differs from enhanced source pins or lower layer")
    if (lock.get("recipeSHA256") != recipe_identity(root, profile, group)
            and not _legacy_recipe_compatible(root, lock, profile, group)):
        raise ValueError("compiled layer recipe differs from the selected graph")
    packages = lock.get("reachedSources", {})
    records = source_records(root, profile)
    if group == "container-sdk":
        verify_source_graph(root, profile, lock.get("sourceGraph"))
    if (not isinstance(packages, dict) or not packages or
            any(name not in lock["sourcePins"] or row.get("sourceCommit") != lock["sourcePins"][name]
                or row.get("sourceLocation") != records[name]["location"]
                or row.get("repository") != repo_name(name) for name, row in packages.items())):
        raise ValueError("foundational bundle package inventory differs from locked sources")
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("foundational bundle requires Apple silicon macOS")
    swiftc = Path(command("/usr/bin/xcrun", "-f", "swiftc", environment=environment))
    sdk = Path(command("/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-path", environment=environment))
    tools = lock.get("toolchain", {})
    if (tools.get("swiftcSHA256") != file_digest(swiftc)
            or tools.get("sdkSettingsSHA256") != file_digest(sdk / "SDKSettings.json")
            or tools.get("sdkVersion") != command("/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-version",
                                                    environment=environment)
            or tools.get("xcodeVersion") != command("/usr/bin/xcodebuild", "-version", environment=environment)
            or tools.get("bazelVersion") != (root / ".bazelversion").read_text().strip()):
        raise ValueError("foundational bundle compiler, SDK, Xcode or Bazel differs from producer")
    if lock.get("developmentProof") and mirror is None:
        raise ValueError("development bundle requires an explicit local mirror")
    if not lock.get("developmentProof"):
        expected_tag = f"layer-{group}-{profile}-{lock['archiveSHA256'][:20]}"
        if (lock.get("repository") != GROUP_REPOSITORIES[group] or
                lock.get("tag") != expected_tag or
                not re.fullmatch(r"[0-9a-f]{40}", lock.get("targetCommit", "")) or
                not re.fullmatch(r"[0-9a-f]{40}", lock.get("producerCommit", "")) or
                (group != "foundation" and
                 lock["targetCommit"] != lock["sourcePins"][next(iter(GROUP_PACKAGES[group]))]) or
                not isinstance(lock.get("asset"), str) or not lock["asset"].endswith(".tar.gz") or
                not isinstance(lock.get("evidenceAsset"), str) or
                not SHA.fullmatch(lock.get("evidenceSHA256", ""))):
            raise ValueError("published package layer lacks exact release and sidecar identity")
    if mirror is not None:
        if not mirror.is_absolute() or mirror.is_symlink() or not mirror.is_file():
            raise ValueError("foundational bundle mirror must be an absolute regular file")
        observed = inspect(mirror, lock["archiveSHA256"])
        manifest = observed["manifest"]
        reached = {name: {key: row[key] for key in ("sourceCommit", "sourceLocation", "repository")}
                   for name, row in manifest["packages"].items()}
        if (manifest.get("group") != group or manifest.get("profile") != profile
                or manifest.get("platform") != "darwin-arm64"
                or manifest.get("configuration") != "opt" or manifest.get("developmentProof") != lock.get("developmentProof")
                or manifest.get("configuredRoot") != lock["configuredRoot"]
                or manifest.get("selectedTargets") != lock["selectedTargets"]
                or manifest.get("compiledOutputBEP") != lock["compiledOutputBEP"]
                or manifest.get("sourceGraph") != lock.get("sourceGraph")
                or manifest.get("lower") != lock["lower"] or manifest.get("toolchain") != tools
                or manifest.get("recipeSHA256") != lock["recipeSHA256"] or reached != packages):
            raise ValueError("foundational bundle mirror manifest differs from compact lock")
    return lock


PRODUCT_ROOT = "//:products"
RETAINED = Path.home() / "Library/Application Support/ContainerFamily/retained/devcontainer"
MIRROR_ENV = {"argument-parser": "DEVCONTAINER_ARGUMENT_PARSER_LAYER_MIRROR",
              "foundation": "DEVCONTAINER_FOUNDATION_LAYER_MIRROR",
              "containerization": "DEVCONTAINER_CONTAINERIZATION_LAYER_MIRROR",
              "engine-api": "DEVCONTAINER_ENGINE_API_LAYER_MIRROR"}


def source_snapshot(root: Path) -> dict:
    tooling = root / "Tools/bazel"
    return json.loads(command("/usr/bin/python3", str(tooling / "input_identity.py"),
                              str(root), "--tooling", str(tooling), cwd=root))


CANCELLATION_SIGNALS = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)


@contextmanager
def cancellation_handlers():
    previous = {number: signal.getsignal(number) for number in CANCELLATION_SIGNALS}

    def cancel(number, _frame):
        for selected in CANCELLATION_SIGNALS:
            signal.signal(selected, signal.SIG_IGN)
        raise RuntimeError("compiled producer received " + signal.Signals(number).name)

    try:
        for number in CANCELLATION_SIGNALS:
            signal.signal(number, cancel)
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


@contextmanager
def ignore_cancellation():
    previous = {number: signal.getsignal(number) for number in CANCELLATION_SIGNALS}
    try:
        for number in CANCELLATION_SIGNALS:
            signal.signal(number, signal.SIG_IGN)
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def runner_command(root: Path, operation: str, flags: list[str], *targets: str,
                   environment: dict[str, str], logs: Path, name: str,
                   timeout: float | None = None) -> tuple[str, Path | None]:
    stdout = logs / (name + ".stdout.log")
    stderr = logs / (name + ".stderr.log")
    with stdout.open("xb") as out, stderr.open("xb") as err:
        process = None
        with cancellation_handlers():
            try:
                previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, CANCELLATION_SIGNALS)
                try:
                    process = subprocess.Popen(
                        [str(root / "Tools/bazel/run.sh"), operation, *flags, *targets],
                        cwd=root, env=environment, stdout=out, stderr=err,
                        start_new_session=True,
                        preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask))
                finally:
                    signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
                status = process.wait(timeout=timeout or (3600 if operation == "build" else 600))
                if session_members(process.pid):
                    raise RuntimeError("producer launcher left owned descendants after completion")
            except BaseException:
                if process is not None:
                    with ignore_cancellation():
                        terminate_session(process)
                raise
    error_text = stderr.read_text(errors="replace")
    if status:
        raise RuntimeError(f"{operation} failed with status {status}; raw logs retained at {logs}: "
                           + error_text[-2000:])
    evidence = re.findall(r"^Bazel evidence: (/.+)$", error_text, re.M)
    if operation == "info":
        if evidence:
            raise ValueError("diagnostic info unexpectedly wrote build evidence")
        return stdout.read_text().strip(), None
    if len(evidence) != 1:
        raise ValueError(f"{operation} lacks one retained launcher invocation")
    return stdout.read_text(), Path(evidence[0])


def admitted_invocation(invocation: Path, source: dict, operation: str,
                        flags: list[str], target: str) -> None:
    before = json.loads((invocation / "inputs-before.json").read_text())
    after = json.loads((invocation / "inputs-after.json").read_text())
    outcome = json.loads((invocation / "outcome.json").read_text())
    events = [json.loads(line) for line in (invocation / "events.json").read_text().splitlines()]
    commands = [row["unstructuredCommandLine"]["args"] for row in events
                if "unstructuredCommandLine" in row]
    finished = [row["finished"] for row in events if "finished" in row]
    options = [row["optionsParsed"]["cmdLine"] for row in events if "optionsParsed" in row]
    if (before != source or after != source or
            outcome.get("bazel_exit_code") != 0 or outcome.get("validation_exit_code") != 0 or
            len(commands) != 1 or commands[0][0] != operation or
            not set(flags).issubset(commands[0]) or target not in commands[0] or
            len(options) != 1 or "--compilation_mode=opt" not in options[0] or
            len(finished) != 1 or finished[0].get("overallSuccess") is not True or
            finished[0].get("exitCode", {}).get("name") != "SUCCESS"):
        raise ValueError("configured layer invocation did not retain exact source, flags and success")


def compiled_build_outputs(invocation: Path, group: str) -> dict[str, dict]:
    """Read only the successful configured aspect's digest-bearing BEP output group."""
    events = [json.loads(line) for line in (invocation / "events.json").read_text().splitlines()]
    commands = [row["unstructuredCommandLine"]["args"] for row in events
                if "unstructuredCommandLine" in row]
    aspect = f"//Tools/bazel/artifacts:compiled_outputs.bzl%{group.replace('-', '_')}_outputs"
    if (len(commands) != 1 or
            f"--aspects={aspect}" not in commands[0] or
            "--output_groups=layer_compiled" not in commands[0]):
        raise ValueError("configured producer did not request the exact compiled aspect")
    completed = [row for row in events if "completed" in row and
                 row.get("id", {}).get("targetCompleted", {}).get("label") == PRODUCT_ROOT]
    ordinary = [row for row in completed if not row["id"]["targetCompleted"].get("aspect")]
    chosen = [row for row in completed if row["id"]["targetCompleted"].get("aspect") == aspect]
    if (len(ordinary) != 1 or len(chosen) != 1 or
            ordinary[0]["completed"].get("success") is not True or
            chosen[0]["completed"].get("success") is not True or
            not ordinary[0]["id"]["targetCompleted"].get("configuration", {}).get("id") or
            ordinary[0]["id"]["targetCompleted"]["configuration"] !=
            chosen[0]["id"]["targetCompleted"].get("configuration")):
        raise ValueError("configured producer target or aspect was not successful")
    groups = [row for row in chosen[0]["completed"].get("outputGroup", [])
              if row.get("name") == "layer_compiled"]
    if len(groups) != 1 or groups[0].get("incomplete") or not groups[0].get("fileSets"):
        raise ValueError("compiled aspect output group is incomplete")
    sets = {}
    for row in events:
        if "namedSetOfFiles" in row:
            identity = row["id"]["namedSet"]["id"]
            if identity in sets:
                raise ValueError("duplicate compiled output set")
            sets[identity] = row["namedSetOfFiles"]
    outputs: dict[str, dict] = {}
    visited: set[str] = set()
    visiting: set[str] = set()

    def walk(identity: str) -> None:
        if identity in visiting or identity not in sets:
            raise ValueError("compiled output set is missing or cyclic")
        if identity in visited:
            return
        visiting.add(identity)
        for item in sets[identity].get("files", []):
            components = [*item.get("pathPrefix", []), item.get("name", "")]
            if (not components or any(not isinstance(component, str) or not component or
                                       "\\" in component for component in components)):
                raise ValueError("compiled output path is not canonical")
            relative = "/".join(components)
            if (not relative.startswith("bazel-out/") or
                    any(part in {"", ".", ".."} for part in relative.split("/"))):
                raise ValueError("compiled output is outside Bazel's configured outputs")
            uri = urlparse(item.get("uri", ""))
            if (uri.scheme != "file" or uri.netloc or uri.query or uri.fragment or
                    not Path(unquote(uri.path)).is_absolute() or
                    not unquote(uri.path).endswith("/" + relative)):
                raise ValueError("compiled output URI differs from configured path")
            sha, length = item.get("digest"), item.get("length")
            if (not isinstance(sha, str) or not SHA.fullmatch(sha) or
                    not isinstance(length, str) or not length.isascii() or not length.isdigit()):
                raise ValueError("compiled output lacks digest and size")
            record = {"sha256": sha, "size": int(length)}
            if relative in outputs and outputs[relative] != record:
                raise ValueError("configured output has conflicting BEP records")
            outputs[relative] = record
        for child in sets[identity].get("fileSets", []):
            walk(child["id"])
        visiting.remove(identity)
        visited.add(identity)

    for entry in groups[0]["fileSets"]:
        walk(entry["id"])
    if not outputs:
        raise ValueError("compiled aspect output group contains no files")
    return outputs


def produce(root: Path, output: Path, lower_lock: Path, development_proof: bool,
            profile: str = "enhanced", group: str = "foundation") -> dict:
    if group not in GROUPS or profile not in {"enhanced", "stock"}:
        raise ValueError("unsupported package group or profile")
    if not output.is_absolute() or output.exists() or output.is_symlink():
        raise ValueError("layer output must be a fresh absolute directory")
    source = command("git", "-C", str(root), "rev-parse", "HEAD")
    dirty = bool(command("git", "-C", str(root), "status", "--porcelain"))
    if dirty and not development_proof:
        raise ValueError("publishable layer requires a clean producer checkout")
    snapshot = source_snapshot(root)
    if snapshot.get("commit") != source or snapshot.get("dirty") != dirty:
        raise ValueError("source identity differs from Git before the producer")
    output.mkdir(parents=True, mode=0o700)
    argument_lock = root / "Tools/bazel/artifacts/argument-parser.lock.json"
    if json.loads(lower_lock.read_text()) != json.loads(argument_lock.read_text()):
        raise ValueError("producer ArgumentParser lock differs from the selected lower lock")
    records = source_records(root, profile)
    pins = {name: row["revision"] for name, row in records.items()}
    if json.loads(argument_lock.read_text()).get("manifest", {}).get("sourceCommit") != pins[PUBLISHED_LOWER]:
        raise ValueError("published ArgumentParser source differs from selected lock")
    lowers = lower_records(root, profile, group, lower_lock)
    environment = dict(os.environ)
    from artifacts.argument_parser import cached_release as cached_argument, verify_consumer as verify_argument
    argument_archive = cached_argument(argument_lock, RETAINED / "binary-layers/argument-parser")
    verify_argument(argument_lock, root, argument_archive, environment)
    environment[MIRROR_ENV["argument-parser"]] = str(argument_archive)
    for lower_group in LOWERS[group]:
        if lower_group == "argument-parser":
            continue
        from artifacts.layer_release import cached_release as cached_layer
        path = layer_lock_path(root, lower_group, profile)
        archive = cached_layer(path, RETAINED / "binary-layers" / lower_group / profile)
        verify_consumer(path, root, archive, environment, profile, lower_group)
        environment[MIRROR_ENV[lower_group]] = str(archive)
    lower_config = {"foundation": ["prebuilt-argument-parser"],
                    "containerization": ["prebuilt-foundation"],
                    "engine-api": ["prebuilt-foundation"],
                    "container-sdk": ["prebuilt-containerization", "prebuilt-engine-api"]}[group]
    flags = ["--config=release", f"--config={profile}"] + ["--config=" + name for name in lower_config]
    allowed = {repo_name(name): name for name in group_pins(pins, group)}
    selected, selection_invocation = runner_command(root, "cquery", flags,
                                                    f"deps({PRODUCT_ROOT})", "--output=label",
                                                    environment=environment, logs=output,
                                                    name="selected-targets")
    if selection_invocation is None:
        raise ValueError("configured selection has no invocation")
    admitted_invocation(selection_invocation, snapshot, "cquery", flags, f"deps({PRODUCT_ROOT})")
    configured = set()
    for line in selected.splitlines():
        for repository in allowed:
            for prefix in (f"@@+dependencies+{repository}//:",
                           f"@+dependencies+{repository}//:", f"@{repository}//:"):
                if line.startswith(prefix):
                    configured.add((repository, line[len(prefix):].split(" ", 1)[0]))
    if not configured:
        raise ValueError("configured four-product closure has no selected group targets")
    aspect = group.replace("-", "_") + "_outputs"
    build_flags = flags + [f"--aspects=//Tools/bazel/artifacts:compiled_outputs.bzl%{aspect}",
                           "--output_groups=layer_compiled"]
    # The aspect uses the configured four-product closure but requests only
    # this group's outputs, so higher package compilation and links are not
    # producer actions for a lower layer.
    _, invocation = runner_command(root, "build", build_flags, PRODUCT_ROOT,
                                   environment=environment, logs=output, name="compiled-build")
    if invocation is None:
        raise ValueError("configured producer has no invocation")
    admitted_invocation(invocation, snapshot, "build", build_flags, PRODUCT_ROOT)
    execution_root, _ = runner_command(root, "info", [], "execution_root", environment=environment,
                                       logs=output, name="execution-root")
    output_base, _ = runner_command(root, "info", [], "output_base", environment=environment,
                                    logs=output, name="output-base")
    query, query_invocation = runner_command(root, "cquery", flags,
                                              f"deps({PRODUCT_ROOT})", "--output=files",
                                              environment=environment, logs=output,
                                              name="configured-files")
    if query_invocation is None:
        raise ValueError("configured output query has no invocation")
    admitted_invocation(query_invocation, snapshot, "cquery", flags, f"deps({PRODUCT_ROOT})")
    build_outputs = compiled_build_outputs(invocation, group)
    if any(not any("/external/+dependencies+" + repository + "/" in "/" + path
                       for repository in allowed) or
           not path.endswith(OUTPUT_SUFFIXES) for path in build_outputs):
        raise ValueError("compiled aspect selected an output outside its owned group")
    binaries, identities = artifact_map(query.splitlines(), Path(execution_root), Path(output_base),
                                        allowed, build_outputs)
    members: dict[str, bytes] = {}
    packages: dict[str, dict] = {}
    selected_targets: list[str] = []
    for repository, identity in identities.items():
        binary_members = binaries[repository]
        if not binary_members:
            continue
        package = allowed[repository]
        external = Path(output_base) / "external" / ("+dependencies+" + repository)
        overlay = package_overlay(repository, external, binary_members, identity)
        prefix = group + "/" + repository + "/"
        members.update({prefix + name: value for name, value in overlay.items()})
        swift_targets = sorted(identity["swift"])
        c_targets = sorted(identity["cc"])
        if any((repository, name) not in configured for name in swift_targets + c_targets):
            raise ValueError("compiled binary target was absent from the analyzed product closure")
        selected_targets.extend(f"@{repository}//:{name}" for name in swift_targets + c_targets)
        packages[package] = {"sourceCommit": pins[package],
                             "sourceLocation": records[package]["location"],
                             "repository": repository, "swiftTargets": swift_targets,
                             "cTargets": c_targets,
                             "metadata": sorted(name for name in overlay if name not in binary_members)}
    required = {"foundation": {"swift-log"}, "containerization": {"containerization"},
                "engine-api": {"container-engine-api"}, "container-sdk": {"container"}}[group]
    if not required.issubset(packages):
        raise ValueError("configured four-product closure omitted required source packages")
    if source_snapshot(root) != snapshot:
        raise ValueError("source changed during configured output export")
    tool = Path(command("/usr/bin/xcrun", "-f", "swiftc", environment=environment))
    sdk = Path(command("/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-path", environment=environment))
    manifest = {"schema": 1, "group": group, "profile": profile, "platform": "darwin-arm64",
                "configuration": "opt", "developmentProof": dirty,
                "configuredRoot": PRODUCT_ROOT, "selectedTargets": sorted(set(selected_targets)),
                "recipeSHA256": recipe_identity(root, profile, group),
                "compiledOutputBEP": {"eventsSHA256": file_digest(invocation / "events.json"),
                                      "files": build_outputs},
                "toolchain": {"swiftcSHA256": file_digest(tool),
                              "swiftVersion": command(str(tool), "--version", environment=environment),
                              "sdkSettingsSHA256": file_digest(sdk / "SDKSettings.json"),
                              "sdkVersion": command("/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-version",
                                                    environment=environment),
                              "xcodeVersion": command("/usr/bin/xcodebuild", "-version", environment=environment),
                              "bazelVersion": (root / ".bazelversion").read_text().strip()},
                "lower": lowers, "packages": packages,
                "files": {name: digest(value) for name, value in members.items()}}
    if group == "container-sdk":
        graph_path = invocation / "source-graph.json"
        if not graph_path.is_file():
            raise ValueError("ContainerSDK producer lacks the loaded source-graph receipt")
        graph = json.loads(graph_path.read_text())
        sealed_graph = {"receipt": graph, "receiptSHA256": file_digest(graph_path)}
        verify_source_graph(root, profile, sealed_graph)
        manifest["sourceGraph"] = sealed_graph
    archive = output / f"{group}-{profile}-darwin-arm64-opt.tar.gz"
    archive.write_bytes(archive_bytes(members, manifest))
    observed = inspect(archive)
    receipt = {"schema": 1, **observed, "archive": str(archive), "producerCommit": source,
               "launcherInvocation": str(invocation), "selectionInvocation": str(selection_invocation),
               "queryInvocation": str(query_invocation),
               "outcomeSHA256": file_digest(invocation / "outcome.json"),
               "producerEventsSHA256": file_digest(invocation / "events.json"),
               "selectionEventsSHA256": file_digest(selection_invocation / "events.json"),
               "queryEventsSHA256": file_digest(query_invocation / "events.json"),
               "sourceInputsSHA256": file_digest(invocation / "inputs-before.json")}
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["produce", "proof-lock"])
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--profile", choices=["enhanced", "stock"], default="enhanced")
    parser.add_argument("--group", choices=GROUPS, default="foundation")
    parser.add_argument("--lower-lock", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--development-proof", action="store_true")
    args = parser.parse_args()
    if args.action == "produce":
        if args.lower_lock is None:
            parser.error("produce requires --lower-lock")
        result = produce(args.root, args.output, args.lower_lock, args.development_proof, args.profile, args.group)
    else:
        if args.receipt is None:
            parser.error("proof-lock requires --receipt")
        result = proof_lock(args.receipt, args.root, args.output)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
