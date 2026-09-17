"""Recovery and false-green regression tests for the replacement runtime harness."""

import copy
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from case_evidence import CaseStore, LANES, canonical, compare_cases, digest, run_case, validate_identity, validate_result


def identity(lane="docker"):
    return {"campaign": "campaign-1", "fixture": "E01-engine-negotiation", "lane": lane,
            "contractSHA256": digest(canonical({"ping": "true"})), "harnessSHA256": "b" * 64,
            "releaseSetSHA256": "c" * 64, "runtimeSHA256": "d" * 64}


def result():
    return {"status": "passed", "observations": {"ping": "true"}, "errors": [],
            "durationsNS": {"setup": 10, "operation": 20, "cleanup": 30},
            "cleanup": {"status": "passed", "remainingOwnedResources": []}}


class CaseEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.scratch.cleanup)
        self.path = Path(self.scratch.name) / "cases.sqlite"
        self.store = CaseStore(self.path)

    def test_completed_case_survives_restart_and_seal_is_idempotent(self):
        self.assertIsNone(self.store.begin(identity()))
        self.store.finish(identity(), result())
        restored = CaseStore(self.path)
        self.assertEqual(restored.begin(identity()), result())
        restored.finish(identity(), result())

    def test_interrupted_case_is_not_implicitly_retried(self):
        self.store.begin(identity())
        with self.assertRaisesRegex(ValueError, "reconcile worker"):
            CaseStore(self.path).begin(identity())
        next_case = dict(identity(), fixture="E02-container-lifecycle")
        self.assertIsNone(self.store.begin(next_case))

    def test_failure_is_retained_not_overwritten_by_a_retry(self):
        failure = dict(result(), status="timeout", errors=["deadline exceeded"])
        self.store.begin(identity())
        self.store.finish(identity(), failure)
        self.assertEqual(self.store.begin(identity()), failure)
        with self.assertRaisesRegex(ValueError, "overwrite"):
            self.store.finish(identity(), result())
        self.assertIsNone(self.store.begin(dict(identity(), campaign="new-explicit-campaign")))

    def test_completion_requires_admission(self):
        with self.assertRaisesRegex(ValueError, "admitted"):
            self.store.finish(identity(), result())

    def test_corrupt_evidence_and_symlinked_storage_are_rejected(self):
        self.store.begin(identity())
        self.store.finish(identity(), result())
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE cases SET result=?", (b"corrupt",))
        with self.assertRaisesRegex(ValueError, "Corrupt"):
            self.store.begin(identity())
        link = self.path.with_name("link.sqlite")
        link.symlink_to(self.path)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            CaseStore(link)

    def test_identity_cannot_omit_fingerprints_or_escape_paths(self):
        for key in identity():
            invalid = identity()
            del invalid[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_identity(invalid)
        for key, value in [("campaign", "../outside"), ("lane", "unknown"), ("harnessSHA256", "main")]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_identity(dict(identity(), **{key: value}))

    def test_missing_cleanup_or_observations_cannot_pass(self):
        invalid = [dict(result(), observations={}), dict(result(), errors=["warning"]),
                   dict(result(), cleanup={"status": "unknown", "remainingOwnedResources": []}),
                   dict(result(), cleanup={"status": "passed", "remainingOwnedResources": ["container"]}),
                   dict(result(), durationsNS={"setup": 0, "operation": True, "cleanup": 0})]
        for item in invalid:
            with self.subTest(result=item), self.assertRaises(ValueError):
                validate_result(item)

    def records(self):
        return [{"identity": identity(lane), "result": result()} for lane in LANES]

    def test_execution_resumes_completed_case_without_repeating_any_phase(self):
        contract = {"ping": "true"}
        request = dict(identity(), contractSHA256=digest(canonical(contract)))
        setup = Mock()
        operation = Mock(return_value=contract)
        cleanup = Mock(return_value=result()["cleanup"])
        with patch("case_evidence.time.monotonic_ns", side_effect=[10, 20, 30, 70, 80, 90]):
            completed = run_case(self.store, request, contract, setup, operation, cleanup)
        self.assertEqual(completed["status"], "passed")
        self.assertEqual(completed["durationsNS"], {"setup": 10, "operation": 40, "cleanup": 10})
        self.assertEqual(run_case(CaseStore(self.path), request, contract, setup, operation, cleanup), completed)
        for callback in (setup, operation, cleanup):
            callback.assert_called_once()

    def test_partial_setup_failure_still_cleans_and_never_runs_the_probe(self):
        contract = {"ping": "true"}
        request = dict(identity(), contractSHA256=digest(canonical(contract)))
        operation = Mock()
        cleanup = Mock(return_value=result()["cleanup"])
        completed = run_case(self.store, request, contract, Mock(side_effect=ValueError("sensitive detail")), operation, cleanup)
        self.assertEqual(completed["status"], "failed")
        self.assertEqual(completed["errors"], ["setup: ValueError"])
        operation.assert_not_called()
        cleanup.assert_called_once()

    def test_timeout_interrupt_and_cleanup_errors_remain_failed_evidence(self):
        contract = {"ping": "true"}
        for index, error in enumerate([TimeoutError(), KeyboardInterrupt()]):
            request = dict(identity(), campaign=f"attempt-{index}", contractSHA256=digest(canonical(contract)))
            completed = run_case(self.store, request, contract, Mock(), Mock(side_effect=error), Mock(side_effect=RuntimeError()))
            self.assertEqual(completed["status"], "timeout" if index == 0 else "interrupted")
            self.assertEqual(completed["cleanup"]["status"], "unknown")
            self.assertEqual(len(completed["errors"]), 2)
            self.assertEqual(self.store.begin(request), completed)

    def test_wrong_contract_is_rejected_before_admission_or_execution(self):
        probe = Mock()
        with self.assertRaisesRegex(ValueError, "admitted contract"):
            run_case(self.store, identity(), {"wrong": "contract"}, probe, probe, probe)
        probe.assert_not_called()
        self.assertIsNone(self.store.begin(identity()))

    def test_equivalent_observations_keep_raw_timing_without_qualification(self):
        records = self.records()
        records[1]["result"]["durationsNS"]["operation"] = 100
        compared = compare_cases(records, {"ping": "true"})
        self.assertTrue(compared["functionalParity"])
        self.assertEqual(compared["operationRatios"]["apple-stock"], 5)
        self.assertFalse(compared["timingQualified"])

    def test_mismatched_contract_or_failed_lane_is_not_parity(self):
        for change in [{"status": "failed"}, {"observations": {"ping": "false"}},
                       {"observations": {"ping": "true", "unexpected": "value"}}]:
            records = self.records()
            records[1]["result"].update(change)
            self.assertFalse(compare_cases(records, {"ping": "true"})["functionalParity"])

    def test_incomplete_duplicate_and_mixed_campaigns_are_rejected(self):
        for records in [self.records()[:2], self.records() + [self.records()[0]]]:
            with self.assertRaises(ValueError):
                compare_cases(records, {"ping": "true"})
        for field in ["campaign", "fixture", "contractSHA256", "harnessSHA256", "releaseSetSHA256"]:
            records = copy.deepcopy(self.records())
            records[1]["identity"][field] = "other" if not field.endswith("SHA256") else "e" * 64
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "mix"):
                compare_cases(records, {"ping": "true"})

    def test_unrelated_expected_contract_cannot_produce_a_passing_comparison(self):
        records = self.records()
        for record in records:
            record["identity"]["contractSHA256"] = "a" * 64
        with self.assertRaisesRegex(ValueError, "admitted contract"):
            compare_cases(records, {"ping": "true"})


if __name__ == "__main__":
    unittest.main()
