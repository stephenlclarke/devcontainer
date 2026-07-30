#!/usr/bin/env python3
"""Render a package README whose repository links target an exact commit."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import quote, urlsplit

from versioning import require_commit


MARKDOWN_LINK_PATTERN = re.compile(
    r"(?P<prefix>!?\[[^\]]*\]\()(?P<target>[^)\s]+)(?P<suffix>\))"
)
HTML_LINK_PATTERN = re.compile(
    r"\b(?P<attribute>href|src)=(?P<quote>['\"])(?P<target>[^'\"]+)(?P=quote)"
)
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def is_external(target: str) -> bool:
    """Return whether a target is already independent of the package root."""

    if target.startswith("#"):
        return True
    return urlsplit(target).scheme.lower() in {"http", "https", "mailto"}


def source_url(
    target: str,
    *,
    image: bool,
    repository: str,
    repository_root: Path,
    revision: str,
) -> str:
    """Resolve one repository-relative target to an immutable source URL."""

    if is_external(target):
        return target
    path_text, separator, fragment = target.partition("#")
    relative = Path(path_text)
    if relative.is_absolute() or ".." in relative.parts or not path_text:
        raise ValueError(f"unsafe package README target: {target}")
    root = repository_root.resolve(strict=True)
    source = (root / relative).resolve(strict=True)
    if source != root and root not in source.parents:
        raise ValueError(f"package README target escapes the repository: {target}")
    encoded_path = quote(relative.as_posix(), safe="/")
    encoded_fragment = quote(fragment, safe="-._~") if separator else ""
    suffix = f"#{encoded_fragment}" if separator else ""
    if image:
        if not source.is_file():
            raise ValueError(f"package README image is not a file: {target}")
        return (
            f"https://raw.githubusercontent.com/{repository}/{revision}/"
            f"{encoded_path}{suffix}"
        )
    kind = "tree" if source.is_dir() else "blob"
    return (
        f"https://github.com/{repository}/{kind}/{revision}/"
        f"{encoded_path}{suffix}"
    )


def render(
    text: str,
    *,
    repository: str,
    repository_root: Path,
    revision: str,
) -> str:
    """Rewrite Markdown and HTML source references in README text."""

    if REPOSITORY_PATTERN.fullmatch(repository) is None:
        raise ValueError(f"invalid GitHub repository name: {repository}")
    revision = require_commit(revision)

    def replace_markdown(match: re.Match[str]) -> str:
        target = source_url(
            match.group("target"),
            image=match.group("prefix").startswith("!"),
            repository=repository,
            repository_root=repository_root,
            revision=revision,
        )
        return f"{match.group('prefix')}{target}{match.group('suffix')}"

    def replace_html(match: re.Match[str]) -> str:
        target = source_url(
            match.group("target"),
            image=match.group("attribute") == "src",
            repository=repository,
            repository_root=repository_root,
            revision=revision,
        )
        quote_character = match.group("quote")
        return (
            f"{match.group('attribute')}={quote_character}"
            f"{target}{quote_character}"
        )

    return HTML_LINK_PATTERN.sub(
        replace_html,
        MARKDOWN_LINK_PATTERN.sub(replace_markdown, text),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        rendered = render(
            arguments.source.read_text(encoding="utf-8"),
            repository=arguments.repository,
            repository_root=arguments.repository_root,
            revision=arguments.revision,
        )
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
