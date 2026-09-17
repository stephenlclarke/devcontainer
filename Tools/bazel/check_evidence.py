"""Validate Bazel test evidence; never schedule or execute builds."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlparse


EXPECTED = {
    "//:BazelFrameworkTests": 2,
    "//:DevContainerModelTests": 11,
    "//:DevContainerProjectTests": 1,
    "//:DevContainerStateTests": 7,
    "//Tools/bazel:version_generator_tests": 1,
}

SOURCE_TESTS = {
    "//:DevContainerModelTests": 11,
    "//:DevContainerStateTests": 7,
    "//:DevContainerCoreTests": 13,
    "//:DevContainerCLITests": 13,
    "//:DevContainerProcessTests": 2,
    "//:DevContainerDockerAPITests": 53,
    "//:DevContainerComposeProviderTests": 12,
    "//:DevContainerComposeCLITests": 10,
    "//:DevContainerAppleRuntimeTests": 97,
    "//:DevContainerServiceTests": 5,
}


def expected_tests(suite: str, profile: str) -> dict[str, int]:
    """Retain minimum discovery counts for both explicitly selected graphs."""
    if suite == "qualification":
        return EXPECTED
    result = dict(SOURCE_TESTS)
    if profile == "enhanced":
        result["//:DevContainerAppleRuntimeTests"] = 108
    return result


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


def require_source_hits(text: str, required: set[str]) -> None:
    """Each mandatory production file must have measured and executed lines."""
    covered = set()
    for record in text.split("end_of_record"):
        sources = [line[3:] for line in record.splitlines() if line.startswith("SF:")]
        if not sources or sources[0] not in required:
            continue
        coverage_counts(record)
        covered.add(sources[0])
    if not required <= covered:
        raise ValueError("Coverage is missing migrated production sources")


def validate(events: list[dict], warm: bool, expected: dict[str, int] | None = None) -> dict:
    """Check exact target results, completion, optional cache reuse, and XML."""
    expected = EXPECTED if expected is None else expected
    finished = [event["finished"] for event in events if "finished" in event]
    if len(finished) != 1 or finished[0]["exitCode"].get("code", 0) != 0:
        raise ValueError("Build did not finish successfully")
    summaries = {event["id"]["testSummary"]["label"]: event["testSummary"] for event in events if "testSummary" in event}
    if set(summaries) != set(expected):
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
            counts[label] = case_count(path.read_text(), expected[label])
    if set(counts) != set(expected):
        raise ValueError("Missing test XML evidence")
    return {"test_cases": counts, "all_tests_cached": all(s.get("totalNumCached") == 1 for s in summaries.values())}


def validate_report(events: list[dict], coverage: Path, suite: str = "qualification") -> dict:
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
    # DevContainerProject only declares static metadata; LLVM reports LF=0.
    # Require execution in files with executable bodies, not declaration-only files.
    required = {"Sources/DevContainerModel/BuildInfo.swift", "Sources/DevContainerState/SQLiteStateStore.swift", "Tools/version-generator/main.swift"}
    if suite == "source":
        required = {
            "Sources/DevContainerModel/AtomicFile.swift",
            "Sources/DevContainerState/SQLiteStateStore.swift",
            "Sources/DevContainerCore/DevContainerConfiguration.swift",
            "Sources/DevContainerCLI/PluginCommand.swift",
            "Sources/DevContainerService/DevContainerServiceCommand.swift",
            "Sources/DevContainerAppleRuntime/AppleContainerRuntime.swift",
        }
    require_source_hits(text, required)
    hit, found = coverage_counts(text)
    scope = "qualification subset, not whole project" if suite == "qualification" else "source unit tests; excludes host integration"
    return {"hit": hit, "found": found, "sha256": hashlib.sha256(contents).hexdigest(), "scope": scope}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events", type=Path)
    parser.add_argument("coverage", type=Path, nargs="?", help="Defaults to this invocation's BEP coverage output")
    parser.add_argument("--warm", action="store_true")
    parser.add_argument("--suite", choices=["qualification", "source"], default="qualification")
    parser.add_argument("--profile", choices=["stock", "enhanced"], default="enhanced")
    args = parser.parse_args()
    events = [json.loads(line) for line in args.events.read_text().splitlines()]
    report = validate(events, args.warm, expected_tests(args.suite, args.profile))
    report["runtime_profile"] = args.profile
    coverage = args.coverage
    if coverage is None:
        reports = [log for event in events for log in event.get("buildToolLogs", {}).get("log", []) if log.get("name") == "coverage_report.lcov"]
        if len(reports) != 1:
            raise ValueError("Missing unique Bazel combined coverage output")
        coverage = Path(unquote(urlparse(reports[0]["uri"]).path))
    report["coverage"] = validate_report(events, coverage, args.suite)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
