#!/usr/bin/env python3
"""Resolve and prepare devcontainer semantic release versions."""

from __future__ import annotations

import argparse
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


SEMVER_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
VERSION_ASSIGNMENT_PATTERN = re.compile(
    r"^DEVCONTAINER_VERSION[ \t]*\?=[ \t]*(?P<version>\S+)[ \t]*$",
    re.MULTILINE,
)
SELECTORS = {"--+", "-+-", "+--"}


@dataclass(frozen=True, order=True)
class SemanticVersion:
    """A strict three-component semantic release version."""

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> SemanticVersion:
        if not SEMVER_PATTERN.fullmatch(value):
            raise ValueError(f"version must be MAJOR.MINOR.PATCH: {value}")
        major, minor, patch = (int(component) for component in value.split("."))
        return cls(major, minor, patch)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def read_makefile_version(path: Path) -> SemanticVersion:
    """Read the repository's single authoritative product version."""

    matches = list(VERSION_ASSIGNMENT_PATTERN.finditer(path.read_text(encoding="utf-8")))
    if len(matches) != 1:
        raise ValueError(
            f"{path} must contain exactly one DEVCONTAINER_VERSION ?= assignment"
        )
    return SemanticVersion.parse(matches[0].group("version"))


def semantic_tags(values: Iterable[str]) -> list[SemanticVersion]:
    """Return strict semantic tags in numeric order."""

    versions = [
        SemanticVersion.parse(value.strip())
        for value in values
        if SEMVER_PATTERN.fullmatch(value.strip())
    ]
    return sorted(set(versions))


def list_git_tags(repository: Path) -> list[str]:
    """List local Git tags without interpreting shell output."""

    result = subprocess.run(
        ["git", "-C", str(repository), "tag", "--list"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def resolve_selector(
    selector: str,
    base: SemanticVersion,
) -> SemanticVersion:
    """Resolve an explicit version or a compose-compatible increment selector."""

    if SEMVER_PATTERN.fullmatch(selector):
        return SemanticVersion.parse(selector)
    if selector not in SELECTORS:
        raise ValueError(
            f"invalid version selector {selector!r}; "
            "expected MAJOR.MINOR.PATCH, --+, -+-, or +--"
        )
    if selector == "+--":
        return SemanticVersion(base.major + 1, 0, 0)
    if selector == "-+-":
        return SemanticVersion(base.major, base.minor + 1, 0)
    return SemanticVersion(base.major, base.minor, base.patch + 1)


def release_version(
    selector: str,
    checked_in: SemanticVersion,
    tags: Iterable[str],
) -> SemanticVersion:
    """Resolve and validate a stable release candidate."""

    releases = semantic_tags(tags)
    latest = releases[-1] if releases else None
    base = latest or checked_in
    candidate = resolve_selector(selector, base)
    if latest is not None and candidate <= latest:
        raise ValueError(
            f"release version {candidate} must be newer than latest release tag {latest}"
        )
    if candidate < checked_in:
        raise ValueError(
            f"release version {candidate} is older than checked-in version {checked_in}"
        )
    return candidate


def write_makefile_version(path: Path, version: SemanticVersion) -> None:
    """Atomically update only the authoritative Makefile declaration."""

    source = path.read_text(encoding="utf-8")
    matches = list(VERSION_ASSIGNMENT_PATTERN.finditer(source))
    if len(matches) != 1:
        raise ValueError(
            f"{path} must contain exactly one DEVCONTAINER_VERSION ?= assignment"
        )
    match = matches[0]
    updated = source[: match.start("version")] + str(version) + source[match.end("version") :]
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        temporary.write(updated)
        temporary_path = Path(temporary.name)
    temporary_path.chmod(path.stat().st_mode)
    temporary_path.replace(path)


def parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve a devcontainer stable version selector."
    )
    parser.add_argument("--selector", required=True)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--makefile", type=Path)
    parser.add_argument(
        "--write",
        action="store_true",
        help="atomically update the Makefile after successful validation",
    )
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    args = parse_args(arguments)
    repository = args.repository.resolve()
    makefile = (args.makefile or repository / "Makefile").resolve()
    try:
        checked_in = read_makefile_version(makefile)
        candidate = release_version(
            args.selector,
            checked_in,
            list_git_tags(repository),
        )
        if args.write:
            write_makefile_version(makefile, candidate)
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        raise SystemExit(str(error)) from error
    print(candidate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
