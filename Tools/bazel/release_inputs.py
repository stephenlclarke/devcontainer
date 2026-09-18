"""Acquire reviewed GitHub binary assets; no compiler or source-build fallback."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
from urllib.parse import quote, urlparse
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener


class HTTPSRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname not in {"api.github.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}:
            raise ValueError("Unexpected GitHub download redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def validate_lock(lock: dict) -> list[dict]:
    if lock.get("schemaVersion") != 1 or not isinstance(lock.get("assets"), list) or not lock["assets"]:
        raise ValueError("Expected a nonempty version-1 release asset lock")
    seen = set()
    for asset in lock["assets"]:
        if not isinstance(asset, dict):
            raise ValueError("Release asset must be an object")
        for key, pattern in [("repository", r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*"),
                             ("tag", r"[A-Za-z0-9][A-Za-z0-9_.-]*"),
                             ("name", r"[A-Za-z0-9][A-Za-z0-9_.-]*"),
                             ("publisher", r"[A-Za-z0-9_\[\]-]+"),
                             ("commit", r"[0-9a-f]{40}"), ("tagObject", r"[0-9a-f]{40}"),
                             ("sha256", r"[0-9a-f]{64}")]:
            if not isinstance(asset.get(key), str) or re.fullmatch(pattern, asset[key]) is None:
                raise ValueError("Invalid release lock field: " + key)
        for key in ("releaseID", "assetID", "size"):
            if type(asset.get(key)) is not int or asset[key] <= 0:
                raise ValueError("Invalid positive release lock field: " + key)
        if asset["size"] > 2 * 1024**3 or asset.get("architecture") != "arm64" or type(asset.get("prerelease")) is not bool:
            raise ValueError("Unsupported release size, architecture or prerelease policy")
        identity = (asset["repository"], asset["assetID"])
        if identity in seen:
            raise ValueError("Duplicate release asset")
        seen.add(identity)
    return lock["assets"]


def api(endpoint: str) -> dict:
    request = Request("https://api.github.com/" + endpoint, headers={"User-Agent": "container-family-release-inputs", "Accept": "application/vnd.github+json"})
    # This public read-only client receives no tokens or shell environment.
    with build_opener(HTTPSHandler(), HTTPSRedirects()).open(request, timeout=30) as response:
        data = response.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("Oversized GitHub metadata")
    return json.loads(data)


def verify_metadata(asset: dict, read_api=api) -> dict:
    prefix = "repos/" + asset["repository"]
    release = read_api(prefix + "/releases/tags/" + quote(asset["tag"], safe=""))
    if (release.get("id"), release.get("tag_name"), release.get("draft"), release.get("prerelease"), release.get("author", {}).get("login")) != (
        asset["releaseID"], asset["tag"], False, asset["prerelease"], asset["publisher"]
    ):
        raise ValueError("Published release identity changed")
    ref = read_api(prefix + "/git/ref/tags/" + quote(asset["tag"], safe=""))
    target = ref.get("object", {})
    if ref.get("ref") != "refs/tags/" + asset["tag"] or target.get("sha") != asset["tagObject"]:
        raise ValueError("Published tag identity changed")
    # Peeling is bounded; cyclic or excessively nested tag objects fail closed.
    for _ in range(8):
        if target.get("type") == "commit":
            break
        if target.get("type") != "tag" or re.fullmatch(r"[0-9a-f]{40}", target.get("sha", "")) is None:
            raise ValueError("Invalid Git tag object")
        target = read_api(prefix + "/git/tags/" + target["sha"]).get("object", {})
    if target.get("type") != "commit" or target.get("sha") != asset["commit"]:
        raise ValueError("Published source commit changed")
    matches = [item for item in release.get("assets", []) if item.get("name") == asset["name"]]
    url = f"https://github.com/{asset['repository']}/releases/download/{asset['tag']}/{asset['name']}"
    if len(matches) != 1:
        raise ValueError("Required release binary asset is missing or ambiguous")
    item = matches[0]
    if (item.get("id"), item.get("size"), item.get("digest"), item.get("state"), item.get("browser_download_url")) != (
        asset["assetID"], asset["size"], "sha256:" + asset["sha256"], "uploaded", url
    ):
        raise ValueError("Published binary asset identity changed")
    return {"release": release, "tag": ref, "url": url}


def download(url: str, destination: Path, expected_size: int) -> None:
    request = Request(url, headers={"User-Agent": "container-family-release-inputs"})
    opener = build_opener(HTTPSHandler(), HTTPSRedirects())
    started = time.monotonic()
    count = 0
    with opener.open(request, timeout=30) as response, destination.open("xb") as output:
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            count += len(block)
            if count > expected_size or time.monotonic() - started > 600:
                raise ValueError("Release download exceeded its size or deadline")
            output.write(block)
        if count != expected_size:
            raise ValueError("Incomplete release download")


def verify_object(path: Path, asset: dict) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size != asset["size"] or sha256(path) != asset["sha256"]:
        raise ValueError("Retained/downloaded release bytes failed verification")


def acquire(lock: dict, retained: Path, scratch: Path, *, offline: bool = False, read_api=api, fetch=download) -> dict:
    """Acquire under the caller's family reference-store lease; resume ingests only."""
    assets = validate_lock(lock)
    for root in (retained, scratch):
        if root.resolve() != root or not root.is_dir():
            raise ValueError("Reference roots must be existing non-symlinked directories")
    objects = retained / "release-objects"
    objects.mkdir(exist_ok=True)
    database = retained / "release-inputs.sqlite"
    if objects.is_symlink() or database.is_symlink():
        raise ValueError("Symlinked reference storage is forbidden")
    lock_digest = hashlib.sha256(canonical(lock).encode()).hexdigest()
    result = {"schemaVersion": 1, "lockSHA256": lock_digest, "offline": offline,
              "scope": "binary-acquisition-only", "runtimeReady": False, "assets": []}
    metadata_cache = {}

    def cached_api(endpoint: str) -> dict:
        if endpoint not in metadata_cache:
            metadata_cache[endpoint] = read_api(endpoint)
        return metadata_cache[endpoint]

    with sqlite3.connect(database) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA temp_store=MEMORY")
        db.execute("CREATE TABLE IF NOT EXISTS objects (sha256 TEXT PRIMARY KEY, state TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS references_verified (identity TEXT PRIMARY KEY, metadata TEXT NOT NULL)")
        for asset in assets:
            identity = hashlib.sha256(canonical(asset).encode()).hexdigest()
            verified = db.execute("SELECT metadata FROM references_verified WHERE identity=?", (identity,)).fetchone()
            if offline and verified is None:
                raise ValueError("Offline acquisition requires previously verified release metadata")
            metadata = json.loads(verified[0]) if offline else verify_metadata(asset, cached_api)
            destination = objects / asset["sha256"]
            state = db.execute("SELECT state FROM objects WHERE sha256=?", (asset["sha256"],)).fetchone()
            if state == ("sealed",):
                verify_object(destination, asset)
            else:
                if offline:
                    raise ValueError("Offline reference bytes are incomplete")
                if destination.is_symlink() or (destination.exists() and state != ("pending",)):
                    raise ValueError("Unregistered reference object; refusing overwrite")
                db.execute("INSERT OR IGNORE INTO objects VALUES (?, 'pending')", (asset["sha256"],))
                db.commit()
                # A crash after the durable copy but before sealing can reuse it.
                complete = destination.is_file() and destination.stat().st_size == asset["size"] and sha256(destination) == asset["sha256"]
                if not complete:
                    with tempfile.TemporaryDirectory(dir=scratch, prefix="release-download-") as temporary:
                        incoming = Path(temporary) / "asset"
                        fetch(metadata["url"], incoming, asset["size"])
                        verify_object(incoming, asset)
                        with incoming.open("rb") as source, destination.open("wb") as output:
                            shutil.copyfileobj(source, output)
                            output.flush()
                            os.fsync(output.fileno())
                        verify_object(destination, asset)
                # Hash-complete pending bytes can still be only in page cache
                # after a previous process crash. Recovered copies need the same
                # file and directory durability barrier as freshly copied ones.
                with destination.open("rb") as durable:
                    os.fsync(durable.fileno())
                directory = os.open(objects, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
                db.execute("UPDATE objects SET state='sealed' WHERE sha256=?", (asset["sha256"],))
            db.execute("INSERT OR REPLACE INTO references_verified VALUES (?, ?)", (identity, canonical(metadata)))
            db.commit()
            result["assets"].append({**asset, "path": str(destination), "verification": "reviewed-lock-and-github-api-sha256", "signatureChecked": False})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lock", type=Path)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    retained = Path.home() / "Library/Application Support/ContainerFamily/retained/workflow"
    if retained.stat().st_dev != Path.home().stat().st_dev:
        raise ValueError("References require internal retained storage")
    os.umask(0o077)
    result = acquire(json.loads(args.lock.read_text()), retained, Path("/Volumes/SSD/cf/bazel/tmp"), offline=args.offline)
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
