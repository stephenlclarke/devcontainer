#!/usr/bin/env python3
"""Boundary tests for consuming completed native public package output."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("consume-native-finalized-package.py")
SPEC = importlib.util.spec_from_file_location("consume_native_finalized_package", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

COMMIT = "0123456789abcdef0123456789abcdef01234567"
SUBMISSION = "12345678-1234-1234-1234-123456789012"
SOURCE_REPOSITORY = Path(os.environ.get("DEVCONTAINER_SOURCE_REPOSITORY", Path(__file__).parents[2]))


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dist_snapshot(repository: Path) -> dict[str, tuple[int, int]] | None:
    """Capture public output names and metadata without copying large archives."""
    dist = repository / "dist"
    if not dist.exists():
        return None
    return {path.name: (path.stat().st_size, path.stat().st_mtime_ns) for path in dist.iterdir()}


def isolated_repository(root: Path) -> Path:
    """Copy only maintained package-context and hashed release helpers to a fake repo."""
    repository = root / "fake-repository"
    tools = repository / "Tools/release"
    tools.mkdir(parents=True)
    shutil.copy2(SOURCE_REPOSITORY / "Makefile", repository / "Makefile")
    for name in ("package-context.py", "versioning.py", "native_signing.py",
                 "create-reproducible-archive.py", "verify-package.py"):
        shutil.copy2(SOURCE_REPOSITORY / "Tools/release" / name, tools / name)
    return repository


def fixture(root: Path, *, unsafe_member: bool = False) -> tuple[object, dict[str, bytes], Path]:
    """Create a small exact five-file finalization result for handoff tests."""
    repository = isolated_repository(root)
    version = next(
        fields[2] for line in (repository / "Makefile").read_text().splitlines()
        if len(fields := line.split()) >= 3 and fields[:2] == ["DEVCONTAINER_VERSION", "?="]
    )
    lane = "stable"
    context = MODULE.expected_context(repository, version, lane, COMMIT, "")
    asset = context["asset"]
    directory = root / "retained/devcontainer/finalized/final-package"
    directory.mkdir(parents=True, mode=0o700)
    scratch = root / "ssd-scratch"
    scratch.mkdir()
    acceptance = {"archiveSHA256": "c" * 64, "id": SUBMISSION, "status": "Accepted"}
    contents = {
        "share/devcontainer/Package.resolved": b'{"pins":[]}\n',
        "share/devcontainer/dependency-licenses.selected.json": b'{"selected":[]}\n',
        "share/devcontainer/notarization.json": json_bytes(acceptance),
    }
    archive_path = directory / asset
    with tarfile.open(archive_path, "w:gz") as archive:
        for relative, body in contents.items():
            member = tarfile.TarInfo(f"devcontainer-{version}/{relative}")
            member.size = len(body)
            member.mode = 0o644
            member.uid = member.gid = 0
            member.uname = "root"
            member.gname = "wheel"
            member.mtime = 0
            archive.addfile(member, io.BytesIO(body))
        if unsafe_member:
            member = tarfile.TarInfo(f"devcontainer-{version}/share/devcontainer/../escape")
            member.size = 4
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(b"evil"))
    archive_bytes = archive_path.read_bytes()
    checksum = f"{sha(archive_bytes)}  {asset}\n".encode()
    verification = json_bytes({"archive": asset, "commit": COMMIT, "lane": lane,
                               "notarized": True, "sha256": sha(archive_bytes), "version": version})
    final_tree = {name: {"sha256": sha(body), "size": len(body), "mode": 0o644}
                  for name, body in contents.items()}
    tool_names = ("native_signing.py", "create-reproducible-archive.py", "verify-package.py")
    proof = {
        "schema": 1,
        "scope": "signed-notarized-package-assembly",
        "distributionReady": False,
        "sourceCommit": COMMIT,
        "runtimeProfile": "stock",
        "candidateReceiptSHA256": "a" * 64,
        "stageProvenanceSHA256": "b" * 64,
        "trustedStateSHA256": "d" * 64,
        "acceptedEvidenceSHA256": "e" * 64,
        "submittedZIPSHA256": acceptance["archiveSHA256"],
        "submissionID": SUBMISSION,
        "finalTree": final_tree,
        "archive": asset,
        "archiveSHA256": sha(archive_bytes),
        "archiveSize": len(archive_bytes),
        "checksumSHA256": sha(checksum),
        "packageContextSHA256": sha(json_bytes(context)),
        "packageVerificationSHA256": sha(verification),
        "releaseToolSHA256": {name: MODULE.digest(repository / "Tools/release" / name)
                               for name in tool_names},
    }
    proof_data = json_bytes(proof)
    files = {asset: archive_bytes, asset + ".sha256": checksum,
             "package-context.json": json_bytes(context),
             "package-verification.json": verification,
             MODULE.PROVENANCE_NAME: proof_data}
    for name, body in files.items():
        (directory / name).write_bytes(body)
    os.chmod(directory, 0o700)
    args = type("Args", (), {"repository": repository, "finalized_directory": directory,
                             "ssd_scratch": scratch, "finalization_sha256": sha(proof_data),
                             "lane": lane, "run_number": "", "publish_sha": COMMIT,
                             "profile": "stock"})()
    return args, files, scratch


class NativePublicPackageBoundaryTests(unittest.TestCase):
    def test_current_and_stable_contexts_are_resolved_from_source_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = isolated_repository(Path(temporary).resolve())
            current = MODULE.expected_context(repository, "1.2.3", "current", COMMIT, "417")
            stable = MODULE.expected_context(repository, "1.2.3", "stable", COMMIT, "")
        self.assertEqual(current["asset"], f"devcontainer-current-{COMMIT[:12]}-arm64.tar.gz")
        self.assertEqual(current["lane"], "current")
        self.assertEqual(stable["asset"], "devcontainer-release-arm64.tar.gz")
        self.assertEqual(stable["releaseTag"], "1.2.3")

    def test_context_tampering_is_rejected(self) -> None:
        expected = {"asset": "devcontainer-release-arm64.tar.gz", "commit": COMMIT,
                    "formulaVersion": "1.2.3", "lane": "stable", "productVersion": "1.2.3",
                    "releaseTag": "1.2.3"}
        MODULE.require_context(expected, expected, {"archive": expected["asset"]})
        with self.assertRaisesRegex(ValueError, "context"):
            MODULE.require_context(dict(expected, commit="f" * 40), expected,
                                  {"archive": expected["asset"]})

    def test_root_and_publish_identity_must_match_clean_head(self) -> None:
        MODULE.require_source_identity(COMMIT, "", COMMIT, COMMIT)
        for proof_source, publish_sha, status in (("f" * 40, COMMIT, ""),
                                                   (COMMIT, "f" * 40, ""),
                                                   (COMMIT, COMMIT, " M tracked")):
            with self.assertRaises(ValueError):
                MODULE.require_source_identity(COMMIT, status, proof_source, publish_sha)

    def test_missing_native_input_fails_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            args = type("Args", (), {"repository": root, "finalized_directory": root / "missing",
                                     "ssd_scratch": root, "finalization_sha256": "a" * 64,
                                     "lane": "stable", "run_number": "", "publish_sha": "",
                                     "profile": "stock"})()
            with self.assertRaises((FileNotFoundError, ValueError)):
                MODULE.consume(args)
            self.assertFalse((root / "dist").exists())

    def test_enrolled_ssd_uuid_mismatch_is_rejected(self) -> None:
        record = plistlib.dumps({"MountPoint": str(MODULE.SSD_ROOT), "Internal": False,
                                 "VolumeUUID": "ffffffff-ffff-ffff-ffff-ffffffffffff"})
        with self.assertRaisesRegex(ValueError, "volume identity"):
            MODULE.validate_ssd_identity("884BCCCF-5C0C-4A9B-B412-04FD1C1A6895", record)

    def test_positive_consume_rechecks_and_writes_existing_public_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args, files, _scratch = fixture(Path(temporary).resolve())
            real_run = MODULE.subprocess.run

            def system(command, **kwargs):
                if len(command) > 1 and command[1] in {"rev-parse", "status"}:
                    return subprocess.CompletedProcess(command, 0,
                        stdout=COMMIT if command[1] == "rev-parse" else "")
                if command[1].endswith("verify-package.py"):
                    Path(command[command.index("--output") + 1]).write_bytes(files["package-verification.json"])
                    return subprocess.CompletedProcess(command, 0, stdout="")
                return real_run(command, **kwargs)

            with patch.object(MODULE, "validate_storage") as storage, \
                    patch.object(MODULE.subprocess, "run", side_effect=system):
                output = MODULE.consume(args)
            storage.assert_called_once_with(args.finalized_directory, args.ssd_scratch)
            output_names = {
                output.name: output.name,
                output.name + ".sha256": output.name + ".sha256",
                output.name + ".context.json": "package-context.json",
                output.name + ".verification.json": "package-verification.json",
                output.name + ".native-finalization-provenance.json": MODULE.PROVENANCE_NAME,
            }
            self.assertEqual({name: (output.parent / name).read_bytes()
                              for name in output_names},
                             {name: files[fixture_name]
                              for name, fixture_name in output_names.items()})

    def test_wrong_ssd_fails_before_copy_or_public_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args, _files, _scratch = fixture(Path(temporary).resolve())
            before = dist_snapshot(args.repository)
            with patch.object(MODULE, "validate_storage", side_effect=ValueError("storage mismatch")), \
                    patch.object(MODULE.shutil, "copyfileobj") as copy:
                with self.assertRaisesRegex(ValueError, "storage mismatch"):
                    MODULE.consume(args)
            copy.assert_not_called()
            self.assertEqual(dist_snapshot(args.repository), before)

    def test_bad_finalizer_digest_fails_before_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args, _files, scratch = fixture(Path(temporary).resolve())
            args.finalization_sha256 = "f" * 64
            before = dist_snapshot(args.repository)
            with patch.object(MODULE, "validate_storage"), patch.object(MODULE.shutil, "copyfileobj") as copy:
                with self.assertRaisesRegex(ValueError, "provenance checksum"):
                    MODULE.consume(args)
            copy.assert_not_called()
            self.assertEqual(list(scratch.iterdir()), [])
            self.assertEqual(dist_snapshot(args.repository), before)

    def test_wrong_runtime_profile_fails_before_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args, _files, scratch = fixture(Path(temporary).resolve())
            args.profile = "enhanced"
            before = dist_snapshot(args.repository)
            with patch.object(MODULE, "validate_storage"), \
                    patch.object(MODULE.shutil, "copyfileobj") as copy:
                with self.assertRaisesRegex(ValueError, "runtime profile"):
                    MODULE.consume(args)
            copy.assert_not_called()
            self.assertEqual(list(scratch.iterdir()), [])
            self.assertEqual(dist_snapshot(args.repository), before)

    def test_archive_tamper_fails_against_authenticated_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args, _files, _scratch = fixture(Path(temporary).resolve())
            before = dist_snapshot(args.repository)
            archive = next(path for path in args.finalized_directory.iterdir()
                           if path.name.endswith(".tar.gz"))
            with archive.open("ab") as stream:
                stream.write(b"tamper")
            real_run = MODULE.subprocess.run

            def system(command, **kwargs):
                if len(command) > 1 and command[1] in {"rev-parse", "status"}:
                    return subprocess.CompletedProcess(command, 0,
                        stdout=COMMIT if command[1] == "rev-parse" else "")
                return real_run(command, **kwargs)

            with patch.object(MODULE, "validate_storage"), \
                    patch.object(MODULE.subprocess, "run", side_effect=system):
                with self.assertRaisesRegex(ValueError, "Completed file hashes"):
                    MODULE.consume(args)
            self.assertEqual(dist_snapshot(args.repository), before)

    def test_raw_unsafe_tar_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args, _files, _scratch = fixture(Path(temporary).resolve(), unsafe_member=True)
            before = dist_snapshot(args.repository)
            real_run = MODULE.subprocess.run

            def system(command, **kwargs):
                if len(command) > 1 and command[1] in {"rev-parse", "status"}:
                    return subprocess.CompletedProcess(command, 0,
                        stdout=COMMIT if command[1] == "rev-parse" else "")
                return real_run(command, **kwargs)

            with patch.object(MODULE, "validate_storage"), \
                    patch.object(MODULE.subprocess, "run", side_effect=system):
                with self.assertRaisesRegex(ValueError, "unsafe entry"):
                    MODULE.consume(args)
            self.assertEqual(dist_snapshot(args.repository), before)


if __name__ == "__main__":
    unittest.main()
