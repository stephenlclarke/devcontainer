"""Retained coverage conversion/recovery without any compiler or test runner."""

import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from coverage_report import export, report_bytes, sonar_xml
from retain_evidence import digest


LCOV = b"SF:Sources/Example.swift\nDA:1,2\nDA:3,0\nLF:2\nLH:1\nend_of_record\n"


class CoverageReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "tmp").mkdir()
        self.database = self.root / "evidence.sqlite"
        identity = {"commit": "a" * 40, "dirty": False, "files": {"source": "hash"}}
        self.contents = {
            "inputs-before.json": json.dumps(identity).encode(),
            "inputs-after.json": json.dumps(identity).encode(),
            "outcome.json": b'{"bazel_exit_code":0,"validation_exit_code":0,"suite":"source"}',
            "source-tests.json": json.dumps({"runtime_profile": "stock", "coverage": {
                "sha256": digest(LCOV), "hit": 1, "found": 2, "scope": "unit only",
            }}).encode(),
            "build:build:coverage_report.lcov": LCOV,
        }

    def store(self, status: int = 0) -> None:
        with sqlite3.connect(self.database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS blobs (sha256 TEXT PRIMARY KEY, bytes BLOB)")
            db.execute("CREATE TABLE IF NOT EXISTS invocations (id TEXT PRIMARY KEY, manifest TEXT, exit_code INTEGER)")
            for data in self.contents.values():
                db.execute("INSERT OR REPLACE INTO blobs VALUES (?,?)", (digest(data), data))
            db.execute("INSERT OR REPLACE INTO invocations VALUES (?,?,?)", (
                "fixture", json.dumps({name: digest(data) for name, data in self.contents.items()}), status,
            ))

    def test_exact_denominator_and_xml_escaping(self) -> None:
        xml, hit, found = sonar_xml(LCOV.replace(b"Example", b"A&B"))
        self.assertEqual((hit, found), (1, 2))
        root = ET.fromstring(xml)
        self.assertEqual(root[0].get("path"), "Sources/A&B.swift")
        self.assertEqual([line.get("covered") for line in root[0]], ["true", "false"])

    def test_invalid_duplicate_and_escaping_records_fail(self) -> None:
        for data in [b"", LCOV * 2, LCOV.replace(b"Sources/", b"../"),
                     LCOV.replace(b"Sources/", b"Sources/../"), LCOV.replace(b"LF:2", b"LF:3"),
                     LCOV.replace(b"LH:1", b"LH:0"), LCOV.replace(b"DA:3,0", b"DA:1,0"),
                     LCOV.replace(b"DA:1,2", b"DA:0,2"), LCOV.replace(b"DA:1,2", b"DA:1,-2")]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                sonar_xml(data)

    def test_restore_reuse_and_no_subprocesses(self) -> None:
        self.store()
        with patch("subprocess.Popen", side_effect=AssertionError("No executable work permitted")):
            output = export(self.database, "fixture", self.root)
            self.assertEqual(export(self.database, "fixture", self.root), output)
            receipt = json.loads((output / "receipt.json").read_text())
            self.assertEqual(receipt["percent"], 50)
            self.assertEqual(receipt["sourceCommit"], "a" * 40)
            self.assertFalse(receipt["releaseAuthority"])
            (output / "coverage.xml").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "Conflicting"):
                export(self.database, "fixture", self.root)

    def test_failed_missing_corrupt_and_dirty_evidence_rejected(self) -> None:
        self.store(status=1)
        with self.assertRaisesRegex(ValueError, "successful"):
            report_bytes(self.database, "fixture")
        self.store()
        for name in ("inputs-before.json", "inputs-after.json"):
            original = self.contents[name]
            identity = json.loads(original)
            identity["dirty"] = True
            self.contents[name] = json.dumps(identity).encode()
            self.store()
            with self.assertRaisesRegex(ValueError, "clean"):
                report_bytes(self.database, "fixture")
            self.contents[name] = original
        self.store()
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE blobs SET bytes=?", (b"corrupt",))
        with self.assertRaisesRegex(ValueError, "corrupt"):
            report_bytes(self.database, "fixture")

    def test_report_binding_and_source_drift_rejected(self) -> None:
        self.contents["inputs-after.json"] = self.contents["inputs-after.json"].replace(b"hash", b"changed")
        self.store()
        with self.assertRaisesRegex(ValueError, "Sources changed"):
            report_bytes(self.database, "fixture")
        self.contents["inputs-after.json"] = self.contents["inputs-before.json"]
        self.contents["build:build:coverage_report.lcov"] = LCOV.replace(b"Example", b"Changed")
        self.store()
        with self.assertRaisesRegex(ValueError, "does not match"):
            report_bytes(self.database, "fixture")

    def test_symlink_output_rejected(self) -> None:
        self.store()
        (self.root / "coverage").symlink_to(self.root / "tmp", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinked"):
            export(self.database, "fixture", self.root)


if __name__ == "__main__":
    unittest.main()
