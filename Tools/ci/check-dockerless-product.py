#!/usr/bin/env python3
"""Fail when a product or release path acquires a non-Apple container runtime."""

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
    ROOT / "Examples",
    ROOT / "Packaging",
    ROOT / "Plugins",
    ROOT / "Sources",
    ROOT / "scripts",
    ROOT / "Tools" / "version-generator",
    ROOT / "Tools" / "release",
    ROOT / "Tools" / "ci",
    ROOT / ".github" / "workflows",
)
IGNORED_PATHS = {
    # This is the quarantined reference oracle, never a product or packaging
    # dependency.
    ROOT / ".github" / "workflows" / "parity.yml",
}
IGNORED_NAMES = {
    "check-dockerless-product.py",
    "test_dockerless_product.py",
}
TEST_SOURCE_ROOTS = {
    ROOT / "Tools" / "ci",
    ROOT / "Tools" / "release",
}
PROCESS_RUNNER = ROOT / "Sources" / "DevContainerProcess" / "ProcessRunner.swift"
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
        r"(?m)^\s*(?:sudo\s+)?(?:/\S+/)?(?:docker(?:d|-compose)?|com\.docker\.cli|podman|nerdctl)"
        r"\s+(?:--?[a-z]|[a-z])"
    ),
    re.compile(
        r"(?im)^\s*(?:exec|nohup|env)\b[^\n]*?\b"
        r"(?:docker(?:d|-compose)?|com\.docker\.cli|colima|podman|nerdctl)"
        r"\s+(?:--?[a-z]|[a-z])"
    ),
    re.compile(r"(?m)^\s*(?:sudo\s+)?(?:/\S+/)?colima\s+(?:--?[a-z]|[a-z])"),
    re.compile(
        r"(?i)\b(?:command\s+-v|which|shutil\.which\()\s*[\"']?"
        r"(?:docker|dockerd|docker-compose|com\.docker\.cli|colima|podman|nerdctl)\b"
    ),
    re.compile(
        r"(?i)\bbrew\s+(?:install|upgrade)\b[^\n]*"
        r"(?:docker|dockerd|docker-compose|com\.docker\.cli|colima|podman|nerdctl)\b"
    ),
    re.compile(
        r"(?i)depends_on\s+[\"']"
        r"(?:docker|dockerd|docker-compose|com\.docker\.cli|colima|podman|nerdctl)[\"']"
    ),
    re.compile(r"(?i)/Applications/Docker\.app\b"),
    re.compile(r"(?im)^\s*open\s+(?:--?application\s+|-a\s+)['\"]?Docker\b"),
    re.compile(r"(?i)https?://(?:get|download|desktop)\.docker\.com\b"),
    re.compile(
        r"(?i)\bsubprocess\.(?:run|Popen|call|check_call|check_output)\s*\(\s*"
        r"(?:\[\s*)?[\"']"
        r"(?:docker|dockerd|docker-compose|docker-buildx|com\.docker\.cli|colima|podman|nerdctl)[\"']"
    ),
    re.compile(
        r"(?i)(?:/opt/homebrew/bin|/usr/local/bin|/usr/bin|/bin)/"
        r"(?:docker|dockerd|docker-compose|docker-buildx|com\.docker\.cli|colima|podman|nerdctl)\b"
    ),
    re.compile(
        r"(?is)(?:executable|executableURL|fileURLWithPath)\s*:\s*"
        r"(?:URL\s*\(\s*fileURLWithPath\s*:\s*)?[\"']"
        r"(?:docker|dockerd|docker-compose|docker-buildx|com\.docker\.cli|colima|podman|nerdctl)[\"']"
    ),
    re.compile(
        r"(?is)\b(?:system|shell_output|IO\.popen)\s*\(?\s*[\"']"
        r"(?:docker|dockerd|docker-compose|docker-buildx|com\.docker\.cli|colima|podman|nerdctl)\b"
    ),
)
UNGUARDED_PROCESS_LAUNCH = re.compile(
    r"\b(?:Process\s*\(|NSTask\b|posix_spawn(?:p)?\s*\(|execv(?:e|p)?\s*\()"
)


def ignored_source(path: Path) -> bool:
    """Exclude only the oracle workflow and repository-owned test modules."""
    if path in IGNORED_PATHS or path.name in IGNORED_NAMES:
        return True
    return path.name.startswith("test_") and any(
        path.is_relative_to(root) for root in TEST_SOURCE_ROOTS
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
                and not ignored_source(path)
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
                findings.append(
                    f"{relative}:{line}: forbidden non-Apple runtime dependency: "
                    f"{excerpt}"
                )
        if (
            path.suffix == ".swift"
            and path.is_relative_to(ROOT / "Sources")
            and path != PROCESS_RUNNER
        ):
            for match in UNGUARDED_PROCESS_LAUNCH.finditer(contents):
                line = contents.count("\n", 0, match.start()) + 1
                relative = path.relative_to(root)
                findings.append(
                    f"{relative}:{line}: child process bypasses the Docker-less "
                    "ProcessRunner policy"
                )
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
