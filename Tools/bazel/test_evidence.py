"""Regression tests for evidence false-green rejection."""

import unittest
from pathlib import Path

from check_evidence import case_count, coverage_counts, expected_tests, require_source_hits, validate, validate_report


class EvidenceTests(unittest.TestCase):
    def test_profile_discovery_inventory(self) -> None:
        stock = expected_tests("source", "stock")
        enhanced = expected_tests("source", "enhanced")
        self.assertEqual(sum(stock.values()), 221)
        self.assertEqual(sum(enhanced.values()), 232)
        self.assertEqual(set(stock), set(enhanced))

    def test_required_sources_each_have_hits(self) -> None:
        require_source_hits("SF:first\nLF:2\nLH:1\nend_of_record\nSF:second\nLF:3\nLH:1\nend_of_record", {"first", "second"})
        for record in ["SF:second\nLF:2\nLH:0\nend_of_record", "SF:third\nLF:2\nLH:1\nend_of_record"]:
            with self.subTest(record=record), self.assertRaises(ValueError):
                require_source_hits("SF:first\nLF:10\nLH:10\nend_of_record\n" + record, {"first", "second"})

    def test_nonempty_coverage(self) -> None:
        self.assertEqual(coverage_counts("LF:10\nLH:7\nLF:3\nLH:2\n"), (9, 13))

    def test_missing_empty_and_impossible_coverage(self) -> None:
        for report in ["", "LF:0\nLH:0", "LF:5\nLH:0", "LF:5\nLH:6"]:
            with self.subTest(report=report), self.assertRaises(ValueError):
                coverage_counts(report)

    def test_nested_discovery(self) -> None:
        self.assertEqual(case_count('<testsuites><testsuite><testcase name="one"/></testsuite><testcase name="two"/></testsuites>', 2), 2)

    def test_missing_failed_or_skipped_cases(self) -> None:
        for xml in ["<testsuite/>", "<testsuite><testcase/></testsuite>", "<testsuite><testcase><failure/></testcase><testcase/></testsuite>", "<testsuite><testcase><skipped/></testcase><testcase/></testsuite>"]:
            with self.subTest(xml=xml), self.assertRaises(ValueError):
                case_count(xml, 2)

    def test_incomplete_failed_or_missing_targets(self) -> None:
        for events in [[], [{"finished": {"exitCode": {"code": 3}}}], [{"finished": {"exitCode": {"code": 0}}}]]:
            with self.subTest(events=events), self.assertRaises(ValueError):
                validate(events, False)

    def test_coverage_must_belong_to_invocation(self) -> None:
        for events in [[], [{"started": {"command": "test"}}], [{"started": {"command": "coverage"}}], [{"started": {"command": "coverage"}}, {"buildToolLogs": {"log": [{"name": "coverage_report.lcov", "uri": "file:///different/report"}]}}]]:
            with self.subTest(events=events), self.assertRaises(ValueError):
                validate_report(events, Path("/requested/report"))


if __name__ == "__main__":
    unittest.main()
