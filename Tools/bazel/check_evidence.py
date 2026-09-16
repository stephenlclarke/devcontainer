"""Validate Bazel qualification evidence; never schedule or execute builds."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlparse


EXPECTED = {
    "//:BazelFrameworkTests": 2,
    "//:DevContainerModelTests": 8,
    "//:DevContainerProjectTests": 1,
    "//:DevContainerStateTests": 7,
    "//Tools/bazel:version_generator_tests": 1,
}


def coverage_counts(text: str) -> tuple[int, int]:
    """Reject an empty or impossible LCOV report instead of accepting zero tests."""
    records = [line.split(":", 1) for line in text.splitlines() if line.startswith(("LF:", "LH:"))]
    found = sum(int(value) for key, value in records if key == "LF")
    hit = sum(int(value) for key, value in records if key == "LH")
    if not 0 < hit <= found:
        raise ValueError("Coverage must contain positive measured and covered line counts")
    return hit, found


def case_count(text: str, minimum: int) -> int:
    """Count actual cases, rejecting failures and skipped discovery probes."""
    root = ET.fromstring(text)
    cases = list(root.iter("testcase"))
    if len(cases) < minimum or any(element.tag in {"failure", "error", "skipped"} for element in root.iter()):
        raise ValueError("Missing, failed or skipped qualification test cases")
    return len(cases)


def validate(events: list[dict], warm: bool) -> dict:
    """Check exact target results, completion, optional cache reuse, and XML."""
    finished = [event["finished"] for event in events if "finished" in event]
    if len(finished) != 1 or finished[0]["exitCode"].get("code", 0) != 0:
        raise ValueError("Build did not finish successfully")
    summaries = {event["id"]["testSummary"]["label"]: event["testSummary"] for event in events if "testSummary" in event}
    if set(summaries) != set(EXPECTED):
        raise ValueError("Qualification target set is incomplete or unexpected")
    for summary in summaries.values():
        if summary.get("overallStatus") != "PASSED" or summary.get("totalRunCount") != 1:
            raise ValueError("Qualification must pass on its first single attempt")
        if warm and summary.get("totalNumCached") != 1:
            raise ValueError("Warm qualification unexpectedly executed a test")
    counts = {}
    for event in events:
        if "testResult" not in event:
            continue
        label = event["id"]["testResult"]["label"]
        for output in event["testResult"].get("testActionOutput", []):
            if output["name"] != "test.xml":
                continue
            uri = urlparse(output["uri"])
            path = Path(unquote(uri.path)).resolve()
            if uri.scheme != "file" or uri.netloc or not path.is_relative_to("/Volumes/SSD/cf/bazel"):
                raise ValueError("Test evidence must be a local SSD file")
            counts[label] = case_count(path.read_text(), EXPECTED[label])
    if set(counts) != set(EXPECTED):
        raise ValueError("Missing test XML evidence")
    return {"test_cases": counts, "all_tests_cached": all(s.get("totalNumCached") == 1 for s in summaries.values())}


def validate_report(events: list[dict], coverage: Path) -> dict:
    """Bind coverage to this invocation and require the migrated source modules."""
    starts = [event["started"] for event in events if "started" in event]
    if len(starts) != 1 or starts[0].get("command") != "coverage":
        raise ValueError("Evidence must come from a coverage invocation")
    reports = [log for event in events for log in event.get("buildToolLogs", {}).get("log", []) if log.get("name") == "coverage_report.lcov"]
    if len(reports) != 1:
        raise ValueError("Missing unique Bazel combined coverage output")
    uri = urlparse(reports[0]["uri"])
    if uri.scheme != "file" or uri.netloc or Path(unquote(uri.path)).resolve() != coverage.resolve():
        raise ValueError("Coverage path does not match the invocation")
    contents = coverage.read_bytes()
    text = contents.decode("utf-8")
    sources = {line[3:] for line in text.splitlines() if line.startswith("SF:")}
    required = {"Sources/DevContainerModel/BuildInfo.swift", "Sources/DevContainerState/SQLiteStateStore.swift", "Sources/DevContainerCore/DevContainerProject.swift", "Tools/version-generator/main.swift"}
    if not required <= sources:
        raise ValueError("Coverage is missing migrated production sources")
    hit, found = coverage_counts(text)
    return {"hit": hit, "found": found, "sha256": hashlib.sha256(contents).hexdigest(), "scope": "qualification subset, not whole project"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events", type=Path)
    parser.add_argument("coverage", type=Path, nargs="?", help="Defaults to this invocation's BEP coverage output")
    parser.add_argument("--warm", action="store_true")
    args = parser.parse_args()
    events = [json.loads(line) for line in args.events.read_text().splitlines()]
    report = validate(events, args.warm)
    coverage = args.coverage
    if coverage is None:
        reports = [log for event in events for log in event.get("buildToolLogs", {}).get("log", []) if log.get("name") == "coverage_report.lcov"]
        if len(reports) != 1:
            raise ValueError("Missing unique Bazel combined coverage output")
        coverage = Path(unquote(urlparse(reports[0]["uri"]).path))
    report["coverage"] = validate_report(events, coverage)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
