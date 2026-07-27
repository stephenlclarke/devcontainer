"""Load the exact resolved dependency and reviewed license ledger."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


ALLOWED_LICENSES = frozenset(
    {
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "ISC",
        "MIT",
        "Unicode-3.0",
        "Zlib",
    }
)


@dataclass(frozen=True)
class Dependency:
    """One immutable SwiftPM pin with its reviewed distribution license."""

    identity: str
    license: str
    location: str
    revision: str
    version: str


def load_dependencies(
    resolved_path: Path,
    license_manifest_path: Path,
) -> list[Dependency]:
    """Return exact pins and fail if the reviewed license ledger has drifted."""

    resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
    raw_pins = resolved.get("pins", resolved.get("object", {}).get("pins", []))
    if not isinstance(raw_pins, list):
        raise ValueError("Package.resolved pins must be an array")
    if not all(isinstance(pin, dict) for pin in raw_pins):
        raise ValueError("Package.resolved contains a non-object pin")
    license_document = json.loads(
        license_manifest_path.read_text(encoding="utf-8")
    )
    if (
        not isinstance(license_document, dict)
        or license_document.get("schemaVersion") != 1
        or not isinstance(license_document.get("licenses"), dict)
    ):
        raise ValueError("dependency license ledger must use schema version 1")
    licenses = license_document["licenses"]
    if not all(
        isinstance(identity, str) and isinstance(identifier, str)
        for identity, identifier in licenses.items()
    ):
        raise ValueError("dependency license ledger entries must be strings")
    identities = [
        pin.get("identity")
        for pin in raw_pins
        if isinstance(pin.get("identity"), str)
    ]
    if len(identities) != len(raw_pins) or len(set(identities)) != len(identities):
        raise ValueError("Package.resolved pin identities are missing or duplicated")
    identity_set = set(identities)
    if identity_set != set(licenses):
        missing = sorted(identity_set.difference(licenses))
        stale = sorted(set(licenses).difference(identity_set))
        raise ValueError(
            f"dependency license ledger drifted (missing={missing}, stale={stale})"
        )

    dependencies: list[Dependency] = []
    for pin in sorted(raw_pins, key=lambda value: value.get("identity", "")):
        identity = pin.get("identity")
        location = pin.get("location")
        state = pin.get("state")
        if (
            not isinstance(identity, str)
            or not isinstance(location, str)
            or not isinstance(state, dict)
        ):
            raise ValueError("Package.resolved pin identity, location, or state is invalid")
        revision = state.get("revision")
        version = state.get("version") or revision
        license_identifier = licenses[identity]
        if (
            not isinstance(revision, str)
            or len(revision) != 40
            or any(character not in "0123456789abcdef" for character in revision)
            or not isinstance(version, str)
            or not isinstance(license_identifier, str)
            or license_identifier not in ALLOWED_LICENSES
        ):
            raise ValueError(f"dependency metadata is invalid for {identity}")
        dependencies.append(
            Dependency(
                identity=identity,
                license=license_identifier,
                location=location,
                revision=revision,
                version=version,
            )
        )
    return dependencies
