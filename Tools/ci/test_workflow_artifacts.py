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

        self.assertEqual(checked_blocks, 6)

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

    def test_hosted_swift_tests_have_process_group_timeouts(self) -> None:
        for name in ("ci.yml", "quality.yml", "sonar.yml"):
            contents = (WORKFLOWS / name).read_text(encoding="utf-8")
            self.assertEqual(
                contents.count('SWIFT_TEST_TIMEOUT_SECONDS: "1200"'),
                1,
                name,
            )

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

    def test_write_permissions_are_scoped_to_mutating_jobs(self) -> None:
        codeql = (WORKFLOWS / "codeql.yml").read_text(encoding="utf-8")
        codeql_top_level = codeql[:codeql.index("\njobs:\n")]
        self.assertNotIn("security-events: write", codeql_top_level)
        self.assertIn(
            "    permissions:\n"
            "      contents: read\n"
            "      security-events: write\n",
            codeql,
        )

        prebuilt = (WORKFLOWS / "prebuilt-binaries.yml").read_text(
            encoding="utf-8"
        )
        prebuilt_top_level = prebuilt[:prebuilt.index("\njobs:\n")]
        self.assertIn("permissions: read-all", prebuilt_top_level)
        self.assertNotIn("contents: write", prebuilt_top_level)
        self.assertIn(
            "    permissions:\n"
            "      attestations: write\n"
            "      contents: write\n"
            "      id-token: write\n",
            prebuilt,
        )

        stable = (WORKFLOWS / "stable-release-gate.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("checks: write", stable)

    def test_stable_authority_is_candidate_bound_without_check_write(self) -> None:
        stable = (WORKFLOWS / "stable-release-gate.yml").read_text(
            encoding="utf-8"
        )
        prebuilt = (WORKFLOWS / "prebuilt-binaries.yml").read_text(
            encoding="utf-8"
        )

        for marker in (
            "stable-authority-${{ inputs.ref }}-${{ needs.resolve.outputs.sha }}",
            "candidateSha: $candidate_sha",
            "workflow: \"Stable Release Gate\"",
            "retention-days: 90",
        ):
            self.assertIn(marker, stable)
        for marker in (
            "gh run download",
            "stable-authority-${ref_name}-${sha}",
            '.headBranch == "main"',
            '.candidateSha == $candidate_sha',
            '.workflow == "Stable Release Gate"',
            '.runId == $run_id',
        ):
            self.assertIn(marker, prebuilt)

    def test_parity_container_images_are_digest_pinned(self) -> None:
        fixtures = ROOT / "Tests" / "Parity" / "fixtures"
        references: list[tuple[Path, str]] = []
        pattern = re.compile(r"^\s*(?:FROM|image:)\s+([^\s]+)", re.MULTILINE)

        for path in fixtures.rglob("*"):
            if path.name != "Dockerfile" and path.suffix not in {".yaml", ".yml"}:
                continue
            contents = path.read_text(encoding="utf-8")
            references.extend((path, value) for value in pattern.findall(contents))

        self.assertGreaterEqual(len(references), 10)
        for path, reference in references:
            self.assertRegex(
                reference,
                r"@sha256:[0-9a-f]{64}$",
                f"{path.relative_to(ROOT)} uses a mutable image reference",
            )

        dependabot = (ROOT / ".github" / "dependabot.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("package-ecosystem: docker\n", dependabot)
        self.assertIn("package-ecosystem: docker-compose\n", dependabot)
        self.assertIn(
            "/Tests/Parity/fixtures/D02-dockerfile-config",
            dependabot,
        )
        self.assertIn(
            "/Tests/Parity/fixtures/V01-vscode-end-to-end",
            dependabot,
        )

    def test_release_publication_promotes_only_a_tested_tap_commit(self) -> None:
        contents = (WORKFLOWS / "prebuilt-binaries.yml").read_text(
            encoding="utf-8"
        )
        stage = contents.index("- name: Stage GitHub release assets")
        render = contents.index("- name: Render and validate tap formula")
        commit = contents.index("- name: Commit candidate tap state locally")
        install = contents.index("- name: Install and test tap formula")
        push = contents.index("- name: Push tested tap state")
        finalize = contents.index("- name: Finalize release after tap promotion")

        self.assertLess(stage, render)
        self.assertLess(render, commit)
        self.assertLess(commit, install)
        self.assertLess(install, push)
        self.assertLess(push, finalize)
        self.assertIn("mode=stable-stage", contents)
        self.assertIn("mode=stable-finalize", contents)
        self.assertIn('brew tap "${tap}" "${PWD}/homebrew-tap"', contents)

    def test_every_swift_build_lane_treats_warnings_as_errors(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        package = (ROOT / "scripts" / "package.sh").read_text(encoding="utf-8")
        docs = (ROOT / "scripts" / "make-docs.sh").read_text(encoding="utf-8")
        codeql = (WORKFLOWS / "codeql.yml").read_text(encoding="utf-8")

        self.assertIn(
            "SWIFT_STRICT_FLAGS ?= -Xswiftc -warnings-as-errors",
            makefile,
        )
        self.assertGreaterEqual(makefile.count("$(SWIFT_STRICT_FLAGS)"), 8)
        for name, contents in (
            ("package", package),
            ("documentation", docs),
            ("CodeQL", codeql),
        ):
            self.assertIn(
                "-Xswiftc -warnings-as-errors",
                contents,
                f"{name} build permits compiler warnings",
            )

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
