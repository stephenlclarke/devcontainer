#!/usr/bin/env python3
"""Create a deterministic gzip-compressed USTAR archive from one package root."""

from __future__ import annotations

import argparse
import gzip
import os
import stat
import tarfile
from pathlib import Path


def entries(source: Path) -> list[Path]:
    """Return the package root and descendants in stable archive-name order."""

    descendants = sorted(
        source.rglob("*"),
        key=lambda path: path.relative_to(source).as_posix(),
    )
    return [source, *descendants]


def archive_name(source: Path, path: Path) -> str:
    """Return the member name rooted at the package directory."""

    if path == source:
        return source.name
    return f"{source.name}/{path.relative_to(source).as_posix()}"


def add_entry(
    archive: tarfile.TarFile,
    source: Path,
    path: Path,
    epoch: int,
) -> None:
    """Add one normalized regular file or directory."""

    status = path.lstat()
    member = tarfile.TarInfo(archive_name(source, path))
    member.uid = 0
    member.gid = 0
    member.uname = "root"
    member.gname = "wheel"
    member.mtime = epoch
    member.mode = stat.S_IMODE(status.st_mode)
    if stat.S_ISDIR(status.st_mode):
        member.type = tarfile.DIRTYPE
        archive.addfile(member)
        return
    if not stat.S_ISREG(status.st_mode):
        raise ValueError(f"unsupported package entry type: {path}")
    member.type = tarfile.REGTYPE
    member.size = status.st_size
    with path.open("rb") as contents:
        archive.addfile(member, contents)


def create_archive(source: Path, output: Path, epoch: int) -> None:
    """Write a byte-reproducible gzip-compressed USTAR archive."""

    source = source.resolve(strict=True)
    output = output.resolve()
    if not source.is_dir():
        raise ValueError(f"package source is not a directory: {source}")
    if output == source or source in output.parents:
        raise ValueError("archive output must be outside the package source")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw,
                compresslevel=9,
                mtime=epoch,
            ) as compressed:
                with tarfile.open(
                    fileobj=compressed,
                    mode="w",
                    format=tarfile.USTAR_FORMAT,
                ) as archive:
                    for path in entries(source):
                        add_entry(archive, source, path, epoch)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epoch", type=int, required=True)
    arguments = parser.parse_args()
    if arguments.epoch < 0:
        parser.error("--epoch must be non-negative")
    create_archive(arguments.source, arguments.output, arguments.epoch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
