#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Verify the loaded enhanced Container manifest agrees with SwiftPM's direct graph."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
CONTAINER_REPOSITORY = "+dependencies+swiftpkg_container"
EXPECTED_URLS = {
    "container": "https://github.com/stephenlclarke/container.git",
    "containerization": "https://github.com/stephenlclarke/containerization.git",
    "container-engine-api": "https://github.com/stephenlclarke/container-engine-api.git",
}
STOCK_URLS = {**EXPECTED_URLS, "container": "https://github.com/apple/container.git",
              "containerization": "https://github.com/apple/containerization.git"}
REVISION = re.compile(r"[0-9a-f]{40}\Z")
SOURCE_OVERRIDES = ("CONTAINER_PACKAGE_PATH", "CONTAINERIZATION_PACKAGE_PATH",
                    "CONTAINER_ENGINE_API_PACKAGE_PATH", "CONTAINERIZATION_SOURCE",
                    "CONTAINERIZATION_REF")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pins(path: Path) -> dict[str, dict]:
    rows = json.loads(path.read_text())["pins"]
    if len({row["identity"] for row in rows}) != len(rows):
        raise ValueError("SwiftPM lock has duplicate package identities")
    return {row["identity"]: row for row in rows}


def direct_requirements(document: dict, identities: set[str], expected_urls: dict[str, str],
                        kind: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for item in document["dependencies"]:
        records = item.get("sourceControl", [])
        for row in records:
            identity = row["identity"]
            if identity not in identities:
                continue
            if identity in found:
                raise ValueError("SwiftPM manifest has a duplicate direct dependency: " + identity)
            urls = row["location"].get("remote", [])
            values = row["requirement"].get(kind, [])
            if (len(urls) != 1 or urls[0].get("urlString") != expected_urls[identity]
                    or len(values) != 1 or (kind == "revision" and not REVISION.fullmatch(values[0]))):
                raise ValueError("SwiftPM manifest has an unreviewed source requirement: " + identity)
            found[identity] = values[0]
    if set(found) != identities:
        raise ValueError("SwiftPM manifest lacks the required direct dependencies")
    return found


def verify_graph(root: Path, container_source: Path,
                 direct: dict, transitive: dict, profile: str = "enhanced") -> dict:
    """Compare actual manifest requirements with both exact source-control locks."""
    if profile not in {"enhanced", "stock"}:
        raise ValueError("source graph profile must be enhanced or stock")
    enhanced = pins(root / "Package.resolved")
    stock = pins(root / "Package.stock.resolved")
    ap_enhanced = enhanced["swift-argument-parser"]["state"]["revision"]
    ap_stock = stock["swift-argument-parser"]["state"]["revision"]
    if ap_enhanced != ap_stock or not REVISION.fullmatch(ap_enhanced):
        raise ValueError("SwiftPM profiles disagree on the ArgumentParser source")
    selected_lock = enhanced if profile == "enhanced" else stock
    expected_urls = EXPECTED_URLS if profile == "enhanced" else STOCK_URLS
    selected = {name: selected_lock[name] for name in expected_urls}
    for name, row in selected.items():
        if (row.get("kind") != "remoteSourceControl"
                or row.get("location") != expected_urls[name]
                or not REVISION.fullmatch(row.get("state", {}).get("revision", ""))):
            raise ValueError("selected lock has an unreviewed package source: " + name)
    revisions = {name: row["state"]["revision"] for name, row in selected.items()}
    nested = pins(container_source / "Package.resolved")
    nested_names = ("containerization", "container-engine-api") if profile == "enhanced" else ("containerization",)
    for name in nested_names:
        if nested.get(name) != selected[name]:
            raise ValueError("loaded Container lock differs from selected graph: " + name)
    if profile == "enhanced":
        if direct_requirements(direct, set(expected_urls), expected_urls, "revision") != revisions:
            raise ValueError("devcontainer manifest differs from selected enhanced lock")
        if direct_requirements(transitive, set(nested_names), expected_urls, "revision") != {
            name: revisions[name] for name in nested_names
        }:
            raise ValueError("loaded Container source requires a different transitive graph")
    else:
        versions = {name: selected[name]["state"]["version"] for name in ("container", "containerization")}
        if (direct_requirements(direct, set(versions), expected_urls, "exact") != versions
                or direct_requirements(direct, {"container-engine-api"}, expected_urls, "revision")
                != {"container-engine-api": revisions["container-engine-api"]}):
            raise ValueError("devcontainer stock manifest differs from selected lock")
        if direct_requirements(transitive, {"containerization"}, expected_urls, "exact") != {
            "containerization": versions["containerization"]
        }:
            raise ValueError("loaded stock Container source requires a different transitive graph")
    return {"schema": 1, "profile": profile, "source": revisions,
            "argumentParserSource": ap_enhanced,
            "devcontainerManifestSHA256": digest(root / "Package.swift"),
            "devcontainerLockSHA256": digest(root / "Package.resolved"),
            "stockLockSHA256": digest(root / "Package.stock.resolved"),
            "loadedContainerManifestSHA256": digest(container_source / "Package.swift"),
            "loadedContainerLockSHA256": digest(container_source / "Package.resolved")}


def dump_package(path: Path, environment: dict[str, str], swift: Path) -> dict:
    result = subprocess.run([str(swift), "package", "dump-package", "--package-path", str(path)],
                            env=environment, text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError("SwiftPM could not evaluate the selected source manifest")
    return json.loads(result.stdout)


def check(output_base: Path, profile: str, root: Path = ROOT) -> dict:
    for name in SOURCE_OVERRIDES:
        if os.environ.get(name):
            raise ValueError("SwiftPM local package override is not admitted: " + name)
    source = output_base / "external" / CONTAINER_REPOSITORY
    if (not source.is_dir() or not (source / "Package.swift").is_file()
            or not (source / "Package.resolved").is_file()):
        raise ValueError("Bazel has not loaded the selected Container source manifest")
    environment = {name: os.environ[name] for name in ("DEVELOPER_DIR", "SDKROOT", "TMPDIR")
                   if name in os.environ}
    environment.update({"HOME": str(Path.home()), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                        "LANG": "C", "LC_ALL": "C", "DEVCONTAINER_RUNTIME_PROFILE": profile})
    tool = subprocess.run(["/usr/bin/xcrun", "--find", "swift"], env=environment,
                          text=True, capture_output=True, timeout=30)
    if tool.returncode or not Path(tool.stdout.strip()).is_absolute():
        raise ValueError("selected Xcode does not provide an absolute Swift compiler path")
    swift = Path(tool.stdout.strip())
    before = {name: digest(path) for name, path in {
        "manifest": root / "Package.swift", "lock": root / "Package.resolved",
        "stockLock": root / "Package.stock.resolved",
        "containerManifest": source / "Package.swift",
        "containerLock": source / "Package.resolved",
    }.items()}
    receipt = verify_graph(root, source, dump_package(root, environment, swift),
                           dump_package(source, environment, swift), profile)
    after = {name: digest(path) for name, path in {
        "manifest": root / "Package.swift", "lock": root / "Package.resolved",
        "stockLock": root / "Package.stock.resolved",
        "containerManifest": source / "Package.swift",
        "containerLock": source / "Package.resolved",
    }.items()}
    if before != after:
        raise ValueError("SwiftPM source graph changed while it was checked")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-base", type=Path, required=True)
    parser.add_argument("--profile", choices=("enhanced", "stock"), required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    receipt = check(args.output_base, args.profile)
    payload = json.dumps(receipt, sort_keys=True) + "\n"
    if args.output is not None:
        if not args.output.is_absolute() or args.output.exists() or args.output.is_symlink():
            raise ValueError("source graph output must be a fresh absolute path")
        args.output.write_text(payload)
    print(payload, end="")


if __name__ == "__main__":
    main()
