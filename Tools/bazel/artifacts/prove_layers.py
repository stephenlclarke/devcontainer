#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Verify an existing four-product build really consumed sealed compiled layers."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __package__:
    from .argument_parser import file_digest
    from .foundation import GROUPS, layer_lock_path, inspect
    from .import_argument_parser import LOCK as AP_LOCK, prepare as prepare_argument, verify_origin as verify_argument_origin
    from .import_layers import admitted, verify_origin
    from .prove_argument_parser import action_graph
else:
    from argument_parser import file_digest
    from foundation import GROUPS, layer_lock_path, inspect
    from import_argument_parser import LOCK as AP_LOCK, prepare as prepare_argument, verify_origin as verify_argument_origin
    from import_layers import admitted, verify_origin
    from prove_argument_parser import action_graph

from input_identity import source_identity, tooling_identity

ROOT = Path(__file__).resolve().parents[3]
PRODUCTS = ("//:devcontainer", "//:devcontainer-engine", "//:devcontainer-compose",
            "//:devcontainer-docker")
ROOT_TARGET = "//:products"
METADATA_ACTIONS = {"FileWrite", "TemplateExpand", "ExecutableSymlink", "SymlinkTree",
                    "RepoMappingManifest", "SourceSymlinkManifest", "Middleman"}
SEMANTIC_OPTIONS = ("--compilation_mode=", "--host_compilation_mode=", "--macos_minimum_os=",
                    "--macos_sdk_version=", "--xcode_version=", "--cpu=", "--host_cpu=",
                    "--apple_platform_type=", "--platforms=", "--host_platform=",
                    "--features=", "--host_features=", "--extra_toolchains=",
                    "--swiftcopt=", "--copt=", "--conlyopt=", "--cxxopt=", "--objcopt=",
                    "--host_copt=", "--host_conlyopt=", "--host_cxxopt=", "--linkopt=",
                    "--@build_bazel_rules_swift//swift:copt=", "--repo_env=", "--define=",
                    "--action_env=", "--host_action_env=")
MODULEMAP_PATH = re.compile(
    r"bazel-out/[^/]+/bin/external/(\+dependencies\+swiftpkg_[^/]+)/"
    r"([^/]+)\.rspm_modulemap_modulemap/_/module\.modulemap\Z")
MODULEMAP_HEADER = re.compile(r'    header "([^"\\\r\n]+)"\Z')


def action_inputs(graph: dict):
    artifacts = {row["id"]: row for row in graph["artifacts"]}
    fragments = {row["id"]: row for row in graph["pathFragments"]}
    dep_sets = {row["id"]: row for row in graph["depSetOfFiles"]}
    cached_paths: dict[int, str] = {}
    cached_sets: dict[int, set[str]] = {}

    def path_of(identity: int, active: set[int] | None = None) -> str:
        if identity in cached_paths:
            return cached_paths[identity]
        if active is None:
            active = set()
        if identity in active or identity not in fragments:
            raise ValueError("configured action graph has a broken path")
        active.add(identity)
        row = fragments[identity]
        parent = row.get("parentId")
        value = (path_of(parent, active) + "/" if parent else "") + row["label"]
        active.remove(identity)
        cached_paths[identity] = value
        return value

    def visit(identity: int, active: set[int] | None = None) -> set[str]:
        if identity in cached_sets:
            return cached_sets[identity]
        if active is None:
            active = set()
        if identity in active or identity not in dep_sets:
            raise ValueError("configured action graph has a broken input set")
        active.add(identity)
        row = dep_sets[identity]
        found = set()
        for item in row.get("directArtifactIds", []):
            if item not in artifacts:
                raise ValueError("configured action graph has a missing artifact")
            found.add(path_of(artifacts[item]["pathFragmentId"]))
        for child in row.get("transitiveDepSetIds", []):
            found.update(visit(child, active))
        active.remove(identity)
        cached_sets[identity] = found
        return found

    def inputs(action: dict) -> set[str]:
        found: set[str] = set()
        for identity in action.get("inputDepSetIds", []):
            found.update(visit(identity))
        return found

    def artifact_path(identity: int) -> str:
        if identity not in artifacts:
            raise ValueError("configured action graph has a missing artifact")
        return path_of(artifacts[identity]["pathFragmentId"])

    return inputs, artifact_path


def declared_modulemap_headers(build: bytes, name: str) -> list[str]:
    """Read only literal generated-modulemap declarations from an admitted BUILD file."""
    try:
        tree = ast.parse(build.decode("utf-8"))
    except (UnicodeDecodeError, SyntaxError) as error:
        raise ValueError("sealed BUILD has invalid modulemap declarations") from error
    matches = []
    for statement in tree.body:
        if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call) and
                isinstance(statement.value.func, ast.Name) and
                statement.value.func.id == "generate_modulemap"):
            continue
        if any(item.arg is None for item in statement.value.keywords):
            raise ValueError("sealed BUILD has expanded modulemap fields")
        fields = {item.arg: item.value for item in statement.value.keywords}
        if len(fields) != len(statement.value.keywords):
            raise ValueError("sealed BUILD has duplicate modulemap fields")
        if set(fields) & {"name", "module_name", "hdrs", "deps"} != {
                "name", "module_name", "hdrs", "deps"}:
            raise ValueError("sealed BUILD has incomplete modulemap declaration")
        try:
            rule_name = ast.literal_eval(fields["name"])
        except (ValueError, TypeError, SyntaxError) as error:
            raise ValueError("sealed BUILD has nonliteral modulemap name") from error
        if rule_name != name + ".rspm_modulemap":
            continue
        try:
            module = ast.literal_eval(fields["module_name"])
            headers = ast.literal_eval(fields["hdrs"])
            deps = ast.literal_eval(fields["deps"])
        except (ValueError, TypeError, SyntaxError) as error:
            raise ValueError("sealed BUILD has nonliteral modulemap fields") from error
        if (module != name or not isinstance(headers, list) or not headers or
                not all(isinstance(item, str) and item and
                        item == posixpath.normpath(item) and not item.startswith("/") and
                        ".." not in PurePosixPath(item).parts for item in headers) or
                len(headers) != len(set(headers)) or deps != []):
            raise ValueError("sealed BUILD has unsupported modulemap rule")
        matches.append(headers)
    if len(matches) != 1:
        raise ValueError("sealed BUILD lacks one exact modulemap rule")
    return matches[0]


def generated_modulemap(path: str, graph: dict, targets: dict, inputs_of,
                        artifact_path, sealed: dict, imported: dict,
                        execution_root: Path) -> dict:
    """Admit one owned FileWrite map whose exact headers come from its sealed BUILD."""
    match = MODULEMAP_PATH.fullmatch(path)
    if match is None:
        raise ValueError("configured action used an unsupported imported path: " + path)
    repository, name = match.groups()
    if repository not in imported:
        raise ValueError("generated modulemap has an unadmitted repository: " + path)
    owner = "@@" + repository + "//:" + name + ".rspm_modulemap"
    producers = [action for action in graph["actions"]
                 if path in [artifact_path(identity) for identity in action.get("outputIds", [])]]
    if len(producers) != 1:
        raise ValueError("generated modulemap lacks one exact producer: " + path)
    producer = producers[0]
    outputs = producer.get("outputIds", [])
    artifacts = {item["id"]: item for item in graph["artifacts"]}
    if (targets.get(producer["targetId"]) != owner or producer["mnemonic"] != "FileWrite" or
            len(outputs) != 1 or producer.get("primaryOutputId") != outputs[0] or
            artifacts[outputs[0]].get("isTreeArtifact") is True or inputs_of(producer) or
            producer.get("arguments") or producer.get("environmentVariables") or
            not re.fullmatch(r"[0-9a-f]{64}", producer.get("actionKey", ""))):
        raise ValueError("generated modulemap has an invalid owned FileWrite action: " + path)
    build_path = "external/" + repository + "/BUILD.bazel"
    build = execution_root / build_path
    if (build_path not in sealed or not build.is_file() or
            file_digest(build) != sealed[build_path]["sha256"]):
        raise ValueError("generated modulemap lacks its sealed BUILD bytes: " + path)
    declared = declared_modulemap_headers(build.read_bytes(), name)
    actual = execution_root / path
    if not actual.is_file() or actual.is_symlink():
        raise ValueError("generated modulemap output is not a regular file: " + path)
    try:
        content = actual.read_bytes().decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("generated modulemap has invalid UTF-8: " + path) from error
    lines = [line for line in content.splitlines() if line]
    if (not content.endswith("\n") or "\r" in content or
            lines[:1] != ['module "' + name + '" {'] or lines[-2:] != ["    export *", "}"] or
            any(line.strip() == "" for line in lines)):
        raise ValueError("generated modulemap has unsupported content: " + path)
    headers = []
    for line in lines[1:-2]:
        header = MODULEMAP_HEADER.fullmatch(line)
        if header is None or header.group(1).startswith("/"):
            raise ValueError("generated modulemap has unsupported header syntax: " + path)
        normalized = posixpath.normpath(posixpath.join(posixpath.dirname(path), header.group(1)))
        prefix = "external/" + repository + "/"
        if not normalized.startswith(prefix):
            raise ValueError("generated modulemap header escapes its repository: " + path)
        header_name = normalized[len(prefix):]
        if header.group(1) != posixpath.relpath(prefix + header_name, posixpath.dirname(path)):
            raise ValueError("generated modulemap header has a noncanonical path: " + path)
        headers.append(header_name)
    if headers != declared:
        raise ValueError("generated modulemap headers differ from sealed BUILD: " + path)
    matched = {}
    for header in headers:
        key = "external/" + repository + "/" + header
        file = execution_root / key
        if (key not in sealed or not file.is_file() or
                file_digest(file) != sealed[key]["sha256"]):
            raise ValueError("generated modulemap uses unsealed header bytes: " + key)
        matched[key] = sealed[key]
    return {"sha256": file_digest(actual), "actionKey": producer["actionKey"],
            "owner": owner, "buildSHA256": sealed[build_path]["sha256"],
            "headers": matched}


def sealed_files(manifests: dict[str, dict], argument_manifest: dict,
                 argument_selected: Path) -> tuple[dict[str, dict], dict[str, str]]:
    """Map every admitted external repository file to its sealed content."""
    files: dict[str, dict] = {}
    repositories: dict[str, str] = {}
    for group, manifest in manifests.items():
        for package in manifest["packages"].values():
            repository = "+dependencies+" + package["repository"]
            if repository in repositories:
                raise ValueError("compiled groups claim the same Bazel repository")
            repositories[repository] = group
        for member, sha in manifest["files"].items():
            parts = PurePosixPath(member).parts
            if (len(parts) < 3 or parts[0] != group or
                    repositories.get("+dependencies+" + parts[1]) != group):
                raise ValueError("compiled manifest contains an unowned file")
            key = "external/+dependencies+" + "/".join(parts[1:])
            if key in files:
                raise ValueError("compiled manifests contain a duplicate file")
            files[key] = {"group": group, "sha256": sha}
    ap = "+dependencies+swiftpkg_swift_argument_parser"
    if ap in repositories:
        raise ValueError("ArgumentParser is claimed by two binary layers")
    repositories[ap] = "argument-parser"
    for name, sha in argument_manifest["files"].items():
        files["external/" + ap + "/" + name] = {"group": "argument-parser", "sha256": sha}
    for name in ("BUILD.bazel", "REPO.bazel"):
        files["external/" + ap + "/" + name] = {
            "group": "argument-parser", "sha256": file_digest(argument_selected / name)}
    return files, repositories


def verify_actions(graph: dict, manifests: dict[str, dict], argument_manifest: dict,
                   argument_selected: Path, execution_root: Path) -> dict:
    if not execution_root.is_absolute() or not execution_root.is_dir():
        raise ValueError("configured execution root is not an absolute directory")
    targets = {row["id"]: row["label"] for row in graph["targets"]}
    sealed, imported = sealed_files(manifests, argument_manifest, argument_selected)
    links: dict[str, set[str]] = {}
    inputs_of, artifact_path = action_inputs(graph)
    imported_inputs: set[str] = set()
    metadata_actions: dict[str, int] = {}
    for action in graph["actions"]:
        label = targets.get(action["targetId"])
        if label is None:
            raise ValueError("configured action has no owner")
        repository = next((name for name in imported if label.startswith(("@@" + name + "//",
                                                                           "@" + name + "//"))), None)
        mnemonic = action["mnemonic"]
        if repository is not None:
            if mnemonic not in METADATA_ACTIONS:
                raise ValueError("imported package still compiles, archives or links: " + label + " " + mnemonic)
            metadata_actions[mnemonic] = metadata_actions.get(mnemonic, 0) + 1
        current = inputs_of(action)
        imported_inputs.update(current)
        if label in PRODUCTS and mnemonic == "CppLink":
            if label in links:
                raise ValueError("product has multiple configured link actions: " + label)
            links[label] = current
    if set(links) != set(PRODUCTS):
        raise ValueError("four-product closure lacks exact configured link actions")
    matched: dict[str, dict[str, str]] = {}
    generated: dict[str, dict] = {}
    for path in imported_inputs:
        parts = PurePosixPath(path).parts
        if any(part in imported for part in parts) and (len(parts) < 3 or parts[0] != "external"):
            generated[path] = generated_modulemap(path, graph, targets, inputs_of,
                                                  artifact_path, sealed, imported, execution_root)
            continue
        if len(parts) < 3 or parts[0] != "external" or parts[1] not in imported:
            continue
        if ".." in parts or path not in sealed:
            raise ValueError("configured action consumed an unsealed imported file: " + path)
        actual = execution_root / path
        if not actual.is_file() or file_digest(actual) != sealed[path]["sha256"]:
            raise ValueError("configured action imported different bytes: " + path)
        matched[path] = sealed[path]
    linked = set().union(*links.values())
    archives = {path: matched[path] for path in linked if path in matched and
                path.endswith((".a", ".lo"))}
    ap_archives = {"external/+dependencies+swiftpkg_swift_argument_parser/" + name
                   for name in argument_manifest["files"] if name.endswith(".a")}
    if len(ap_archives) != 2 or not ap_archives.issubset(archives):
        raise ValueError("four-product links omit a published ArgumentParser archive")
    required = set(manifests) | {"argument-parser"}
    if {row["group"] for row in archives.values()} != required:
        raise ValueError("four-product links omit a released compiled group")
    return {"products": list(PRODUCTS), "matchedInputs": matched,
            "linkedArchives": archives,
            "generatedModulemaps": generated,
            "importedMetadataActions": metadata_actions, "configuredActionCount": len(graph["actions"])}


def admitted_invocations(build: Path, query: Path, profile: str, root: Path = ROOT) -> dict:
    rows = []
    semantic_options = []
    for directory, operation in ((build, "build"), (query, "aquery")):
        before = json.loads((directory / "inputs-before.json").read_text())
        after = json.loads((directory / "inputs-after.json").read_text())
        outcome = json.loads((directory / "outcome.json").read_text())
        events = [json.loads(line) for line in (directory / "events.json").read_text().splitlines()]
        commands = [item["unstructuredCommandLine"]["args"] for item in events
                    if "unstructuredCommandLine" in item]
        parsed = [item["optionsParsed"] for item in events if "optionsParsed" in item]
        finished = [item["finished"] for item in events if "finished" in item]
        if len(parsed) != 1:
            raise ValueError("compiled consumer lacks exact parsed build options")
        explicit = parsed[0]["explicitCmdLine"]
        effective = parsed[0]["cmdLine"]
        configs = [item for item in explicit if item.startswith("--config=")]
        requested = [f"--config={profile}", "--config=release", "--config=prebuilt-container-sdk"]
        layer_markers = {"DEVCONTAINER_ARGUMENT_PARSER_LAYER", "DEVCONTAINER_FOUNDATION_LAYER",
                         "DEVCONTAINER_CONTAINERIZATION_LAYER", "DEVCONTAINER_ENGINE_API_LAYER",
                         "DEVCONTAINER_CONTAINER_SDK_LAYER"}
        effective_layers = {name: [item for item in effective if item.startswith("--repo_env=" + name + "=")][-1:]
                            for name in layer_markers}
        effective_profile = [item for item in effective
                             if item.startswith("--repo_env=DEVCONTAINER_RUNTIME_PROFILE=")][-1:]
        if (configs != requested or
                any(item.startswith(("--output_groups", "--aspects", "--aspect_deps",
                                     "--swiftcopt", "--copt", "--host_copt",
                                     "--@build_bazel_rules_swift//swift:copt")) or
                    item in {"--nobuild", "--build=false"} for item in explicit) or
                (operation == "build" and any(item in {"--nobuild", "--build=false"}
                                              for item in effective)) or
                (operation == "aquery" and "--build=false" in effective) or
                [item for item in effective if item.startswith("--compilation_mode=")][-1:] !=
                ["--compilation_mode=opt"] or
                effective_profile != [f"--repo_env=DEVCONTAINER_RUNTIME_PROFILE={profile}"] or
                any(effective_layers[name] != ["--repo_env=" + name + "=prebuilt"]
                    for name in layer_markers)):
            raise ValueError("compiled consumer has contradictory or ineffective configuration")
        semantic_options.append({"configs": configs,
                                 "overrides": sorted(item for item in explicit
                                                     if item.startswith("--override_repository=")),
                                 "effective": sorted(item for item in effective
                                                     if item.startswith(SEMANTIC_OPTIONS))})
        if (before != after or outcome.get("bazel_exit_code") != 0 or
                outcome.get("validation_exit_code") != 0 or
                len(commands) != 1 or commands[0][0] != operation or
                f"--config={profile}" not in commands[0] or "--config=release" not in commands[0] or
                "--config=prebuilt-container-sdk" not in commands[0] or
                (ROOT_TARGET if operation == "build" else "deps(" + ROOT_TARGET + ")") not in commands[0] or
                len(finished) != 1 or finished[0].get("overallSuccess") is not True):
            raise ValueError("compiled consumer lacks same-profile successful source-bound invocations")
        if operation == "build":
            completed = [item["completed"] for item in events
                         if item.get("id", {}).get("targetCompleted", {}).get("label") == ROOT_TARGET]
            if (len(completed) != 1 or completed[0].get("success") is not True or
                    not any(row.get("name") == "default" and row.get("fileSets")
                            for row in completed[0].get("outputGroup", []))):
                raise ValueError("four-product build BEP omits its default executable outputs")
        rows.append(before)
    current = source_identity(root)
    current["tooling"] = tooling_identity(root / "Tools/bazel", root)
    if (rows[0] != rows[1] or rows[0] != current or semantic_options[0] != semantic_options[1] or
            not re.fullmatch(r"[0-9a-f]{40}", rows[0].get("commit", ""))):
        raise ValueError("compiled build and action query have different source/tooling snapshots")
    return {"source": rows[0]["commit"], "dirty": rows[0]["dirty"],
            "inputsSHA256": file_digest(build / "inputs-before.json"),
            "buildEventsSHA256": file_digest(build / "events.json"),
            "queryEventsSHA256": file_digest(query / "events.json")}


def verified_aquery_stdout(query: Path, raw_aquery: Path) -> str:
    """Bind the decoded graph to stdout captured by the leased Bazel invocation."""
    captured = query / "aquery.stdout.log"
    if (not raw_aquery.is_file() or not captured.is_file() or
            raw_aquery.read_bytes() != captured.read_bytes()):
        raise ValueError("configured graph differs from leased aquery stdout")
    return file_digest(captured)


def prove(build: Path, query: Path, raw_aquery: Path, output_base: Path, execution_root: Path,
          profile: str, output: Path, root: Path = ROOT) -> dict:
    if output.exists() or not output.is_absolute():
        raise ValueError("compiled consumer proof needs a fresh absolute output path")
    if (not output_base.is_absolute() or not output_base.is_dir() or
            execution_root.resolve(strict=True) != (output_base / "execroot/_main").resolve(strict=True)):
        raise ValueError("configured execution root differs from the selected Bazel output base")
    query_stdout_sha = verified_aquery_stdout(query, raw_aquery)
    source = admitted_invocations(build, query, profile, root)
    argument = prepare_argument(root)
    verify_argument_origin(output_base, Path(argument["selected"]))
    groups = admitted(root, profile, set(GROUPS))
    verify_origin(output_base, groups)
    manifests = {group: selected["manifest"] for group, selected in groups.items()}
    graph = verify_actions(action_graph(raw_aquery.read_text()), manifests,
                           json.loads(AP_LOCK.read_text())["manifest"],
                           Path(argument["selected"]), execution_root)
    receipt = {"schema": 1, "profile": profile, "source": source,
               "developmentProof": source["dirty"], "releaseQualified": False,
               "locks": {group: file_digest(layer_lock_path(root, group, profile)) for group in GROUPS},
               "archives": {group: inspect(Path(row["archive"]))["archiveSHA256"]
                            for group, row in groups.items()},
               "sourceGraph": manifests["container-sdk"]["sourceGraph"],
               "argumentParserLockSHA256": file_digest(AP_LOCK),
               "actionGraphSHA256": file_digest(raw_aquery),
               "queryStdoutSHA256": query_stdout_sha, "graph": graph}
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-invocation", type=Path, required=True)
    parser.add_argument("--query-invocation", type=Path, required=True)
    parser.add_argument("--aquery-json", type=Path, required=True)
    parser.add_argument("--output-base", type=Path, required=True)
    parser.add_argument("--execution-root", type=Path, required=True)
    parser.add_argument("--profile", choices=("enhanced", "stock"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prove(args.build_invocation, args.query_invocation, args.aquery_json,
                           args.output_base, args.execution_root, args.profile, args.output), sort_keys=True))


if __name__ == "__main__":
    main()
