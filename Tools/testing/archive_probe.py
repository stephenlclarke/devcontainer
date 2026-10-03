"""Docker-independent E05 assertions for an admitted disposable container.

The caller owns container/image admission, its durable resource journal, the
whole-phase deadline and cleanup. This probe never creates or removes a runtime
resource. It writes only /tmp/archive inside that caller-owned container and
never extracts an untrusted response onto the host filesystem.
"""

from __future__ import annotations

import io
from pathlib import Path, PurePosixPath
import re
import tarfile
import time

from engine_probe import request


LONG_NAME = "long-" + "x" * 110
FILES = {"regular.txt": b"archive-content\n", LONG_NAME: b"long-name\n",
         "large.bin": bytes(range(256)) * 4096}
LIMIT = 8 * 1024**2


def source_archive() -> bytes:
    """Deterministic PAX input retains E05's long name, mode, link and MiB payload."""
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        directory = tarfile.TarInfo("archive")
        directory.type, directory.mode = tarfile.DIRTYPE, 0o755
        archive.addfile(directory)
        for name, content in FILES.items():
            entry = tarfile.TarInfo("archive/" + name)
            entry.mode = 0o750 if name == "regular.txt" else 0o644
            entry.size = len(content)
            archive.addfile(entry, io.BytesIO(content))
        link = tarfile.TarInfo("archive/regular-link")
        link.type, link.linkname, link.mode = tarfile.SYMTYPE, "regular.txt", 0o777
        archive.addfile(link)
    return output.getvalue()


def observations(payload: bytes) -> dict[str, str]:
    """Inspect tar metadata without resolving links or writing any returned path."""
    if len(payload) > LIMIT:
        raise ValueError("Archive response exceeds fixture limit")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        entries = {}
        expected = {"archive/regular-link", *("archive/" + name for name in FILES)}
        for entry in archive:
            path = PurePosixPath(entry.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Unsafe archive entry path")
            name = path.as_posix()
            if name not in expected:
                continue  # E05 does not assert an exact tar inventory or root entry.
            if name in entries:
                raise ValueError("Duplicate expected archive entry")
            entries[name] = entry
        if entries.keys() != expected:
            raise ValueError("Archive response lacks expected fixture files")

        def matches(name: str) -> bool:
            entry = entries["archive/" + name]
            if not entry.isfile() or entry.size != len(FILES[name]):
                return False
            with archive.extractfile(entry) as stream:
                return stream.read(entry.size + 1) == FILES[name]

        link = entries["archive/regular-link"]
        return {"content": str(matches("regular.txt")).lower(),
                "large_file": str(matches("large.bin")).lower(),
                "long_path": str(matches(LONG_NAME)).lower(),
                "mode": oct(entries["archive/regular.txt"].mode),
                "symlink": str(link.issym() and link.linkname == "regular.txt").lower()}


def archive_copy(socket: Path, container_id: str, api_version: str, *, observe=None) -> dict[str, str]:
    """PUT/GET the original E05 payload through the same API in every lane."""
    if re.fullmatch(r"[0-9a-f]{64}", container_id) is None or re.fullmatch(r"[0-9]+\.[0-9]+", api_version) is None:
        raise ValueError("Archive probe requires an admitted full container ID and API version")

    def probe(method: str, destination: str, body=None):
        route = f"/v{api_version}/containers/{container_id}/archive?path={destination}"
        event = {"method": method, "route": route}
        started = time.monotonic_ns()
        try:
            status, response = request(socket, method, route, body, content_type="application/x-tar", max_bytes=LIMIT)
            event["status"] = status
            if status != 200:
                raise ValueError("Archive operation failed")
            return response
        except (Exception, KeyboardInterrupt) as error:
            event["error"] = type(error).__name__
            raise
        finally:
            event["durationNS"] = time.monotonic_ns() - started
            if observe is not None:
                observe(event)

    probe("PUT", "/tmp", source_archive())
    return observations(probe("GET", "/tmp/archive"))
