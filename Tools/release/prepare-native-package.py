#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Stage an authenticated native candidate; never sign or qualify a distribution.

Install this file at Tools/release/prepare-native-package.py. The CLI accepts
explicit retained and SSD roots so it does not discover SwiftPM build output.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile


PRODUCTS = frozenset({"devcontainer", "devcontainer-engine", "devcontainer-compose", "devcontainer-docker"})
LOWER_GROUPS = frozenset({"argument-parser", "foundation", "containerization", "engine-api", "container-sdk"})


def digest(data: bytes | Path) -> str:
    if isinstance(data, Path):
        value = hashlib.sha256()
        with data.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(block)
        return value.hexdigest()
    return hashlib.sha256(data).hexdigest()


def canonical(path: Path, *, existing: bool = True) -> Path:
    path = path.expanduser()
    if not path.is_absolute() or path.is_symlink() or path.resolve(strict=existing) != path:
        raise ValueError(f"path is absent or aliased: {path}")
    if existing and not path.exists():
        raise ValueError(f"path is absent: {path}")
    return path


def modules(repository: Path):
    bazel = str(repository / "Tools/bazel")
    release = str(repository / "Tools/release")
    for path in (bazel, release):
        if path not in sys.path:
            sys.path.insert(0, path)
    import layered_build  # pylint: disable=import-outside-toplevel
    import prepare_candidate  # pylint: disable=import-outside-toplevel
    import prepare_releases  # pylint: disable=import-outside-toplevel
    return layered_build, prepare_candidate, prepare_releases


def retained_bytes(database: Path, invocation: str) -> tuple[dict, dict[str, bytes]]:
    """Read exact successful invocation blobs; absence never falls back to live paths."""
    names = ("inputs-before.json", "events.json", "artifact:candidate_archive.json",
             "artifact:candidate_archive.tar.gz")
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as db:
        row = db.execute("SELECT manifest, exit_code FROM invocations WHERE id=?", (invocation,)).fetchone()
        if row is None or row[1] != 0:
            raise ValueError("candidate invocation was not retained successfully")
        manifest = json.loads(row[0])
        contents = {}
        for name in names:
            expected = manifest.get(name)
            value = db.execute("SELECT bytes FROM blobs WHERE sha256=?", (expected,)).fetchone()
            if value is None or digest(value[0]) != expected:
                raise ValueError("missing or corrupt retained candidate blob: " + name)
            contents[name] = value[0]
    return manifest, contents


def retained_compiled(database: Path, expected_events: str, expected_inputs: str,
                      invocation_path: Path, scratch: Path, repository: Path,
                      query_stdout: str | None = None) -> tuple[str, dict[str, bytes]]:
    """Recover one successful build/query from retained blobs after SSD cleanup."""
    if (invocation_path.parent != scratch / "invocations"
            or re.fullmatch(r"run\.[A-Za-z0-9]+", invocation_path.name) is None):
        raise ValueError("compiled proof names a foreign invocation directory")
    if re.fullmatch(r"[0-9a-f]{64}", expected_events) is None:
        raise ValueError("compiled proof has no exact retained event hash")
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as db:
        matches = []
        for identifier, raw, code in db.execute("SELECT id, manifest, exit_code FROM invocations WHERE manifest LIKE ?",
                                                 ("%" + expected_events + "%",)):
            manifest = json.loads(raw)
            if manifest.get("events.json") == expected_events:
                matches.append((identifier, manifest, code))
        if len(matches) != 1 or matches[0][2] != 0:
            raise ValueError("compiled invocation lacks unique successful retained evidence")
        identifier, manifest, _ = matches[0]
        names = ("owner.json", "events.json", "inputs-before.json", "inputs-after.json", "outcome.json")
        if query_stdout is not None:
            names += ("aquery.stdout.log",)
        contents = {}
        for name in names:
            sha = manifest.get(name)
            row = db.execute("SELECT bytes FROM blobs WHERE sha256=?", (sha,)).fetchone()
            if row is None or digest(row[0]) != sha:
                raise ValueError("compiled invocation retained blob is missing or corrupt: " + name)
            contents[name] = row[0]
    before = json.loads(contents["inputs-before.json"])
    owner = json.loads(contents["owner.json"])
    outcome = json.loads(contents["outcome.json"])
    events = [json.loads(line) for line in contents["events.json"].splitlines() if line]
    starts = [item["started"] for item in events if "started" in item]
    if (digest(contents["inputs-before.json"]) != expected_inputs
            or before != json.loads(contents["inputs-after.json"])
            or before.get("dirty") is not False
            or outcome.get("bazel_exit_code") != 0 or outcome.get("validation_exit_code") != 0
            or len(starts) != 1 or starts[0].get("uuid") != identifier
            or owner.get("schemaVersion") != 1 or owner.get("kind") != "bazel-invocation"
            or owner.get("directory") != invocation_path.name
            or owner.get("workspaceKey") != digest(str(repository).encode())
            or (query_stdout is not None and digest(contents["aquery.stdout.log"]) != query_stdout)):
        raise ValueError("retained compiled invocation differs from sealed source or owner")
    return identifier, contents


def admit_archive_events(contents: dict[str, bytes]) -> dict[str, str]:
    """Bind the selected archive and receipt to Bazel's successful output set."""
    return output_hashes(contents["events.json"], "//:candidate_archive",
                         {name.removeprefix("artifact:"): digest(value)
                          for name, value in contents.items() if name.startswith("artifact:")})


def output_hashes(events_bytes: bytes, target: str, expected: dict[str, str]) -> dict[str, str]:
    """Require exact SHA-256 output files of one successful BEP target."""
    events = [json.loads(line) for line in events_bytes.splitlines() if line]
    starts = [event for event in events if "started" in event]
    finished = [event.get("finished", {}) for event in events if "buildFinished" in event.get("id", {})]
    targets = [event for event in events if event.get("id", {}).get("targetCompleted", {}).get("label") == target]
    if (len(starts) != 1 or starts[0]["started"].get("command") not in {"test", "build"}
            or len(finished) != 1 or not finished[0].get("overallSuccess")
            or len(targets) != 1 or not targets[0].get("completed", {}).get("success")):
        raise ValueError("BEP lacks one successful selected target")
    sets = {event["id"]["namedSet"]["id"]: event["namedSetOfFiles"]
            for event in events if "namedSetOfFiles" in event}
    groups = targets[0]["completed"].get("outputGroup", [])
    if len(groups) != 1 or groups[0].get("name") != "default":
        raise ValueError("candidate archive output group is ambiguous")
    pending = [item["id"] for item in groups[0].get("fileSets", [])]
    found, visited = {}, set()
    while pending:
        key = pending.pop()
        if key in visited or key not in sets:
            raise ValueError("candidate archive output set is missing or cyclic")
        visited.add(key)
        pending.extend(item["id"] for item in sets[key].get("fileSets", []))
        for item in sets[key].get("files", []):
            name = item.get("name")
            if name in found or name not in expected:
                raise ValueError("selected BEP output is unexpected or duplicated")
            found[name] = item.get("digest")
    if found != expected:
        raise ValueError("retained bytes differ from BEP output hashes")
    return found


def admit_proof(proof_path: Path, profile: str, commit: str, inputs_sha: str,
                products: dict[str, str], retained: Path, scratch: Path, repository: Path,
                source_snapshot: dict) -> dict:
    """Require the maintained same-source four-product compiled-consumer record."""
    proof_path = canonical(proof_path)
    # The workflow SQLite store and compiled-consumer controller normally live
    # in sibling directories below ContainerFamily/retained.
    if not proof_path.is_relative_to(retained.parent):
        raise ValueError("compiled proof must be retained internally")
    proof = json.loads(proof_path.read_text())
    controller_path = canonical(proof_path.parent / "consumers.json")
    controller = json.loads(controller_path.read_text())
    controller_source = canonical(proof_path.parent / "source.json")
    row = controller.get("profiles", {}).get(profile, {})
    source = proof.get("source", {})
    graph = proof.get("graph", {})
    archives = graph.get("linkedArchives", {})
    lower_locks = proof.get("locks", {})
    lower_archives = proof.get("archives", {})
    commands = row.get("commands", [])
    builds = [item for item in commands if item.get("operation") == "build" and item.get("status") == "passed"]
    queries = [item for item in commands if item.get("operation") == "aquery" and item.get("status") == "passed"]
    if (controller.get("schema") != 1 or controller.get("scope") != "released-layer-consumption"
            or controller.get("status") != "passed" or controller.get("developmentProof") is not False
            or controller.get("sourceCommit") != commit or row.get("status") != "passed"
            or controller.get("sourceSHA256") != digest(controller_source)
            or json.loads(controller_source.read_text()) != source_snapshot
            or row.get("proof") != str(proof_path) or row.get("proofSHA256") != digest(proof_path)
            or proof.get("schema") != 1 or proof.get("profile") != profile
            or proof.get("developmentProof") is not False or proof.get("releaseQualified") is not False
            or source.get("source") != commit or source.get("dirty") is not False
            or source.get("inputsSHA256") != inputs_sha
            or set(graph.get("products", [])) != {"//:" + item for item in PRODUCTS}
            or not isinstance(archives, dict)
            or {value.get("group") for value in archives.values() if isinstance(value, dict)} != LOWER_GROUPS
            or len(archives) < len(LOWER_GROUPS) or len(builds) != 1 or len(queries) != 1
            or set(lower_locks) != LOWER_GROUPS - {"argument-parser"}
            or set(lower_archives) != LOWER_GROUPS - {"argument-parser"}
            or any(not isinstance(value, str) or len(value) != 64 for value in
                   [*lower_locks.values(), *lower_archives.values(), proof.get("argumentParserLockSHA256")])
            or set(products) != PRODUCTS):
        raise ValueError("compiled-consumer proof differs from candidate source, profile or four-product closure")
    database = canonical(retained / "bazel-evidence.sqlite")
    build_id, build = retained_compiled(database, source.get("buildEventsSHA256"), inputs_sha,
                                        Path(builds[0].get("invocation", "")), scratch, repository)
    query_id, query = retained_compiled(database, source.get("queryEventsSHA256"), inputs_sha,
                                        Path(queries[0].get("invocation", "")), scratch, repository,
                                        proof.get("queryStdoutSHA256"))
    if proof.get("actionGraphSHA256") != digest(query["aquery.stdout.log"]):
        raise ValueError("retained action graph differs from compiled proof")
    output_hashes(build["events.json"], "//:products", products)
    return {"proofSHA256": digest(proof_path), "controllerSHA256": digest(controller_path),
            "buildInvocation": build_id, "queryInvocation": query_id,
            "buildEventsSHA256": digest(build["events.json"]),
            "queryEventsSHA256": digest(query["events.json"]), "productBEPHashes": dict(products),
            "lowerLocks": proof.get("locks", {}), "lowerArchives": proof.get("archives", {}),
            "argumentParserLockSHA256": proof.get("argumentParserLockSHA256"),
            "linkedArchiveInputs": {name: row["sha256"] for name, row in sorted(archives.items())}}


def selected_licenses(repository: Path, resolved: Path, output: Path) -> dict:
    """Project reviewed global licenses onto exactly the selected stock/enhanced pins."""
    global_ledger = json.loads((repository / "Tools/release/dependency-licenses.json").read_text())
    pins = json.loads(resolved.read_text()).get("pins")
    if (global_ledger.get("schemaVersion") != 1 or not isinstance(global_ledger.get("licenses"), dict)
            or not isinstance(pins, list)):
        raise ValueError("selected dependency metadata is invalid")
    identities = [pin.get("identity") for pin in pins if isinstance(pin, dict)]
    if (len(identities) != len(pins) or len(set(identities)) != len(identities)
            or not set(identities) <= set(global_ledger["licenses"])):
        raise ValueError("selected pins lack a unique reviewed license declaration")
    selected = {"schemaVersion": 1,
                "licenses": {name: global_ledger["licenses"][name] for name in sorted(identities)}}
    output.write_text(json.dumps(selected, indent=2, sort_keys=True) + "\n")
    release = str(repository / "Tools/release")
    if release not in sys.path:
        sys.path.insert(0, release)
    from dependency_metadata import load_dependencies  # pylint: disable=import-outside-toplevel
    load_dependencies(resolved, output)
    return selected


def legal_notices(rows: list[dict]) -> bytes:
    """Reconstruct the collector's complete notice text from admitted Git blobs."""
    sections = ["devcontainer third-party notices", "================================", "",
                "Legal texts from the exact Git revisions in the selected dependency lock.", ""]
    for row in rows:
        sections.extend(["=" * 78, "Dependency: " + row["identity"], "Version: " + row["version"],
                         "Revision: " + row["revision"], "Source: " + row["location"],
                         "Declared license: " + row["license"], ""])
        for file in row["files"]:
            sections.extend(["----- " + file["name"] + " -----",
                             file["text"].replace("\r\n", "\n").rstrip(), ""])
    return ("\n".join(sections).rstrip() + "\n").encode()


def admit_legal_bundle(repository: Path, retained: Path, bundle: Path, expected_sha: str,
                       selected_lock: Path, selected_ledger: Path) -> tuple[dict, dict[str, bytes]]:
    """Replay an exact, internally retained source-pinned legal collection."""
    bundle = canonical(bundle)
    if (not bundle.is_dir() or not bundle.is_relative_to(retained.parent)
            or bundle.stat().st_dev != retained.stat().st_dev
            or bundle.stat().st_uid != os.getuid()
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha) is None):
        raise ValueError("legal bundle must be physical, internally retained and SHA-pinned")
    names = ("legal.json", "dependency-licenses.json", "THIRD-PARTY-NOTICES.txt")
    if {path.name for path in bundle.iterdir()} != set(names):
        raise ValueError("legal bundle has incomplete or unexpected contents")
    contents = {}
    for name in names:
        path = canonical(bundle / name)
        if not path.is_file() or path.stat().st_uid != os.getuid():
            raise ValueError("legal bundle member is not an owned regular file")
        contents[name] = path.read_bytes()
    if digest(contents["legal.json"]) != expected_sha:
        raise ValueError("legal bundle differs from trusted caller SHA")
    document = json.loads(contents["legal.json"])
    if not isinstance(document, dict):
        raise ValueError("legal bundle manifest must be an object")
    policy_names = ("dependency_metadata.py", "write-third-party-notices.py")
    policies = {name: digest(canonical(repository / "Tools/release" / name)) for name in policy_names}
    collector_sha = digest(canonical(repository / "Tools/release/prepare-native-legal.py"))
    reviewed = canonical(repository / "Tools/release/dependency-licenses.json")
    if (document.get("schemaVersion") != 1 or document.get("kind") != "source-pinned-legal-bundle"
            or document.get("resolvedSHA256") != digest(selected_lock)
            or document.get("reviewedLedgerSHA256") != digest(reviewed)
            or document.get("selectedLedgerSHA256") != digest(contents["dependency-licenses.json"])
            or contents["dependency-licenses.json"] != selected_ledger.read_bytes()
            or document.get("noticesSHA256") != digest(contents["THIRD-PARTY-NOTICES.txt"])
            or document.get("policySHA256") != policies
            or document.get("collectorSHA256") != collector_sha):
        raise ValueError("legal bundle source, lock, ledger or policy differs")
    release = str(repository / "Tools/release")
    if release not in sys.path:
        sys.path.insert(0, release)
    from dependency_metadata import load_dependencies  # pylint: disable=import-outside-toplevel
    spec = importlib.util.spec_from_file_location("legal_notice_writer", repository / "Tools/release/write-third-party-notices.py")
    writer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(writer)
    dependencies = load_dependencies(selected_lock, selected_ledger)
    rows = document.get("dependencies")
    if not dependencies or not isinstance(rows, list) or len(rows) != len(dependencies):
        raise ValueError("legal bundle dependency closure is incomplete")
    for dependency, row in zip(dependencies, rows):
        expected = {name: getattr(dependency, name) for name in
                    ("identity", "revision", "location", "version", "license")}
        files = row.get("files") if isinstance(row, dict) else None
        if (not isinstance(row, dict) or any(row.get(key) != value for key, value in expected.items())
                or not isinstance(row.get("rootTree"), str)
                or re.fullmatch(r"[0-9a-f]{40}", row["rootTree"]) is None
                or not isinstance(files, list) or not files):
            raise ValueError("legal bundle dependency identity differs from selected pin")
        seen = set()
        for file in files:
            if not isinstance(file, dict):
                raise ValueError("legal bundle contains an invalid Git file")
            name, text = file.get("name"), file.get("text")
            if (not isinstance(name, str) or not name or "/" in name or name in seen
                    or any(ord(char) < 32 or ord(char) == 127 for char in name)
                    or not isinstance(text, str)):
                raise ValueError("legal bundle Git filename or text is invalid")
            seen.add(name)
            raw = text.encode("utf-8")
            blob = b"blob " + str(len(raw)).encode() + b"\0" + raw
            if (not raw or len(raw) > 2 * 1024 * 1024 or file.get("sha256") != digest(raw)
                    or file.get("gitBlob") != hashlib.sha1(blob).hexdigest()):
                raise ValueError("legal bundle Git blob SHA, size or content differs")
        if not files[0]["name"].casefold().startswith(("license", "copying")):
            raise ValueError("legal bundle lacks a root license file")
        writer.require_declared_license(dependency, files[0]["text"])
    if legal_notices(rows) != contents["THIRD-PARTY-NOTICES.txt"]:
        raise ValueError("legal bundle rendered notice bytes differ from Git text closure")
    # The trusted caller's manifest SHA authenticates the collector's prior
    # commit/tree API checks; Git root trees cannot be reconstructed here.
    return {"bundleSHA256": expected_sha, "collectorSHA256": collector_sha,
            "policySHA256": policies, "reviewedLedgerSHA256": document["reviewedLedgerSHA256"],
            "selectedLedgerSHA256": document["selectedLedgerSHA256"],
            "noticesSHA256": document["noticesSHA256"],
            "dependencies": [dependency.identity for dependency in dependencies]}, contents


def run_metadata(repository: Path, script: str, arguments: list[str]) -> None:
    command = [sys.executable, str(repository / "Tools/release" / script), *arguments]
    subprocess.run(command, check=True, cwd=repository, timeout=120,
                   env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "PYTHONDONTWRITEBYTECODE": "1"})


def payload_inventory(prepared_module, tree: Path) -> dict[str, dict]:
    """Seal exact product-root-relative files for the later signer."""
    entries = prepared_module.inventory(tree)
    return {name: {field: entry[field] for field in ("sha256", "size", "mode")}
            for name, entry in entries.items() if entry["kind"] == "file"}


def source_date_epoch(repository: Path, commit: str) -> int:
    """Retain the exact selected commit time for checkout-free finalization."""
    epoch = int(subprocess.check_output(["/usr/bin/git", "-C", str(repository),
                                         "show", "-s", "--format=%ct", commit]).strip())
    if epoch < 0:
        raise ValueError("source commit has an invalid date epoch")
    return epoch


def stage(repository: Path, retained: Path, scratch: Path, invocation: str, profile: str,
          proof_path: Path, destination: Path, lane: str, run_number: str | None = None,
          *, legal_bundle: Path, legal_sha256: str,
          ssd_root: Path = Path("/Volumes/SSD")) -> dict:
    """Caller holds scratch/locks/reference-store.lock for the whole transaction."""
    if profile not in {"stock", "enhanced"} or lane not in {"development", "current", "stable"}:
        raise ValueError("unsupported profile or release lane")
    repository, retained, scratch = (canonical(item) for item in (repository, retained, scratch))
    destination = canonical(destination, existing=False)
    if (destination.exists() or destination.is_symlink() or not destination.parent.is_dir()
            or not scratch.is_relative_to(canonical(ssd_root))
            or destination.parent != scratch / "native-packages"
            or scratch.is_relative_to(retained) or retained.is_relative_to(scratch)):
        raise ValueError("stage must be a fresh path on enrolled SSD scratch")
    layered, candidate, prepared_module = modules(repository)
    database = canonical(retained / "bazel-evidence.sqlite")
    source = layered.snapshot(repository)
    if source.get("dirty") is not False:
        raise ValueError("native package staging requires a clean source checkout")
    receipt, _ = candidate.retained_candidate(database, invocation)
    if receipt.get("schemaVersion") != 2 or receipt["runtimeProfile"] != profile or receipt["commit"] != source["commit"]:
        raise ValueError("native schema-2 candidate source or profile differs")
    manifest, contents = retained_bytes(database, invocation)
    if json.loads(contents["inputs-before.json"]) != source:
        raise ValueError("captured candidate differs from current clean source")
    output_hashes = admit_archive_events(contents)
    proof = admit_proof(proof_path, profile, source["commit"],
                        digest(contents["inputs-before.json"]), receipt["products"],
                        retained, scratch, repository, source)
    from artifacts.foundation import GROUPS, layer_lock_path  # pylint: disable=import-outside-toplevel
    if (any(digest(layer_lock_path(repository, group, profile)) != proof["lowerLocks"][group]
            for group in GROUPS)
            or digest(repository / "Tools/bazel/artifacts/argument-parser.lock.json")
            != proof["argumentParserLockSHA256"]):
        raise ValueError("source lower-layer locks differ from compiled-consumer proof")
    candidate.prepare_candidate(retained, scratch, invocation)
    admitted = candidate.admit_candidate(retained, invocation, profile)
    source_prepared = canonical(Path(admitted["root"]))
    saved = json.loads((retained / "candidate-receipts" / (admitted["preparationSHA256"] + ".json")).read_text())
    checked = prepared_module.validate_prepared(source_prepared, saved["specification"], saved)
    if checked["inventory"] != saved["inventory"]:
        raise ValueError("prepared candidate inventory drifted")
    lock_name = "Package.stock.resolved" if profile == "stock" else "Package.resolved"
    if (digest(repository / lock_name) != receipt["dependencyLockSHA256"]
            or source["files"][lock_name]["sha256"] != receipt["dependencyLockSHA256"]):
        raise ValueError("selected source lock differs from candidate")
    root_name = f"devcontainer-{receipt['version']}"
    if set(source_prepared.iterdir()) != {source_prepared / root_name, source_prepared / ".prepared-release.json"}:
        raise ValueError("candidate prepared tree contains unexpected roots")
    scratch_tmp = scratch / "tmp"
    canonical(scratch_tmp)
    with tempfile.TemporaryDirectory(prefix="native-stage-", dir=scratch_tmp) as temporary:
        working = Path(temporary)
        tree = working / root_name
        shutil.copytree(source_prepared / root_name, tree, symlinks=True)
        copied = prepared_module.inventory(working)
        original = {name: value for name, value in saved["inventory"].items()
                    if name == root_name or name.startswith(root_name + "/")}
        if copied != original:
            raise ValueError("copied candidate tree differs from authenticated prepared bytes and modes")
        shared = tree / "share/devcontainer"
        selected_lock = shared / "Package.resolved"
        if digest(selected_lock) != receipt["dependencyLockSHA256"]:
            raise ValueError("staged selected lock differs from candidate")
        ledger = shared / "dependency-licenses.selected.json"
        selected_licenses(repository, selected_lock, ledger)
        legal, legal_contents = admit_legal_bundle(repository, retained, legal_bundle,
                                                   legal_sha256, selected_lock, ledger)
        notices = shared / "THIRD-PARTY-NOTICES.txt"
        notices.write_bytes(legal_contents["THIRD-PARTY-NOTICES.txt"])
        ledger.write_bytes(legal_contents["dependency-licenses.json"])
        runtime_legal = {}
        runtime_root = tree / "libexec/devcontainer/reference"
        for name in ("NODE-LICENSE.txt", "cli/LICENSE.txt", "cli/ThirdPartyNotices.txt"):
            member = canonical(runtime_root / name)
            expected = receipt["referenceRuntime"]["files"].get(name)
            if not member.is_file() or member.stat().st_size == 0 or digest(member) != expected:
                raise ValueError("private Node/CLI legal file differs from candidate closure")
            runtime_legal[name] = expected
        context_args = ["--product-version", receipt["version"], "--lane", lane,
                        "--commit", source["commit"]]
        if run_number is not None:
            context_args.extend(["--run-number", run_number])
        context_path = working / "package-context.json"
        with context_path.open("xb") as context:
            subprocess.run([sys.executable, str(repository / "Tools/release/package-context.py"), *context_args],
                           check=True, stdout=context, cwd=repository, timeout=120,
                           env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "PYTHONDONTWRITEBYTECODE": "1"})
        context = json.loads(context_path.read_text())
        if context.get("commit") != source["commit"] or context.get("productVersion") != receipt["version"]:
            raise ValueError("package context differs from candidate")
        run_metadata(repository, "write-build-info.py", ["--version", receipt["version"], "--commit", source["commit"],
                     "--lane", lane, "--architecture", "arm64", "--output", str(shared / "build-info.json")])
        run_metadata(repository, "render-package-readme.py", ["--source", "README.md", "--repository-root", str(repository),
                     "--repository", "stephenlclarke/devcontainer", "--revision", source["commit"],
                     "--output", str(shared / "README.md")])
        epoch = source_date_epoch(repository, source["commit"])
        run_metadata(repository, "write-sbom.py", ["--version", receipt["version"], "--commit", source["commit"],
                     "--source-date-epoch", str(epoch), "--resolved", str(selected_lock),
                     "--license-manifest", str(ledger), "--reference-runtime-root", str(runtime_root),
                     "--output", str(shared / "devcontainer.spdx.json")])
        if layered.snapshot(repository) != source or candidate.admit_candidate(retained, invocation, profile) != admitted:
            raise ValueError("source or retained candidate changed during staging")
        final_inventory = prepared_module.inventory(working)
        payload = payload_inventory(prepared_module, tree)
        provenance = {"schema": 1, "scope": "unsigned-native-package-stage", "sourceCommit": source["commit"],
                      "sourceSHA256": digest(contents["inputs-before.json"]), "profile": profile, "lane": lane,
                      "sourceDateEpoch": epoch,
                      "candidateInvocation": invocation, "candidateAssetSHA256": admitted["assetSHA256"],
                      "candidatePreparationSHA256": admitted["preparationSHA256"],
                      "candidateOutputsBEP": output_hashes, "candidateProducts": receipt["products"],
                      "candidateTerminalLaunchers": receipt["terminalLaunchers"],
                      "goSDKLicenseSHA256": receipt["goSDKLicenseSHA256"],
                      "compiledConsumer": proof, "selectedLockSHA256": receipt["dependencyLockSHA256"],
                      "selectedLicenseLedgerSHA256": digest(ledger), "privateRuntime": receipt["referenceRuntime"],
                      "legalBundle": legal, "privateRuntimeLegalFiles": runtime_legal,
                      "unsignedInventorySHA256": digest(json.dumps(final_inventory, sort_keys=True, separators=(",", ":")).encode()),
                      "unsignedPayloadInventory": payload,
                      "packageContext": context, "legalCompleteness": True, "distributionReady": False,
                      "signingComplete": False, "notarizationComplete": False}
        (working / "native-stage-provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
        if destination.exists() or destination.is_symlink():
            raise ValueError("native stage destination was created concurrently")
        working.rename(destination)
    return provenance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--retained-root", type=Path, required=True)
    parser.add_argument("--ssd-scratch", type=Path, required=True)
    parser.add_argument("--candidate-invocation", required=True)
    parser.add_argument("--profile", choices=("stock", "enhanced"), required=True)
    parser.add_argument("--compiled-proof", type=Path, required=True)
    parser.add_argument("--legal-bundle", type=Path, required=True)
    parser.add_argument("--legal-bundle-sha256", required=True)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--lane", choices=("development", "current", "stable"), required=True)
    parser.add_argument("--run-number")
    parser.add_argument("--under-reference-lease", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    os.umask(0o077)
    scratch = canonical(args.ssd_scratch)
    if not args.under_reference_lease:
        lock = scratch / "locks/reference-store.lock"
        canonical(lock)
        os.execv("/usr/bin/lockf", ["/usr/bin/lockf", "-k", "-t", "300", str(lock), sys.executable,
                                    str(Path(__file__).resolve()), *sys.argv[1:], "--under-reference-lease"])
    print(json.dumps(stage(args.repository, args.retained_root, scratch, args.candidate_invocation,
                           args.profile, args.compiled_proof, args.stage, args.lane, args.run_number,
                           legal_bundle=args.legal_bundle, legal_sha256=args.legal_bundle_sha256), sort_keys=True))


if __name__ == "__main__":
    main()
