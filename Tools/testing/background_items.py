"""Read-only resolution of launchd's UUID-bound ServiceManagement registrations.

The diagnostic text format is not a stable API. Unknown/ambiguous output fails
closed. Only an existing noninteractive administrator grant is used; this module
never requests consent, changes registration or launches an application.
"""

from __future__ import annotations

import os
from pathlib import Path
import plistlib
import re
import selectors
import signal
import stat
import subprocess
import time
from urllib.parse import unquote, urlparse
import uuid


FIELDS = {"UUID", "Identifier", "Parent Identifier", "Bundle Identifier", "URL", "Executable Path"}
APPLICATION_ROOTS = (Path("/Applications"), Path("/Library/Application Support"), Path("/System/Applications"))


def records(payload: bytes, uid: int) -> list[dict]:
    if len(payload) > 4 * 1024 * 1024:
        raise ValueError("Background registration inventory exceeds its size limit")
    selected, current_uid, current, in_embedded_items = [], None, None, False
    for line in payload.decode("utf-8", errors="strict").splitlines():
        header = re.fullmatch(r"\s*Records for UID (-?\d+) : [A-Fa-f0-9-]+", line)
        if header:
            current_uid, current = int(header[1]), None
            in_embedded_items = False
        elif re.fullmatch(r"\s*#\d+:\s*", line):
            current = {} if current_uid == uid else None
            in_embedded_items = False
            if current is not None:
                selected.append(current)
        elif re.fullmatch(r"\s+Embedded Item Identifiers:\s*", line):
            in_embedded_items = True
        elif in_embedded_items and re.fullmatch(r"\s+#\d+: [A-Za-z0-9._-]+", line):
            continue
        elif line.lstrip().startswith(("Records for UID", "#")):
            raise ValueError("Malformed background registration boundary")
        elif current is not None:
            field = re.fullmatch(r"\s+([A-Za-z ]+): (.*)", line)
            if field:
                in_embedded_items = False
            if field and field[1] in FIELDS:
                if field[1] in current:
                    raise ValueError("Duplicate background registration field")
                current[field[1]] = field[2]
    if not selected or any(not {"UUID", "Identifier", "URL"} <= item.keys() for item in selected):
        raise ValueError("Background registration inventory is incomplete")
    return selected


def capture_inventory() -> bytes:
    # stderr goes nowhere: do not collect unrelated private diagnostic output.
    # sudo remains the signal-forwarding supervisor; no password input exists.
    process = subprocess.Popen(["/usr/bin/sudo", "-n", "/usr/bin/sfltool", "dumpbtm"],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                               start_new_session=True, env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"})
    payload = bytearray()
    expires = time.monotonic() + 10
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = expires - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError("Background registration inspection exceeded ten seconds")
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                payload.extend(chunk)
                if len(payload) > 4 * 1024 * 1024:
                    raise ValueError("Background registration inventory exceeds its size limit")
        if process.wait(timeout=max(0.001, expires - time.monotonic())) != 0:
            raise RuntimeError("Background inspection needs existing noninteractive administrator access")
        return bytes(payload)
    finally:
        process.stdout.close()
        if process.poll() is None:
            stop_capture(process)


def stop_capture(process):
    """Allow a two-second cleanup grace even when the phase alarm expires."""
    started = time.monotonic()
    timer = signal.setitimer(signal.ITIMER_REAL, 0)
    handlers = {number: signal.signal(number, signal.SIG_IGN) for number in (signal.SIGTERM, signal.SIGINT)}
    unverified = False
    try:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            unverified = True
    finally:
        remaining = timer[0] - (time.monotonic() - started)
        for number, previous in handlers.items():
            signal.signal(number, previous)
        if timer[0] and remaining > 0:
            signal.setitimer(signal.ITIMER_REAL, remaining, timer[1])
    if unverified:
        # Do not kill only the supervisor and orphan a privileged child.
        raise RuntimeError(f"Background inventory helper {process.pid} did not stop; activation refused")
    if timer[0] and remaining <= 0:
        raise TimeoutError("Background registration inspection exceeded its phase deadline; helper stopped")


def host_records() -> list[dict]:
    return records(capture_inventory(), os.getuid())


def unique(items: list[dict], key: str, value: str) -> dict:
    matches = [item for item in items if item.get(key) == value]
    if len(matches) != 1:
        raise ValueError("Background registration identity is absent or ambiguous")
    return matches[0]


def read_plist(path: Path) -> dict:
    internal_path(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.getuid()} or
                info.st_nlink != 1 or info.st_mode & 0o022 or info.st_size > 1024 * 1024):
            raise ValueError("Background metadata is not a trusted regular file")
        data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("Background metadata grew beyond its size limit")
    value = plistlib.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Background metadata is not a dictionary")
    return value


def internal_path(path: Path):
    """Reject aliases/mounts before traversing into protected or external data."""
    roots = [root for root in APPLICATION_ROOTS if path.is_relative_to(root)]
    if not roots or ".." in path.parts:
        raise ValueError("Background metadata must be in an internal application root")
    root = roots[0]
    device = None
    current = Path("/")
    for part in path.parts[1:]:
        current /= part
        info = current.lstat()
        if current == root:
            if info.st_dev not in {Path("/").stat().st_dev, Path.home().stat().st_dev}:
                raise ValueError("Background metadata application root is not internal")
            device = info.st_dev
        if stat.S_ISLNK(info.st_mode) or (device is not None and info.st_dev != device):
            raise ValueError("Background metadata path contains an alias or mount")


def bundle_root(value: str) -> Path:
    if value.startswith("file:"):
        parsed = urlparse(value)
        if parsed.netloc not in {"", "localhost"} or parsed.query or parsed.fragment:
            raise ValueError("Background bundle URL is not a local file")
        value = unquote(parsed.path)
    path = Path(value)
    if not path.is_absolute() or path.suffix != ".app":
        raise ValueError("Background bundle location is not canonical")
    internal_path(path)
    return path


def embedded(root: Path, value: str) -> Path:
    path = Path(value)
    if not value or path.is_absolute() or any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("Background item escapes its registered bundle")
    return root / path


def field(output: str, name: str) -> str:
    matches = re.findall(r"^\t" + re.escape(name) + r" = ([^\n]+)$", output, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError("Background job identity is incomplete")
    return matches[0]


def identity(output: str) -> tuple:
    return tuple(field(output, key) for key in ("type", "managed_by", "program identifier", "BTM uuid",
                                                "parent bundle identifier", "parent bundle version"))


def resolve(output: str, label: str, items: list[dict]) -> str:
    if field(output, "type") != "Submitted" or field(output, "managed_by") != "com.apple.xpc.ServiceManagement":
        raise ValueError("Unrecognised background job owner")
    identifier = re.fullmatch(r"(.+) \(mode: ([12])\)", field(output, "program identifier"))
    if identifier is None:
        raise ValueError("Unsupported background program identifier")
    item = unique(items, "UUID", str(uuid.UUID(field(output, "BTM uuid"))).upper())
    parent_id = field(output, "parent bundle identifier")
    if item.get("Parent Identifier") != "2." + parent_id:
        raise ValueError("Background parent registration differs")
    parent = unique(items, "Identifier", item["Parent Identifier"])
    if parent.get("Bundle Identifier") != parent_id:
        raise ValueError("Background parent bundle differs")
    root = bundle_root(parent["URL"])
    info = read_plist(root / "Contents/Info.plist")
    # App updates can leave launchd's historical parent version unchanged.
    # This resolves current registered ownership, not executable-version parity;
    # the historical version still participates in the before/after job check.
    if info.get("CFBundleIdentifier") != parent_id:
        raise ValueError("Background parent metadata changed")
    child = embedded(root, item["URL"])
    if identifier[2] == "2":
        definition = read_plist(child)
        if (item["Identifier"] != "8." + label or item.get("Executable Path") != identifier[1] or
                definition.get("Label") != label or definition.get("BundleProgram") != identifier[1] or
                "Program" in definition):
            raise ValueError("Background agent executable differs")
        executable = embedded(root, identifier[1])
    else:
        info = read_plist(child / "Contents/Info.plist")
        name = info.get("CFBundleExecutable")
        if (item["Identifier"] != "4." + identifier[1] or item.get("Bundle Identifier") != identifier[1] or
                info.get("CFBundleIdentifier") != identifier[1] or not isinstance(name, str) or
                not name or name in {".", ".."} or "/" in name):
            raise ValueError("Background login executable differs")
        executable = child / "Contents/MacOS" / name
    # Keep the registered lexical path; the caller resolves aliases when
    # comparing against its slot. Refuse aliases before that can traverse an
    # unrelated protected/removable location and trigger permission requests.
    internal_path(executable)
    return str(executable)
