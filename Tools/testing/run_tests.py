"""Expose individual Python test cases and monotonic timings to Bazel's JUnit reader."""

import argparse
import os
from pathlib import Path
import time
import unittest
import xml.etree.ElementTree as ET


class Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = ET.Element("testsuite", name="runtime-harness")
        self.case = None

    def startTest(self, test):
        super().startTest(test)
        self.started = time.monotonic_ns()
        self.case = ET.SubElement(self.cases, "testcase", name=test.id())

    def stopTest(self, test):
        self.case.set("time", str((time.monotonic_ns() - self.started) / 1e9))
        super().stopTest(test)
        self.case = None

    def event_case(self, test):
        # unittest reports class/module fixture errors without startTest. Give
        # them their own identity instead of modifying the preceding test.
        if self.case is not None:
            return self.case
        return ET.SubElement(self.cases, "testcase", name=test.id(), classname="fixture", time="0")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        ET.SubElement(self.case, "failure").text = self._exc_info_to_string(err, test)

    def addError(self, test, err):
        super().addError(test, err)
        ET.SubElement(self.event_case(test), "error").text = self._exc_info_to_string(err, test)

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        ET.SubElement(self.event_case(test), "skipped", message=reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        ET.SubElement(self.case, "skipped", message="Expected failure is not passing conformance evidence")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        ET.SubElement(self.case, "failure", message="Unexpected success: expected-failure annotation is stale")

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            ET.SubElement(self.case, "failure", message=str(subtest)).text = self._exc_info_to_string(err, test)


def main(directory: Path = Path(__file__).parent):
    suite = unittest.defaultTestLoader.discover(str(directory), pattern="test_*.py")
    result = unittest.TextTestRunner(resultclass=Result).run(suite)
    output = os.environ.get("XML_OUTPUT_FILE")
    if output:
        for key, value in {"tests": len(result.cases), "failures": len(result.cases.findall("testcase/failure")),
                           "errors": len(result.cases.findall("testcase/error")),
                           "skipped": len(result.cases.findall("testcase/skipped"))}.items():
            result.cases.set(key, str(value))
        ET.ElementTree(result.cases).write(output, encoding="utf-8", xml_declaration=True)
    raise SystemExit(0 if result.testsRun > 0 and result.wasSuccessful() and not result.skipped and not result.expectedFailures else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(__file__).parent)
    main(parser.parse_args().directory)
