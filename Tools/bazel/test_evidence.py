"""Regression tests for evidence false-green rejection."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from check_evidence import case_count, coverage_counts, expected_tests, load_policy, require_coverage_logs, require_source_hits, validate, validate_report


class EvidenceTests(unittest.TestCase):
    def test_default_coverage_suites_reject_out_of_range_counters(self) -> None:
        required = ["Sources/DevContainerModel/BuildInfo.swift", "Sources/DevContainerState/SQLiteStateStore.swift",
                    "Tools/version-generator/main.swift", "Sources/DevContainerModel/AtomicFile.swift",
                    "Sources/DevContainerCore/DevContainerConfiguration.swift", "Sources/DevContainerCLI/PluginCommand.swift",
                    "Sources/DevContainerService/DevContainerServiceCommand.swift",
                    "Sources/DevContainerAppleRuntime/AppleContainerRuntime.swift"]
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            coverage = Path(directory) / "coverage.lcov"
            log = Path(directory) / "test.log"
            log.write_text("All tests passed\n")
            events = [{"started": {"command": "coverage"}},
                      {"testResult": {"testActionOutput": [{"name": "test.log", "uri": log.as_uri()}]}},
                      {"buildToolLogs": {"log": [{"name": "coverage_report.lcov", "uri": coverage.as_uri()}]}}]
            valid = "".join(f"SF:{source}\nDA:1,1\nLF:1\nLH:1\nend_of_record\n" for source in required)
            for suite in ("source", "qualification"):
                with self.subTest(suite=suite):
                    coverage.write_text(valid)
                    self.assertEqual(validate_report(events, coverage, suite)["found"], len(required))
                    coverage.write_text(valid.replace("DA:1,1", "DA:1,18446744073709551615", 1))
                    with self.assertRaisesRegex(ValueError, "LCOV line"):
                        validate_report(events, coverage, suite)

    def test_coverage_merger_warning_is_a_gate_failure(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            log = Path(directory) / "test.log"
            log.write_text("All tests passed\n")
            events = [{"testResult": {"testActionOutput": [{"name": "test.log", "uri": log.as_uri()}]}}]
            require_coverage_logs(events)
            log.write_text("WARNING: Tracefile contains an invalid number on DA line DA:521,18446744073709551615\n")
            with self.assertRaisesRegex(ValueError, "tracefile"):
                require_coverage_logs(events)
            for outputs in ([], events[0]["testResult"]["testActionOutput"] * 2,
                            [{"name": "test.log", "uri": "https://example.com/test.log"}]):
                with self.subTest(outputs=outputs), self.assertRaises(ValueError):
                    require_coverage_logs([{"testResult": {"testActionOutput": outputs}}])

    def test_combined_inventory_adds_components_without_changing_unit_evidence(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            path = Path(directory) / "policy.json"
            policy = {"schema": 2, "scope": "unit only", "component_scope": "unit plus CLI, no runtime",
                      "source_roots": ["Sources"], "profiles": {
                          profile: {"tests": {f"//:{profile}": minimum},
                                    "component_tests": {"//Tools/bazel:cli_contracts": 12},
                                    "required_sources": ["Sources/main.swift"]}
                          for profile, minimum in [("stock", 3), ("enhanced", 4)]}}
            path.write_text(json.dumps(policy))
            for profile, minimum in [("stock", 3), ("enhanced", 4)]:
                unit = load_policy(path, profile)
                combined = load_policy(path, profile, "unit-cli")
                self.assertEqual(unit["tests"], {f"//:{profile}": minimum})
                self.assertEqual(combined["tests"], {**unit["tests"], "//Tools/bazel:cli_contracts": 12})
                self.assertEqual((unit["inventory"], unit["scope"]), ("unit", "unit only"))
                self.assertEqual((combined["inventory"], combined["scope"]), ("unit-cli", "unit plus CLI, no runtime"))
                self.assertEqual(unit["sha256"], combined["sha256"])
            for components in ({}, {"//:stock": 1}, {"//:Extra": 0}, {"//:Extra": True}, {"@external//:test": 1}, None):
                changed = copy.deepcopy(policy)
                changed["profiles"]["stock"]["component_tests"] = components
                path.write_text(json.dumps(changed))
                with self.subTest(components=components), self.assertRaises(ValueError):
                    load_policy(path, "enhanced")
            for scope in ("", None, []):
                path.write_text(json.dumps({**policy, "component_scope": scope}))
                with self.subTest(scope=scope), self.assertRaises(ValueError):
                    load_policy(path, "stock", "unit-cli")

    def test_consumer_policy_is_explicit_and_profile_specific(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            path = Path(directory) / "policy.json"
            policy = {"schema": 1, "scope": "unit only", "source_roots": ["Sources", "Tools/helper"], "profiles": {
                "stock": {"tests": {"//:StockTests": 3}, "required_sources": ["Sources/Stock.swift"]},
                "enhanced": {"tests": {"//:EnhancedTests": 4}, "required_sources": ["Tools/helper/main.go"]},
            }}
            path.write_text(json.dumps(policy))
            self.assertEqual(load_policy(path, "stock")["tests"], {"//:StockTests": 3})
            enhanced = load_policy(path, "enhanced")
            self.assertEqual(enhanced["tests"], {"//:EnhancedTests": 4})
            self.assertEqual(len(enhanced["sha256"]), 64)
            with self.assertRaisesRegex(ValueError, "requested inventory"):
                load_policy(path, "stock", "unit-cli")
            invalid = []
            for key, value in [("schema", 2), ("scope", ""), ("source_roots", []), ("source_roots", ["../Sources"]), ("source_roots", ["/Sources"]), ("source_roots", ["Sources", "Sources"])]:
                changed = copy.deepcopy(policy)
                changed[key] = value
                invalid.append(changed)
            for key, value in [("tests", {}), ("tests", {"//:StockTests": 0}), ("tests", {"//:StockTests": True}), ("tests", {"@external//:test": 1}), ("required_sources", []), ("required_sources", ["Tests/helper.swift"]), ("required_sources", ["Sources/../escape"])]:
                changed = copy.deepcopy(policy)
                changed["profiles"]["stock"][key] = value
                invalid.append(changed)
            for changed in invalid:
                path.write_text(json.dumps(changed))
                with self.subTest(policy=changed), self.assertRaises(ValueError):
                    load_policy(path, "enhanced")

    def test_consumer_xml_inventory_and_warm_reuse(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            xml = Path(directory) / "test.xml"
            xml.write_text('<testsuite><testcase name="one"/><testcase name="two"/></testsuite>')
            events = [{"finished": {"exitCode": {"code": 0}}},
                      {"id": {"testSummary": {"label": "//:Consumer"}}, "testSummary": {"overallStatus": "PASSED", "totalRunCount": 1, "totalNumCached": 1}},
                      {"id": {"testResult": {"label": "//:Consumer"}}, "testResult": {"testActionOutput": [{"name": "test.xml", "uri": xml.as_uri()}]}}]
            self.assertEqual(validate(events, True, {"//:Consumer": 2})["test_cases"], {"//:Consumer": 2})
            # A unit result is not a complete combined result and an extra
            # component cannot silently change the meaning of a unit receipt.
            with self.assertRaisesRegex(ValueError, "target set"):
                validate(events, False, {"//:Consumer": 2, "//:CLI": 12})
            extra = {"id": {"testSummary": {"label": "//:CLI"}}, "testSummary": {"overallStatus": "PASSED", "totalRunCount": 1}}
            with self.assertRaisesRegex(ValueError, "target set"):
                validate([*events, extra], False, {"//:Consumer": 2})
            with self.assertRaises(ValueError):
                validate(events, False, {"//:Consumer": 3})
            events[1]["testSummary"]["totalNumCached"] = 0
            with self.assertRaises(ValueError):
                validate(events, True, {"//:Consumer": 2})
            xml.write_text('<testsuite/>')
            with self.assertRaises(ValueError):
                validate(events, False, {"//:Consumer": 2})

    def test_consumer_coverage_validates_real_lines_and_required_sources(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            coverage = Path(directory) / "coverage.lcov"
            data = "SF:Tools/helper/main.go\nDA:1,2\nDA:2,0\nLF:2\nLH:1\nend_of_record\n"
            coverage.write_text(data)
            events = [{"started": {"command": "coverage"}}, {"buildToolLogs": {"log": [{"name": "coverage_report.lcov", "uri": coverage.as_uri()}]}}]
            policy = {"required_sources": ["Tools/helper/main.go"], "source_roots": ["Tools/helper"], "scope": "helper unit only"}
            result = validate_report(events, coverage, "source", policy)
            self.assertEqual((result["hit"], result["found"], result["scope"]), (1, 2, "helper unit only"))
            for changed in [data.replace("LH:1", "LH:2"), data.replace("Tools/helper/main.go", "Tests/main.go")]:
                coverage.write_text(changed)
                with self.assertRaises(ValueError):
                    validate_report(events, coverage, "source", policy)

    def test_profile_discovery_inventory(self) -> None:
        stock = expected_tests("source", "stock")
        enhanced = expected_tests("source", "enhanced")
        self.assertEqual(sum(stock.values()), 223)
        self.assertEqual(sum(enhanced.values()), 234)
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
