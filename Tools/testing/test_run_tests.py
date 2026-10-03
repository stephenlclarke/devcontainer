"""The JUnit report cannot conceal a skip, failure, error or unexpected success."""

import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from run_tests import Result, main


class ReportTests(unittest.TestCase):
    def test_empty_discovery_cannot_pass(self):
        with patch("run_tests.unittest.defaultTestLoader.discover", return_value=unittest.TestSuite()), \
                patch.dict(os.environ, XML_OUTPUT_FILE=""), self.assertRaises(SystemExit) as failure:
            main()
        self.assertEqual(failure.exception.code, 1)

    def test_class_fixture_errors_have_their_own_case_not_the_previous_test(self):
        class Successful(unittest.TestCase):
            def test_success(self):
                # Intentionally successful control case for fixture-error XML.
                pass

        class SetupError(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise ValueError("setup failed")

            def test_not_reached(self):
                self.fail("setup should prevent test execution")

        class TeardownError(Successful):
            @classmethod
            def tearDownClass(cls):
                raise ValueError("teardown failed")

        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(kind)
                                   for kind in (Successful, SetupError, TeardownError))
        report = unittest.TextTestRunner(stream=io.StringIO(), resultclass=Result).run(suite)
        self.assertFalse(report.wasSuccessful())
        errors = report.cases.findall("testcase/error")
        self.assertEqual(len(errors), 2)
        self.assertEqual(len(report.cases), 4)
        self.assertIsNone(report.cases[0].find("error"))
        self.assertTrue(report.cases[1].attrib["name"].startswith("setUpClass"))
        self.assertTrue(report.cases[3].attrib["name"].startswith("tearDownClass"))

    def test_all_nonpassing_outcomes_are_visible_in_xml(self):
        class Cases(unittest.TestCase):
            def test_failure(self):
                self.fail("deliberate assertion failure")

            def test_error(self):
                raise ValueError("deliberate error")

            @unittest.skip("deliberate skip")
            def test_skip(self):
                self.fail("skipped body must not run")

            @unittest.expectedFailure
            def test_expected_failure(self):
                self.fail("deliberate expected failure")

            @unittest.expectedFailure
            def test_unexpected_success(self):
                # Intentionally successful body to detect stale expected failures.
                pass

            def test_subtest_failure(self):
                with self.subTest(value=1):
                    self.fail("deliberate subtest failure")

        report = unittest.TextTestRunner(stream=io.StringIO(), resultclass=Result).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(Cases))
        self.assertFalse(report.wasSuccessful())
        self.assertEqual(len(report.cases.findall("testcase/failure")), 3)
        self.assertEqual(len(report.cases.findall("testcase/error")), 1)
        self.assertEqual(len(report.cases.findall("testcase/skipped")), 2)
        self.assertTrue(all(float(case.attrib["time"]) >= 0 for case in report.cases))

    def test_main_writes_junit_and_rejects_skipped_only_suites(self):
        class Cases(unittest.TestCase):
            @unittest.skip("not conformance proof")
            def test_skip(self):
                self.fail("skipped body must not run")

        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            output = Path(directory) / "junit.xml"
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(Cases)
            with patch("run_tests.unittest.defaultTestLoader.discover", return_value=suite), \
                    patch.dict(os.environ, XML_OUTPUT_FILE=str(output)), self.assertRaises(SystemExit) as failure:
                main()
            self.assertEqual(failure.exception.code, 1)
            root = ET.parse(output).getroot()
            self.assertEqual(root.attrib["tests"], "1")
            self.assertEqual(root.attrib["skipped"], "1")


if __name__ == "__main__":
    unittest.main()
