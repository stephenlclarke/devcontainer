#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Reuse exact immutable prerelease assets while recording new publication tools."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


class ResumeError(ValueError):
    """Existing release assets cannot be authenticated for resumption."""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def api(endpoint: str, binary: bool = False) -> bytes:
    args = ["gh", "api", endpoint]
    if binary:
        args += ["-H", "Accept: application/octet-stream"]
    result = subprocess.run(args, capture_output=True, check=False)
    if result.returncode:
        if not binary and b"HTTP 404" in result.stderr:
            return b"null"
        raise ResumeError("GitHub release evidence could not be read")
    return result.stdout


def authenticate(release: dict | None, assets: Path, source: str,
                 verifier_sha: str, fetch_asset) -> dict:
    """Authenticate every package asset before reusing the original tool identity."""
    if release is None:
        return {"immutableResume": False}
    if release.get("draft") is not False or release.get("prerelease") is not True:
        raise ResumeError("existing release is not a staged prerelease")
    if release.get("immutable") is not True:
        return {"immutableResume": False}
    local = {path.name: path for path in assets.iterdir() if path.is_file()}
    entries = release.get("assets", [])
    names = [entry.get("name") for entry in entries]
    if len(names) != len(set(names)) or set(names) != set(local):
        raise ResumeError("immutable release asset inventory differs")
    original = None
    hashes = {}
    for entry in entries:
        name = entry["name"]
        path = local[name]
        if path.is_symlink():
            raise ResumeError("assembled asset is aliased")
        data = (fetch_asset(entry["id"]) if name == "publication-verifier.json"
                else path.read_bytes())
        if len(data) != entry.get("size") or "sha256:" + sha(data) != entry.get("digest"):
            raise ResumeError("immutable release asset bytes differ: " + name)
        hashes[name] = sha(data)
        if name == "publication-verifier.json":
            if len(data) > 16384:
                raise ResumeError("publication verifier identity exceeds its size limit")
            original = data
    if original is None:
        raise ResumeError("immutable release lacks publication verifier identity")
    identity = json.loads(original)
    if (identity.get("schemaVersion") != 1
            or identity.get("scope") != "publication-verifier-identity"
            or identity.get("sourceCommit") != source
            or identity.get("verifierSHA256") != verifier_sha
            or re.fullmatch(r"[0-9a-f]{40}", identity.get("verifierCommit", "")) is None):
        raise ResumeError("original publication verifier differs from the selected source or code")
    # This replaces only an assembled scratch copy with authenticated published bytes.
    (assets / "publication-verifier.json").write_bytes(original)
    return {"immutableResume": True, "releaseId": release["id"],
            "originalVerifierCommit": identity["verifierCommit"], "assetsSHA256": hashes}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--tool-commit", required=True)
    parser.add_argument("--verifier", type=Path, required=True)
    parser.add_argument("--installation-tool", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    release = json.loads(api(f"repos/{args.repository}/releases/tags/{args.tag}"))
    verifier_sha = sha(args.verifier.read_bytes())
    result = authenticate(release, args.assets, args.source_commit, verifier_sha,
                          lambda asset_id: api(f"repos/{args.repository}/releases/assets/{asset_id}", True))
    result.update({"schemaVersion": 1, "scope": "publication-operation-context",
                   "releaseAuthority": False,
                   "sourceCommit": args.source_commit, "toolCommit": args.tool_commit,
                   "verifierSHA256": verifier_sha,
                   "installationToolSHA256": sha(args.installation_tool.read_bytes())})
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"immutableResume": result["immutableResume"]}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ResumeError, OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit("immutable release resumption failed: " + str(error)) from error
