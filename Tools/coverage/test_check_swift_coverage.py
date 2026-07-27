"""Tests for the Swift coverage gate."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("check-swift-coverage.py")
SPEC = importlib.util.spec_from_file_location("check_swift_coverage", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CoverageTests(unittest.TestCase):
    def test_counts_only_first_party_sources(self) -> None:
        report = {
            "data": [
                {
                    "files": [
                        {
                            "filename": "/repo/Sources/Core/A.swift",
                            "segments": [
                                [3, 1, 2, True, True, False],
                                [7, 1, 0, True, True, False],
                            ],
                            "summary": {"lines": {"count": 10, "covered": 9}},
                        },
                        {
                            "filename": "/repo/.build/checkouts/X/Sources/X.swift",
                            "summary": {"lines": {"count": 100, "covered": 0}},
                        },
                    ]
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "coverage.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            covered, count, files = MODULE.coverage(path)
        self.assertEqual((covered, count), (1, 2))
        self.assertEqual(len(files), 1)

    def test_merges_repeated_executable_line_segments(self) -> None:
        report = {
            "data": [
                {
                    "files": [
                        {
                            "filename": "/repo/Sources/Core/A.swift",
                            "segments": [
                                [3, 1, 0, True, True, False],
                                [3, 8, 4, True, True, False],
                            ],
                        }
                    ]
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "coverage.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            covered, count, files = MODULE.coverage(path)
        self.assertEqual((covered, count), (1, 1))
        self.assertEqual(files[0][1], 100.0)

    def test_writes_sonar_generic_coverage_for_executable_source_lines(self) -> None:
        report = {
            "data": [
                {
                    "files": [
                        {
                            "filename": "/repo/Sources/Core/A.swift",
                            "segments": [
                                [3, 1, 4, True, True, False],
                                [3, 8, 0, False, False, False],
                                [7, 1, 0, True, True, False],
                                [8, 1, 12, True, True, True],
                            ],
                            "summary": {"lines": {"count": 2, "covered": 1}},
                        },
                        {
                            "filename": "/repo/.build/checkouts/X/Sources/X.swift",
                            "segments": [[1, 1, 0, True, True, False]],
                            "summary": {"lines": {"count": 1, "covered": 0}},
                        },
                    ]
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "coverage.json"
            output_path = Path(directory) / "coverage.xml"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            MODULE.write_sonar_report(report_path, output_path, Path("/repo"))
            root = ET.parse(output_path).getroot()

        self.assertEqual(root.attrib, {"version": "1"})
        file_element = root.find("file")
        self.assertIsNotNone(file_element)
        assert file_element is not None
        self.assertEqual(file_element.attrib["path"], "Sources/Core/A.swift")
        self.assertEqual(
            [line.attrib for line in file_element.findall("lineToCover")],
            [
                {"lineNumber": "3", "covered": "true"},
                {"lineNumber": "7", "covered": "false"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
