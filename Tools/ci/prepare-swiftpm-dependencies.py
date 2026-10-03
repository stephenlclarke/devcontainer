#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Prepare exact enhanced SwiftPM checkouts with the reviewed Bazel patches."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import subprocess
import tempfile
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
PATCHES = (
    {"identity": "zstd", "revision": "f8745da6ff1ad1e7bab384bd1f9d742439278e99",
     "location": "https://github.com/facebook/zstd.git", "patch": "zstd-public-module.patch",
     "sha256": "4750e8650eaa5205db05a5d792478633b6d30154cea31fdba628b5b97cc15927"},
    {"identity": "containerization", "revision": "6db16197bbad8196a78132f86529daa89125aafb",
     "location": "https://github.com/stephenlclarke/containerization.git",
     "patch": "containerization-ext4-unaligned.patch",
     "sha256": "960284f67cca0ba416da98f624934454e092d204b4525daf9902e0a0bbe7038d"},
    {"identity": "container-engine-api", "revision": "48e44d74d738ca3d24351ba02c4869be1a3e6998",
     "location": "https://github.com/stephenlclarke/container-engine-api.git",
     "patch": "gateway-recovery-capability.patch",
     "sha256": "be69369a63c8c372b79ef83931125790881d057719846ca5499539c99df8bfb7"},
)
SOURCE_OVERRIDES = (
    "CONTAINER_PACKAGE_PATH", "CONTAINERIZATION_PACKAGE_PATH",
    "CONTAINER_ENGINE_API_PACKAGE_PATH",
)
PROFILE_LOCKS = {"enhanced": "Package.resolved", "stock": "Package.stock.resolved"}


@dataclass(frozen=True)
class PatchSpec:
    identity: str
    revision: str
    location: str
    checkout: Path
    patch: Path
    sha256: str


@dataclass(frozen=True)
class PatchState:
    spec: PatchSpec
    paths: tuple[str, ...]
    expected: Mapping[str, tuple[bytes, int] | None]
    disposition: str


def run(command: Sequence[str], *, cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True,
                          timeout=timeout, check=False)


def checked(command: Sequence[str], *, cwd: Path, timeout: int = 30) -> str:
    result = run(command, cwd=cwd, timeout=timeout)
    if result.returncode:
        raise ValueError("command failed: " + " ".join(command) + ": " + result.stderr.strip())
    return result.stdout.strip()


def pins(lock_path: Path) -> dict[str, dict]:
    rows = json.loads(lock_path.read_text(encoding="utf-8")).get("pins")
    if not isinstance(rows, list):
        raise ValueError("selected SwiftPM lock has no pin list")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("identity"), str):
            raise ValueError("selected SwiftPM lock has a malformed pin")
        if row["identity"] in result:
            raise ValueError("selected SwiftPM lock has duplicate identities")
        result[row["identity"]] = row
    return result


def validate_lock(lock: Path, profile: str, patch_rows: Sequence[Mapping[str, str]]) -> None:
    if profile == "enhanced":
        expected = patch_rows
    else:
        expected = (
            {"identity": "container", "revision": "9a8917ca2da5cd6ba059b9ba5ca5a74892e9bb7d",
             "location": "https://github.com/apple/container.git"},
            {"identity": "containerization", "revision": "9eacc197d7c3663eb29cbab6d51244ede6d1cd7d",
             "location": "https://github.com/apple/containerization.git"},
            {"identity": "container-engine-api", "revision": "40436017e1e93012b8dab7cfc3c79783538065c3",
             "location": "https://github.com/stephenlclarke/container-engine-api.git"},
        )
    actual = pins(lock)
    for pin in expected:
        row = actual.get(pin["identity"])
        state = row.get("state") if isinstance(row, dict) else None
        if (not isinstance(state, dict) or row.get("kind") != "remoteSourceControl"
                or row.get("location") != pin["location"]
                or state.get("revision") != pin["revision"]):
            raise ValueError("selected SwiftPM lock has an unexpected pin: " + pin["identity"])


def patch_paths(patch: Path) -> tuple[str, ...]:
    paths = set()
    for line in patch.read_text(encoding="utf-8").splitlines():
        if not line.startswith("diff --git a/"):
            continue
        match = re.fullmatch(r"diff --git a/(.+) b/(.+)", line)
        if not match or match.group(1) != match.group(2):
            raise ValueError("patch contains an unsupported or renamed path")
        relative = PurePosixPath(match.group(1))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("patch contains an unsafe path")
        paths.add(relative.as_posix())
    if not paths:
        raise ValueError("patch contains no file changes")
    return tuple(sorted(paths))


def status_paths(spec: PatchSpec) -> tuple[str, ...]:
    result = run(["git", "status", "--porcelain=v1", "--ignored", "-z",
                  "--untracked-files=all"],
                 cwd=spec.checkout)
    if result.returncode:
        raise ValueError("could not inspect SwiftPM checkout status: " + spec.identity)
    output = result.stdout
    paths = []
    for record in output.split("\0"):
        if not record:
            continue
        if len(record) < 4 or record[2] != " ":
            raise ValueError("checkout status output is malformed")
        paths.append(record[3:])
    return tuple(sorted(paths))


def _baseline_bytes(checkout: Path, relative: str) -> bytes | None:
    result = subprocess.run(["git", "show", "HEAD:" + relative], cwd=checkout,
                            capture_output=True, check=False)
    return result.stdout if result.returncode == 0 else None


def expected_patch_output(spec: PatchSpec, paths: Sequence[str]) -> dict[str, tuple[bytes, int] | None]:
    """Apply the reviewed patch to a temporary tree containing only HEAD blobs it touches."""
    with tempfile.TemporaryDirectory(prefix="swiftpm-patch-proof-") as temporary:
        tree = Path(temporary)
        for relative in paths:
            original = _baseline_bytes(spec.checkout, relative)
            if original is not None:
                target = tree / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(original)
        checked(["git", "init", "-q"], cwd=tree)
        checked(["git", "config", "user.name", "Patch verifier"], cwd=tree)
        checked(["git", "config", "user.email", "patch-verifier@example.invalid"], cwd=tree)
        checked(["git", "add", "--all"], cwd=tree)
        checked(["git", "commit", "-q", "-m", "pinned baseline"], cwd=tree)
        checked(["git", "apply", "--check", str(spec.patch)], cwd=tree)
        checked(["git", "apply", str(spec.patch)], cwd=tree)
        checked(["git", "add", "--all"], cwd=tree)
        modes = {}
        for row in checked(["git", "ls-files", "--stage", "-z"], cwd=tree).split("\0"):
            if row:
                metadata, relative = row.split("\t", 1)
                mode = int(metadata.split()[0], 8)
                if mode not in (0o100644, 0o100755):
                    raise ValueError("reviewed patch has an unsupported file mode: " + relative)
                modes[relative] = mode
        return {relative: ((tree / relative).read_bytes(), modes[relative])
                if relative in modes else None for relative in paths}


def verify_checkout_identity(spec: PatchSpec) -> None:
    path = spec.checkout
    if path.is_symlink() or not path.is_dir() or path.resolve() != path:
        raise ValueError("SwiftPM checkout is missing or noncanonical: " + spec.identity)
    if Path(checked(["git", "rev-parse", "--show-toplevel"], cwd=path)).resolve() != path:
        raise ValueError("SwiftPM checkout is not its own Git root: " + spec.identity)
    if checked(["git", "rev-parse", "HEAD"], cwd=path) != spec.revision:
        raise ValueError("SwiftPM checkout revision differs from selected lock: " + spec.identity)
    mirror_path = Path(checked(["git", "remote", "get-url", "origin"], cwd=path))
    repositories = path.parent.parent / "repositories"
    if mirror_path.is_symlink() or not mirror_path.is_absolute():
        raise ValueError("SwiftPM checkout origin is not its scratch-local package mirror: " + spec.identity)
    mirror = mirror_path.resolve(strict=True)
    if (not mirror.is_dir()
            or mirror.parent != repositories.resolve(strict=True)
            or not mirror.name.startswith(spec.identity + "-")):
        raise ValueError("SwiftPM checkout origin is not its scratch-local package mirror: " + spec.identity)
    if checked(["git", "rev-parse", "--is-bare-repository"], cwd=mirror) != "true":
        raise ValueError("SwiftPM package mirror is not bare: " + spec.identity)
    if checked(["git", "remote", "get-url", "origin"], cwd=mirror) != spec.location:
        raise ValueError("SwiftPM package mirror origin differs from selected lock: " + spec.identity)
    checked(["git", "cat-file", "-e", spec.revision + "^{commit}"], cwd=mirror)


def preflight(spec: PatchSpec) -> PatchState:
    if hashlib.sha256(spec.patch.read_bytes()).hexdigest() != spec.sha256:
        raise ValueError("reviewed patch bytes changed: " + spec.identity)
    verify_checkout_identity(spec)
    paths = patch_paths(spec.patch)
    if run(["git", "diff", "--cached", "--quiet"], cwd=spec.checkout).returncode:
        raise ValueError("SwiftPM checkout has staged changes: " + spec.identity)
    actual = status_paths(spec)
    expected = expected_patch_output(spec, paths)
    if not actual:
        checked(["git", "apply", "--check", str(spec.patch)], cwd=spec.checkout)
        return PatchState(spec, paths, expected, "pending")
    if actual != paths:
        raise ValueError("SwiftPM checkout has unrelated or partial changes: " + spec.identity)
    for relative, wanted in expected.items():
        current = spec.checkout / relative
        if wanted is None:
            if current.exists() or current.is_symlink():
                raise ValueError("unexpected patch-added file: " + relative)
        elif (current.is_symlink() or not current.is_file()
              or current.read_bytes() != wanted[0]
              or stat.S_IMODE(current.stat().st_mode) != wanted[1] & 0o777):
            raise ValueError("checkout differs from exact reviewed patch output: " + spec.identity)
    return PatchState(spec, paths, expected, "already-applied")


def verify_applied(state: PatchState) -> None:
    verify_checkout_identity(state.spec)
    if status_paths(state.spec) != state.paths:
        raise ValueError("patch application produced unexpected checkout changes: " + state.spec.identity)
    for relative, wanted in state.expected.items():
        actual = state.spec.checkout / relative
        if wanted is None:
            if actual.exists() or actual.is_symlink():
                raise ValueError("unexpected patch-added path: " + relative)
        elif (actual.is_symlink() or not actual.is_file()
              or actual.read_bytes() != wanted[0]
              or stat.S_IMODE(actual.stat().st_mode) != wanted[1] & 0o777):
            raise ValueError("patch output differs from reviewed bytes or mode: " + state.spec.identity)


def _resolve_missing(swift: Sequence[str], root: Path, scratch: Path,
                     lock: Path, original_lock: bytes, profile: str,
                     patch_rows: Sequence[Mapping[str, str]],
                     env: Mapping[str, str], timeout: int) -> None:
    existing = [scratch / "checkouts" / row["identity"] for row in patch_rows]
    for checkout in existing:
        if checkout.exists() and (checkout.is_symlink() or not checkout.is_dir()):
            raise ValueError("SwiftPM scratch checkout path is not a real directory")
    if any(path.exists() for path in existing):
        raise ValueError("partial SwiftPM patch checkout set; resolve explicitly before preparation")
    before = lock.read_bytes()
    resolve_env = {**env, "DEVCONTAINER_RUNTIME_PROFILE": profile}
    command = [*swift, "package", "--scratch-path", str(scratch), "resolve"]
    result = subprocess.run(command, cwd=root, env=dict(resolve_env), text=True,
                            capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise ValueError("SwiftPM dependency resolution failed: " + result.stderr.strip())
    if lock.read_bytes() != before or before != original_lock:
        raise ValueError("SwiftPM resolve changed the selected lock bytes")


def prepare(root: Path, scratch_path: Path, profile: str | None = None,
            swift: Sequence[str] = ("swift",), environment: Mapping[str, str] | None = None,
            timeout: int = 180, *,
            patch_rows: Sequence[Mapping[str, str]] = PATCHES) -> dict:
    env = dict(os.environ if environment is None else environment)
    selected = profile or env.get("DEVCONTAINER_RUNTIME_PROFILE") or "enhanced"
    if selected not in PROFILE_LOCKS:
        raise ValueError("DEVCONTAINER_RUNTIME_PROFILE must be enhanced or stock")
    for name in SOURCE_OVERRIDES:
        if env.get(name):
            raise ValueError("authoritative SwiftPM preparation rejects package override: " + name)
    root = root.resolve(strict=True)
    lock = root / PROFILE_LOCKS[selected]
    lock_bytes = lock.read_bytes()
    validate_lock(lock, selected, patch_rows)
    if selected == "stock":
        if (root / "Package.resolved").read_bytes() != lock_bytes:
            raise ValueError("active Package.resolved differs from authoritative stock lock")
        return {"schemaVersion": 1, "profile": "stock", "status": "unmodified"}

    scratch = scratch_path if scratch_path.is_absolute() else root / scratch_path
    scratch = scratch.resolve()
    if not scratch.is_relative_to(root):
        raise ValueError("SwiftPM scratch path must remain inside repository")
    if any(not (scratch / "checkouts" / row["identity"]).exists() for row in patch_rows):
        _resolve_missing(swift, root, scratch, lock, lock_bytes, selected,
                         patch_rows, env, timeout)
    if lock.read_bytes() != lock_bytes:
        raise ValueError("selected SwiftPM lock changed during preparation")
    specs = [PatchSpec(row["identity"], row["revision"], row["location"],
                       scratch / "checkouts" / row["identity"],
                       root / "Tools/bazel" / row["patch"], row["sha256"])
                      for row in patch_rows]
    states = [preflight(spec) for spec in specs]
    applied = []
    try:
        for state in states:
            if state.disposition == "already-applied":
                continue
            if status_paths(state.spec):
                raise ValueError("SwiftPM checkout changed after preflight: " + state.spec.identity)
            verify_checkout_identity(state.spec)
            checked(["git", "apply", str(state.spec.patch)], cwd=state.spec.checkout)
            applied.append(state)
        for state in states:
            verify_applied(state)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        for state in reversed(applied):
            try:
                verify_applied(state)
                checked(["git", "apply", "--reverse", str(state.spec.patch)], cwd=state.spec.checkout)
                if status_paths(state.spec):
                    raise ValueError("rollback left checkout changes: " + state.spec.identity)
            except (OSError, ValueError, subprocess.SubprocessError) as rollback_error:
                raise ValueError("patch failure left uncertain checkout state: " +
                                 str(rollback_error)) from error
        raise ValueError("patch preparation failed and earlier patches were rolled back: " +
                         str(error)) from error
    if lock.read_bytes() != lock_bytes:
        raise ValueError("selected SwiftPM lock changed while patches were applied")
    return {"schemaVersion": 1, "profile": "enhanced", "status": "prepared",
            "patches": [{"identity": item.spec.identity, "revision": item.spec.revision,
                         "sha256": item.spec.sha256, "status": "applied"} for item in states]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--scratch-path", type=Path, required=True)
    parser.add_argument("--profile", choices=tuple(PROFILE_LOCKS))
    parser.add_argument("--swift", default="swift")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()
    result = prepare(args.repository, args.scratch_path, args.profile,
                     shlex.split(args.swift), timeout=args.timeout_seconds)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
