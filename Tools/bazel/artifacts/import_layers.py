#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Admit exact compiled package archives before Bazel loads their repositories."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat

if __package__:
    from .argument_parser import file_digest
    from .foundation import (GROUPS, layer_lock_path, inspect, verify_consumer)
    from .layer_release import cached_release
    from .import_argument_parser import owned_cache
else:
    from argument_parser import file_digest
    from foundation import GROUPS, layer_lock_path, inspect, verify_consumer
    from layer_release import cached_release
    from import_argument_parser import owned_cache

ROOT = Path(__file__).resolve().parents[3]
CACHE = Path.home() / "Library/Application Support/ContainerFamily/retained/devcontainer/binary-layers"
ORDER = ("foundation", "containerization", "engine-api", "container-sdk")


def admitted(root: Path, profile: str, groups: set[str], cache: Path = CACHE) -> dict[str, dict]:
    if profile not in {"enhanced", "stock"} or not groups or not groups.issubset(GROUPS):
        raise ValueError("compiled layer selection needs an exact profile and groups")
    owned_cache(cache)
    result = {}
    for group in ORDER:
        if group not in groups:
            continue
        lock_path = layer_lock_path(root, group, profile)
        verify_consumer(lock_path, root, None, os.environ, profile, group)
        archive = cached_release(lock_path, cache / group / profile)
        lock = verify_consumer(lock_path, root, archive, os.environ, profile, group)
        manifest = inspect(archive, lock["archiveSHA256"])["manifest"]
        result[group] = {"archive": str(archive), "lock": lock,
                         "manifest": manifest, "lockSHA256": file_digest(lock_path)}
    return result


def verify_origin(output_base: Path, admitted_groups: dict[str, dict]) -> None:
    if not output_base.is_absolute() or output_base.is_symlink() or not output_base.is_dir():
        raise ValueError("Bazel output base is not a real absolute directory")
    external = output_base / "external"
    for group, selected in admitted_groups.items():
        manifest = selected["manifest"]
        for package in manifest["packages"].values():
            repository = package["repository"]
            loaded = external / ("+dependencies+" + repository)
            if loaded.is_symlink() or not loaded.is_dir():
                raise ValueError(f"Bazel did not load canonical {repository} repository")
            prefix = group + "/" + repository + "/"
            members = {name[len(prefix):]: sha for name, sha in manifest["files"].items()
                       if name.startswith(prefix)}
            if "BUILD.bazel" not in members or "prebuilt.bzl" not in members:
                raise ValueError(f"sealed {repository} import recipe is incomplete")
            for name, sha in members.items():
                path = loaded / name
                current = path.parent
                while current != loaded:
                    if current.is_symlink() or not current.is_dir():
                        raise ValueError(f"Bazel loaded a noncanonical compiled path: {repository}/{name}")
                    current = current.parent
                if (path.is_symlink() or not path.is_file() or
                        stat.S_IMODE(path.stat().st_mode) & 0o022 or file_digest(path) != sha):
                    raise ValueError(f"Bazel loaded different compiled bytes: {repository}/{name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify-origin"))
    parser.add_argument("--profile", required=True, choices=("enhanced", "stock"))
    parser.add_argument("--group", action="append", choices=GROUPS, required=True)
    parser.add_argument("--output-base", type=Path)
    args = parser.parse_args()
    selected = admitted(ROOT, args.profile, set(args.group))
    if args.action == "prepare":
        for group in ORDER:
            if group in selected:
                archive = Path(selected[group]["archive"])
                print(group + "\t" + str(archive) + "\t" + archive.as_uri())
    else:
        if args.output_base is None:
            parser.error("verify-origin requires --output-base")
        verify_origin(args.output_base, selected)


if __name__ == "__main__":
    main()
