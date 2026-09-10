#!/usr/bin/env python3
"""Fail when a product or release path acquires a Docker/Colima dependency."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCANNED_PATHS = (
    ROOT / "Package.swift",
    ROOT / "Package.resolved",
    ROOT / "Package.stock.resolved",
    ROOT / "Makefile",
    ROOT / "Sources",
    ROOT / "scripts",
    ROOT / "Tools" / "release",
    ROOT / "Tools" / "ci" / "bootstrap.sh",
    ROOT / ".github" / "workflows" / "ci.yml",
    ROOT / ".github" / "workflows" / "codeql.yml",
    ROOT / ".github" / "workflows" / "dependency-review.yml",
    ROOT / ".github" / "workflows" / "docs.yml",
    ROOT / ".github" / "workflows" / "homebrew.yml",
    ROOT / ".github" / "workflows" / "prebuilt-binaries.yml",
    ROOT / ".github" / "workflows" / "quality.yml",
    ROOT / ".github" / "workflows" / "scorecard.yml",
    ROOT / ".github" / "workflows" / "sonar.yml",
    ROOT / ".github" / "workflows" / "specification-drift.yml",
    ROOT / ".github" / "workflows" / "stable-release-gate.yml",
)
IGNORED_NAMES = {
    "check-dockerless-product.py",
    "test_dockerless_product.py",
}
TEXT_SUFFIXES = {
    "",
    ".in",
    ".json",
    ".py",
    ".resolved",
    ".sh",
    ".swift",
    ".toml",
    ".yml",
    ".yaml",
}

# Docker vocabulary is intentionally present in the compatibility protocol. These
# expressions prohibit executable/runtime acquisition, not protocol names such as
# `devcontainer-docker`, `DockerHTTPRequest`, DOCKER_HOST, or dockerPath.
FORBIDDEN = (
    re.compile(
        r"(?m)^\s*(?:sudo\s+)?(?:/\S+/)?docker(?:-compose)?"
        r"\s+(?:--?[a-z]|[a-z])"
    ),
    re.compile(r"(?m)^\s*(?:sudo\s+)?(?:/\S+/)?colima\s+(?:--?[a-z]|[a-z])"),
    re.compile(r"(?i)\b(?:command\s+-v|which|shutil\.which\()\s*[\"']?(?:docker|docker-compose|colima)\b"),
    re.compile(r"(?i)\bbrew\s+(?:install|upgrade)\b[^\n]*(?:docker|docker-compose|colima)\b"),
    re.compile(r"(?i)depends_on\s+[\"'](?:docker|docker-compose|colima)[\"']"),
    re.compile(r"(?i)/Applications/Docker\.app\b"),
)


def source_files() -> list[Path]:
    """Return the fail-closed product/build/release source inventory."""
    files: list[Path] = []
    for candidate in SCANNED_PATHS:
        if candidate.is_file():
            files.append(candidate)
        elif candidate.is_dir():
            files.extend(
                path
                for path in candidate.rglob("*")
                if path.is_file()
                and path.name not in IGNORED_NAMES
                and not path.name.startswith("test_")
                and path.suffix.lower() in TEXT_SUFFIXES
            )
        else:
            raise FileNotFoundError(f"required audit path is missing: {candidate}")
    return sorted(set(files))


def violations(root: Path = ROOT) -> list[str]:
    """Report forbidden product dependencies with stable file/line locations."""
    findings: list[str] = []
    for path in source_files():
        contents = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN:
            for match in pattern.finditer(contents):
                line = contents.count("\n", 0, match.start()) + 1
                relative = path.relative_to(root)
                excerpt = match.group(0).strip().replace("\n", " ")
                findings.append(f"{relative}:{line}: forbidden Docker/Colima product dependency: {excerpt}")
    return sorted(set(findings))


def main() -> int:
    """Run the audit, keeping the real Docker oracle outside the scanned paths."""
    findings = violations()
    if findings:
        print("\n".join(findings), file=sys.stderr)
        return 1
    print("Docker-less product boundary verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
