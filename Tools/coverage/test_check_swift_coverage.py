"""Tests for the Swift coverage gate."""

from __future__ import annotations

import importlib.util
import json
import subprocess
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
                                [3, 2, 0, False, False, False],
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
                                [7, 2, 0, False, False, False],
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

    def test_writes_lcov_with_the_same_unique_line_semantics(self) -> None:
        report = {
            "data": [
                {
                    "files": [
                        {
                            "filename": "/repo/Sources/Core/A.swift",
                            "segments": [
                                [3, 1, 0, True, True, False],
                                [3, 8, 4, True, True, False],
                                [3, 9, 0, False, False, False],
                                [7, 1, 0, True, True, False],
                            ],
                        }
                    ]
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "coverage.json"
            output_path = Path(directory) / "coverage.lcov"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            MODULE.write_lcov_report(report_path, output_path, Path("/repo"))
            output = output_path.read_text(encoding="utf-8")

        self.assertEqual(
            output,
            "\n".join(
                [
                    "TN:devcontainer",
                    "SF:Sources/Core/A.swift",
                    "DA:3,4",
                    "DA:7,0",
                    "LF:2",
                    "LH:1",
                    "end_of_record",
                    "",
                ]
            ),
        )

    def test_parses_added_lines_from_zero_context_diff(self) -> None:
        diff = """\
diff --git a/Sources/Core/A.swift b/Sources/Core/A.swift
--- a/Sources/Core/A.swift
+++ b/Sources/Core/A.swift
@@ -2,0 +3,2 @@
+first
+second
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-before
+after
"""
        self.assertEqual(
            MODULE.diff_changed_lines(diff),
            {"Sources/Core/A.swift": {3, 4}},
        )

    def test_changed_coverage_intersects_diff_with_executable_lines(self) -> None:
        report = {
            "data": [
                {
                    "files": [
                        {
                            "filename": "/repo/Sources/Core/A.swift",
                            "segments": [
                                [3, 1, 4, True, True, False],
                                [3, 2, 0, False, False, False],
                                [4, 1, 0, True, True, False],
                                [4, 2, 0, False, False, False],
                                [5, 1, 0, False, False, False],
                            ],
                        },
                        {
                            "filename": "/repo/Sources/Core/Declarations.swift",
                            "segments": [],
                        },
                    ]
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "coverage.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            result = MODULE.changed_coverage(
                report_path,
                Path("/repo"),
                {
                    "Sources/Core/A.swift": {3, 4, 5},
                    "Sources/Core/Declarations.swift": {1},
                },
            )

        self.assertEqual(
            result,
            (1, 2, [("Sources/Core/A.swift", 4)], []),
        )

    def test_changed_coverage_fails_closed_for_unreported_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "coverage.json"
            report_path.write_text('{"data": []}', encoding="utf-8")
            result = MODULE.changed_coverage(
                report_path,
                Path("/repo"),
                {"Sources/Core/Missing.swift": {1}},
            )

        self.assertEqual(result, (0, 0, [], ["Sources/Core/Missing.swift"]))

    def test_line_inherits_active_count_from_spanning_llvm_region(self) -> None:
        item = {
            "segments": [
                [203, 49, 2, True, True, False],
                [204, 53, 0, True, True, False],
                [205, 10, 0, False, False, False],
            ]
        }

        self.assertEqual(
            MODULE.sonar_lines(item),
            {203: 2, 204: 2},
        )

    def test_git_changed_lines_uses_merge_base_and_source_hunks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            subprocess.run(["git", "init", "-q", repository], check=True)
            subprocess.run(
                ["git", "-C", repository, "config", "user.email", "test@example.com"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", repository, "config", "user.name", "Coverage Test"],
                check=True,
            )
            source = repository / "Sources" / "Core" / "A.swift"
            source.parent.mkdir(parents=True)
            source.write_text("let first = 1\n", encoding="utf-8")
            subprocess.run(["git", "-C", repository, "add", "."], check=True)
            subprocess.run(
                ["git", "-C", repository, "commit", "-qm", "base"],
                check=True,
            )
            base = subprocess.run(
                ["git", "-C", repository, "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            source.write_text("let first = 1\nlet second = 2\n", encoding="utf-8")
            subprocess.run(["git", "-C", repository, "add", "."], check=True)
            subprocess.run(
                ["git", "-C", repository, "commit", "-qm", "change"],
                check=True,
            )

            merge_base, changed = MODULE.git_changed_lines(
                repository,
                base,
                "HEAD",
            )

        self.assertEqual(merge_base, base)
        self.assertEqual(changed, {"Sources/Core/A.swift": {2}})


if __name__ == "__main__":
    unittest.main()
