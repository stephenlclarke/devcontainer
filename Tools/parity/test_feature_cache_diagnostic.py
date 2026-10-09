# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from feature_cache_diagnostic import (
    DiagnosticError,
    WARMUP_TIMEOUT_SECONDS,
    _cache_proof,
    prepare_d05_feature_cache,
    verify_d05_feature_cache,
)


IDENTITY = {
    "scope": "finalized-native-package-runtime-input",
    "runtimeProfile": "stock",
    "sourceCommit": "a" * 40,
    "archiveSHA256": "b" * 64,
    "candidateReceiptSHA256": "c" * 64,
}


def make_fixture(root: Path) -> Path:
    (root / ".devcontainer").mkdir(parents=True)
    files = {
        "contract.json": b"{}\n",
        ".devcontainer/devcontainer.json": b'{"image":"example"}\n',
        ".devcontainer/devcontainer-lock.json": b"{}\n",
        "probe.sh": b"#!/bin/sh\nexit 0\n",
    }
    for relative, content in files.items():
        (root / relative).write_bytes(content)
    return root


def cached_progress() -> str:
    return (
        "#41 [dev_containers_target_stage 5/7] RUN echo common-utils_0 && apt-get install\n"
        "#41 CACHED\n"
        "#42 [dev_containers_target_stage 6/7] RUN echo git_1 && apt-get install\n"
        "#42 CACHED\n"
    )


class FeatureCacheDiagnosticTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = make_fixture(self.root / "fixture")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_prepare_uses_frozen_lockfile_and_retains_raw_output(self) -> None:
        evidence = self.root / "warmup"
        captured = {}

        def invoke(argv, timeout):
            captured["argv"] = list(argv)
            captured["timeout"] = timeout
            return SimpleNamespace(returncode=0, stdout=b"build raw\n", stderr=b"")

        receipt = prepare_d05_feature_cache(
            lane="apple-stock", fixture_id="D05-features", workspace=self.workspace,
            backend_arguments=["--docker-path", "/candidate/docker"],
            candidate_identity=IDENTITY, evidence_dir=evidence, invoke=invoke)
        self.assertEqual(captured["timeout"], WARMUP_TIMEOUT_SECONDS)
        self.assertIn("--frozen-lockfile", captured["argv"])
        self.assertEqual(captured["argv"][0], "build")
        self.assertEqual(captured["argv"][captured["argv"].index("--workspace-folder") + 1],
                         str(self.workspace.resolve()))
        self.assertEqual((evidence / "warmup.stdout").read_bytes(), b"build raw\n")
        self.assertEqual(receipt["stdoutSHA256"], hashlib.sha256(b"build raw\n").hexdigest())
        self.assertEqual((evidence.stat().st_mode & 0o777), 0o700)
        self.assertEqual((evidence / "warmup.stdout").stat().st_mode & 0o777, 0o600)

    def test_timeout_and_failed_build_preserve_partial_raw_outputs(self) -> None:
        evidence = self.root / "timeout"

        def invoke(_argv, timeout):
            self.assertEqual(timeout, 1800)
            error = TimeoutError("deadline")
            error.stdout = b"partial stdout"
            error.stderr = b"partial stderr"
            raise error

        receipt = prepare_d05_feature_cache(
            lane="container-compose", fixture_id="D05-features", workspace=self.workspace,
            backend_arguments=[], candidate_identity=IDENTITY,
            evidence_dir=evidence, invoke=invoke)
        self.assertEqual(receipt["status"], "not_comparable")
        self.assertTrue(receipt["timedOut"])
        self.assertEqual((evidence / "warmup.stdout").read_bytes(), b"partial stdout")
        self.assertEqual((evidence / "warmup.stderr").read_bytes(), b"partial stderr")

    def test_rejects_unexpected_fixture_before_invoking_cli(self) -> None:
        (self.workspace / "extra.txt").write_text("extra")
        called = False

        def invoke(_argv, _timeout):
            nonlocal called
            called = True
            raise AssertionError("must not invoke CLI")

        with self.assertRaisesRegex(DiagnosticError, "exact D05"):
            prepare_d05_feature_cache(
                lane="apple-stock", fixture_id="D05-features", workspace=self.workspace,
                backend_arguments=[], candidate_identity=IDENTITY,
                evidence_dir=self.root / "rejected", invoke=invoke)
        self.assertFalse(called)

    def test_cache_proof_requires_both_named_stages_and_no_install(self) -> None:
        self.assertEqual(_cache_proof(cached_progress().encode(), b"")[0], True)
        missing = cached_progress().replace("#42 CACHED", "#42 DONE")
        self.assertIn("not proven cached", _cache_proof(missing.encode(), b"")[1])
        absent = cached_progress().replace("git_1", "other-feature")
        self.assertIn("missing or ambiguous", _cache_proof(absent.encode(), b"")[1])
        install = cached_progress() + "\napt-get install curl"
        self.assertIn("apt package installation", _cache_proof(install.encode(), b"")[1])

    def test_json_events_are_supported_but_unknown_shapes_fail_closed(self) -> None:
        events = "\n".join(json.dumps({"type": "raw", "text": line})
                           for line in cached_progress().splitlines())
        self.assertTrue(_cache_proof(events.encode(), b"")[0])
        unknown = b'{"type":"progress","message":"looks cached"}\n'
        self.assertIn("unrecognized JSON", _cache_proof(unknown, b"")[1])

    def test_verification_binds_same_candidate_and_retains_up_evidence(self) -> None:
        warmup = {
            "schema": "d05-feature-cache-warmup-v1",
            "lane": "apple-stock", "fixture": "D05-features",
            "candidate": {
                "scope": IDENTITY["scope"], "sourceCommit": IDENTITY["sourceCommit"],
                "packageSHA256": IDENTITY["archiveSHA256"],
                "admissionReceiptSHA256": IDENTITY["candidateReceiptSHA256"],
            },
            "fixtureInput": {
                "files": {
                    path.relative_to(self.workspace).as_posix(): {
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "mode": path.stat().st_mode & 0o777,
                    }
                    for path in self.workspace.rglob("*") if path.is_file()
                },
            },
            "status": "warmup_completed_unverified", "comparisonEligible": True,
        }
        # inventory digest is intentionally sourced from an authentic prepare receipt.
        prepare_dir = self.root / "first-warmup"
        receipt = prepare_d05_feature_cache(
            lane="apple-stock", fixture_id="D05-features", workspace=self.workspace,
            backend_arguments=[], candidate_identity=IDENTITY, evidence_dir=prepare_dir,
            invoke=lambda _argv, _timeout: SimpleNamespace(returncode=0, stdout=b"", stderr=b""))
        warmup.update(receipt)
        evidence = self.root / "verify"
        result = verify_d05_feature_cache(
            lane="apple-stock", fixture_id="D05-features", workspace=self.workspace,
            candidate_identity=IDENTITY, warmup_receipt=warmup,
            functional_started_monotonic_ns=time.monotonic_ns(),
            up_stdout=cached_progress(), up_stderr="", evidence_dir=evidence)
        self.assertTrue(result["performanceComparisonEligible"])
        self.assertEqual((evidence / "functional-up.stdout").read_text(), cached_progress())
        self.assertTrue((evidence / "cache-verification.json").is_file())
        early = verify_d05_feature_cache(
            lane="apple-stock", fixture_id="D05-features", workspace=self.workspace,
            candidate_identity=IDENTITY, warmup_receipt=warmup,
            functional_started_monotonic_ns=warmup["finishedMonotonicNS"] - 1,
            up_stdout=cached_progress(), up_stderr="",
            evidence_dir=self.root / "verify-early")
        self.assertFalse(early["performanceComparisonEligible"])
        self.assertIn("before the functional timing interval", early["reason"])

    def test_candidate_mismatch_is_not_comparable_and_does_not_overwrite(self) -> None:
        receipt = {
            "schema": "d05-feature-cache-warmup-v1", "lane": "apple-stock",
            "fixture": "D05-features", "candidate": {}, "fixtureInput": {},
            "status": "warmup_completed_unverified", "comparisonEligible": True,
        }
        evidence = self.root / "verify-mismatch"
        result = verify_d05_feature_cache(
            lane="apple-stock", fixture_id="D05-features", workspace=self.workspace,
            candidate_identity=IDENTITY, warmup_receipt=receipt,
            functional_started_monotonic_ns=time.monotonic_ns(),
            up_stdout="secret diagnostic raw", up_stderr="", evidence_dir=evidence)
        self.assertFalse(result["performanceComparisonEligible"])
        self.assertEqual(result["status"], "not_comparable")
        with self.assertRaises(FileExistsError):
            verify_d05_feature_cache(
                lane="apple-stock", fixture_id="D05-features", workspace=self.workspace,
                candidate_identity=IDENTITY, warmup_receipt=receipt,
                functional_started_monotonic_ns=time.monotonic_ns(),
                up_stdout="again", up_stderr="", evidence_dir=evidence)

    def test_rejects_docker_lane_and_unadmitted_package(self) -> None:
        with self.assertRaisesRegex(DiagnosticError, "native lanes"):
            prepare_d05_feature_cache(
                lane="docker", fixture_id="D05-features", workspace=self.workspace,
                backend_arguments=[], candidate_identity=IDENTITY,
                evidence_dir=self.root / "docker", invoke=lambda *_: None)
        invalid = dict(IDENTITY, scope="arbitrary")
        with self.assertRaisesRegex(DiagnosticError, "admitted native"):
            prepare_d05_feature_cache(
                lane="apple-stock", fixture_id="D05-features", workspace=self.workspace,
                backend_arguments=[], candidate_identity=invalid,
                evidence_dir=self.root / "identity", invoke=lambda *_: None)


if __name__ == "__main__":
    unittest.main()
