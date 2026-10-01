#!/usr/bin/env python3
"""Portable admission checks for accepted native package assembly."""

from __future__ import annotations

import hashlib
import gzip
import importlib.util
import json
import argparse
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest import mock


SOURCE = Path(__file__).with_name("finalize-native-package.py")
SPEC = importlib.util.spec_from_file_location("finalize_native_package", SOURCE)
FINAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FINAL)


def sha(contents: bytes) -> str:
    """Return one fixture's literal checksum."""
    return hashlib.sha256(contents).hexdigest()


def inventory(tree: Path, excluded: Path) -> dict:
    """Model the maintained signer's regular-file inventory contract."""
    result = {}
    for path in tree.rglob("*"):
        if path.is_file() and path != excluded:
            content = path.read_bytes()
            result[path.relative_to(tree).as_posix()] = {
                "sha256": sha(content), "size": len(content), "mode": stat.S_IMODE(path.stat().st_mode)}
    return result


class Signer:
    """Only the signer operations needed for portable finalizer admission tests."""

    BINARIES = ("bin/devcontainer",)
    NODE = "bin/devcontainer"
    JIT = {"com.apple.security.cs.allow-jit": True}

    def __init__(self):
        self.provenance_calls = 0
        self.zip_calls = 0
        self.signature_calls = 0
        self.smoke_calls = 0
        self.payload = {}

    def authenticate_provenance(self, path, expected, receipt, tree):
        if FINAL.digest(path) != expected:
            raise ValueError("provenance checksum")
        self.provenance_calls += 1

    def require_signed_payload(self, unsigned, signed):
        if set(unsigned) != set(signed):
            raise ValueError("signed closure")

    def verify_zip(self, archive, stage, signed):
        self.zip_calls += 1

    @staticmethod
    def inventory(stage, evidence):
        return inventory(stage, evidence)

    @staticmethod
    def physical(path):
        return path

    @staticmethod
    def validate_storage(stage, state, scratch, evidence):
        scratch.mkdir(parents=True, exist_ok=True)

    def restore_signed_tree(self, archive, destination, tree):
        destination.mkdir()
        for name, value in self.payload.items():
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
            path.chmod(tree[name]["mode"])

    def verify_signatures(self, tree, state, team, prefix):
        self.signature_calls += 1
        self.last_signatures = (tree, team, prefix)
        return {"bin/devcontainer": {"sha256": sha((tree / "bin/devcontainer").read_bytes()),
                                      "teamIdentifier": team, "entitlements": self.JIT}}

    def smoke(self, tree, state, scratch, receipt, prefix):
        self.smoke_calls += 1
        self.last_smoke = (tree, receipt["commit"], prefix)


class Archiver:
    """Model the maintained deterministic archive helper without native tools."""

    @staticmethod
    def create_archive(tree, destination, epoch):
        with destination.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=epoch) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as output:
                for path in [tree, *sorted(tree.rglob("*"))]:
                    member = tarfile.TarInfo(tree.name if path == tree else tree.name + "/" + path.relative_to(tree).as_posix())
                    member.uid, member.gid = 0, 0
                    member.uname, member.gname = "root", "wheel"
                    member.mtime = epoch
                    member.mode = stat.S_IMODE(path.stat().st_mode)
                    if path.is_dir():
                        member.type = tarfile.DIRTYPE
                        output.addfile(member)
                    else:
                        member.type = tarfile.REGTYPE
                        member.size = path.stat().st_size
                        with path.open("rb") as contents:
                            output.addfile(member, contents)


class FinalizationTests(unittest.TestCase):
    """Test immutable authority and byte-exact final payload boundaries."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.state.chmod(0o700)
        (self.state / "owner.lock").write_bytes(b"")
        self.stage = self.root / "missing-stage"
        self.evidence = self.root / "accepted.json"
        self.archive = self.state / "submitted.zip"
        self.archive.write_bytes(b"retained accepted zip")
        self.evidence.write_bytes(b'{"archiveSHA256":"' + sha(self.archive.read_bytes()).encode()
                                  + b'","id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee","status":"Accepted"}\n')
        self.candidate = {"commit": "a" * 40, "runtimeProfile": "stock", "version": "1.2.3"}
        candidate_bytes = FINAL.canonical_json(self.candidate)
        (self.state / "candidate-receipt.json").write_bytes(candidate_bytes)
        provenance_bytes = FINAL.canonical_json({"scope": "unsigned-native-package-stage"})
        (self.state / "stage-provenance.json").write_bytes(provenance_bytes)
        self.signed = {"bin/devcontainer": {"sha256": sha(b"signed"), "size": 6, "mode": 0o755}}
        self.record = {
            "schema": 1, "phase": "accepted", "notary": {"status": "Accepted",
            "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
            "submissionID": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "archiveSHA256": sha(self.archive.read_bytes()), "archiveSize": self.archive.stat().st_size,
            "candidateSHA256": sha(candidate_bytes), "candidateCommit": "a" * 40,
            "runtimeProfile": "stock", "stageProvenanceSHA256": sha(provenance_bytes),
            "unsignedTree": self.signed, "signedTree": self.signed,
            "signatures": {"bin/devcontainer": {"sha256": sha(b"signed"),
            "teamIdentifier": "TEAM123456", "entitlements": Signer.JIT}},
            "teamIdentifier": "TEAM123456", "acceptanceSHA256": sha(self.evidence.read_bytes()),
            "stage": str(self.stage), "evidence": str(self.evidence),
        }
        self.save_record()
        self.signer = Signer()

    def save_record(self):
        """Persist one terminal state and return the caller-trusted digest."""
        path = self.state / "state.json"
        path.write_bytes(FINAL.canonical_json(self.record))
        return sha(path.read_bytes())

    def test_accepted_state_binds_all_retained_authorities_without_original_stage(self):
        trusted = self.save_record()
        record, receipt, provenance, acceptance = FINAL.admitted_state(self.state, trusted, self.signer)
        self.assertEqual(record, self.record)
        self.assertEqual(receipt, self.candidate)
        self.assertEqual(provenance["scope"], "unsigned-native-package-stage")
        self.assertEqual(acceptance, self.evidence.read_bytes())
        self.assertEqual((self.signer.provenance_calls, self.signer.zip_calls), (1, 1))

    def test_wrong_state_hash_or_nonaccepted_phase_fails(self):
        with self.assertRaisesRegex(ValueError, "Trusted accepted-state"):
            FINAL.admitted_state(self.state, "0" * 64, self.signer)
        self.record["phase"] = "submitted"
        trusted_hash = self.save_record()
        with self.assertRaisesRegex(ValueError, "not one accepted"):
            FINAL.admitted_state(self.state, trusted_hash, self.signer)

    def test_zip_or_acceptance_mismatch_fails(self):
        trusted = self.save_record()
        self.archive.write_bytes(b"different zip")
        with self.assertRaisesRegex(ValueError, "Submitted notary ZIP"):
            FINAL.admitted_state(self.state, trusted, self.signer)
        self.archive.write_bytes(b"retained accepted zip")
        self.evidence.write_bytes(b'{"archiveSHA256":"' + sha(self.archive.read_bytes()).encode()
                                  + b'","id":"ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee","status":"Accepted"}\n')
        self.record["acceptanceSHA256"] = sha(self.evidence.read_bytes())
        trusted_hash = self.save_record()
        with self.assertRaisesRegex(ValueError, "unexpected fields"):
            FINAL.admitted_state(self.state, trusted_hash, self.signer)

    def test_only_a_nonnegative_integer_stager_epoch_is_admitted(self):
        self.assertEqual(FINAL.source_epoch({"sourceDateEpoch": 0}), 0)
        self.assertEqual(FINAL.source_epoch({"sourceDateEpoch": 1234567890}), 1234567890)
        for value in (None, -1, True, "1234567890", 1.5):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "source date epoch"):
                FINAL.source_epoch({"sourceDateEpoch": value})

    def test_signed_closure_is_exact_with_only_acceptance_added(self):
        tree = self.root / "tree"
        (tree / "bin").mkdir(parents=True)
        (tree / "share/devcontainer").mkdir(parents=True)
        binary = tree / "bin/devcontainer"
        binary.write_bytes(b"signed")
        binary.chmod(0o755)
        metadata = tree / "share/devcontainer/candidate.json"
        metadata.write_bytes(b"metadata")
        metadata.chmod(0o600)
        signed = inventory(tree, tree / "missing")
        result = FINAL.normalized_inventory(tree, signed, self.evidence.read_bytes(), self.signer)
        self.assertEqual(set(result), set(signed) | {"share/devcontainer/notarization.json"})
        self.assertEqual(binary.read_bytes(), b"signed")
        self.assertEqual(metadata.read_bytes(), b"metadata")
        self.assertEqual(result["share/devcontainer/candidate.json"]["mode"], 0o644)
        self.assertEqual(result["bin/devcontainer"]["mode"], 0o755)
        self.assertEqual((tree / "share/devcontainer/notarization.json").read_bytes(), self.evidence.read_bytes())

    def test_extra_signed_payload_fails_before_normalization(self):
        tree = self.root / "tree"
        tree.mkdir()
        (tree / "injected").write_bytes(b"unexpected")
        with self.assertRaisesRegex(ValueError, "Restored signed ZIP tree differs"):
            FINAL.normalized_inventory(tree, {}, b"accepted", self.signer)

    def test_tar_closure_requires_exact_bytes_and_normalized_metadata(self):
        tree = self.root / "devcontainer-1.2.3"
        tree.mkdir()
        payload = tree / "payload"
        payload.write_bytes(b"content")
        payload.chmod(0o644)
        archive = self.root / "archive.tar.gz"
        with archive.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=1234567890) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as output:
                for path in (tree, payload):
                    member = tarfile.TarInfo(tree.name if path == tree else tree.name + "/payload")
                    member.uid, member.gid = 0, 0
                    member.uname, member.gname = "root", "wheel"
                    member.mtime = 1234567890
                    member.mode = 0o755 if path == tree else 0o644
                    member.type = tarfile.DIRTYPE if path == tree else tarfile.REGTYPE
                    member.size = 0 if path == tree else 7
                    if path == tree:
                        output.addfile(member)
                    else:
                        with path.open("rb") as contents:
                            output.addfile(member, contents)
        FINAL.verify_tar_closure(archive, tree, {"payload": {"sha256": sha(b"content"),
                                                             "size": 7, "mode": 0o644}}, 1234567890)
        wrong_closure = {"payload": {"sha256": sha(b"wrong"), "size": 5, "mode": 0o644}}
        with self.assertRaisesRegex(ValueError, "closure differs"):
            FINAL.verify_tar_closure(archive, tree, wrong_closure, 1234567890)
        unnormalized_closure = {"payload": {"sha256": sha(b"content"), "size": 7, "mode": 0o644}}
        with self.assertRaisesRegex(ValueError, "metadata is not normalized"):
            FINAL.verify_tar_closure(archive, tree, unnormalized_closure, 1)

    def test_full_assembly_calls_signature_and_cli_smoke_before_promotion(self):
        self.signer.payload = {
            "bin/devcontainer": b"signed",
            "share/devcontainer/Package.resolved": b"resolved",
            "share/devcontainer/dependency-licenses.selected.json": b"licenses",
        }
        self.signed = {name: {"sha256": sha(contents), "size": len(contents),
                              "mode": 0o755 if name == "bin/devcontainer" else 0o600}
                       for name, contents in self.signer.payload.items()}
        self.record["signedTree"] = self.signed
        self.record["unsignedTree"] = self.signed
        provenance = {"scope": "unsigned-native-package-stage", "lane": "development",
                      "sourceDateEpoch": 1234567890,
                      "packageContext": {"asset": "devcontainer-1.2.3-macos-arm64.tar.gz",
                                         "commit": "a" * 40, "lane": "development", "productVersion": "1.2.3"}}
        data = FINAL.canonical_json(provenance)
        (self.state / "stage-provenance.json").write_bytes(data)
        self.record["stageProvenanceSHA256"] = sha(data)
        self.record["signatures"]["bin/devcontainer"]["sha256"] = self.signed["bin/devcontainer"]["sha256"]
        trusted = self.save_record()
        repository = self.root / "repository"
        tools = repository / "Tools/release"
        tools.mkdir(parents=True)
        for name in ("native_signing.py", "create-reproducible-archive.py", "verify-package.py"):
            (tools / name).write_bytes(b"reviewed helper")
        output = self.root / "finalized/package"
        output.parent.mkdir()
        args = argparse.Namespace(repository=repository, state_directory=self.state,
                                  trusted_state_sha256=trusted, scratch_directory=self.root / "ssd",
                                  output_directory=output)

        archiver_paths = []

        class WatchedArchiver:
            @staticmethod
            def create_archive(source, destination, epoch):
                archiver_paths.append((source, destination, epoch))
                Archiver.create_archive(source, destination, epoch)

        def load(path, name):
            return self.signer if path.name == "native_signing.py" else WatchedArchiver

        def verified_package(command, **kwargs):
            archive = Path(command[command.index("--archive") + 1])
            self.assertTrue(archive.is_relative_to(args.scratch_directory))
            verification = Path(command[command.index("--output") + 1])
            self.assertTrue(verification.is_relative_to(args.scratch_directory))
            verification.write_bytes(FINAL.canonical_json(
                {"notarized": True, "sha256": FINAL.digest(archive)}))

        with mock.patch.object(FINAL, "FINAL_ROOT", output.parent), \
                mock.patch.object(FINAL, "load_tool", side_effect=load), \
                mock.patch.object(FINAL.subprocess, "check_output", side_effect=AssertionError("git is forbidden")), \
                mock.patch.object(FINAL.subprocess, "run", side_effect=verified_package):
            proof = FINAL.finalize(args)
        self.assertEqual(len(archiver_paths), 1)
        self.assertTrue(archiver_paths[0][0].is_relative_to(args.scratch_directory))
        self.assertTrue(archiver_paths[0][1].is_relative_to(args.scratch_directory))
        self.assertEqual(archiver_paths[0][2], 1234567890)
        self.assertEqual((self.signer.signature_calls, self.signer.smoke_calls), (1, 1))
        self.assertEqual(proof["scope"], "signed-notarized-package-assembly")
        self.assertIs(proof["distributionReady"], False)
        self.assertEqual(proof["signedTree"], self.signed)
        self.assertEqual(set(proof["finalTree"]), set(self.signed) | {"share/devcontainer/notarization.json"})
        self.assertEqual(FINAL.digest(output / proof["archive"]), proof["archiveSHA256"])

    def test_partial_internal_copy_never_declares_completed_output(self):
        source = self.root / "ssd-outputs"
        source.mkdir()
        (source / "archive.tar.gz").write_bytes(b"archive")
        for name in ("archive.tar.gz.sha256", "package-context.json",
                     "package-verification.json", "native-finalization-provenance.json"):
            (source / name).write_bytes(name.encode())
        expected = {path.name: FINAL.digest(path) for path in source.iterdir()}
        destination = self.root / "internal/completed"
        destination.parent.mkdir()
        with self.assertRaisesRegex(ValueError, "exact five"):
            FINAL.promote_completed_files(source, destination, {"archive.tar.gz": expected["archive.tar.gz"]})
        original = FINAL.shutil.copyfileobj
        calls = 0

        def copy_or_fail(read, write):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("partial-copy diagnostic")
            original(read, write)

        with mock.patch.object(FINAL.shutil, "copyfileobj", side_effect=copy_or_fail):
            with self.assertRaisesRegex(OSError, "partial-copy"):
                FINAL.promote_completed_files(source, destination, expected)
        self.assertFalse(destination.exists())
        self.assertEqual(list(destination.parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
