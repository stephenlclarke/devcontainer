"""Private durable service-switch journal, separate from publishable case evidence."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import sqlite3
import stat

from service_switch import ServiceSwitch


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def chain_digest(previous: str, name: str, data: bytes) -> str:
    return digest(json.dumps([previous, name, digest(data)], separators=(",", ":")).encode())


class ServiceJournal:
    """Atomic append-only records in user-private internal storage.

    Snapshot payloads can contain operator credentials. Never export their bytes
    to Bazel XML, logs or public artifacts; only the receipt contains safe hashes.
    The owning host guard remains required until restoration has been verified.
    """

    def __init__(self, path: Path, owner: dict, *, create: bool = False):
        if not path.is_absolute() or path.parent.resolve() != path.parent:
            raise ValueError("Private journal needs canonical retained storage")
        parent = path.parent.stat()
        if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise ValueError("Private journal parent must be user-owned mode 0700")
        flags = os.O_RDWR | os.O_NOFOLLOW
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        descriptor = os.open(path, flags, 0o600)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError("Private journal must be a user-owned mode 0600 file")
        finally:
            os.close(descriptor)
        self.path = path
        self.owner = json.dumps(owner, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if create:
            with self.connect() as db:
                db.execute("CREATE TABLE entries (sequence INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, payload BLOB NOT NULL, seal TEXT NOT NULL)")
                db.execute("CREATE TABLE head (sequence INTEGER NOT NULL, seal TEXT NOT NULL)")
                db.execute("INSERT INTO head VALUES (0, ?)", (digest(self.owner),))
            self.put("owner.json", self.owner)
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        if self.records().get("owner.json") != self.owner:
            raise ValueError("Private journal belongs to another runtime owner")

    @contextmanager
    def connect(self):
        # A removed/aliased journal must never be silently recreated on resume.
        info = self.path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError("Private journal ownership changed")
        db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=5)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA temp_store=MEMORY")
            with db:
                yield db
        finally:
            db.close()

    def read_chain(self, db) -> tuple[dict[str, bytes], int, str]:
        records = {}
        previous = digest(self.owner)
        sequence = 0
        for number, name, data, seal in db.execute("SELECT sequence,name,payload,seal FROM entries ORDER BY sequence"):
            sequence += 1
            if number != sequence or name in records or chain_digest(previous, name, data) != seal:
                raise ValueError("Corrupt private service journal")
            records[name], previous = data, seal
        if db.execute("SELECT sequence,seal FROM head").fetchall() != [(sequence, previous)]:
            raise ValueError("Incomplete private service journal")
        return records, sequence, previous

    def records(self) -> dict[str, bytes]:
        with self.connect() as db:
            db.execute("BEGIN")
            return self.read_chain(db)[0]

    def put(self, name: str, data: bytes):
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", name) or not isinstance(data, bytes) or len(data) > 8 * 1024**2:
            raise ValueError("Invalid private service journal entry")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            records, sequence, previous = self.read_chain(db)
            if name in records:
                if records[name] != data:
                    raise ValueError("Private service journal entries are immutable")
                return
            if sequence >= 4096 or sum(map(len, records.values())) + len(data) > 32 * 1024**2:
                raise ValueError("Private service journal exceeds its bounded size")
            seal = chain_digest(previous, name, data)
            db.execute("INSERT INTO entries VALUES (?, ?, ?, ?)", (sequence + 1, name, data, seal))
            db.execute("UPDATE head SET sequence=?, seal=?", (sequence + 1, seal))

    def receipt(self) -> dict:
        with self.connect() as db:
            db.execute("BEGIN")
            _records, sequence, seal = self.read_chain(db)
        return {"schema": 1, "ownerSHA256": digest(self.owner), "records": sequence, "seal": seal,
                "visibility": "private-do-not-export"}

    def recover_switch(self, launchd, owned_root: Path) -> ServiceSwitch:
        if json.loads(self.owner).get("root") != str(owned_root):
            raise ValueError("Recovery root differs from the journal owner")
        records = self.records()
        if "service-originals.plist" not in records:
            raise ValueError("No committed service snapshot; do not infer a mutation")
        prior = plistlib.loads(records["service-originals.plist"])
        if not isinstance(prior, list):
            raise ValueError("Invalid private service snapshot")
        for index, original in enumerate(prior):
            if not isinstance(original, dict) or set(original) != {"label", "path", "program", "sha256"}:
                raise ValueError("Invalid original service fields")
            payload = records.get(f"service-original-{index:04d}.plist")
            if payload is None or digest(payload) != original["sha256"]:
                raise ValueError("Original service payload is missing or corrupt")
            original["payload"] = payload
        events = [int(match[1]) for name in records if (match := re.fullmatch(r"service-event-([0-9]{4}).txt", name))]
        return ServiceSwitch.recover(launchd, prior, owned_root, self.put, max(events, default=0))
