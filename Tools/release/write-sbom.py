#!/usr/bin/env python3
"""Create a deterministic SPDX 2.3 SBOM from Package.resolved."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from dependency_metadata import load_dependencies
from versioning import require_commit, require_semantic_version


def spdx_id(name: str) -> str:
    """Return a stable SPDX identifier for a package name."""

    normalized = "".join(
        character if character.isalnum() else "-"
        for character in name
    )
    return "SPDXRef-" + normalized


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolved", type=Path, default=Path("Package.resolved"))
    parser.add_argument(
        "--license-manifest",
        type=Path,
        default=Path("Tools/release/dependency-licenses.json"),
    )
    args = parser.parse_args()
    version = require_semantic_version(args.version)
    commit = (
        args.commit
        if args.commit == "unspecified"
        else require_commit(args.commit)
    )
    if args.source_date_epoch < 0:
        parser.error("--source-date-epoch must be non-negative")
    created = datetime.fromtimestamp(
        args.source_date_epoch,
        timezone.utc,
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    dependencies = load_dependencies(args.resolved, args.license_manifest)
    packages = [
        {
            "SPDXID": "SPDXRef-Package-devcontainer",
            "name": "devcontainer",
            "versionInfo": version,
            "downloadLocation": "https://github.com/stephenlclarke/devcontainer",
            "licenseConcluded": "Apache-2.0",
            "licenseDeclared": "Apache-2.0",
            "filesAnalyzed": False,
            "sourceInfo": f"Exact Git commit {commit}",
        }
    ]
    relationships = []
    for dependency in dependencies:
        identifier = spdx_id(dependency.identity)
        packages.append(
            {
                "SPDXID": identifier,
                "name": dependency.identity,
                "versionInfo": dependency.version,
                "downloadLocation": dependency.location,
                "licenseConcluded": dependency.license,
                "licenseDeclared": dependency.license,
                "filesAnalyzed": False,
                "sourceInfo": f"Exact Git revision {dependency.revision}",
            }
        )
        relationships.append(
            {
                "spdxElementId": "SPDXRef-Package-devcontainer",
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": identifier,
            }
        )
    namespace_hash = hashlib.sha256(
        f"devcontainer:{version}:{commit}".encode()
    ).hexdigest()
    document = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"devcontainer-{version}",
        "documentNamespace": (
            "https://github.com/stephenlclarke/devcontainer/sbom/"
            + namespace_hash
        ),
        "creationInfo": {
            "created": created,
            "creators": ["Tool: devcontainer-write-sbom"],
        },
        "packages": packages,
        "relationships": relationships,
    }
    args.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
