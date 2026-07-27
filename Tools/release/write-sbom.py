#!/usr/bin/env python3
"""Create a deterministic SPDX 2.3 SBOM from Package.resolved."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def spdx_id(name: str) -> str:
    return "SPDXRef-" + "".join(character if character.isalnum() else "-" for character in name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    resolved = json.loads(Path("Package.resolved").read_text(encoding="utf-8"))
    pins = resolved.get("pins", resolved.get("object", {}).get("pins", []))
    packages = [
        {
            "SPDXID": "SPDXRef-Package-devcontainer",
            "name": "devcontainer",
            "versionInfo": args.version,
            "downloadLocation": "https://github.com/stephenlclarke/devcontainer",
            "licenseConcluded": "Apache-2.0",
            "licenseDeclared": "Apache-2.0",
            "filesAnalyzed": False,
        }
    ]
    relationships = []
    for pin in sorted(pins, key=lambda value: value.get("identity", "")):
        name = pin.get("identity", "unknown")
        state = pin.get("state", {})
        version = state.get("version") or state.get("revision") or "unspecified"
        identifier = spdx_id(name)
        packages.append(
            {
                "SPDXID": identifier,
                "name": name,
                "versionInfo": version,
                "downloadLocation": pin.get("location", "NOASSERTION"),
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "filesAnalyzed": False,
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
        f"devcontainer:{args.version}".encode()
    ).hexdigest()
    document = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"devcontainer-{args.version}",
        "documentNamespace": (
            "https://github.com/stephenlclarke/devcontainer/sbom/"
            + namespace_hash
        ),
        "creationInfo": {
            "created": "2026-01-01T00:00:00Z",
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
