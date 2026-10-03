"""Portable signing-state tests; all signing, Apple transport and smoke tools are mocked."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

import native_signing as signing

TOOLS = Path(__file__).resolve().parent
SUBMISSION_ID = "01234567-89ab-cdef-0123-456789abcdef"
TEAM = "012345ABCD"
ENV = {"DEVCONTAINER_SIGNING_IDENTITY": "Developer ID Application: Fixture (012345ABCD)",
       "DEVCONTAINER_SIGNING_TEAM_ID": TEAM, "DEVCONTAINER_NOTARY_PROFILE": "fixture-profile"}


class Fixture:
    def __init__(self, root):
        self.root = root
        self.stage = root / "stage/devcontainer-1.2.3"
        self.state = root / "internal/notary"
        self.scratch = root / "scratch"
        self.scratch.mkdir()
        self.state.parent.mkdir()
        for relative in signing.BINARIES:
            path = self.stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(struct.pack("<IIII", 0xFEEDFACF, 0x0100000C, 0, 2) + relative.encode())
            path.chmod(0o755)
        (self.stage / signing.PLUGIN).write_bytes((self.stage / "bin/devcontainer").read_bytes())
        reference = self.stage / signing.REFERENCE
        for relative in signing.REFERENCE_FILES - {"node"}:
            path = reference / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(relative)
        (reference / "runtime-lock.json").write_text(json.dumps({"node": {"version": "24.21.0"}, "cli": {"version": "0.88.0"}}))
        (reference / "cli/package.json").write_text(json.dumps({"version": "0.88.0"}))
        shared = self.stage / "share/devcontainer"
        shared.mkdir(parents=True)
        for name in ("THIRD-PARTY-NOTICES.txt", "devcontainer.spdx.json", "build-info.json", "README.md", "LICENSE", "dependency-licenses.selected.json"):
            (shared / name).write_text(name + " authenticated metadata\n")
        (shared / "Package.resolved").write_text('{"pins": []}\n')
        self.receipt = {"schemaVersion": 2, "kind": "unsigned-native-candidate", "version": "1.2.3",
                        "commit": "a" * 40, "runtimeProfile": "stock", "architecture": "arm64",
                        "compilationMode": "opt", "distributionReady": False,
                        "dependencyLockSHA256": signing.digest(shared / "Package.resolved"),
                        "products": {name: signing.digest(self.stage / "bin" / name) for name in signing.PRODUCTS},
                        "referenceRuntime": {"nodeVersion": "24.21.0", "cliVersion": "0.88.0",
                                             "lockSHA256": signing.digest(reference / "runtime-lock.json"),
                                             "files": {name: signing.digest(reference / name) for name in signing.REFERENCE_FILES}}}
        (shared / "candidate.json").write_text(json.dumps(self.receipt))
        self.receipt_path = root / "candidate_archive.json"
        self.receipt_path.write_text(json.dumps({**self.receipt, "archiveSHA256": "f" * 64, "archiveSize": 100}))
        self.provenance_path = root / "native-stage-provenance.json"
        self.provenance = {
            "schema": 1, "scope": "unsigned-native-package-stage", "sourceCommit": self.receipt["commit"],
            "profile": self.receipt["runtimeProfile"], "candidateAssetSHA256": "f" * 64,
            "candidateProducts": self.receipt["products"], "selectedLockSHA256": self.receipt["dependencyLockSHA256"],
            "privateRuntime": self.receipt["referenceRuntime"], "legalCompleteness": True,
            "distributionReady": False, "signingComplete": False, "notarizationComplete": False,
            "unsignedPayloadInventory": signing.inventory(self.stage, root / "notarization.json"),
        }
        self.provenance_path.write_text(json.dumps(self.provenance))
        self.args = argparse.Namespace(stage=self.stage, evidence=root / "notarization.json",
                                       candidate_receipt=self.receipt_path,
                                       stage_provenance=self.provenance_path,
                                       stage_provenance_sha256=signing.digest(self.provenance_path),
                                       candidate_sha256=signing.digest(self.receipt_path),
                                       state_directory=self.state, scratch_directory=self.scratch,
                                       timeout_seconds=1, resume=False)
        self.calls = []
        self.entitlements = {}
        self.bad_signature = None
        self.bad_entitlements = False
        self.info_status = "Accepted"
        self.info_id = SUBMISSION_ID
        self.submit_interruption = None
        self.node_version = "v24.21.0\n"

    def tool(self, command, state, label, _timeout, *, env=None):
        self.calls.append(command)
        out, err = b"", b""
        if command[0] == "/usr/bin/codesign":
            path = Path(command[-1])
            relative = next(name for name in sorted(signing.BINARIES, key=len, reverse=True) if path.as_posix().endswith("/" + name))
            if "--force" in command:
                assert signing.digest(state / "stage-provenance.json") == self.args.stage_provenance_sha256
                assert json.loads((state / "state.json").read_bytes())["stageProvenanceSHA256"] == self.args.stage_provenance_sha256
                path = Path(command[-1]); path.write_bytes(path.read_bytes() + b"-signed")
                self.entitlements[relative] = plistlib.loads(Path(command[command.index("--entitlements") + 1]).read_bytes()) if "--entitlements" in command else {}
            elif "--verbose=4" in command:
                lines = ["Authority=Developer ID Application: Fixture (012345ABCD)",
                         "Authority=Developer ID Certification Authority", "Authority=Apple Root CA",
                         "TeamIdentifier=" + TEAM, "Timestamp=1 Oct 2026", "CodeDirectory flags=0x10000(runtime)"]
                if self.bad_signature == "team": lines[3] = "TeamIdentifier=XXXXXXXXXX"
                if self.bad_signature == "timestamp": lines.pop(4)
                if self.bad_signature == "runtime": lines[-1] = "CodeDirectory flags=0x0(none)"
                err = "\n".join(lines).encode()
            elif "--entitlements" in command:
                value = self.entitlements[relative]
                if self.bad_entitlements and relative == signing.NODE:
                    value = {**value, "com.apple.security.cs.disable-library-validation": True}
                out = plistlib.dumps(value)
        elif command[0] == "/usr/bin/ditto":
            with zipfile.ZipFile(command[-1], "w") as archive:
                for path in self.stage.rglob("*"):
                    if path.is_file(): archive.write(path, self.stage.name + "/" + path.relative_to(self.stage).as_posix())
        elif command[:3] == ["/usr/bin/xcrun", "notarytool", "submit"]:
            record = json.loads((state / "state.json").read_bytes())
            assert record["phase"] == "submit-intent"
            assert Path(command[3]) == state / "submitted.zip"
            assert signing.digest(Path(command[3])) == record["archiveSHA256"]
            out = b"" if self.submit_interruption == "unknown" else json.dumps({"id": SUBMISSION_ID}).encode()
        elif command[:3] == ["/usr/bin/xcrun", "notarytool", "info"]:
            assert command[3] == SUBMISSION_ID
            out = json.dumps({"id": self.info_id, "status": self.info_status, "private": "not-public"}).encode()
        elif command[0].endswith("/" + signing.NODE):
            assert "NODE_OPTIONS" not in env
            assert "DYLD_INSERT_LIBRARIES" not in env
            out = self.node_version.encode() if len(command) == 2 else b"0.88.0\n"
        elif command[0].endswith("/bin/devcontainer"):
            assert command[1:] == ["version", "--format", "json"]
            out = json.dumps({"version": self.receipt["version"], "commit": self.receipt["commit"],
                              "architecture": "arm64", "lane": "candidate", "buildType": "release"}).encode()
        else:
            raise AssertionError(command)
        (state / (label + ".stdout")).write_bytes(out)
        (state / (label + ".stderr")).write_bytes(err)
        if label == "submit" and self.submit_interruption:
            raise InterruptedError("mock interruption")
        return 0, out, err

    def run(self):
        with mock.patch.dict(os.environ, ENV), mock.patch.object(signing, "validate_storage"), mock.patch.object(signing, "tool", self.tool):
            return signing.perform(self.args)


class SigningToolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixture = Fixture(Path(self.temporary.name).resolve())

    def test_six_paths_exact_entitlements_readback_smoke_and_durable_zip(self):
        f = self.fixture; result = f.run()
        signs = [cmd for cmd in f.calls if cmd[0] == "/usr/bin/codesign" and "--force" in cmd]
        self.assertEqual({str(Path(cmd[-1]).relative_to(f.stage)) for cmd in signs}, set(signing.BINARIES))
        self.assertEqual(len(signs), 6)
        self.assertEqual([str(Path(cmd[-1]).relative_to(f.stage)) for cmd in signs if "--entitlements" in cmd], [signing.NODE])
        self.assertEqual(f.entitlements[signing.NODE], signing.JIT)
        self.assertEqual(len([cmd for cmd in f.calls if cmd[0] == "/usr/bin/codesign" and "--verify" in cmd]), 6)
        self.assertEqual(len([cmd for cmd in f.calls if cmd[0] == str(f.stage / signing.NODE)]), 2)
        self.assertEqual(result["phase"], "accepted")
        self.assertEqual(len(result["signatures"]), 6)
        self.assertNotEqual(result["unsignedTree"][signing.NODE]["sha256"], result["signedTree"][signing.NODE]["sha256"])
        value = json.loads(f.args.evidence.read_bytes())
        self.assertEqual(value, {"id": SUBMISSION_ID, "status": "Accepted", "archiveSHA256": signing.digest(f.state / "submitted.zip")})
        self.assertEqual(list(f.scratch.iterdir()), [])
        self.assertNotIn("fixture-profile", f.args.evidence.read_text())
        self.assertNotIn("private", value)

    def test_same_candidate_authorized_lane_variants_have_isolated_default_transactions(self):
        current = self.fixture
        stable_root = current.root / "stable"
        stable_root.mkdir()
        stable = Fixture(stable_root)
        home = current.root / "operator"
        parent = home / "Library/Application Support/ContainerFamily/retained/devcontainer/notary"
        parent.mkdir(parents=True)
        for lane, fixture in (("current", current), ("stable", stable)):
            context = {"lane": lane, "version": fixture.receipt["version"]}
            (fixture.stage / "share/devcontainer/build-info.json").write_text(json.dumps(context))
            fixture.provenance.update(lane=lane, packageContext=context,
                                      unsignedPayloadInventory=signing.inventory(fixture.stage, fixture.args.evidence))
            fixture.provenance_path.write_text(json.dumps(fixture.provenance))
            fixture.args.stage_provenance_sha256 = signing.digest(fixture.provenance_path)
            fixture.args.state_directory = None
            fixture.state = parent / (fixture.args.candidate_sha256 + "-" + fixture.args.stage_provenance_sha256)
        self.assertEqual(current.args.candidate_sha256, stable.args.candidate_sha256)
        self.assertNotEqual(current.args.stage_provenance_sha256, stable.args.stage_provenance_sha256)
        with mock.patch.object(signing, "account_home", return_value=home):
            self.assertEqual(current.run()["phase"], "accepted")
            current_record = (current.state / "state.json").read_bytes()
            self.assertEqual(stable.run()["phase"], "accepted")
            self.assertEqual((current.state / "state.json").read_bytes(), current_record)
            self.assertEqual(set(parent.iterdir()), {current.state, stable.state})
            self.assertNotEqual(signing.digest(current.state / "submitted.zip"), signing.digest(stable.state / "submitted.zip"))
            for fixture, other in ((current, stable), (stable, current)):
                before_calls = len(fixture.calls)
                record = (other.state / "state.json").read_bytes()
                archive = signing.digest(other.state / "submitted.zip")
                # Keep all other resume identities equal; only provenance must refuse the cross-lane resume.
                with mock.patch.multiple(fixture.args, resume=True, state_directory=other.state,
                                         stage=other.args.stage, evidence=other.args.evidence):
                    with self.assertRaisesRegex(ValueError, "Resume identity"):
                        fixture.run()
                self.assertEqual(len(fixture.calls), before_calls)
                self.assertEqual((other.state / "state.json").read_bytes(), record)
                self.assertEqual(signing.digest(other.state / "submitted.zip"), archive)
                with self.assertRaises(FileExistsError):
                    fixture.run()
                self.assertEqual(len(fixture.calls), before_calls)

    def test_missing_sixth_executable_fails_before_signing(self):
        (self.fixture.stage / "bin/devcontainer-docker").unlink()
        with self.assertRaises(ValueError): self.fixture.run()
        self.assertEqual(self.fixture.calls, [])

    def test_wrong_receipt_fails_before_signing(self):
        f = self.fixture
        f.args.candidate_sha256 = "0" * 64
        with self.assertRaisesRegex(ValueError, "checksum"): f.run()
        self.assertEqual(f.calls, [])

    def test_plugin_substitution_fails_before_signing(self):
        (self.fixture.stage / signing.PLUGIN).write_bytes(b"not the authenticated CLI")
        with self.assertRaises(ValueError): self.fixture.run()
        self.assertEqual(self.fixture.calls, [])

    def test_symlink_fails_closed(self):
        f = self.fixture
        (f.stage / "bin/extra").symlink_to(f.stage / "bin/devcontainer")
        with self.assertRaises(ValueError): f.run()
        self.assertEqual(f.calls, [])

    def test_unlisted_macho_fails_before_signing(self):
        f = self.fixture
        (f.stage / "bin/extra").write_bytes((f.stage / "bin/devcontainer").read_bytes())
        with self.assertRaisesRegex(ValueError, "six declared"):
            f.run()
        self.assertEqual(f.calls, [])

    def test_modified_reference_script_fails_before_signing(self):
        f = self.fixture
        (f.stage / signing.REFERENCE / "cli/devcontainer.js").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "runtime bytes"):
            f.run()
        self.assertEqual(f.calls, [])

    def test_non_macho_node_cannot_be_signed(self):
        f = self.fixture
        (f.stage / signing.NODE).write_bytes(b"#!/bin/sh\nexit 0\n")
        with self.assertRaises(ValueError):
            f.run()
        self.assertEqual(f.calls, [])

    def test_unsigned_metadata_tamper_rejects_before_signing(self):
        for name in ("THIRD-PARTY-NOTICES.txt", "devcontainer.spdx.json", "build-info.json", "README.md", "LICENSE"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory).resolve())
                (f.stage / "share/devcontainer" / name).write_text("substituted metadata")
                with self.assertRaisesRegex(ValueError, "metadata or payload differs"):
                    f.run()
                self.assertEqual(f.calls, [])
                self.assertFalse(f.args.evidence.exists())

    def test_missing_or_extra_unsigned_metadata_and_mode_drift_reject(self):
        for mutation in ("missing", "extra", "mode"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory).resolve())
                path = f.stage / "share/devcontainer/README.md"
                if mutation == "missing": path.unlink()
                if mutation == "extra": (path.parent / "unadmitted.json").write_text("extra")
                if mutation == "mode": path.chmod(0o755)
                with self.assertRaisesRegex(ValueError, "metadata or payload differs"):
                    f.run()
                self.assertEqual(f.calls, [])

    def test_wrong_or_missing_stage_provenance_authority_rejects(self):
        for mutation in ("hash", "path", "bytes"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory).resolve())
                if mutation == "hash": f.args.stage_provenance_sha256 = "0" * 64
                if mutation == "path": f.args.stage_provenance = None
                if mutation == "bytes": f.provenance_path.write_text("changed provenance")
                with self.assertRaises(ValueError): f.run()
                self.assertEqual(f.calls, [])

    def test_provenance_wrong_source_profile_candidate_or_completion_flags_reject(self):
        for key, value in (("sourceCommit", "b" * 40), ("profile", "enhanced"),
                           ("candidateAssetSHA256", "e" * 64), ("candidateProducts", {}),
                           ("selectedLockSHA256", "e" * 64), ("privateRuntime", {}),
                           ("legalCompleteness", False), ("distributionReady", True),
                           ("signingComplete", True), ("notarizationComplete", True)):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory).resolve())
                f.provenance[key] = value
                f.provenance_path.write_text(json.dumps(f.provenance))
                f.args.stage_provenance_sha256 = signing.digest(f.provenance_path)
                with self.assertRaisesRegex(ValueError, "legal authority differs"):
                    f.run()
                self.assertEqual(f.calls, [])

    def test_resume_requires_same_retained_provenance_and_trusted_digest(self):
        for mutation in ("argument", "retained", "unsigned-tree", "signed-metadata"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory).resolve()); f.submit_interruption = "returned-id"
                with self.assertRaises(InterruptedError): f.run()
                f.args.resume = True; f.submit_interruption = None
                before = len(f.calls)
                if mutation == "argument": f.args.stage_provenance_sha256 = "0" * 64
                if mutation == "retained": (f.state / "stage-provenance.json").write_text("tampered")
                if mutation in {"unsigned-tree", "signed-metadata"}:
                    path = f.state / "state.json"; record = json.loads(path.read_bytes())
                    key = "unsignedTree" if mutation == "unsigned-tree" else "signedTree"
                    record[key]["share/devcontainer/README.md"]["sha256"] = "0" * 64
                    path.write_text(json.dumps(record))
                with self.assertRaises(ValueError): f.run()
                self.assertEqual(len(f.calls), before)
                self.assertEqual(len([cmd for cmd in f.calls if cmd[:3] == ["/usr/bin/xcrun", "notarytool", "submit"]]), 1)

    def test_bad_signature_team_timestamp_or_runtime_cannot_submit(self):
        for problem in ("team", "timestamp", "runtime"):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory).resolve()); f.bad_signature = problem
                with self.assertRaises(ValueError): f.run()
                self.assertFalse(any(cmd[0] == "/usr/bin/xcrun" for cmd in f.calls))
                self.assertFalse(f.args.evidence.exists())

    def test_extra_node_entitlement_cannot_submit(self):
        f = self.fixture; f.bad_entitlements = True
        with self.assertRaisesRegex(ValueError, "entitlements"): f.run()
        self.assertFalse(any(cmd[0] == "/usr/bin/xcrun" for cmd in f.calls))

    def test_wrong_signed_node_version_cannot_submit(self):
        f = self.fixture; f.node_version = "v99.0.0\n"
        with self.assertRaisesRegex(ValueError, "Node version"): f.run()
        self.assertFalse(any(cmd[0] == "/usr/bin/xcrun" for cmd in f.calls))

    def test_rejection_retains_zip_status_and_creates_no_acceptance(self):
        f = self.fixture; f.info_status = "Invalid"
        with self.assertRaisesRegex(ValueError, "rejected"): f.run()
        self.assertFalse(f.args.evidence.exists())
        self.assertTrue((f.state / "submitted.zip").is_file())
        self.assertEqual(json.loads((f.state / "state.json").read_bytes())["phase"], "rejected")
        self.assertTrue(list(f.state.glob("failure-*.json")))
        f.args.resume = True
        with self.assertRaises(ValueError): f.run()
        self.assertEqual(len([cmd for cmd in f.calls if cmd[:3] == ["/usr/bin/xcrun", "notarytool", "submit"]]), 1)

    def test_interrupted_submit_with_id_resumes_info_only(self):
        f = self.fixture; f.submit_interruption = "returned-id"
        with self.assertRaises(InterruptedError): f.run()
        original = signing.digest(f.state / "submitted.zip")
        self.assertFalse(f.args.evidence.exists())
        f.args.resume = True; f.submit_interruption = None
        self.assertEqual(f.run()["phase"], "accepted")
        self.assertEqual(signing.digest(f.state / "submitted.zip"), original)
        self.assertEqual(len([cmd for cmd in f.calls if cmd[:3] == ["/usr/bin/xcrun", "notarytool", "submit"]]), 1)
        self.assertEqual(len([cmd for cmd in f.calls if cmd[0] == "/usr/bin/codesign" and "--force" in cmd]), 6)

    def test_resume_survives_loss_of_original_stage_and_receipt(self):
        f = self.fixture; f.submit_interruption = "returned-id"
        with self.assertRaises(InterruptedError): f.run()
        shutil.rmtree(f.stage)
        f.receipt_path.unlink()
        f.provenance_path.unlink()
        f.args.stage_provenance = None
        f.args.resume = True; f.args.candidate_receipt = None; f.submit_interruption = None
        self.assertEqual(f.run()["phase"], "accepted")
        self.assertTrue(f.args.evidence.is_file())
        self.assertTrue((f.state / "submitted.zip").is_file())
        self.assertFalse(f.stage.exists())
        self.assertEqual(list(f.scratch.iterdir()), [])
        self.assertEqual(len([cmd for cmd in f.calls if cmd[:3] == ["/usr/bin/xcrun", "notarytool", "submit"]]), 1)

    def test_unknown_submission_never_resubmits(self):
        f = self.fixture; f.submit_interruption = "unknown"
        f.args.state_directory = None
        f.state = f.root / "Library/Application Support/ContainerFamily/retained/devcontainer/notary" / (
            f.args.candidate_sha256 + "-" + f.args.stage_provenance_sha256)
        f.state.parent.mkdir(parents=True)
        with mock.patch.object(signing, "account_home", return_value=f.root):
            with self.assertRaises(InterruptedError): f.run()
            original = signing.digest(f.state / "submitted.zip")
            with self.assertRaises(FileExistsError): f.run()
            f.args.resume = True
            with self.assertRaisesRegex(ValueError, "unknown"): f.run()
        self.assertEqual(signing.digest(f.state / "submitted.zip"), original)
        self.assertEqual(len([cmd for cmd in f.calls if cmd[:3] == ["/usr/bin/xcrun", "notarytool", "submit"]]), 1)
        self.assertFalse(f.args.evidence.exists())

    def test_pending_can_resume_and_wrong_info_id_is_rejected(self):
        f = self.fixture; f.info_status = "In Progress"
        with mock.patch.object(signing.time, "monotonic", side_effect=[0, 2]):
            with self.assertRaisesRegex(ValueError, "pending"): f.run()
        f.args.resume = True; f.info_status = "Accepted"; f.info_id = "99999999-89ab-cdef-0123-456789abcdef"
        with self.assertRaisesRegex(ValueError, "identity"): f.run()
        self.assertFalse(f.args.evidence.exists())
        f.info_id = SUBMISSION_ID
        self.assertEqual(f.run()["phase"], "accepted")
        self.assertEqual(len([cmd for cmd in f.calls if cmd[:3] == ["/usr/bin/xcrun", "notarytool", "submit"]]), 1)

    def test_stage_drift_on_resume_is_rejected_before_transport(self):
        f = self.fixture; f.submit_interruption = "returned-id"
        with self.assertRaises(InterruptedError): f.run()
        (f.stage / signing.NODE).write_bytes(b"changed")
        f.args.resume = True; before = len(f.calls)
        with self.assertRaisesRegex(ValueError, "stage changed"): f.run()
        self.assertEqual(len(f.calls), before)

    def test_retained_archive_drift_on_resume_rejects_without_info(self):
        f = self.fixture; f.submit_interruption = "returned-id"
        with self.assertRaises(InterruptedError): f.run()
        (f.state / "submitted.zip").write_bytes(b"tampered")
        f.args.resume = True
        with self.assertRaisesRegex(ValueError, "ZIP changed"): f.run()
        self.assertFalse(any(cmd[:3] == ["/usr/bin/xcrun", "notarytool", "info"] for cmd in f.calls))

    def test_repeat_without_resume_preserves_existing_state(self):
        f = self.fixture; f.run(); original = (f.state / "state.json").read_bytes(); count = len(f.calls)
        with self.assertRaises(FileExistsError): f.run()
        self.assertEqual((f.state / "state.json").read_bytes(), original)
        self.assertEqual(len(f.calls), count)

    def test_ssd_or_external_durable_state_is_rejected_by_production_policy(self):
        f = self.fixture
        with self.assertRaisesRegex(ValueError, "internal retained"):
            signing.validate_storage(f.stage, f.state, f.scratch, f.args.evidence)

    def test_missing_credentials_fail_before_signing(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "Signing identity"):
                signing.perform(self.fixture.args)
        self.assertEqual(self.fixture.calls, [])

    def test_existing_sanitizer_refuses_nonaccepted_notary_document(self):
        spec = importlib.util.spec_from_file_location("notary", TOOLS / "write-notarization-evidence.py")
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with self.assertRaises(ValueError):
            module.sanitized_evidence({"id": SUBMISSION_ID, "status": "Invalid"}, "a" * 64)

    def test_owned_tool_launch_preserves_handle_before_unmask(self):
        process = mock.Mock(pid=12345)
        process.wait.return_value = 0
        with mock.patch.object(signing.subprocess, "Popen", return_value=process) as spawn, mock.patch.object(signing, "session_members", return_value=set()):
            result = signing.run_owned(["fixture"], cwd=self.fixture.root, env={}, stdout=None, stderr=None, timeout=1)
        self.assertEqual(result, 0)
        self.assertTrue(spawn.call_args.kwargs["start_new_session"])
        self.assertEqual(spawn.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_owned_timeout_uses_exact_session_cleanup(self):
        process = mock.Mock(pid=12345)
        process.wait.side_effect = subprocess.TimeoutExpired("fixture", 1)
        with mock.patch.object(signing.subprocess, "Popen", return_value=process), mock.patch.object(signing, "terminate_session") as terminate:
            with self.assertRaises(subprocess.TimeoutExpired):
                signing.run_owned(["fixture"], cwd=self.fixture.root, env={}, stdout=None, stderr=None, timeout=1)
        terminate.assert_called_once_with(process)

    def test_signal_on_spawn_unmask_still_cleans_owned_handle(self):
        process = mock.Mock(pid=12345)
        with mock.patch.object(signing.subprocess, "Popen", return_value=process), mock.patch.object(signing.signal, "pthread_sigmask", side_effect=[set(), InterruptedError()]), mock.patch.object(signing, "terminate_session") as terminate:
            with self.assertRaises(InterruptedError):
                signing.run_owned(["fixture"], cwd=self.fixture.root, env={}, stdout=None, stderr=None, timeout=1)
        terminate.assert_called_once_with(process)

    def test_shell_help_does_not_need_credentials(self):
        result = subprocess.run([str(TOOLS / "sign-and-notarize.sh"), "--help"], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--resume", result.stdout)


if __name__ == "__main__":
    unittest.main()
