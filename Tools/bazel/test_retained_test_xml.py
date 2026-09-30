"""Immutable XML admission, including reused Bazel test output paths."""

from __future__ import annotations

from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from artifacts import layer_release
from check_evidence import validate
import layered_build
from retained_test_xml import read, result_name


def seal(database: Path, invocation: Path, uuid: str, events: list[dict],
         xml: dict[str, bytes], source: dict) -> None:
    """Store the same keyed blobs and manifest as retain_evidence.retain."""
    invocation.mkdir()
    files = {
        "events.json": ("\n".join(json.dumps(event) for event in events) + "\n").encode(),
        "inputs-before.json": json.dumps(source).encode(),
        "inputs-after.json": json.dumps(source).encode(),
        "outcome.json": json.dumps({"bazel_exit_code": 0, "validation_exit_code": 0}).encode(),
        **xml,
    }
    manifest = {name: sha256(data).hexdigest() for name, data in files.items()}
    for name in ("events.json", "inputs-before.json", "inputs-after.json", "outcome.json"):
        (invocation / name).write_bytes(files[name])
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("CREATE TABLE IF NOT EXISTS blobs (sha256 TEXT PRIMARY KEY, bytes BLOB NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS invocations "
                           "(id TEXT PRIMARY KEY, manifest TEXT NOT NULL, exit_code INTEGER NOT NULL)")
        for name, data in files.items():
            connection.execute("INSERT OR IGNORE INTO blobs VALUES (?, ?)", (manifest[name], data))
        connection.execute("INSERT INTO invocations VALUES (?, ?, 0)", (uuid, json.dumps(manifest)))


def records(uuid: str, counts: dict[str, int], *, profile: str = "stock",
            release: bool = False) -> tuple[list[dict], dict[str, bytes]]:
    flags = ["--config=" + profile]
    if release:
        flags += ["--config=release", "--config=prebuilt-argument-parser"]
    target = sorted(counts) if release else ["//:layer_model_tests"]
    events = [
        {"started": {"uuid": uuid}},
        {"unstructuredCommandLine": {"args": ["test", *flags, *target]}},
        {"optionsParsed": {"cmdLine": ["--compilation_mode=" + ("opt" if release else "dbg"),
                                          "--config=" + profile, "--flaky_test_attempts=1"]}},
        {"finished": {"overallSuccess": True, "exitCode": {"name": "SUCCESS", "code": 0}}},
    ]
    xml = {}
    for label, count in counts.items():
        identity = {"label": label, "run": 1, "shard": 1, "attempt": 1,
                    "configuration": {"id": profile}}
        events += [
            {"id": {"testSummary": {"label": label}},
             "testSummary": {"overallStatus": "PASSED", "totalRunCount": 1, "totalNumCached": 0}},
            {"id": {"testResult": identity}, "testResult": {"testActionOutput": [
                {"name": "test.xml", "uri": "file:///Volumes/SSD/cf/bazel/testlogs/shared/test.xml"}]}},
        ]
        xml[result_name(identity)] = ("<testsuite>" + '<testcase name="pass"/>' * count +
                                      "</testsuite>").encode()
    return events, xml


class RetainedTestXMLTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / "evidence.sqlite"
        self.source = {"commit": "a" * 40, "dirty": False}

    def test_later_profile_cannot_replace_original_xml_or_use_live_path(self) -> None:
        enhanced_id, stock_id = "1" * 8 + "-1111-1111-1111-" + "1" * 12, "2" * 8 + "-2222-2222-2222-" + "2" * 12
        enhanced, first = records(enhanced_id, {"//:Unit": 2}, profile="enhanced")
        stock, second = records(stock_id, {"//:Unit": 1})
        first_run, second_run = self.root / "enhanced", self.root / "stock"
        seal(self.database, first_run, enhanced_id, enhanced, first, self.source)
        seal(self.database, second_run, stock_id, stock, second, self.source)
        # Both BEP streams name the same mutable output URI; neither live XML
        # exists here. The original profile must still count its sealed cases.
        self.assertEqual(validate(enhanced, False, {"//:Unit": 2},
                                  retained_xml=read(first_run, enhanced, self.database))["test_cases"],
                         {"//:Unit": 2})
        self.assertEqual(validate(stock, False, {"//:Unit": 1},
                                  retained_xml=read(second_run, stock, self.database))["test_cases"],
                         {"//:Unit": 1})
        with self.assertRaisesRegex(ValueError, "immutable"):
            validate(enhanced, False, {"//:Unit": 2}, retained_xml={})

    def test_missing_corrupt_and_misbound_blobs_fail_closed(self) -> None:
        uuid = "3" * 8 + "-3333-3333-3333-" + "3" * 12
        events, xml = records(uuid, {"//:Unit": 2})
        run = self.root / "run"
        seal(self.database, run, uuid, events, xml, self.source)
        original = read(run, events, self.database)
        (run / "inputs-after.json").write_text('{"commit":"changed"}')
        with self.assertRaisesRegex(ValueError, "differs"):
            read(run, events, self.database)
        (run / "inputs-after.json").write_text(json.dumps(self.source))
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("DELETE FROM blobs WHERE sha256=?", (sha256(next(iter(xml.values()))).hexdigest(),))
        with self.assertRaisesRegex(ValueError, "Missing or corrupt"):
            read(run, events, self.database)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("INSERT INTO blobs VALUES (?, ?)",
                               (sha256(next(iter(xml.values()))).hexdigest(), b"corrupt"))
        with self.assertRaisesRegex(ValueError, "Missing or corrupt"):
            read(run, events, self.database)
        self.assertEqual(len(original), 1)

    def test_release_and_source_controller_admit_exact_retained_xml(self) -> None:
        profile = "enhanced"
        policy = layer_release.TEST_LABELS["foundation"]
        source_counts = {"//:DevContainerCLITests": 55, "//:DevContainerServiceTests": 24}
        self.assertEqual(set(source_counts), policy)
        release_id = "4" * 8 + "-4444-4444-4444-" + "4" * 12
        events, xml = records(release_id, source_counts, profile=profile, release=True)
        release = self.root / "release"
        seal(self.database, release, release_id, events, xml, self.source)
        admitted = layer_release.admitted_tests(release, self.source, "foundation", profile, self.database)
        self.assertEqual(admitted["caseCounts"], source_counts)
        self.assertEqual(len(admitted["retainedXMLSHA256"]), 2)
        with self.assertRaisesRegex(ValueError, "retained test evidence database"):
            layer_release.admitted_tests(release, self.source, "foundation", profile,
                                         self.root / "missing.sqlite")

        stage = layered_build.plan(layered_build.ROOT, ("stock",))[0]
        source_id = "5" * 8 + "-5555-5555-5555-" + "5" * 12
        events, xml = records(source_id, stage["tests"])
        source_run = self.root / "source"
        seal(self.database, source_run, source_id, events, xml, self.source)
        admitted = layered_build.admit(source_run, stage, self.source, self.database)
        self.assertEqual(admitted["test_cases"], stage["tests"])
        self.assertEqual(len(admitted["retainedXMLSHA256"]), len(stage["tests"]))
        with self.assertRaisesRegex(ValueError, "retained test evidence database"):
            layered_build.admit(source_run, stage, self.source, self.root / "missing.sqlite")


if __name__ == "__main__":
    unittest.main()
