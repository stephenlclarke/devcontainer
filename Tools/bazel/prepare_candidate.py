"""Admit clean retained native builds for integration, never release qualification."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sqlite3

from prepare_releases import layout, prepare, retain_prepared, validate_prepared
from release_inputs import canonical
from retain_evidence import digest, restore_candidate


PRODUCTS = {"devcontainer", "devcontainer-engine", "devcontainer-compose"}
SCOPE = "local-candidate-integration-only"


def retained_candidate(database: Path, invocation: str) -> tuple[dict, dict]:
    """Read authenticated evidence, requiring a clean unchanged source snapshot."""
    if database.resolve() != database or not database.is_file():
        raise ValueError("Missing or aliased candidate evidence database")
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as db:
        row = db.execute("SELECT manifest, exit_code FROM invocations WHERE id=?", (invocation,)).fetchone()
        if row is None or row[1] != 0:
            raise ValueError("Candidate requires a successful retained invocation")
        manifest = json.loads(row[0])
        contents = {}
        for name in ("inputs-before.json", "inputs-after.json", "outcome.json",
                     "artifact:candidate_archive.json", "artifact:candidate_archive.tar.gz"):
            expected = manifest.get(name)
            blob = db.execute("SELECT bytes FROM blobs WHERE sha256=?", (expected,)).fetchone()
            if blob is None or digest(blob[0]) != expected:
                raise ValueError("Missing or corrupt candidate evidence")
            contents[name] = blob[0]
    inputs = json.loads(contents["inputs-before.json"])
    if (inputs != json.loads(contents["inputs-after.json"]) or inputs.get("dirty") is not False
            or inputs.get("schema") != 1 or not re.fullmatch(r"[a-f0-9]{40}", inputs.get("commit", ""))):
        raise ValueError("Candidate requires clean unchanged source evidence")
    outcome = json.loads(contents["outcome.json"])
    if outcome.get("bazel_exit_code") != 0 or outcome.get("validation_exit_code") != 0:
        raise ValueError("Candidate validation did not pass")
    receipt = json.loads(contents["artifact:candidate_archive.json"])
    archive = contents["artifact:candidate_archive.tar.gz"]
    schema = receipt.get("schemaVersion")
    products = PRODUCTS | {"devcontainer-docker"} if schema == 2 else PRODUCTS
    if (schema not in {1, 2} or receipt.get("kind") != "unsigned-native-candidate"
            or receipt.get("distributionReady") is not False or receipt.get("architecture") != "arm64"
            or receipt.get("compilationMode") != "opt" or receipt.get("runtimeProfile") not in {"stock", "enhanced"}
            or receipt.get("commit") != inputs["commit"]
            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", receipt.get("version", ""))
            or receipt.get("archiveSHA256") != digest(archive) or receipt.get("archiveSize") != len(archive)
            or set(receipt.get("products", {})) != products):
        raise ValueError("Invalid native candidate identity")
    resolved = "Package.stock.resolved" if receipt["runtimeProfile"] == "stock" else "Package.resolved"
    if receipt.get("dependencyLockSHA256") != inputs.get("files", {}).get(resolved, {}).get("sha256"):
        raise ValueError("Candidate dependency lock differs from captured source")
    if schema == 2:
        reference = receipt.get("referenceRuntime", {})
        source_lock = inputs.get("files", {}).get("Tools/bazel/devcontainers-cli.lock.json", {}).get("sha256")
        if (reference.get("nodeVersion") != "24.21.0" or reference.get("cliVersion") != "0.88.0"
                or not source_lock or reference.get("lockSHA256") != source_lock):
            raise ValueError("Candidate private runtime differs from captured source")
    asset = {"repository": "local/devcontainer-candidate", "tag": receipt["version"],
             "name": "candidate_archive_v2.tar.gz" if schema == 2 else "candidate_archive.tar.gz",
             "sha256": receipt["archiveSHA256"], "size": receipt["archiveSize"]}
    return receipt, asset


def admit_candidate(retained: Path, invocation: str, profile: str) -> dict:
    """Read-only admission works after all disposable build/restore outputs vanish."""
    receipt, asset = retained_candidate(retained / "bazel-evidence.sqlite", invocation)
    if receipt["runtimeProfile"] != profile:
        raise ValueError("Candidate profile does not match the selected runtime lane")
    specification = {"schemaVersion": 1, "assetSHA256": asset["sha256"], "layout": layout(asset)}
    key = digest(canonical(specification).encode())
    root = retained / "prepared-candidates" / key
    saved = retained / "candidate-receipts" / (key + ".json")
    pending = root.parent / (key + ".pending.json")
    if root.resolve() != root or saved.resolve() != saved or pending.exists() or pending.is_symlink():
        raise ValueError("Candidate preparation is aliased or unfinished")
    prepared = validate_prepared(root, specification, json.loads(saved.read_text()))
    shared = root / f"devcontainer-{receipt['version']}/share/devcontainer"
    embedded = {key: value for key, value in receipt.items() if key not in {"archiveSHA256", "archiveSize"}}
    if json.loads((shared / "candidate.json").read_text()) != embedded:
        raise ValueError("Embedded candidate identity differs")
    if digest((shared / "Package.resolved").read_bytes()) != receipt["dependencyLockSHA256"]:
        raise ValueError("Embedded candidate dependency lock differs")
    executables = specification["layout"]["executables"]
    if any(prepared["inventory"][executables[name]]["sha256"] != expected
           for name, expected in receipt["products"].items()):
        raise ValueError("Candidate product digests differ")
    if receipt["schemaVersion"] == 2:
        reference = receipt["referenceRuntime"]
        prefix = f"devcontainer-{receipt['version']}/libexec/devcontainer/reference/"
        expected_files = {"node": executables["reference-node"], **specification["layout"]["files"]}
        if (set(reference.get("files", {})) != set(expected_files)
                or reference["files"].get("runtime-lock.json") != reference["lockSHA256"]
                or any(prepared["inventory"][prefix + name]["sha256"] != reference["files"][name]
                       for name in expected_files)):
            raise ValueError("Candidate private runtime digests differ")
    return {"assetSHA256": asset["sha256"], "preparationSHA256": key, "root": str(root),
            "inventorySHA256": digest(canonical(prepared["inventory"]).encode()),
            "executables": {name: str(root / path) for name, path in executables.items()},
            "scope": SCOPE, "candidateInvocation": invocation, "sourceCommit": receipt["commit"],
            "runtimeProfile": profile, "dependencyLockSHA256": receipt["dependencyLockSHA256"]}


def prepare_candidate(retained: Path, scratch: Path, invocation: str) -> dict:
    """Caller holds the reference-store lease; reuse the existing publication journal."""
    receipt, asset = retained_candidate(retained / "bazel-evidence.sqlite", invocation)
    restored = restore_candidate(retained / "bazel-evidence.sqlite", invocation, scratch)
    source = restored / "candidate_archive.tar.gz"
    staging, published, receipts = scratch / "prepared-candidates", retained / "prepared-candidates", retained / "candidate-receipts"
    for directory in (staging, published, receipts):
        if directory.resolve() != directory:
            raise ValueError("Aliased candidate storage")
        directory.mkdir(mode=0o700, exist_ok=True)
    prepare(asset, source, staging, receipts)
    retain_prepared(asset, source, staging, published, receipts)
    return admit_candidate(retained, invocation, receipt["runtimeProfile"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("invocation", help="successful retained native candidate invocation")
    args = parser.parse_args()
    os.umask(0o077)
    retained = Path.home() / "Library/Application Support/ContainerFamily/retained/workflow"
    if retained.stat().st_dev != Path.home().stat().st_dev:
        raise ValueError("Candidates require internal retained storage")
    print(canonical(prepare_candidate(retained, Path("/Volumes/SSD/cf/bazel"), args.invocation)))


if __name__ == "__main__":
    main()
