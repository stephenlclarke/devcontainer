"""Published binary acquisition tests: no builds, live runtime or network."""

import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from release_inputs import acquire, validate_lock, verify_metadata


class ReleaseInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.retained, self.scratch = self.root / "retained", self.root / "scratch"
        self.retained.mkdir()
        self.scratch.mkdir()
        self.data = b"published binary fixture"
        self.asset = {"repository": "owner/product", "tag": "1.2.3", "releaseID": 123,
                      "tagObject": "a" * 40, "commit": "b" * 40, "publisher": "github-actions[bot]",
                      "assetID": 456, "name": "product-arm64.tar.gz", "size": len(self.data),
                      "sha256": hashlib.sha256(self.data).hexdigest(), "architecture": "arm64", "prerelease": False}
        self.lock = {"schemaVersion": 1, "assets": [self.asset]}
        self.url = "https://github.com/owner/product/releases/download/1.2.3/product-arm64.tar.gz"
        self.metadata = {
            "repos/owner/product/releases/tags/1.2.3": {"id": 123, "tag_name": "1.2.3", "draft": False,
                "prerelease": False, "author": {"login": "github-actions[bot]"}, "assets": [
                    {"id": 456, "name": self.asset["name"], "size": len(self.data), "state": "uploaded",
                     "digest": "sha256:" + self.asset["sha256"], "browser_download_url": self.url}]},
            "repos/owner/product/git/ref/tags/1.2.3": {"ref": "refs/tags/1.2.3", "object": {"type": "tag", "sha": "a" * 40}},
            "repos/owner/product/git/tags/" + "a" * 40: {"object": {"type": "commit", "sha": "b" * 40}},
        }
        self.downloads = 0
        self.reads = 0

    def read_api(self, endpoint: str) -> dict:
        self.reads += 1
        return copy.deepcopy(self.metadata[endpoint])

    def fetch(self, url: str, output: Path, size: int) -> None:
        self.assertEqual((url, size), (self.url, len(self.data)))
        self.downloads += 1
        output.write_bytes(self.data)

    def run_acquire(self, offline: bool = False) -> dict:
        return acquire(self.lock, self.retained, self.scratch, offline=offline, read_api=self.read_api, fetch=self.fetch)

    def test_download_once_revalidate_online_and_restore_without_scratch(self) -> None:
        first = self.run_acquire()
        self.assertEqual(self.downloads, 1)
        self.assertEqual(self.reads, 3)
        self.assertFalse(first["runtimeReady"])
        self.assertFalse(first["assets"][0]["signatureChecked"])
        self.assertEqual(Path(first["assets"][0]["path"]).read_bytes(), self.data)
        self.run_acquire()
        self.assertEqual(self.downloads, 1)
        self.assertEqual(self.reads, 6)
        self.scratch.rmdir()
        self.scratch.mkdir()
        offline = self.run_acquire(offline=True)
        self.assertTrue(offline["offline"])
        self.assertEqual(self.reads, 6)
        self.assertEqual(self.downloads, 1)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_offline_does_not_acquire_or_invent_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "previously verified"):
            self.run_acquire(offline=True)
        self.assertEqual((self.downloads, self.reads), (0, 0))

    def test_changed_release_tag_and_asset_are_rejected_even_when_cached(self) -> None:
        self.run_acquire()
        for endpoint, key, value in [
            ("repos/owner/product/releases/tags/1.2.3", "id", 999),
            ("repos/owner/product/releases/tags/1.2.3", "draft", True),
            ("repos/owner/product/git/ref/tags/1.2.3", "object", {"sha": "c" * 40, "type": "commit"}),
            ("repos/owner/product/git/tags/" + "a" * 40, "object", {"sha": "c" * 40, "type": "commit"}),
        ]:
            before = self.metadata[endpoint][key]
            self.metadata[endpoint][key] = value
            with self.assertRaises(ValueError):
                self.run_acquire()
            self.metadata[endpoint][key] = before
        published = self.metadata["repos/owner/product/releases/tags/1.2.3"]["assets"][0]
        for key, value in [("id", 999), ("size", 1), ("digest", "sha256:" + "0" * 64), ("state", "new"), ("browser_download_url", "https://evil.invalid")]:
            before = published[key]
            published[key] = value
            with self.assertRaisesRegex(ValueError, "asset identity changed"):
                self.run_acquire()
            published[key] = before
        self.assertEqual(self.downloads, 1)

    def test_bad_download_never_seals_and_can_resume(self) -> None:
        def corrupt_fetch(url, output, size):
            output.write_bytes(b"invalid")
        with self.assertRaisesRegex(ValueError, "verification"):
            acquire(self.lock, self.retained, self.scratch, read_api=self.read_api, fetch=corrupt_fetch)
        with sqlite3.connect(self.retained / "release-inputs.sqlite") as db:
            self.assertEqual(db.execute("SELECT state FROM objects").fetchall(), [("pending",)])
            self.assertEqual(db.execute("SELECT count(*) FROM references_verified").fetchone()[0], 0)
        self.run_acquire()
        self.assertEqual(self.downloads, 1)

    def test_copied_pending_ingest_is_sealed_without_redownload(self) -> None:
        self.run_acquire()
        with sqlite3.connect(self.retained / "release-inputs.sqlite") as db:
            db.execute("UPDATE objects SET state='pending'")
        with patch("release_inputs.os.fsync", wraps=os.fsync) as sync:
            self.run_acquire()
            self.assertEqual(sync.call_count, 2)
        self.assertEqual(self.downloads, 1)

    def test_corrupt_sealed_object_fails_without_overwrite(self) -> None:
        result = self.run_acquire()
        path = Path(result["assets"][0]["path"])
        path.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "verification"):
            self.run_acquire()
        self.assertEqual(self.downloads, 1)
        self.assertEqual(path.read_bytes(), b"corrupt")

    def test_unregistered_objects_and_symlink_roots_fail_closed(self) -> None:
        objects = self.retained / "release-objects"
        objects.mkdir()
        path = objects / self.asset["sha256"]
        path.write_bytes(b"unregistered")
        with self.assertRaisesRegex(ValueError, "Unregistered"):
            self.run_acquire()
        self.assertEqual(path.read_bytes(), b"unregistered")
        path.unlink()
        path.symlink_to(self.root / "external")
        with self.assertRaisesRegex(ValueError, "Unregistered"):
            self.run_acquire()

    def test_invalid_lock_is_rejected_before_network(self) -> None:
        for key, value in [("repository", "../escape"), ("tag", "../../main"), ("name", "../secret"), ("sha256", "bad"),
                           ("assetID", True), ("size", -1), ("size", 3 * 1024**3), ("architecture", "x86_64"), ("prerelease", None)]:
            before = self.asset[key]
            self.asset[key] = value
            with self.assertRaises(ValueError):
                self.run_acquire()
            self.asset[key] = before
        self.assertEqual(self.reads, 0)
        self.lock["assets"].append(self.asset.copy())
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            validate_lock(self.lock)

    def test_missing_asset_and_cyclic_tags_fail_closed(self) -> None:
        self.metadata["repos/owner/product/releases/tags/1.2.3"]["assets"] = []
        with self.assertRaisesRegex(ValueError, "missing"):
            verify_metadata(self.asset, self.read_api)
        self.metadata["repos/owner/product/git/tags/" + "a" * 40] = {"object": {"type": "tag", "sha": "a" * 40}}
        with self.assertRaisesRegex(ValueError, "commit changed"):
            verify_metadata(self.asset, self.read_api)
