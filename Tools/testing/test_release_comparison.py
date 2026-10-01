"""Previous/current comparisons preserve identity, failures and unqualified scope."""

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import release_comparison as comparison
from campaign_report import read_records
from case_evidence import CaseStore, canonical, digest
from test_case_evidence import identity, result


class ReleaseComparisonTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.retained = self.root / "Library/Application Support/ContainerFamily/retained/workflow"
        self.retained.mkdir(parents=True)
        self.database = self.retained / "runtime-cases.sqlite"
        self.store = CaseStore(self.database)
        self.database.chmod(0o600)
        self.fixture = "E01-engine-negotiation"
        self.fixture_root = self.root / "Tests/Parity/fixtures" / self.fixture
        self.fixture_root.mkdir(parents=True)
        (self.fixture_root / "contract.json").write_bytes(canonical({"expected": {"ping": True}}))
        (self.root / "Tests/Parity/manifest.json").write_bytes(canonical({"fixtures": [{"id": self.fixture}]}))
        self.runtime = {"releases": [{"assetSHA256": "a" * 64}, {"assetSHA256": "c" * 64}],
                        "versions": ["old", "provider"], "machine": "arm64", "os": "test",
                        "guestInputs": {"image": "d" * 64}}
        self.admission = {"scope": "released-engine-case-only", "runtime": self.runtime, "releaseLock": {"assets": [
            {"repository": "stephenlclarke/devcontainer", "sha256": "a" * 64, "commit": "a" * 40,
             "tag": "1.0.1", "prerelease": False}]}}

    def add(self, campaign, *, current=False, duration=100, status="passed", observations=None,
            change_runtime=None, change_identity=None, change_admission=None, attach=True):
        admission = copy.deepcopy(self.admission)
        if current:
            admission["scope"] = comparison.CANDIDATE
            admission["runtime"]["releases"][0] = {"scope": comparison.CANDIDATE, "assetSHA256": "b" * 64,
                                                    "sourceCommit": "b" * 40}
            admission["runtime"]["versions"][0] = "new"
        if change_runtime:
            change_runtime(admission["runtime"])
        request = dict(identity("apple-stock"), campaign=campaign, runtimeSHA256=digest(canonical(admission["runtime"])),
                       releaseSetSHA256=("a" if not current else "b") * 64)
        if change_identity:
            request.update(change_identity)
        if change_admission:
            change_admission(admission)
        completed = result()
        completed["durationsNS"]["operation"] = duration
        completed["status"] = status
        if status != "passed":
            completed["errors"] = ["SECRET-error"]
        if observations is not None:
            completed["observations"] = observations
        self.store.begin(request)
        if attach:
            self.store.attach(request, "admission.json", canonical(admission))
        self.store.attach(request, "private.log", b"SECRET-private-path")
        self.store.finish(request, completed)
        return request

    def report(self, baseline=None, target=None, fixtures=None, lane="apple-stock"):
        return comparison.compare(self.database, self.root, baseline or ["before"], target or ["after"],
                                  fixtures or [self.fixture], lane)

    def test_exact_samples_report_raw_statistics_and_never_claim_release_or_quiet(self):
        for index, (before, after) in enumerate(((100, 50), (300, 200), (200, 60))):
            self.add("before" + str(index), duration=before)
            self.add("after" + str(index), current=True, duration=after)
        before_bytes = self.database.read_bytes()
        report = self.report(["before0", "before1", "before2"], ["after0", "after1", "after2"])
        row = report["fixtures"][0]
        self.assertEqual(row["baseline"]["summary"], {"medianNS": 200, "p95NS": 300})
        self.assertEqual(row["target"]["summary"], {"medianNS": 60, "p95NS": 200})
        self.assertEqual(row["rawDifference"]["medianNS"]["savedNS"], 140)
        self.assertAlmostEqual(row["rawDifference"]["medianNS"]["savedPercent"], 70)
        self.assertEqual(row["baseline"]["product"]["kind"], "published-release")
        self.assertEqual(row["target"]["product"]["kind"], "local-candidate")
        self.assertFalse(report["releaseQualified"])
        self.assertFalse(report["timingQualified"])
        self.assertEqual(self.database.read_bytes(), before_bytes)
        for rendered in (json.dumps(report), comparison.markdown(report)):
            self.assertNotIn("SECRET", rendered)
            self.assertIn("not a", comparison.markdown(report))
        self.assertIn("raw median difference", comparison.markdown(report))

    def test_one_failed_sample_disqualifies_whole_role_without_dropping_it(self):
        self.add("before", status="failed", duration=1)
        self.add("before2", duration=100)
        self.add("after", current=True, duration=50)
        self.add("after2", current=True, duration=50)
        row = self.report(["before", "before2"], ["after", "after2"])["fixtures"][0]
        self.assertIsNone(row["rawDifference"])
        self.assertIsNone(row["baseline"]["summary"])
        self.assertEqual(len(row["baseline"]["samples"]), 2)
        self.assertEqual(row["baseline"]["samples"][0]["durationsNS"]["operation"], 1)

    def test_invalid_observation_zero_timeout_and_interruption_never_generate_ratios(self):
        for index, values in enumerate(({"observations": {"private": "SECRET"}}, {"duration": 0},
                                       {"status": "timeout"}, {"status": "interrupted"})):
            for current in (False, True):
                suffix = str(index) + str(current)
                self.add("before" + suffix, **(values if not current else {}))
                self.add("after" + suffix, current=True, **(values if current else {}))
                report = self.report(["before" + suffix], ["after" + suffix])
                self.assertIsNone(report["fixtures"][0]["rawDifference"])
                self.assertNotIn("SECRET", json.dumps(report))
                self.assertIn("not compared", comparison.markdown(report))

    def test_order_of_magnitude_is_visible_without_claiming_a_qualified_gate(self):
        self.add("before", duration=100)
        self.add("after", current=True, duration=1000)
        row = self.report()["fixtures"][0]
        self.assertTrue(row["rawOrderOfMagnitudeRegression"])
        self.assertEqual(row["rawDifference"]["medianNS"]["savedPercent"], -900)

    def test_missing_duplicate_and_reused_samples_are_rejected(self):
        self.add("before")
        with self.assertRaisesRegex(ValueError, "Missing"):
            self.report()
        self.add("after", current=True)
        for baseline, target in ((["before"], ["before"]), (["before", "before"], ["after", "after"]),
                                 (["before"], ["after", "other"]), ([], [])):
            with self.assertRaisesRegex(ValueError, "distinct"):
                comparison.compare(self.database, self.root, baseline, target, [self.fixture], "apple-stock")
        self.add("after", current=True, change_identity={"releaseSetSHA256": "f" * 64})
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.report()

    def test_harness_contract_provider_and_os_drift_are_rejected(self):
        self.add("before")
        mutations = [("harness", {"change_identity": {"harnessSHA256": "f" * 64}}),
                     ("contract", {"change_identity": {"contractSHA256": "f" * 64}}),
                     ("provider", {"change_runtime": lambda r: r["releases"][1].update(assetSHA256="f" * 64)}),
                     ("os", {"change_runtime": lambda r: r.update(os="other")}),
                     ("image", {"change_runtime": lambda r: r["guestInputs"].update(image="e" * 64)})]
        for name, change in mutations:
            self.add("after" + name, current=True, **change)
            with self.assertRaises(ValueError):
                self.report(target=["after" + name])

    def test_legacy_and_bundled_frontends_can_differ_but_remain_in_original_seals(self):
        self.add("before", change_runtime=lambda r: r["guestInputs"].update(legacyFrontend={"private": "SECRET-old"}))
        self.add("after", current=True, change_runtime=lambda r: r["guestInputs"].update(devcontainerCandidate={"private": "SECRET-new"}))
        report = self.report()
        self.assertIsNotNone(report["fixtures"][0]["rawDifference"])
        self.assertNotIn("SECRET", json.dumps(report))

    def test_compose_candidate_is_not_misrepresented_as_published_compose(self):
        self.add("before", change_runtime=lambda r: r["guestInputs"].update(composeCandidate={}))
        self.add("after", current=True)
        with self.assertRaisesRegex(ValueError, "Compose"):
            self.report()

    def test_admission_missing_drift_and_corruption_are_rejected(self):
        self.add("before", attach=False)
        self.add("after", current=True)
        with self.assertRaisesRegex(ValueError, "retained runtime admission"):
            self.report()
        self.add("drift", change_admission=lambda a: a["runtime"].update(os="changed-after-hash"))
        with self.assertRaisesRegex(ValueError, "runtime fingerprint"):
            self.report(baseline=["drift"])
        with self.store.connect() as db:
            db.execute("UPDATE artifacts SET bytes=x'00' WHERE name='admission.json'")
        with self.assertRaisesRegex(ValueError, "Corrupt"):
            read_records(self.database, "after", {self.fixture}, include_admission=True)

    def test_other_lanes_do_not_need_native_admission(self):
        self.add("before")
        self.add("before", change_identity={"lane": "docker"}, attach=False)
        self.add("after", current=True)
        self.assertIsNotNone(self.report()["fixtures"][0]["rawDifference"])

    def test_changed_product_within_a_role_is_not_pooled(self):
        self.add("before")
        self.add("before2")
        self.add("after", current=True)
        self.add("after2", current=True, change_runtime=lambda r: r["releases"][0].update(sourceCommit="f" * 40))
        with self.assertRaisesRegex(ValueError, "within one comparison role"):
            self.report(["before", "before2"], ["after", "after2"])

    def test_same_binary_cannot_be_a_new_release(self):
        self.add("before")
        self.add("after")
        with self.assertRaisesRegex(ValueError, "different product"):
            self.report()

    def test_product_scope_and_provenance_fail_closed(self):
        self.add("before")
        changes = [lambda a: a.update(scope="published-maybe"),
                   lambda a: a["runtime"]["releases"][0].update(assetSHA256="bad"),
                   lambda a: a["runtime"]["releases"][0].update(sourceCommit="bad"),
                   lambda a: a["runtime"]["releases"][0].update(scope="wrong")]
        for index, change in enumerate(changes):
            admission = copy.deepcopy(self.admission)
            admission["scope"] = comparison.CANDIDATE
            admission["runtime"]["releases"][0].update(scope=comparison.CANDIDATE, sourceCommit="b" * 40)
            change(admission)
            with self.assertRaises(ValueError):
                comparison.product_identity({"admission": admission})
        for field, value in (("prerelease", True), ("tag", "SECRET-version"), ("commit", "invalid"), ("sha256", "f" * 64)):
            admission = copy.deepcopy(self.admission)
            admission["releaseLock"]["assets"][0][field] = value
            with self.assertRaises(ValueError):
                comparison.product_identity({"admission": admission})

    def test_explicit_lane_and_scope_are_required(self):
        for lane, fixtures in (("docker", [self.fixture]), ("apple-stock", [])):
            with self.assertRaisesRegex(ValueError, "Explicit"):
                comparison.compare(self.database, self.root, ["before"], ["after"], fixtures, lane)
        with self.assertRaisesRegex(ValueError, "Unknown"):
            self.report(fixtures=["missing"])

    def test_cli_renders_actual_data_with_local_candidate_label(self):
        self.add("before")
        self.add("after", current=True)
        arguments = ["compare", "--baseline", "before", "--target", "after", "--fixture", self.fixture,
                     "--lane", "apple-stock"]
        for format_name in ("json", "markdown"):
            output = io.StringIO()
            with patch("sys.argv", arguments + ["--format", format_name]), patch.object(Path, "home", return_value=self.root), \
                    patch.object(comparison, "__file__", str(self.root / "Tools/testing/release_comparison.py")), contextlib.redirect_stdout(output):
                comparison.main()
            self.assertIn("local-candidate", output.getvalue())


if __name__ == "__main__":
    unittest.main()
