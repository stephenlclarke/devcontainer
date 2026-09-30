#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Build one CLI against the published ArgumentParser archive and inspect its actions."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

if __package__:
    from .import_argument_parser import LOCK, ROOT, prepare, sha256
else:
    from import_argument_parser import LOCK, ROOT, prepare, sha256

RUNNER = ROOT / "Tools/bazel/run.sh"
FLAGS = ("--config=release", "--config=prebuilt-argument-parser")
TARGET = "//:devcontainer"
AP_REPO = "+dependencies+swiftpkg_swift_argument_parser"
ARCHIVES = ("libArgumentParser.rspm.__impl.a", "libArgumentParserToolInfo.rspm.__impl.a")


def run(output: Path, name: str, command: str, *arguments: str) -> tuple[Path, str]:
    result = subprocess.run([str(RUNNER), command, *FLAGS, *arguments], cwd=ROOT,
                            text=True, capture_output=True, timeout=1200)
    (output / (name + ".stdout.log")).write_text(result.stdout)
    (output / (name + ".stderr.log")).write_text(result.stderr)
    if result.returncode:
        raise ValueError(f"{name} failed with exit {result.returncode}; retained raw logs")
    matches = re.findall(r"Bazel evidence: (/[^\n]+)", result.stderr)
    if len(matches) != 1:
        raise ValueError(f"{name} lacks one retained Bazel invocation")
    return Path(matches[0]), result.stdout


def action_graph(text: str) -> dict:
    start = text.find("{")
    if start < 0:
        raise ValueError("configured aquery has no JSON graph")
    graph, _ = json.JSONDecoder().raw_decode(text[start:])
    for key in ("actions", "targets", "artifacts", "depSetOfFiles", "pathFragments"):
        if not isinstance(graph.get(key), list):
            raise ValueError("configured aquery lacks an action inventory")
    return graph


def verify_actions(graph: dict, selected: Path, manifest: dict) -> dict:
    targets = {row["id"]: row["label"] for row in graph["targets"]}
    artifacts = {row["id"]: row for row in graph["artifacts"]}
    fragments = {row["id"]: row for row in graph["pathFragments"]}
    dep_sets = {row["id"]: row for row in graph["depSetOfFiles"]}

    def path_of(identity: int) -> str:
        parts, visited = [], set()
        while identity:
            if identity in visited or identity not in fragments:
                raise ValueError("configured aquery has a broken path")
            visited.add(identity)
            row = fragments[identity]
            parts.append(row["label"])
            identity = row.get("parentId")
        return "/".join(reversed(parts))

    def inputs(action: dict) -> set[str]:
        found, visited = set(), set()
        def visit(identity: int) -> None:
            if identity in visited or identity not in dep_sets:
                if identity not in dep_sets:
                    raise ValueError("configured aquery has a missing input set")
                return
            visited.add(identity)
            row = dep_sets[identity]
            for item in row.get("directArtifactIds", []):
                if item not in artifacts:
                    raise ValueError("configured aquery has a missing input artifact")
                found.add(path_of(artifacts[item]["pathFragmentId"]))
            for child in row.get("transitiveDepSetIds", []):
                visit(child)
        for identity in action.get("inputDepSetIds", []):
            visit(identity)
        return found

    imported_actions, links = {}, []
    for action in graph["actions"]:
        label = targets.get(action["targetId"])
        if label is None:
            raise ValueError("configured aquery action has no owner")
        if label.startswith("@@" + AP_REPO + "//"):
            mnemonic = action["mnemonic"]
            if mnemonic not in {"FileWrite", "TemplateExpand", "ExecutableSymlink", "SymlinkTree",
                                "RepoMappingManifest", "SourceSymlinkManifest", "Middleman"}:
                raise ValueError("published ArgumentParser source action remains reachable: " + mnemonic)
            imported_actions[mnemonic] = imported_actions.get(mnemonic, 0) + 1
        if label == TARGET and action["mnemonic"] == "CppLink":
            links.append(inputs(action))
    if len(links) != 1:
        raise ValueError("devcontainer CLI lacks one configured link action")
    matched = {}
    for archive in ARCHIVES:
        action_input = f"external/{AP_REPO}/{archive}"
        if action_input not in links[0] or sha256(selected / archive) != manifest["files"][archive]:
            raise ValueError("devcontainer link lacks one sealed ArgumentParser archive: " + archive)
        matched[archive] = {"actionInput": action_input, "sha256": manifest["files"][archive]}
    return {"importedActions": imported_actions, "linkTarget": TARGET,
            "archiveInputs": matched, "configuredActionCount": len(graph["actions"])}


def verify_invocations(build: Path, query: Path) -> dict:
    """Require one unchanged source/tooling snapshot and successful retained clients."""
    rows = []
    for directory in (build, query):
        before = json.loads((directory / "inputs-before.json").read_text())
        after = json.loads((directory / "inputs-after.json").read_text())
        outcome = json.loads((directory / "outcome.json").read_text())
        if (outcome.get("bazel_exit_code") != 0 or outcome.get("validation_exit_code") != 0
                or before != after):
            raise ValueError("configured consumer has a failed or mixed-source invocation")
        rows.append(before)
    if rows[0] != rows[1] or not re.fullmatch(r"[0-9a-f]{40}", rows[0].get("commit", "")):
        raise ValueError("build and action graph use different source or tooling")
    return {"commit": rows[0]["commit"], "dirty": rows[0]["dirty"],
            "inputsSHA256": sha256(build / "inputs-before.json"),
            "buildOutcomeSHA256": sha256(build / "outcome.json"),
            "queryOutcomeSHA256": sha256(query / "outcome.json")}


def prove(output: Path) -> dict:
    if not output.is_absolute() or output.exists() or output.is_symlink():
        raise ValueError("proof output must be a fresh absolute directory")
    output.mkdir(parents=True, mode=0o700)
    admitted = prepare()
    build, _ = run(output, "build", "build", TARGET)
    events = build / "events.json"
    document = [json.loads(line) for line in events.read_text().splitlines()]
    finished = [row["finished"] for row in document if "finished" in row]
    completed = [row["completed"] for row in document if "completed" in row and
                 row.get("id", {}).get("targetCompleted", {}).get("label") == TARGET]
    if (len(finished) != 1 or finished[0].get("overallSuccess") is not True
            or len(completed) != 1 or completed[0].get("success") is not True):
        raise ValueError("devcontainer production BEP lacks a successful requested target")
    query, raw = run(output, "aquery", "aquery", "deps(" + TARGET + ")", "--output=jsonproto")
    source = verify_invocations(build, query)
    graph = verify_actions(action_graph(raw), Path(admitted["selected"]),
                           json.loads(LOCK.read_text())["manifest"])
    receipt = {"schema": 1, "source": source["commit"], "developmentProof": source["dirty"],
               "selectedProfile": "enhanced", "sourceSnapshot": source,
               "selected": admitted, "flags": list(FLAGS), "buildBEP": str(events),
               "buildBEPSHA256": sha256(events), "aqueryBEP": str(query / "events.json"),
               "aqueryBEPSHA256": sha256(query / "events.json"),
               "aqueryRawSHA256": hashlib.sha256(raw.encode()).hexdigest(), "graph": graph}
    (output / "compiled-consumer.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    print(json.dumps(prove(parser.parse_args().output), sort_keys=True))


if __name__ == "__main__":
    main()
