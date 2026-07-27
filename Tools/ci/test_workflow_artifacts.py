"""Regression tests for GitHub Actions artifact evidence contracts."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
STEP_BOUNDARY = re.compile(r"^ {6}- name:", re.MULTILINE)
SMOKE_FIXTURE = ROOT / "Tools" / "ci" / "docker-compose-smoke-fixture.sh"


class WorkflowArtifactTests(unittest.TestCase):
    def test_all_external_actions_are_immutable_sha_pinned(self) -> None:
        uses_pattern = re.compile(r"^\s+uses:\s+([^@\s]+)@([^\s#]+)", re.MULTILINE)
        checked = 0

        for workflow in WORKFLOWS.glob("*.yml"):
            contents = workflow.read_text(encoding="utf-8")
            for action, revision in uses_pattern.findall(contents):
                if action.startswith("./"):
                    continue
                checked += 1
                self.assertRegex(
                    revision,
                    r"^[0-9a-f]{40}$",
                    f"{workflow.name}: {action} is not immutable-SHA pinned",
                )

        self.assertGreater(checked, 20)

    def test_supply_chain_workflows_are_fail_closed(self) -> None:
        dependency_review = (
            WORKFLOWS / "dependency-review.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("fail-on-severity: moderate", dependency_review)
        self.assertIn(
            "fail-on-scopes: runtime, development, unknown",
            dependency_review,
        )
        self.assertIn("license-check: true", dependency_review)
        self.assertNotIn("warn-only: true", dependency_review)

        scorecard = (WORKFLOWS / "scorecard.yml").read_text(encoding="utf-8")
        self.assertIn("publish_results: true", scorecard)
        self.assertIn("security-events: write", scorecard)
        self.assertIn("id-token: write", scorecard)
        self.assertIn("persist-credentials: false", scorecard)
        self.assertIn("results_format: sarif", scorecard)

    def test_hidden_build_evidence_is_explicitly_included(self) -> None:
        checked_blocks = 0

        for workflow in WORKFLOWS.glob("*.yml"):
            contents = workflow.read_text(encoding="utf-8")
            boundaries = [match.start() for match in STEP_BOUNDARY.finditer(contents)]
            boundaries.append(len(contents))

            for start, end in zip(boundaries, boundaries[1:]):
                block = contents[start:end]
                if "uses: actions/upload-artifact@" not in block:
                    continue
                if ".build/" not in block:
                    continue

                checked_blocks += 1
                self.assertIn(
                    "include-hidden-files: true",
                    block,
                    f"{workflow.name} omits hidden .build evidence",
                )

        self.assertEqual(checked_blocks, 5)

    def test_hosted_workflows_cancel_superseded_runs(self) -> None:
        workflows = (
            "ci.yml",
            "codeql.yml",
            "dependency-review.yml",
            "docs.yml",
            "homebrew.yml",
            "quality.yml",
            "scorecard.yml",
            "sonar.yml",
        )
        for name in workflows:
            contents = (WORKFLOWS / name).read_text(encoding="utf-8")
            self.assertIn("\nconcurrency:\n", contents, name)
            self.assertIn("  cancel-in-progress: true\n", contents, name)

    def test_codeql_traces_first_party_sources_after_dependency_build(self) -> None:
        contents = (WORKFLOWS / "codeql.yml").read_text(encoding="utf-8")
        prime = contents.index("- name: Prime resolved SwiftPM dependencies")
        initialize = contents.index("- name: Initialize CodeQL")
        recompile = contents.index("- name: Build first-party Swift")
        analyze = contents.index("- name: Analyze")

        self.assertIn("timeout-minutes: 90", contents)
        self.assertIn("build-mode: manual", contents)
        self.assertEqual(
            contents.count("swift build --disable-automatic-resolution --arch arm64"),
            2,
        )
        self.assertIn("find Sources -type f -name '*.swift' -exec touch {} +", contents)
        self.assertLess(prime, initialize)
        self.assertLess(initialize, recompile)
        self.assertLess(recompile, analyze)

    def test_docker_compose_smoke_fixture_is_strict(self) -> None:
        success = subprocess.run(
            [SMOKE_FIXTURE, "compose", "version"],
            capture_output=True,
            text=True,
        )
        invalid = subprocess.run(
            [SMOKE_FIXTURE, "version"],
            capture_output=True,
            text=True,
        )

        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(success.stdout, '{"Version":"fixture"}\n')
        self.assertEqual(invalid.returncode, 64)
        self.assertIn("expected: compose version", invalid.stderr)


if __name__ == "__main__":
    unittest.main()
