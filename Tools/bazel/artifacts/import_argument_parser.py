#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Admit the published ArgumentParser binary under its canonical Bazel repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tarfile

if __package__:
    from .argument_parser import FILES, cached_release, inspect, verify_consumer
else:
    from argument_parser import FILES, cached_release, inspect, verify_consumer

ROOT = Path(__file__).resolve().parents[3]
LOCK = Path(__file__).with_name("argument-parser.lock.json")
BUILD = Path(__file__).with_name("argument_parser_import.BUILD.bazel")
REPO = Path(__file__).with_name("argument_parser_import.REPO.bazel")
CACHE = Path.home() / "Library/Application Support/ContainerFamily/retained/devcontainer/binary-layers/argument-parser"
REPOSITORY = "+dependencies+swiftpkg_swift_argument_parser"
IMPORT_BUILD_SHA256 = "691d6272fa8ca5c5bb8cded57dfebb8c8b27493014f0f0d1541b04556b58ac06"


def recipe_sha256() -> str:
    paths = (Path(__file__), Path(__file__).with_name("argument_parser.py"),
             Path(__file__).with_name("release_asset.py"), BUILD, REPO)
    values = {path.name: sha256(path) for path in paths}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_profile_pins(root: Path, source_commit: str) -> None:
    """Both SwiftPM profiles must select the archive's exact source revision."""
    for filename in ("Package.resolved", "Package.stock.resolved"):
        pins = json.loads((root / filename).read_text())["pins"]
        matches = [row for row in pins if row.get("identity") == "swift-argument-parser"]
        if (len(matches) != 1 or matches[0].get("kind") != "remoteSourceControl"
                or matches[0].get("state", {}).get("revision") != source_commit):
            raise ValueError(filename + " lacks the admitted ArgumentParser source pin")


def owned_cache(path: Path) -> None:
    home = Path.home()
    if not path.is_relative_to(home) or home.is_symlink() or home.stat().st_uid != os.getuid():
        raise ValueError("ArgumentParser cache must be beneath the owned home")
    current = home
    for part in path.relative_to(home).parts:
        current /= part
        if current.is_symlink() or (current.exists() and
                                    (not current.is_dir() or current.stat().st_uid != os.getuid())):
            raise ValueError("ArgumentParser cache ancestor is not an owned directory")


def inventory(directory: Path, manifest: dict) -> None:
    expected = set(FILES) | {"BUILD.bazel", "REPO.bazel", "layer.json", "import-receipt.json"}
    observed = {path.relative_to(directory).as_posix() for path in directory.rglob("*")}
    if observed != expected:
        raise ValueError("ArgumentParser cache inventory changed")
    for name in expected:
        path = directory / name
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o644:
            raise ValueError("ArgumentParser cached file type or mode changed")
        if name in manifest["files"] and sha256(path) != manifest["files"][name]:
            raise ValueError("ArgumentParser cached module or archive bytes changed")
    if sha256(directory / "BUILD.bazel") != IMPORT_BUILD_SHA256:
        raise ValueError("ArgumentParser loaded BUILD differs from reviewed import recipe")
    if sha256(directory / "REPO.bazel") != sha256(REPO):
        raise ValueError("ArgumentParser loaded REPO differs from reviewed import recipe")
    if json.loads((directory / "layer.json").read_text()) != manifest:
        raise ValueError("ArgumentParser cached manifest changed")


def prepare(root: Path = ROOT, cache: Path = CACHE) -> dict:
    owned_cache(cache)
    lock = verify_consumer(LOCK, root)
    if sha256(BUILD) != IMPORT_BUILD_SHA256:
        raise ValueError("ArgumentParser importer BUILD differs from reviewed import recipe")
    archive = cached_release(LOCK, cache / "downloads")
    verify_consumer(LOCK, root, archive)
    observed = inspect(archive, lock["archiveSHA256"])
    manifest = observed["manifest"]
    verify_profile_pins(root, manifest["sourceCommit"])
    selected = cache / (sha256(LOCK) + "-" + lock["archiveSHA256"] + "-" + recipe_sha256()[:12])
    if not selected.exists():
        if selected.is_symlink():
            raise ValueError("ArgumentParser selected cache is a symlink")
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = cache / (selected.name + ".pending-" + str(os.getpid()))
        temporary.mkdir(mode=0o700)
        try:
            with tarfile.open(archive, "r:gz") as source:
                for item in source:
                    name = item.name.removeprefix("argument-parser/")
                    if (item.name != "argument-parser/" + name or
                            name not in set(FILES) | {"layer.json"} or not item.isfile()):
                        raise ValueError("ArgumentParser archive has an unexpected member")
                    with source.extractfile(item) as stream:
                        (temporary / name).write_bytes(stream.read())
                    (temporary / name).chmod(0o644)
            (temporary / "BUILD.bazel").write_bytes(BUILD.read_bytes())
            (temporary / "BUILD.bazel").chmod(0o644)
            (temporary / "REPO.bazel").write_bytes(REPO.read_bytes())
            (temporary / "REPO.bazel").chmod(0o644)
            receipt = {"schema": 1, "archiveSHA256": lock["archiveSHA256"],
                       "lockSHA256": sha256(LOCK), "sourceCommit": manifest["sourceCommit"],
                       "buildSHA256": sha256(BUILD), "repoSHA256": sha256(REPO),
                       "recipeSHA256": recipe_sha256(),
                       "selectedRepository": REPOSITORY}
            (temporary / "import-receipt.json").write_text(json.dumps(receipt, sort_keys=True) + "\n")
            (temporary / "import-receipt.json").chmod(0o644)
            inventory(temporary, manifest)
            temporary.rename(selected)
        finally:
            if temporary.exists():
                import shutil
                shutil.rmtree(temporary)
    if selected.is_symlink() or not selected.is_dir():
        raise ValueError("ArgumentParser selected cache is invalid")
    inventory(selected, manifest)
    receipt = json.loads((selected / "import-receipt.json").read_text())
    if receipt != {"schema": 1, "archiveSHA256": lock["archiveSHA256"],
                   "lockSHA256": sha256(LOCK), "sourceCommit": manifest["sourceCommit"],
                   "buildSHA256": sha256(BUILD), "repoSHA256": sha256(REPO),
                   "recipeSHA256": recipe_sha256(),
                   "selectedRepository": REPOSITORY}:
        raise ValueError("ArgumentParser import receipt changed")
    return {**receipt, "selected": str(selected), "archive": str(archive)}


def verify_origin(output_base: Path, selected: Path) -> None:
    loaded = output_base / "external" / REPOSITORY
    if (not loaded.exists() or loaded.resolve(strict=True) != selected.resolve(strict=True)
            or sha256(loaded / "BUILD.bazel") != sha256(BUILD)):
        raise ValueError("Bazel did not load the admitted canonical ArgumentParser repository")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify-origin"))
    parser.add_argument("--output-base", type=Path)
    parser.add_argument("--selected", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        print(json.dumps(prepare(), sort_keys=True))
    else:
        if args.output_base is None or args.selected is None:
            parser.error("verify-origin needs --output-base and --selected")
        verify_origin(args.output_base, args.selected)


if __name__ == "__main__":
    main()
