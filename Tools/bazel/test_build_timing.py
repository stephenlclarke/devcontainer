"""Timing fidelity, failure evidence and incompatible-comparison regression tests."""

import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from build_timing import compare, measure, observation
from retain_evidence import retain


class BuildTimingTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    def record(self):
        return {"host": {"model": "fixture"}, "runtimeProfile": "stock", "elapsedNS": 100,
                "exitCode": 0, "validationExitCode": 0, "dirty": False, "configurationSHA256": "test-config",
                "metrics": {"command": "build", "targets": ["//:product"]}}

    def test_measure_uses_monotonic_clock_and_preserves_failed_child(self):
        output = self.root / "timing.json"
        with patch("build_timing.host_identity", return_value={"model": "fixture"}), \
                patch("build_timing.time.monotonic_ns", side_effect=[100, 300]), \
                patch("build_timing.subprocess.run", return_value=subprocess.CompletedProcess(["fixture"], 17)) as child:
            self.assertEqual(measure(output, "stock", ["fixture", "argument"]), 17)
        child.assert_called_once()
        self.assertEqual(child.call_args.args, (["fixture", "argument"],))
        self.assertFalse(child.call_args.kwargs["check"])
        self.assertNotIn("SDKROOT", child.call_args.kwargs["env"])
        self.assertNotIn("CPATH", child.call_args.kwargs["env"])
        self.assertNotIn("SONAR_TOKEN", child.call_args.kwargs["env"])
        record = json.loads(output.read_text())
        self.assertEqual(record["elapsedNS"], 200)
        self.assertEqual(record["exitCode"], 17)
        self.assertFalse(record["quietHostVerified"])
        self.assertNotIn("environment", record)

    def test_ratio_is_informational_and_never_an_authoritative_campaign(self):
        baseline, candidate = self.record(), self.record()
        candidate["elapsedNS"] = 2000
        result = compare(baseline, candidate)
        self.assertEqual(result["candidateOverBaseline"], 20)
        self.assertEqual(result["incompatible"], [])
        self.assertFalse(result["authoritativeBenchmark"])

    def test_invalid_or_incompatible_comparisons_fail_closed(self):
        baseline = self.record()
        for key, value in [("host", {}), ("runtimeProfile", "enhanced"), ("configurationSHA256", "other"), ("exitCode", 1),
                           ("validationExitCode", 1), ("dirty", True),
                           ("metrics", {"command": "coverage", "targets": ["//:unit"]})]:
            candidate = copy.deepcopy(baseline)
            candidate[key] = value
            with self.subTest(key=key):
                self.assertIsNone(compare(baseline, candidate)["candidateOverBaseline"])
        for value in (0, -1, 2.5):
            with self.assertRaises(ValueError):
                compare(baseline, dict(baseline, elapsedNS=value))

    def test_timings_are_sealed_and_survive_scratch_cleanup(self):
        database = self.root / "evidence.sqlite"
        events = self.root / "events.json"
        events.write_text('\n'.join(json.dumps(row) for row in [
            {"started": {"uuid": "run", "command": "build", "buildToolVersion": "8.8.0"}},
            {"buildMetrics": {"actionSummary": {"actionsExecuted": 0}, "timingMetrics": {"wallTimeInMs": 12}}},
            {"finished": {"exitCode": {"code": 0}}},
        ]))
        timing = self.root / "timing.json"
        timing.write_text(json.dumps(self.record()))
        for name in ("inputs-before.json", "inputs-after.json"):
            (self.root / name).write_text(json.dumps({"commit": "a" * 40, "dirty": False, "files": {}}))
        retain(events, database, self.root)
        timing.unlink()
        result = observation(database, "run")
        self.assertEqual(result["elapsedNS"], 100)
        self.assertEqual(result["metrics"]["actionSummary"]["actionsExecuted"], 0)
        self.assertEqual(result["sourceCommit"], "a" * 40)
        with self.assertRaisesRegex(ValueError, "Unknown"):
            observation(database, "missing")
        with sqlite3.connect(database) as db:
            db.execute("UPDATE blobs SET bytes=?", (b"corrupt",))
        with self.assertRaisesRegex(ValueError, "corrupt"):
            observation(database, "run")

    def test_dirty_after_snapshot_cannot_produce_clean_comparison(self):
        from retain_evidence import digest

        database = self.root / "changed.sqlite"
        before = {"commit": "a" * 40, "dirty": False, "files": {}}
        after = dict(before, dirty=True, files={"MODULE.bazel.lock": {"sha256": "changed"}})
        contents = {"inputs-before.json": before, "inputs-after.json": after,
                    "timing.json": self.record(), "build-metrics.json": self.record()["metrics"]}
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE blobs (sha256 TEXT PRIMARY KEY, bytes BLOB)")
            db.execute("CREATE TABLE invocations (id TEXT, manifest TEXT, exit_code INTEGER)")
            manifest = {}
            for name, value in contents.items():
                data = json.dumps(value).encode()
                manifest[name] = digest(data)
                db.execute("INSERT INTO blobs VALUES (?, ?)", (digest(data), data))
            db.execute("INSERT INTO invocations VALUES (?, ?, 0)", ("changed", json.dumps(manifest)))
        candidate = observation(database, "changed")
        self.assertTrue(candidate["dirty"])
        self.assertIsNone(compare(self.record(), candidate)["candidateOverBaseline"])


if __name__ == "__main__":
    unittest.main()
