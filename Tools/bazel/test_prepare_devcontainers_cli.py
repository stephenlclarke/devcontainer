"""Official CLI references are immutable, offline-reusable and never installed."""

import base64
import copy
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

import prepare_devcontainers_cli as cli
import prepare_releases as releases
from release_inputs import canonical


NODE = "node-v24.21.0-darwin-arm64/"


class ReferenceCLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.scratch, self.retained = self.root / "scratch", self.root / "retained"
        for path in (self.scratch, self.retained, self.scratch / "tmp"):
            path.mkdir(mode=0o700)
        self.package = {"name": "@devcontainers/cli", "version": "0.88.0",
                        "bin": {"devcontainer": "devcontainer.js"}, "dependencies": {}}
        self.sources, self.lock, self.fetches = {}, {"schemaVersion": 1}, []
        self.make_archive("node", [(NODE + "bin/node", b"node executable", 0o755),
                                   (NODE + "LICENSE", b"Node license", 0o644)])
        self.make_cli()
        self.oracle = {"version": "0.88.0", "npmIntegrity": self.lock["cli"]["integrity"]}

    def make_archive(self, name, members):
        source = self.root / (name + ".tgz")
        with tarfile.open(source, "w:gz") as archive:
            for entry in members:
                if isinstance(entry, tarfile.TarInfo):
                    archive.addfile(entry)
                else:
                    path, data, mode = entry
                    member = tarfile.TarInfo(path)
                    member.size, member.mode = len(data), mode
                    archive.addfile(member, io.BytesIO(data))
        source.chmod(0o600)
        version, url, algorithm, maximum, _ = cli.SOURCES[name]
        integrity = algorithm + "-" + base64.b64encode(hashlib.new(algorithm, source.read_bytes()).digest()).decode()
        self.lock[name] = {"version": version, "url": url, "integrity": integrity, "maxBytes": maximum}
        self.sources[url] = source

    def make_cli(self):
        self.make_archive("cli", [("package/devcontainer.js", b"require('./dist/main.js');", 0o755),
                                  ("package/dist/main.js", b"console.log('0.88.0');", 0o644),
                                  ("package/package.json", json.dumps(self.package).encode(), 0o644),
                                  ("package/LICENSE", b"MIT license", 0o644)])

    def fetch(self, item, directory):
        self.fetches.append(item["url"])
        target = directory / "archive"
        shutil.copyfile(self.sources[item["url"]], target)
        target.chmod(0o600)
        return target

    def prepare(self, **kwargs):
        return cli.prepare_cli(self.lock, self.oracle, self.scratch, self.retained, fetch=self.fetch, **kwargs)

    def archive_paths(self, name="node"):
        key = hashlib.sha256(canonical(self.lock[name]).encode()).hexdigest()
        return tuple(self.retained / "devcontainers-cli" / (key + suffix)
                     for suffix in (".tar.gz", ".json", ".pending.json"))

    def test_offline_and_online_reuse_survive_ssd_eviction_without_download_or_extraction(self):
        result = self.prepare()
        self.assertFalse(result["runtimeReady"])
        self.assertEqual(result["scope"], "devcontainers-reference-tools-only")
        self.assertEqual(Path(result["node"]).read_bytes(), b"node executable")
        self.assertEqual(Path(result["cli"]).read_bytes(), b"require('./dist/main.js');")
        self.assertEqual(Path(result["prepared"]["node"]["files"]["license"]).read_bytes(), b"Node license")
        shutil.rmtree(self.scratch / "prepared-releases")
        with patch.object(cli, "prepare", side_effect=AssertionError("no extraction")):
            self.assertEqual(self.prepare(offline=True), result)
            self.assertEqual(self.prepare(), result)
        self.assertEqual(len(self.fetches), 2)
        self.assertEqual(list((self.scratch / "tmp").iterdir()), [])

    def test_invalid_lock_fails_before_any_storage_change(self):
        locks = [{}, dict(self.lock, schemaVersion=True), dict(self.lock, extra=True)]
        for name, key, value in [("node", "version", "latest"), ("node", "url", "http://nodejs.org/file"),
                                 ("node", "integrity", "sha512-AAAA"), ("node", "integrity", "sha256-!!!!"),
                                 ("cli", "integrity", "sha512-AAAA"), ("cli", "maxBytes", True),
                                 ("node", "maxBytes", 2**30), ("cli", "maxBytes", 0)]:
            changed = copy.deepcopy(self.lock)
            changed[name][key] = value
            locks.append(changed)
        for value in locks:
            with self.subTest(lock=value), self.assertRaises(ValueError):
                cli.prepare_cli(value, self.oracle, self.scratch, self.retained, fetch=self.fetch)
        with self.assertRaises(ValueError):
            cli.prepare_cli(self.lock, {}, self.scratch, self.retained, fetch=self.fetch)
        self.assertEqual(list(self.retained.iterdir()), [])
        self.assertEqual(self.fetches, [])

    def test_offline_missing_storage_does_not_create_or_download(self):
        with self.assertRaises(ValueError):
            self.prepare(offline=True)
        self.assertEqual(list(self.retained.iterdir()), [])
        self.assertEqual(self.fetches, [])

    def test_changed_payload_or_archive_is_never_repaired(self):
        result = self.prepare()
        binary = Path(result["node"])
        original = binary.read_bytes()
        binary.write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            self.prepare()
        binary.write_bytes(original)
        archive, _, _ = self.archive_paths("cli")
        archive.write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            self.prepare(offline=True)
        self.assertEqual(len(self.fetches), 2)

    def test_hash_complete_pending_copy_seals_without_refetch(self):
        expected = self.prepare()
        archive, receipt, pending = self.archive_paths()
        receipt.rename(pending)
        before = archive.read_bytes()
        with self.assertRaises(ValueError):
            self.prepare(offline=True)
        self.assertEqual(self.prepare(), expected)
        self.assertEqual(archive.read_bytes(), before)
        self.assertFalse(pending.exists())
        self.assertEqual(len(self.fetches), 2)

    def test_partial_pending_copy_recovers_but_offline_preserves_it(self):
        expected = self.prepare()
        archive, receipt, pending = self.archive_paths()
        receipt.rename(pending)
        archive.write_bytes(b"partial")
        with self.assertRaises(ValueError):
            self.prepare(offline=True)
        self.assertEqual(archive.read_bytes(), b"partial")
        self.assertEqual(self.prepare(), expected)
        self.assertFalse(pending.exists())
        self.assertEqual(len(self.fetches), 3)

    def test_completed_receipt_with_pending_intent_requires_explicit_online_recovery(self):
        expected = self.prepare()
        _, receipt, pending = self.archive_paths()
        shutil.copyfile(receipt, pending)
        pending.chmod(0o600)
        with self.assertRaises(ValueError):
            self.prepare(offline=True)
        self.assertEqual(self.prepare(), expected)
        self.assertFalse(pending.exists())
        self.assertEqual(len(self.fetches), 2)

    def test_changed_receipt_or_intent_and_unregistered_archive_are_refused(self):
        self.prepare()
        archive, receipt, pending = self.archive_paths()
        expected = receipt.read_bytes()
        receipt.write_text("{}")
        with self.assertRaises(ValueError):
            self.prepare()
        receipt.write_bytes(expected)
        pending.write_text("{}")
        pending.chmod(0o600)
        with self.assertRaises(ValueError):
            self.prepare()
        receipt.unlink()
        with self.assertRaises(ValueError):
            self.prepare()
        pending.unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.prepare()
        self.assertTrue(archive.exists())
        self.assertEqual(len(self.fetches), 2)

    def test_prepared_pending_state_is_not_admitted_offline(self):
        expected = self.prepare()
        root = Path(expected["prepared"]["node"]["root"])
        pending = root.with_name(root.name + ".pending.json")
        pending.write_text("{}")
        with self.assertRaises(ValueError):
            self.prepare(offline=True)
        self.assertEqual(len(self.fetches), 2)

    def test_aliases_and_unsafe_directory_modes_are_refused(self):
        self.scratch.chmod(0o777)
        with self.assertRaises(ValueError):
            self.prepare()
        self.scratch.chmod(0o755)  # Enrolled SSD parents are not group writable.
        alias = self.retained / "devcontainers-cli"
        alias.symlink_to(self.scratch, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(self.fetches, [])

    def test_hash_failure_or_oversized_download_leaves_no_sealed_archive(self):
        source = self.sources[self.lock["node"]["url"]]
        source.write_bytes(b"wrong content")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(list((self.retained / "devcontainers-cli").iterdir()), [])
        item = dict(self.lock["node"], maxBytes=1)
        with self.assertRaises(ValueError):
            cli.verify_archive(source, item)
        self.assertEqual(list((self.scratch / "tmp").iterdir()), [])

    def test_selected_node_payload_omits_unneeded_npm_links(self):
        link = tarfile.TarInfo(NODE + "bin/npm")
        link.type, link.linkname = tarfile.SYMTYPE, "../lib/node_modules/npm/bin/npm-cli.js"
        self.make_archive("node", [(NODE + "bin/node", b"node", 0o755), (NODE + "LICENSE", b"license", 0o644), link])
        result = self.prepare()
        self.assertFalse((Path(result["node"]).parent / "npm").exists())

    def test_selected_tar_rejects_links_missing_duplicate_and_unsafe_members(self):
        binary = (NODE + "bin/node", b"node", 0o755)
        license_file = (NODE + "LICENSE", b"license", 0o644)
        link = tarfile.TarInfo(NODE + "bin/node")
        link.type, link.linkname = tarfile.SYMTYPE, "/tmp/foreign"
        cases = [[link, license_file], [binary], [binary, binary, license_file],
                 [binary, license_file, ("../foreign", b"x", 0o644)]]
        for index, members in enumerate(cases):
            self.make_archive("node", members)
            target = self.root / str(index)
            target.mkdir()
            specification = releases.layout({"repository": "nodejs.org/dist", "tag": "24.21.0",
                                             "name": "node-v24.21.0-darwin-arm64.tar.gz"})
            with self.subTest(case=index), self.assertRaises(ValueError):
                releases.unpack_selected_tar(self.sources[self.lock["node"]["url"]], target, specification)

    def test_unexpected_cli_package_dependencies_are_rejected(self):
        self.package["dependencies"] = {"unreviewed": "latest"}
        self.make_cli()
        self.oracle["npmIntegrity"] = self.lock["cli"]["integrity"]
        with self.assertRaises(ValueError):
            self.prepare()

    def test_selected_tar_limits_apply_even_to_omitted_members(self):
        source = self.sources[self.lock["node"]["url"]]
        specification = releases.layout({"repository": "nodejs.org/dist", "tag": "24.21.0",
                                         "name": "node-v24.21.0-darwin-arm64.tar.gz"})
        for key in ("MAX_FILES", "MAX_BYTES"):
            target = self.root / key
            target.mkdir()
            with self.subTest(limit=key), patch.object(releases, key, 1), self.assertRaises(ValueError):
                releases.unpack_selected_tar(source, target, specification)

    def test_downloader_failure_leaves_no_durable_intent(self):
        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("curl", 330)):
            with self.assertRaises(subprocess.TimeoutExpired):
                cli.prepare_cli(self.lock, self.oracle, self.scratch, self.retained)
        self.assertEqual(list((self.retained / "devcontainers-cli").iterdir()), [])
        self.assertEqual(list((self.scratch / "tmp").iterdir()), [])

    def test_downloader_has_fixed_bounds_and_clean_environment(self):
        def run(arguments, **kwargs):
            self.assertEqual(arguments[:2], ["/usr/bin/curl", "-q"])
            self.assertNotIn("--location", arguments)
            self.assertEqual(arguments[arguments.index("--retry") + 1], "0")
            self.assertEqual(kwargs["timeout"], 330)
            self.assertEqual(set(kwargs["env"]), {"PATH", "HOME", "TMPDIR"})
            self.assertEqual(arguments[arguments.index("--max-filesize") + 1], str(self.lock["node"]["maxBytes"]))
            Path(arguments[arguments.index("--output") + 1]).write_bytes(b"downloaded")
            return SimpleNamespace(returncode=0)
        with patch.object(subprocess, "run", side_effect=run):
            target = cli.download(self.lock["node"], self.root)
        self.assertEqual(target.read_bytes(), b"downloaded")
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
