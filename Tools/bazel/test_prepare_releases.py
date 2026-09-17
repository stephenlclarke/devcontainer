"""Release preparation never installs, compiles, overwrites or trusts SSD residue."""

import io
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import prepare_releases as preparation
from release_inputs import sha256


class PrepareReleasesTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name).resolve()
        self.prepared = self.root / "prepared"
        self.receipts = self.root / "receipts"
        self.prepared.mkdir()
        self.receipts.mkdir()
        self.source = self.root / "download"
        self.asset = {"repository": "stephenlclarke/container-compose", "tag": "0.15.1",
                      "name": "container-compose-plugin-release-arm64.tar.gz"}

    def archive(self, extra=(), *, executable=True):
        with tarfile.open(self.source, "w:gz") as archive:
            directory = tarfile.TarInfo("./")
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
            for name in ["compose", "compose/bin", "compose/resources"]:
                directory = tarfile.TarInfo(name)
                directory.type = tarfile.DIRTYPE
                archive.addfile(directory)
            for name in ["compose/bin/compose", "compose/resources/compose-normalizer"]:
                entry = tarfile.TarInfo(name)
                entry.size = 7
                entry.mode = 0o755 if executable else 0o644
                archive.addfile(entry, io.BytesIO(b"fixture"))
            for entry in extra:
                archive.addfile(entry, io.BytesIO(b"x" * entry.size))
        self.asset.update(size=self.source.stat().st_size, sha256=sha256(self.source))

    def prepare(self, **kwargs):
        return preparation.prepare(self.asset, self.source, self.prepared, self.receipts, **kwargs)

    def test_all_five_published_layouts_are_explicit(self):
        for repository, name, count in [
            ("stephenlclarke/devcontainer", "devcontainer-release-arm64.tar.gz", 3),
            ("stephenlclarke/container-compose", "container-compose-plugin-release-arm64.tar.gz", 2),
            ("stephenlclarke/container-compose", "container-release-arm64.tar.gz", 3),
            ("apple/container", "container-1.4.1-installer-signed.pkg", 2),
            ("docker/compose", "docker-compose-darwin-aarch64", 1),
        ]:
            value = preparation.layout({"repository": repository, "name": name, "tag": "1.4.1"})
            self.assertEqual(len(value["executables"]), count)
        with self.assertRaisesRegex(ValueError, "reviewed layout"):
            preparation.layout({"repository": "unknown/repo", "name": "tool", "tag": "1"})

    def test_tar_reuses_authenticated_preparation_without_extraction(self):
        self.archive()
        result = self.prepare()
        self.assertEqual(Path(result["executables"]["compose"]).read_bytes(), b"fixture")
        with patch.object(preparation, "unpack_tar", side_effect=AssertionError("must not repeat")):
            self.assertEqual(self.prepare(), result)
        shutil.rmtree(Path(result["root"]))
        self.assertEqual(self.prepare(), result)  # SSD eviction never loses the retained asset.
        self.assertEqual(len(list(self.prepared.iterdir())), 1)

    def test_runtime_admission_never_recreates_missing_payloads(self):
        self.archive()
        with self.assertRaises(FileNotFoundError):
            preparation.require_prepared(self.asset, self.source, self.prepared, self.receipts)
        prepared = self.prepare()
        self.assertEqual(preparation.require_prepared(self.asset, self.source, self.prepared, self.receipts), prepared)
        shutil.rmtree(Path(prepared["root"]))
        with self.assertRaisesRegex(ValueError, "root or receipt"):
            preparation.require_prepared(self.asset, self.source, self.prepared, self.receipts)
        self.assertEqual(list(self.prepared.iterdir()), [])

    def test_raw_release_has_only_the_pinned_binary(self):
        self.source.write_bytes(b"released executable")
        self.asset.update(repository="docker/compose", name="docker-compose-darwin-aarch64",
                          size=self.source.stat().st_size, sha256=sha256(self.source))
        result = self.prepare()
        command = Path(result["executables"]["docker-compose"])
        self.assertEqual(command.read_bytes(), self.source.read_bytes())
        self.assertEqual(command.stat().st_mode & 0o777, 0o755)

    def test_package_expansion_not_installation_and_exact_layout(self):
        self.source.write_bytes(b"signed package fixture")
        self.asset.update(repository="apple/container", tag="1.4.1", name="container-1.4.1-installer-signed.pkg",
                          size=self.source.stat().st_size, sha256=sha256(self.source))

        def expand(source, destination):
            self.assertEqual(source, self.source)
            binary_root = destination / "Payload/bin"
            binary_root.mkdir(parents=True)
            for name in ("container", "container-apiserver"):
                binary = binary_root / name
                binary.write_bytes(b"fixture")
                binary.chmod(0o755)

        result = self.prepare(expand=expand)
        self.assertTrue(Path(result["executables"]["container"]).is_file())
        with patch.object(preparation.subprocess, "run") as run:
            preparation.expand_package(self.source, self.root / "expansion")
        self.assertEqual(run.call_args.args[0][:2], ["/usr/sbin/pkgutil", "--expand-full"])
        self.assertEqual(run.call_args.kwargs["timeout"], 120)
        self.assertNotIn("HOME", run.call_args.kwargs["env"])

    def test_archive_path_and_member_types_fail_before_publication(self):
        for name, kind in [("/outside", tarfile.REGTYPE), ("../outside", tarfile.REGTYPE),
                           ("bad\\path", tarfile.REGTYPE), (preparation.RECEIPT, tarfile.REGTYPE),
                           ("compose/bin/compose", tarfile.REGTYPE), ("link", tarfile.SYMTYPE),
                           ("hardlink", tarfile.LNKTYPE), ("device", tarfile.CHRTYPE), (".", tarfile.REGTYPE)]:
            with self.subTest(name=name):
                entry = tarfile.TarInfo(name)
                entry.type = kind
                entry.linkname = "/outside"
                self.archive([entry])
                with self.assertRaises(ValueError):
                    self.prepare()
                self.assertEqual(list(self.prepared.iterdir()), [])
                self.assertEqual(list(self.receipts.iterdir()), [])

    def test_bounded_inventory_and_expansion(self):
        self.archive()
        with patch.object(preparation, "MAX_FILES", 1), self.assertRaisesRegex(ValueError, "excessive"):
            self.prepare()
        with patch.object(preparation, "MAX_BYTES", 1), self.assertRaisesRegex(ValueError, "size limit"):
            self.prepare()
        result = self.prepare()
        with patch.object(preparation, "MAX_BYTES", 1), self.assertRaisesRegex(ValueError, "inventory limits"):
            preparation.inventory(Path(result["root"]))

    def test_missing_execute_permission_is_not_a_ready_binary(self):
        self.archive(executable=False)
        with self.assertRaisesRegex(ValueError, "not executable"):
            self.prepare()
        self.assertEqual(list(self.prepared.iterdir()), [])

    def test_mutated_download_and_payload_are_rejected(self):
        self.archive()
        result = self.prepare()
        command = Path(result["executables"]["compose"])
        command.write_bytes(b"substitution")
        with self.assertRaisesRegex(ValueError, "bytes or modes"):
            self.prepare()
        self.source.write_bytes(b"changed retained archive")
        with self.assertRaisesRegex(ValueError, "failed verification"):
            self.prepare()

    def test_changed_ssd_receipt_cannot_authorize_changed_payload(self):
        self.archive()
        result = self.prepare()
        root = Path(result["root"])
        Path(result["executables"]["compose"]).write_bytes(b"substitution")
        receipt = root / preparation.RECEIPT
        value = json.loads(receipt.read_text())
        value["inventory"] = preparation.inventory(root)
        receipt.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "internally retained"):
            self.prepare()

    def test_changed_specification_and_missing_receipt_are_rejected(self):
        self.archive()
        result = self.prepare()
        root = Path(result["root"])
        receipt = json.loads((root / preparation.RECEIPT).read_text())
        with self.assertRaisesRegex(ValueError, "identity differs"):
            preparation.validate_prepared(root, {}, receipt)
        next(self.receipts.iterdir()).unlink()
        with self.assertRaises(FileNotFoundError):
            self.prepare()

    def test_symlinks_and_unknown_residue_are_never_overwritten(self):
        self.archive()
        alias = self.root / "alias"
        alias.symlink_to(self.prepared)
        with self.assertRaisesRegex(ValueError, "non-symlinked"):
            preparation.prepare(self.asset, self.source, alias, self.receipts)
        result = self.prepare()
        command = Path(result["executables"]["compose"])
        command.unlink()
        command.symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, "link or special"):
            self.prepare()
        retained = next(self.receipts.iterdir())
        retained.unlink()
        retained.symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, "Symlinked retained"):
            self.prepare()
        with self.assertRaisesRegex(ValueError, "Symlinked retained"):
            preparation.retain_receipt(retained, {})

    def test_recovery_refuses_inconsistent_retained_manifest(self):
        self.archive()
        result = self.prepare()
        shutil.rmtree(Path(result["root"]))
        next(self.receipts.iterdir()).write_text("{}")
        with self.assertRaisesRegex(ValueError, "different retained"):
            self.prepare()
        self.assertEqual(list(self.prepared.iterdir()), [])

    def test_matching_recovered_receipt_reestablishes_file_and_directory_durability(self):
        path = self.receipts / "recovered.json"
        path.write_text('{"receipt":"fixture"}')
        with patch.object(preparation.os, "fsync", wraps=os.fsync) as sync:
            preparation.retain_receipt(path, {"receipt": "fixture"})
        self.assertEqual(sync.call_count, 2)


if __name__ == "__main__":
    unittest.main()
