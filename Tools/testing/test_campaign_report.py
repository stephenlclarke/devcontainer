"""Public reporting preserves failures, scope, raw timings and private evidence."""

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import campaign_report
from case_evidence import CaseStore, LANES, canonical, digest
from test_case_evidence import identity, result


class CampaignReportTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.database = self.root / "cases.sqlite"
        self.store = CaseStore(self.database)
        self.database.chmod(0o600)
        self.fixtures = ["E01-engine-negotiation", "E02-container-lifecycle"]
        self.manifest = self.root / "Tests/Parity/manifest.json"
        self.manifest.parent.mkdir(parents=True)
        self.manifest.write_bytes(canonical({"fixtures": [{"id": name} for name in self.fixtures]}))
        for name in self.fixtures:
            contract = self.root / f"Tests/Parity/fixtures/{name}/contract.json"
            contract.parent.mkdir(parents=True)
            contract.write_bytes(canonical({"expected": {"ping": True}}))

    def test_contract_reading_preserves_mixed_case_and_only_formats_booleans(self):
        raw = {"order": "onCreate,updateContent,postCreate,postStart,postAttach", "marker": "TRUE", "ready": True,
               "absent": False, "uid": 1000}
        path = self.root / f"Tests/Parity/fixtures/{self.fixtures[0]}/contract.json"
        path.write_bytes(canonical({"expected": raw}))
        expected, _ = campaign_report.contracts(self.root, self.fixtures[:1])
        self.assertEqual(expected[self.fixtures[0]], dict(raw, ready="true", absent="false", uid="1000"))

    def populate(self, failure=False):
        for index, lane in enumerate(LANES):
            request = identity(lane)
            completed = result()
            completed["durationsNS"]["operation"] = (1, 5, 10)[index] * 100
            if failure and lane == "apple-stock":
                completed.update(status="timeout", errors=["SECRET-ERROR"], observations={"private": "SECRET-OBSERVATION"})
                completed["cleanup"] = {"status": "failed", "remainingOwnedResources": ["SECRET-RESOURCE"]}
            self.store.begin(request)
            self.store.attach(request, "private.log", b"SECRET-LOG")
            self.store.finish(request, completed)

    def report(self, selected=None):
        return campaign_report.report(self.database, self.root, "campaign-1", self.fixtures[:1] if selected is None else selected)

    def test_subset_compares_exact_durations_without_claiming_full_or_quiet_qualification(self):
        self.populate()
        data = self.report()
        self.assertTrue(data["functionalParity"])
        self.assertFalse(data["completeManifest"])
        self.assertFalse(data["timingQualified"])
        self.assertFalse(data["releaseQualified"])
        self.assertEqual(data["unrequestedFixtures"], self.fixtures[1:])
        fixture = data["fixtures"][0]
        self.assertEqual(fixture["rawOrderOfMagnitudeCandidates"], ["container-compose"])
        self.assertEqual(fixture["comparison"]["operationRatios"]["apple-stock"], 5)
        self.assertNotIn("SECRET", json.dumps(data))
        self.assertIn("10.000x", campaign_report.markdown(data))
        xml = ET.fromstring(campaign_report.junit(data))
        self.assertEqual(xml.attrib["failures"], "0")
        properties = {p.attrib["name"]: p.attrib["value"] for p in xml.find("testcase/properties")}
        self.assertEqual(properties["operationNS"], "100")
        self.assertEqual(properties["timingQualified"], "false")

    def test_default_scope_never_shrinks_to_existing_results(self):
        self.populate()
        data = self.report([])
        self.assertTrue(data["completeManifest"])
        self.assertFalse(data["functionalParity"])
        self.assertEqual(len(data["fixtures"][1]["missingLanes"]), 3)
        self.assertIn("missing", campaign_report.markdown(data))
        xml = ET.fromstring(campaign_report.junit(data))
        self.assertEqual(xml.attrib["tests"], "6")
        self.assertEqual(xml.attrib["failures"], "3")

    def test_failed_case_and_private_payloads_are_not_hidden_or_published(self):
        self.populate(failure=True)
        data = self.report()
        self.assertFalse(data["functionalParity"])
        case = next(c for c in data["fixtures"][0]["cases"] if c["identity"]["lane"] == "apple-stock")
        self.assertEqual(case["status"], "timeout")
        self.assertFalse(case["observationsMatch"])
        self.assertEqual(case["remainingOwnedResourceCount"], 1)
        self.assertEqual(case["errorCount"], 1)
        for rendered in (json.dumps(data), campaign_report.markdown(data), campaign_report.junit(data)):
            self.assertNotIn("SECRET", rendered)
        self.assertEqual(ET.fromstring(campaign_report.junit(data)).attrib["failures"], "1")

    def test_snapshot_is_read_only_and_does_not_create_missing_database(self):
        self.populate()
        before = self.database.read_bytes()
        self.report()
        self.assertEqual(self.database.read_bytes(), before)
        missing = self.root / "absent.sqlite"
        selected = set(self.fixtures)
        with self.assertRaises(FileNotFoundError):
            campaign_report.read_records(missing, "campaign-1", selected)
        self.assertFalse(missing.exists())

    def test_unfinished_corrupt_and_ambiguous_cases_fail_closed(self):
        self.populate()
        extra = dict(identity(), runtimeSHA256="f" * 64)
        self.store.begin(extra)
        with self.assertRaisesRegex(ValueError, "Unfinished"):
            self.report()
        self.store.finish(extra, result())
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            self.report()
        with self.store.connect() as db:
            db.execute("UPDATE artifacts SET bytes=x'00'")
        with self.assertRaisesRegex(ValueError, "Corrupt"):
            self.report()

    def test_wrong_key_and_contract_are_rejected(self):
        self.populate()
        contract = self.root / f"Tests/Parity/fixtures/{self.fixtures[0]}/contract.json"
        contract.write_bytes(canonical({"expected": {"different": True}}))
        with self.assertRaisesRegex(ValueError, "contract differs"):
            self.report()
        with self.store.connect() as db:
            db.execute("UPDATE cases SET id='wrong' WHERE id=?", (digest(canonical(identity())),))
        with self.assertRaisesRegex(ValueError, "key differs"):
            self.report()

    def test_mixed_release_identities_cannot_produce_a_report(self):
        for lane in LANES:
            request = dict(identity(lane), releaseSetSHA256=("a" if lane == "docker" else "b") * 64)
            self.store.begin(request)
            self.store.finish(request, result())
        with self.assertRaisesRegex(ValueError, "Cannot mix"):
            self.report()

    def test_missing_lane_is_a_failure_not_an_empty_pass(self):
        data = self.report()
        self.assertFalse(data["functionalParity"])
        self.assertEqual(data["fixtures"][0]["missingLanes"], sorted(LANES))

    def test_cross_fixture_drift_is_rejected_even_when_each_fixture_passes(self):
        self.populate()
        for lane in LANES:
            request = dict(identity(lane), fixture=self.fixtures[1], harnessSHA256="f" * 64)
            self.store.begin(request)
            self.store.finish(request, result())
        with self.assertRaisesRegex(ValueError, "Cannot mix"):
            self.report([])

    def test_incomplete_fixture_still_rejects_mixed_identities(self):
        for lane in LANES[:2]:
            request = dict(identity(lane), releaseSetSHA256=("a" if lane == "docker" else "b") * 64)
            self.store.begin(request)
            self.store.finish(request, result())
        with self.assertRaisesRegex(ValueError, "Cannot mix"):
            self.report()

    def test_invalid_scope_and_storage_are_rejected(self):
        for selection in (["unknown"], self.fixtures[:1] * 2):
            with self.assertRaisesRegex(ValueError, "selected fixture"):
                self.report(selection)
        selected = set(self.fixtures)
        for campaign in ("../escape", ""):
            with self.assertRaisesRegex(ValueError, "Invalid campaign"):
                campaign_report.read_records(self.database, campaign, selected)
        alias = self.root / "alias.sqlite"
        alias.symlink_to(self.database)
        with self.assertRaisesRegex(ValueError, "private canonical"):
            campaign_report.read_records(alias, "campaign-1", selected)
        self.database.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "private canonical"):
            self.report()
        for names in ([], ["../escape"], [self.fixtures[0]] * 2):
            self.manifest.write_bytes(canonical({"fixtures": [{"id": name} for name in names]}))
            with self.assertRaisesRegex(ValueError, "Invalid fixture inventory"):
                campaign_report.contracts(self.root, [])

    def test_cli_renders_each_format_and_preserves_failure_exit_code(self):
        self.populate()
        data = self.report()
        retained = self.root / "Library/Application Support/ContainerFamily/retained/workflow"
        retained.mkdir(parents=True)
        actual_script = Path(campaign_report.__file__).resolve()
        launcher_script = actual_script.parent.parent / "bazel/../testing/campaign_report.py"
        for format_name in ("json", "markdown", "junit"):
            with patch.object(Path, "home", return_value=self.root), patch.object(campaign_report, "report", return_value=data) as render, \
                    patch.object(campaign_report, "__file__", str(launcher_script)), \
                    patch("sys.argv", ["report", "campaign-1", "--fixture", self.fixtures[0], "--format", format_name]), \
                    contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit) as exited:
                campaign_report.main()
            self.assertEqual(exited.exception.code, 0)
            self.assertTrue(output.getvalue())
            self.assertEqual(render.call_args.args[1], actual_script.parents[2])
        with patch.object(Path, "home", return_value=self.root), patch.object(campaign_report, "report", return_value=self.report([])), \
                patch("sys.argv", ["report", "campaign-1"]), contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit) as exited:
            campaign_report.main()
        self.assertEqual(exited.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
