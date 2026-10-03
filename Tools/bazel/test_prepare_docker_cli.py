"""Docker oracle acquisition is digest-bound, daemon-free and recoverable."""

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import prepare_docker_cli as cli
from release_inputs import canonical, sha256


class DockerCLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.scratch, self.retained = self.root / "scratch", self.root / "retained"
        for directory in (self.scratch, self.retained, self.scratch / "tmp"):
            directory.mkdir(mode=0o700)
        self.source = self.root / "bottle.tar.gz"
        with tarfile.open(self.source, "w:gz") as archive:
            for name, data in (("bin/docker", b"fixture executable"), ("LICENSE", b"Apache-2.0")):
                entry = tarfile.TarInfo("docker/29.6.2/" + name)
                entry.size, entry.mode = len(data), 0o755 if name == "bin/docker" else 0o644
                archive.addfile(entry, io.BytesIO(data))
        self.lock = {"schemaVersion": 1, "repository": "ghcr.io/homebrew/core/docker", "version": "29.6.2",
                     "manifest": "1" * 64, "manifestSize": 123, "bottleSHA256": sha256(self.source),
                     "bottleSize": self.source.stat().st_size,
                     "executableSHA256": hashlib.sha256(b"fixture executable").hexdigest()}
        self.oracle = {"cliVersion": self.lock["version"], "cliSHA256": self.lock["executableSHA256"],
                       "cliBottleSHA256": self.lock["bottleSHA256"]}
        self.fetches = 0

    def fetch(self, _lock, directory):
        self.fetches += 1
        path = directory / "bottle"
        shutil.copyfile(self.source, path)
        return path

    def prepare(self, **kwargs):
        return cli.prepare_cli(self.lock, self.oracle, self.scratch, self.retained, fetch=self.fetch, **kwargs)

    def paths(self):
        key = hashlib.sha256(canonical(self.lock).encode()).hexdigest()
        return tuple(self.retained / "docker-cli" / (key + suffix)
                     for suffix in (".tar.gz", ".json", ".pending.json"))

    def test_prepare_offline_reuse_and_ssd_eviction_preserve_exact_binary_and_notices(self):
        first = self.prepare()
        self.assertFalse(first["runtimeReady"])
        self.assertEqual(first["scope"], "docker-cli-only")
        self.assertEqual(Path(first["executables"]["docker"]).read_bytes(), b"fixture executable")
        self.assertEqual((Path(first["root"]) / "docker/29.6.2/LICENSE").read_text(), "Apache-2.0")
        shutil.rmtree(self.scratch / "prepared-releases")
        # An empty SSD extraction cache is not required by read-only admission.
        with patch.object(cli, "prepare", side_effect=AssertionError("no repeated extraction")):
            self.assertEqual(self.prepare(offline=True), first)
            self.assertEqual(self.prepare(), first)
        self.assertEqual(self.fetches, 1)
        self.assertFalse(list((self.scratch / "tmp").iterdir()))

    def test_lock_rejects_bad_sources_types_sizes_and_oracle_drift_before_writes(self):
        for key, value in (("schemaVersion", True), ("repository", "example.org/docker"), ("version", "latest"),
                           ("manifest", "../x"), ("bottleSHA256", 1), ("bottleSize", True),
                           ("manifestSize", 0), ("bottleSize", 2**30), ("executableSHA256", "f" * 64)):
            changed = dict(self.lock, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                cli.prepare_cli(changed, self.oracle, self.scratch, self.retained, fetch=self.fetch)
        self.assertEqual(list(self.retained.iterdir()), [])
        self.assertEqual(self.fetches, 0)

    def test_offline_missing_assets_does_not_write_or_fetch(self):
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.prepare(offline=True)
        self.assertEqual(list(self.retained.iterdir()), [])
        self.assertEqual(self.fetches, 0)

    def test_corrupt_sealed_archive_receipt_and_executable_fail_without_repair(self):
        result = self.prepare()
        target, receipt, _ = self.paths()
        for path, data in ((target, b"corrupt"), (receipt, b"{}"),
                           (Path(result["executables"]["docker"]), b"changed")):
            original = path.read_bytes()
            path.write_bytes(data)
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.prepare()
            path.write_bytes(original)
        self.assertEqual(self.fetches, 1)

    def test_wrong_executable_pin_is_not_success(self):
        self.lock["executableSHA256"] = "e" * 64
        self.oracle["cliSHA256"] = "e" * 64
        with self.assertRaisesRegex(ValueError, "executable pin"):
            self.prepare()

    def test_interrupted_raw_copy_requires_recovery_and_is_reused_afterward(self):
        original = cli.durable_file
        def interrupt(path, source):
            original(path, source)
            raise OSError("simulated interruption")
        with patch.object(cli, "durable_file", side_effect=interrupt), self.assertRaises(OSError):
            self.prepare()
        target, receipt, pending = self.paths()
        self.assertTrue(target.exists() and pending.exists())
        self.assertFalse(receipt.exists())
        with self.assertRaisesRegex(ValueError, "not sealed"):
            self.prepare(offline=True)
        self.assertEqual(self.fetches, 1)
        result = self.prepare()
        self.assertEqual(self.prepare(offline=True), result)
        self.assertFalse(pending.exists())
        self.assertEqual(self.fetches, 2)

    def test_sealed_pending_recovery_validates_owner_without_download(self):
        first = self.prepare()
        _, _, pending = self.paths()
        pending.write_text(canonical(self.lock))
        pending.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "Pending"):
            self.prepare(offline=True)
        pending.write_text("{}")
        with self.assertRaisesRegex(ValueError, "ownership changed"):
            self.prepare()
        pending.write_text(canonical(self.lock))
        self.assertEqual(self.prepare(), first)
        self.assertFalse(pending.exists())
        self.assertEqual(self.fetches, 1)

    def test_unregistered_alias_or_foreign_pending_bytes_are_not_overwritten(self):
        archives = self.retained / "docker-cli"
        archives.mkdir(mode=0o700)
        target, _, pending = self.paths()
        target.write_bytes(b"unregistered")
        target.chmod(0o600)
        with self.assertRaises(FileNotFoundError):
            self.prepare()
        self.assertEqual(target.read_bytes(), b"unregistered")
        pending.write_text("{}")
        pending.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "ownership changed"):
            self.prepare()
        target.unlink()
        target.symlink_to(self.source)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(self.fetches, 0)

    def test_storage_alias_and_world_readable_roots_fail(self):
        self.retained.rmdir()
        self.retained.symlink_to(self.scratch)
        with self.assertRaisesRegex(ValueError, "private canonical"):
            self.prepare()
        self.retained.unlink()
        self.retained.mkdir(mode=0o700)
        (self.scratch / "tmp").chmod(0o777)
        with self.assertRaisesRegex(ValueError, "private, canonical"):
            self.prepare()
        self.assertEqual(self.fetches, 0)

    def test_nonprivate_retained_child_fails_before_acquisition(self):
        (self.retained / "docker-cli").mkdir(mode=0o755)
        (self.retained / "docker-cli").chmod(0o755)  # Do not let the runner's umask make the negative fixture private.
        with self.assertRaisesRegex(ValueError, "private canonical"):
            self.prepare()
        self.assertEqual(self.fetches, 0)

    def test_command_dispatch_and_storage_separation(self):
        module = self.root / "Tools/bazel/prepare_docker_cli.py"
        module.parent.mkdir(parents=True)
        oracle = self.root / "Tests/Parity/manifest.json"
        oracle.parent.mkdir(parents=True)
        oracle.write_text(canonical({"referencePins": {"docker": self.oracle}}))
        lock = self.root / "lock.json"
        lock.write_text(canonical(self.lock))
        with patch.object(cli, "__file__", str(module)), patch.object(Path, "home", return_value=self.root), \
                patch.object(Path, "stat", autospec=True, side_effect=lambda path, **kwargs:
                             SimpleNamespace(st_dev=2 if str(path) == "/Volumes/SSD/cf/bazel" else 1)), \
                patch.object(cli.os, "umask"), patch.object(cli, "prepare_cli", return_value={"runtimeReady": False}) as prepare, \
                patch("sys.argv", ["prepare_docker_cli.py", str(lock), "--offline"]), patch("sys.stdout", new_callable=io.StringIO) as output:
            cli.main()
            self.assertEqual(json.loads(output.getvalue()), {"runtimeReady": False})
            self.assertEqual(prepare.call_args.args[:2], (self.lock, self.oracle))
            self.assertTrue(prepare.call_args.kwargs["offline"])
            prepare.reset_mock()
            with patch.object(Path, "stat", return_value=SimpleNamespace(st_dev=1)), \
                    self.assertRaisesRegex(ValueError, "separate SSD"):
                cli.main()
            prepare.assert_not_called()

    def test_failed_or_changed_download_never_seals(self):
        with patch.object(self, "fetch", side_effect=subprocess.TimeoutExpired("skopeo", 300)), self.assertRaises(subprocess.TimeoutExpired):
            self.prepare()
        self.source.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(list((self.retained / "docker-cli").iterdir()))

    def test_download_is_anonymous_bounded_and_pins_manifest_and_bottle(self):
        manifest = {"layers": [{"digest": "sha256:" + self.lock["bottleSHA256"], "size": self.lock["bottleSize"],
                                "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip"}]}
        def set_manifest(value):
            data = canonical(value).encode()
            self.lock.update(manifest=hashlib.sha256(data).hexdigest(), manifestSize=len(data))
            return data
        data = set_manifest(manifest)
        def copy(*args, **kwargs):
            blobs = self.root / "layout/blobs/sha256"
            blobs.mkdir(parents=True, exist_ok=True)
            (blobs / self.lock["manifest"]).write_bytes(data)
            shutil.copyfile(self.source, blobs / self.lock["bottleSHA256"])
        with patch.object(cli.subprocess, "run", side_effect=copy) as run:
            self.assertEqual(sha256(cli.download(self.lock, self.root)), self.lock["bottleSHA256"])
            self.assertIn("--src-no-creds", run.call_args.args[0])
            self.assertIn("--preserve-digests", run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs["timeout"], 300)
            self.assertEqual(set(run.call_args.kwargs["env"]), {"PATH", "TMPDIR", "HOME"})
            self.assertEqual(run.call_args.kwargs["env"]["HOME"], str(self.root))
            self.assertIn("--src-tls-verify=true", run.call_args.args[0])
            self.assertIn("--src-cert-dir", run.call_args.args[0])
            self.assertIn("--registries.d", run.call_args.args[0])
            self.assertNotIn("--registries-conf", run.call_args.args[0])
            self.assertEqual(json.loads((self.root / "policy.json").read_text()), {
                "default": [{"type": "reject"}], "transports": {"docker": {
                    self.lock["repository"]: [{"type": "insecureAcceptAnything"}]}}})
            self.assertEqual((self.root / ".config/containers/registries.conf").read_text(),
                             'unqualified-search-registries = []\n')
            for value in ({"layers": []}, {"layers": [dict(manifest["layers"][0], size=1)]}):
                data = set_manifest(value)
                with self.assertRaisesRegex(ValueError, "pinned bottle"):
                    cli.download(self.lock, self.root)
            data = b"modified manifest"
            with self.assertRaises(ValueError):
                cli.download(self.lock, self.root)


if __name__ == "__main__":
    unittest.main()
