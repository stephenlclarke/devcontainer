"""Record source provenance without replacing Bazel's action keys."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import subprocess


def file_identity(path: Path, root: Path) -> dict:
    """Include permissions and linked bytes; external source links fail closed."""
    link = None
    if path.is_symlink():
        link = str(path.readlink())
        path = path.resolve(strict=True)
        if not path.is_relative_to(root.resolve()):
            raise ValueError("Source symlinks must stay within the repository")
    if not path.exists():
        return {"deleted": True}
    if not path.is_file():
        raise ValueError(f"Unsupported source input: {path}")
    result = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "mode": stat.S_IMODE(path.stat().st_mode)}
    if link is not None:
        result["link"] = link
    return result


def source_identity(root: Path) -> dict:
    """Hash Git-visible sources, including new files, without following links."""
    def git(*arguments: str) -> bytes:
        return subprocess.check_output(["/usr/bin/git", "-C", str(root), *arguments])

    paths = git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    files = {}
    for raw in sorted(set(paths.split(b"\0")) - {b""}):
        name = raw.decode("utf-8")
        path = root / name
        files[name] = file_identity(path, root)
    return {
        "schema": 1,
        "commit": git("rev-parse", "HEAD").decode().strip(),
        "dirty": bool(git("status", "--porcelain")),
        "files": files,
    }


def verify(before: dict, after: dict) -> None:
    """Reject a mixed editable-source run; only Bazel's generated lock may move."""
    if before["commit"] != after["commit"]:
        raise ValueError("Source commit changed during the invocation")
    old = {key: value for key, value in before["files"].items() if key != "MODULE.bazel.lock"}
    new = {key: value for key, value in after["files"].items() if key != "MODULE.bazel.lock"}
    if old != new:
        raise ValueError("Sources changed during the invocation; do not accept mixed-source evidence")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    current = source_identity(args.repository)
    if args.verify:
        verify(json.loads(args.verify.read_text()), current)
    print(json.dumps(current, sort_keys=True))


if __name__ == "__main__":
    main()
