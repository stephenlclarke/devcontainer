"""Pinned guest data, offline reuse and interrupted-publication regressions."""

import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from prepare_guest_images import download, main, prepare, require_image, validate_image, verify_archive
from release_inputs import canonical


def blob(data, media):
    return {"digest": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data), "mediaType": media}


class GuestImageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.retained = self.root / "retained"
        self.retained.mkdir(mode=0o700)
        config = canonical({"os": "linux", "architecture": "arm64"}).encode()
        layer = b"fixture layer bytes; never loaded"
        self.config = blob(config, "application/vnd.oci.image.config.v1+json")
        self.layer = blob(layer, "application/vnd.oci.image.layer.v1.tar+gzip")
        manifest = canonical({"schemaVersion": 2, "config": self.config, "layers": [self.layer]}).encode()
        descriptor = blob(manifest, "application/vnd.oci.image.manifest.v1+json")
        reference = "example.test/fixture:one"
        descriptor["annotations"] = {"org.opencontainers.image.ref.name": reference}
        self.image = {"name": "fixture", "repository": "example.test/fixture", "reference": reference,
                      "manifest": descriptor["digest"], "config": self.config["digest"]}
        self.files = {"oci-layout": canonical({"imageLayoutVersion": "1.0.0"}).encode(),
                      "index.json": canonical({"schemaVersion": 2, "manifests": [descriptor]}).encode(),
                      "blobs/sha256/" + descriptor["digest"].split(":")[1]: manifest,
                      "blobs/sha256/" + self.config["digest"].split(":")[1]: config,
                      "blobs/sha256/" + self.layer["digest"].split(":")[1]: layer}
        self.fetches = 0

    def archive(self, directory):
        self.fetches += 1
        path = directory / "image.tar"
        with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as archive:
            for name, value in sorted(self.files.items()):
                entry = tarfile.TarInfo(name)
                entry.size, entry.mode = len(value), 0o600
                archive.addfile(entry, io.BytesIO(value))
        return path

    def prepare(self, **kwargs):
        return prepare(self.image, self.root, self.retained,
                       fetch=lambda image, directory: self.archive(directory), **kwargs)

    def test_complete_image_is_retained_and_reused_offline_without_fetch(self):
        first = self.prepare()
        self.assertEqual(self.prepare(offline=True), first)
        self.assertEqual(self.fetches, 1)
        self.assertEqual(first["image"]["config"], self.image["config"])
        self.assertTrue(Path(first["path"]).is_relative_to(self.retained))
        self.assertFalse(list(self.root.glob("guest-image-*")))
        self.assertFalse(list(self.retained.glob("*.pending.json")))

    def test_no_offline_download_or_repair(self):
        with self.assertRaisesRegex(ValueError, "offline"):
            self.prepare(offline=True)
        self.assertEqual(self.fetches, 0)

    def test_runtime_admission_is_read_only_and_refuses_pending_or_changed_storage(self):
        with self.assertRaises(FileNotFoundError):
            require_image(self.image, self.retained)
        first = self.prepare()
        self.assertEqual(require_image(self.image, self.retained), first)
        target = Path(first["path"])
        pending = target.with_suffix(".pending.json")
        pending.write_text(canonical({key: value for key, value in first.items() if key != "path"}))
        pending.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "Pending"):
            require_image(self.image, self.retained)
        self.assertTrue(pending.exists())
        pending.unlink()
        receipt = target.with_suffix(".json")
        receipt.write_text("{}")
        with self.assertRaisesRegex(ValueError, "changed"):
            require_image(self.image, self.retained)
        self.retained.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "private canonical"):
            require_image(self.image, self.retained)
        self.retained.chmod(0o700)
        self.assertEqual(self.fetches, 1)

    def test_sealed_payload_and_receipt_corruption_are_not_repaired(self):
        result = self.prepare()
        target = Path(result["path"])
        original = target.read_bytes()
        target.write_bytes(b"corrupt")
        with self.assertRaises(tarfile.TarError):
            self.prepare()
        target.write_bytes(original)
        target.with_suffix(".json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "Sealed"):
            self.prepare()
        self.assertEqual(self.fetches, 1)

    def test_interrupted_copy_resumes_with_same_pinned_bytes(self):
        def partial(path, source):
            path.write_bytes(b"partial")
            path.chmod(0o600)
            raise OSError("interruption")
        with patch("prepare_guest_images.durable_file", side_effect=partial), self.assertRaises(OSError):
            self.prepare()
        self.assertEqual(len(list(self.retained.glob("*.pending.json"))), 1)
        with self.assertRaisesRegex(ValueError, "offline"):
            self.prepare(offline=True)
        result = self.prepare()
        self.assertEqual(verify_archive(Path(result["path"]), self.image)["sha256"], result["sha256"])
        self.assertFalse(list(self.retained.glob("*.pending.json")))

    def test_crash_after_seal_only_finishes_owned_intent(self):
        result = self.prepare()
        pending = Path(result["path"]).with_suffix(".pending.json")
        pending.write_text(canonical({key: value for key, value in result.items() if key != "path"}))
        pending.chmod(0o600)
        self.assertEqual(self.prepare(offline=True), result)
        self.assertFalse(pending.exists())
        pending.write_text("{}")
        pending.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "owner changed"):
            self.prepare()

    def test_foreign_pending_marker_and_unregistered_archive_refused(self):
        key = hashlib.sha256(canonical(self.image).encode()).hexdigest()
        target = self.retained / (key + ".tar")
        target.write_bytes(b"foreign")
        target.chmod(0o600)
        with self.assertRaises(FileNotFoundError):
            self.prepare()
        pending = self.retained / (key + ".pending.json")
        pending.write_text("{}")
        pending.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "owner changed"):
            self.prepare()
        self.assertEqual(target.read_bytes(), b"foreign")

    def test_aliases_and_nonprivate_paths_refused(self):
        result = self.prepare()
        target = Path(result["path"])
        target.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "private"):
            self.prepare()
        target.chmod(0o600)
        alias = self.root / "alias"
        alias.symlink_to(self.retained)
        with self.assertRaisesRegex(ValueError, "canonical"):
            prepare(self.image, self.root, alias, offline=True)
        self.retained.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "private"):
            self.prepare()

    def test_wrong_manifest_config_or_architecture_cannot_be_admitted(self):
        path = self.archive(self.root)
        for key in ("manifest", "config"):
            wrong = dict(self.image, **{key: "sha256:" + "a" * 64})
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "differs from the pin"):
                verify_archive(path, wrong)

    def test_missing_or_corrupted_layer_is_rejected_by_compose_validator(self):
        layer = "blobs/sha256/" + self.layer["digest"].split(":")[1]
        self.files[layer] = b"corrupt"
        corrupted = self.archive(self.root)
        with self.assertRaisesRegex(ValueError, "mismatch"):
            verify_archive(corrupted, self.image)
        del self.files[layer]
        missing = self.archive(self.root)
        with self.assertRaisesRegex(ValueError, "missing"):
            verify_archive(missing, self.image)

    def test_archive_and_member_count_bounds(self):
        path = self.archive(self.root)
        with patch("prepare_guest_images.LIMIT", 1), self.assertRaisesRegex(ValueError, "bound"):
            verify_archive(path, self.image)
        self.files.update({f"extra-{n}": b"" for n in range(130)})
        excessive = self.archive(self.root)
        with self.assertRaisesRegex(ValueError, "inventory"):
            verify_archive(excessive, self.image)

    def test_oversized_json_is_rejected(self):
        self.files["index.json"] += b" " * (1024**2)
        oversized = self.archive(self.root)
        with self.assertRaisesRegex(ValueError, "metadata"):
            verify_archive(oversized, self.image)

    def test_publication_rechecks_copied_bytes(self):
        from prepare_guest_images import durable_file
        def corrupt(path, source):
            durable_file(path, source)
            with path.open("ab") as output:
                output.write(b"different trailing bytes")
        with patch("prepare_guest_images.durable_file", side_effect=corrupt), self.assertRaisesRegex(ValueError, "publication"):
            self.prepare()
        self.assertEqual(len(list(self.retained.glob("*.pending.json"))), 1)
        self.assertEqual(len(list(self.retained.glob("*.json"))), 1)

    def test_entrypoint_validates_inventory_and_passes_offline_policy(self):
        lock = self.root / "lock.json"
        parent = self.root / "Library/Application Support/ContainerFamily/retained/workflow"
        parent.mkdir(parents=True)
        lock.write_text(canonical({"schemaVersion": 1, "images": [self.image]}))
        with patch("sys.argv", ["prepare", str(lock), "--offline"]), patch("prepare_guest_images.os.umask"), \
                patch("prepare_guest_images.Path.home", return_value=self.root), \
                patch("prepare_guest_images.Path.stat", side_effect=[SimpleNamespace(st_dev=n) for n in (1, 1, 1, 2)]), \
                patch("prepare_guest_images.prepare", return_value={"fixture": True}) as prepare_image, \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            main()
            prepare_image.assert_called_once_with(self.image, Path("/Volumes/SSD/cf/bazel/tmp"), parent / "guest-images", offline=True)
            self.assertFalse(json.loads(output.getvalue())["runtimeReady"])
        for value in ({}, {"schemaVersion": 1, "images": []}, {"schemaVersion": 1, "images": [self.image] * 2}):
            lock.write_text(canonical(value))
            with patch("sys.argv", ["prepare", str(lock)]), patch("prepare_guest_images.os.umask"), self.assertRaises(ValueError):
                main()

    def test_lock_validation_rejects_mutable_and_unsafe_identities(self):
        invalid = [None, {}, dict(self.image, config="latest"), dict(self.image, name="../x"),
                   dict(self.image, repository="--option"), dict(self.image, reference="other.test/image:tag")]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                validate_image(item)

    def test_downloader_is_anonymous_digest_preserving_and_deterministic(self):
        def populate(arguments, **kwargs):
            self.assertIn("--src-no-creds", arguments)
            self.assertIn("--preserve-digests", arguments)
            self.assertIn("@" + self.image["manifest"], arguments[-2])
            self.assertEqual(set(kwargs["env"]), {"PATH", "TMPDIR"})
            layout = self.root / "layout"
            layout.mkdir(exist_ok=True)
            for name, content in self.files.items():
                path = layout / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        with patch("prepare_guest_images.subprocess.run", side_effect=populate):
            first = download(self.image, self.root).read_bytes()
            self.assertEqual(download(self.image, self.root).read_bytes(), first)
        verify_archive(self.root / "image.tar", self.image)
        (self.root / "layout/link").symlink_to("index.json")
        with patch("prepare_guest_images.subprocess.run"), self.assertRaisesRegex(ValueError, "nonregular"):
            download(self.image, self.root)


if __name__ == "__main__":
    unittest.main()
