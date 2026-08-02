"""Regression tests for GitHub Actions artifact evidence contracts."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
STEP_BOUNDARY = re.compile(r"^ {6}- name:", re.MULTILINE)
SMOKE_FIXTURE = ROOT / "Tools" / "ci" / "docker-compose-smoke-fixture.sh"
GITHUB_HOSTED_RUNNER_LABELS = frozenset(
    {
        "macos-26",
        "ubuntu-24.04",
        "ubuntu-latest",
    }
)
RUBY_RUNS_ON_SELECTORS = r"""
require "json"
require "yaml"

selectors = {}
ARGV.each do |path|
  document = YAML.safe_load(
    File.read(path, encoding: "UTF-8"),
    permitted_classes: [],
    permitted_symbols: [],
    aliases: true
  )
  jobs = document.fetch("jobs")
  raise "jobs must be a mapping: #{path}" unless jobs.is_a?(Hash)
  selectors[path] = jobs.filter_map do |name, job|
    next unless job.is_a?(Hash)
    if job.key?("uses")
      {
        "job" => name.to_s,
        "reusable_workflow" => job.fetch("uses")
      }
    elsif job.key?("runs-on")
      {"job" => name.to_s, "selector" => job.fetch("runs-on")}
    end
  end
end
STDOUT.write(JSON.generate(selectors))
"""


def workflow_files(directory: Path = WORKFLOWS) -> list[Path]:
    """Return every supported GitHub Actions workflow file."""
    return sorted((*directory.glob("*.yml"), *directory.glob("*.yaml")))


def workflow_runner_labels(
    workflows: list[Path],
) -> list[tuple[Path, str, list[str]]]:
    """Parse workflow job selectors with Ruby's standard YAML implementation."""
    result = subprocess.run(
        [
            "ruby",
            "-e",
            RUBY_RUNS_ON_SELECTORS,
            *(str(path) for path in workflows),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    document = json.loads(result.stdout)
    parsed: list[tuple[Path, str, list[str]]] = []
    for workflow in workflows:
        for entry in document[str(workflow)]:
            if "reusable_workflow" in entry:
                raise ValueError(
                    f"{workflow.name}:{entry['job']} uses an uninspected "
                    "reusable workflow"
                )
            selector = entry["selector"]
            if isinstance(selector, str):
                labels = [selector]
            elif isinstance(selector, list) and all(
                isinstance(label, str) for label in selector
            ):
                labels = selector
            else:
                raise ValueError(
                    f"{workflow.name}:{entry['job']} has an opaque runs-on selector"
                )
            parsed.append((workflow, entry["job"], labels))
    return parsed


def is_self_hosted_selector(labels: list[str]) -> bool:
    """Require designated routing unless a selector is known to be hosted."""
    if "self-hosted" in labels:
        return True
    if any("${{" in label for label in labels):
        raise ValueError("dynamic runs-on selectors cannot prove hosted isolation")
    return not (
        len(labels) == 1 and labels[0] in GITHUB_HOSTED_RUNNER_LABELS
    )


class WorkflowArtifactTests(unittest.TestCase):
    def test_all_external_actions_are_immutable_sha_pinned(self) -> None:
        uses_pattern = re.compile(r"^\s+uses:\s+([^@\s]+)@([^\s#]+)", re.MULTILINE)
        checked = 0

        for workflow in workflow_files():
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

    def test_repeated_external_actions_use_one_revision(self) -> None:
        uses_pattern = re.compile(r"^\s+uses:\s+([^@\s]+)@([^\s#]+)", re.MULTILINE)
        revisions: dict[str, set[str]] = {}
        occurrences: dict[str, int] = {}

        for workflow in workflow_files():
            for action, revision in uses_pattern.findall(
                workflow.read_text(encoding="utf-8")
            ):
                if action.startswith("./"):
                    continue
                revisions.setdefault(action, set()).add(revision)
                occurrences[action] = occurrences.get(action, 0) + 1

        repeated = {
            action: values
            for action, values in revisions.items()
            if occurrences[action] > 1
        }
        self.assertTrue(repeated)
        for action, values in repeated.items():
            self.assertEqual(
                len(values),
                1,
                f"{action} uses inconsistent immutable revisions: {sorted(values)}",
            )

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
        pinned_uploader = (
            ROOT / "Tools" / "ci" / "upload-artifact-pinned.sh"
        ).read_text(encoding="utf-8")

        for workflow in workflow_files():
            contents = workflow.read_text(encoding="utf-8")
            boundaries = [match.start() for match in STEP_BOUNDARY.finditer(contents)]
            boundaries.append(len(contents))

            for start, end in zip(boundaries, boundaries[1:]):
                block = contents[start:end]
                uses_action = "uses: actions/upload-artifact@" in block
                uses_pinned_uploader = (
                    "uses: ./Tools/ci/upload-artifact-action" in block
                )
                if not uses_action and not uses_pinned_uploader:
                    continue
                if ".build/" not in block:
                    continue

                checked_blocks += 1
                if uses_action:
                    self.assertIn(
                        "include-hidden-files: true",
                        block,
                        f"{workflow.name} omits hidden .build evidence",
                    )
                else:
                    self.assertIn(
                        "INPUT_INCLUDE-HIDDEN-FILES=true",
                        pinned_uploader,
                        "pinned uploader omits hidden .build evidence",
                    )

        self.assertEqual(checked_blocks, 6)

    def test_self_hosted_parity_lane_avoids_runner_action_downloads(self) -> None:
        contents = (WORKFLOWS / "parity.yml").read_text(encoding="utf-8")
        lane = contents[contents.index("  lane:\n"):contents.index("  compare:\n")]

        self.assertNotRegex(lane, r"\n        uses:\s+[^./]")
        self.assertIn(
            'git -C "${GITHUB_WORKSPACE}" fetch --no-tags --depth=1 origin',
            lane,
        )
        self.assertIn("uses: ./Tools/ci/upload-artifact-action", lane)

    def test_self_hosted_jobs_require_the_designated_mbp(self) -> None:
        checked = 0

        for workflow, job, labels in workflow_runner_labels(workflow_files()):
            if not is_self_hosted_selector(labels):
                continue
            checked += 1
            self.assertIn(
                "devcontainer-designated-mbp",
                labels,
                f"{workflow.name}:{job} can route outside the designated MBP",
            )
            self.assertNotIn("devcontainer-ultuk2m30000", labels)

        self.assertEqual(checked, 2)

    def test_runner_specification_parser_covers_yaml_forms(self) -> None:
        contents = """
jobs:
  scalar:
    runs-on: self-hosted
  flow:
    runs-on: [self-hosted, macOS]
  next-line-flow:
    runs-on:
      [self-hosted, macOS]
  block:
    'runs-on':
      - self-hosted
      - macOS
  quoted:
    "runs-on": [
      self-hosted,
      "macOS,ARM64"
    ]
"""
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "forms.yml"
            workflow.write_text(contents, encoding="utf-8")
            parsed = {
                job: labels
                for _, job, labels in workflow_runner_labels([workflow])
            }
        self.assertEqual(
            parsed,
            {
                "scalar": ["self-hosted"],
                "flow": ["self-hosted", "macOS"],
                "next-line-flow": ["self-hosted", "macOS"],
                "block": ["self-hosted", "macOS"],
                "quoted": ["self-hosted", "macOS,ARM64"],
            },
        )

    def test_runner_comments_cannot_supply_required_labels(self) -> None:
        contents = """
jobs:
  spoofed:
    runs-on: [self-hosted, macOS] # devcontainer-designated-mbp
"""
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "comment.yaml"
            workflow.write_text(contents, encoding="utf-8")
            parsed = workflow_runner_labels([workflow])
        self.assertEqual(parsed[0][2], ["self-hosted", "macOS"])

    def test_reusable_workflow_jobs_fail_closed(self) -> None:
        contents = """
jobs:
  delegated:
    uses: owner/repository/.github/workflows/build.yml@0123456789abcdef
"""
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "reusable.yml"
            workflow.write_text(contents, encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError,
                "uses an uninspected reusable workflow",
            ):
                workflow_runner_labels([workflow])

    def test_development_contract_declares_the_required_ruby(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        build = (ROOT / "BUILD.md").read_text(encoding="utf-8")
        bootstrap = (ROOT / "Tools" / "ci" / "bootstrap.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("Ruby 2.7 or newer", readme)
        self.assertIn("Ruby 2.7 or newer with Psych", build)
        self.assertIn("Ruby 2.7 or newer with its standard Psych", build)
        self.assertIn('minimum_ruby_version="2.7"', bootstrap)
        self.assertIn("RUBY_VERSION", bootstrap)

    def test_dynamic_runner_selection_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot prove hosted isolation"):
            is_self_hosted_selector(["${{ matrix.runner }}"])

    def test_only_known_github_hosted_selectors_skip_designated_routing(
        self,
    ) -> None:
        for label in GITHUB_HOSTED_RUNNER_LABELS:
            self.assertFalse(is_self_hosted_selector([label]), label)

        for labels in (
            ["devcontainer-release"],
            ["macOS", "ARM64"],
            ["future-github-hosted-label"],
        ):
            self.assertTrue(is_self_hosted_selector(labels), labels)

    def test_release_docs_require_the_designated_mbp(self) -> None:
        contents = (ROOT / "RELEASE.md").read_text(encoding="utf-8")
        self.assertIn(
            "runs-on: [self-hosted, macOS, ARM64, devcontainer-release, "
            "devcontainer-designated-mbp]",
            contents,
        )
        self.assertIn(
            "both the `devcontainer-release` and "
            "`devcontainer-designated-mbp` labels",
            contents,
        )

    def test_workflow_discovery_includes_both_yaml_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("one.yml", "two.yaml", "ignored.txt"):
                (root / name).touch()
            self.assertEqual(
                [path.name for path in workflow_files(root)],
                ["one.yml", "two.yaml"],
            )

    def test_pinned_uploader_verifies_the_action_archive(self) -> None:
        contents = (
            ROOT / "Tools" / "ci" / "upload-artifact-pinned.sh"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "action_revision=043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
            contents,
        )
        self.assertIn(
            "archive_sha256="
            "d14fb1cada435a236a66b448fbb370cd126564c2c2d6cb52abd14d20bcbb9748",
            contents,
        )
        self.assertIn('[[ "${actual_sha256}" != "${archive_sha256}" ]]', contents)

        action = (
            ROOT / "Tools" / "ci" / "upload-artifact-action" / "action.yml"
        ).read_text(encoding="utf-8")
        wrapper = (
            ROOT / "Tools" / "ci" / "upload-artifact-action" / "index.js"
        ).read_text(encoding="utf-8")
        self.assertIn("using: node24", action)
        self.assertIn("'upload-artifact-pinned.sh'", wrapper)
        self.assertIn("env: process.env", wrapper)

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
                contents.count('SWIFT_TEST_ATTEMPTS: "1"'),
                1,
                name,
            )
            self.assertEqual(
                contents.count('SWIFT_TEST_TIMEOUT_SECONDS: "300"'),
                1,
                name,
            )

    def test_hosted_swift_tests_reuse_the_resolved_default_scratch(self) -> None:
        ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
        quality = (WORKFLOWS / "quality.yml").read_text(encoding="utf-8")
        sonar = (WORKFLOWS / "sonar.yml").read_text(encoding="utf-8")
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn("SWIFT_COVERAGE_SCRATCH_PATH: .build", ci)
        self.assertIn("SWIFT_COVERAGE_SCRATCH_PATH: .build", sonar)
        self.assertIn("SWIFT_ASAN_SCRATCH_PATH: .build", quality)
        self.assertIn("SWIFT_TSAN_SCRATCH_PATH: .build", quality)
        self.assertEqual(ci.count("- name: Resolve exact dependencies"), 1)
        self.assertEqual(quality.count("- name: Resolve exact dependencies"), 1)
        self.assertEqual(sonar.count("- name: Resolve exact dependencies"), 1)
        self.assertIn(
            "SWIFT_COVERAGE_SCRATCH_PATH ?= .build/coverage",
            makefile,
        )
        self.assertIn("SWIFT_ASAN_SCRATCH_PATH ?= .build/asan", makefile)
        self.assertIn("SWIFT_TSAN_SCRATCH_PATH ?= .build/tsan", makefile)
        self.assertNotIn("--scratch-path .build/coverage", makefile)
        self.assertNotIn("--scratch-path .build/asan", makefile)
        self.assertNotIn("--scratch-path .build/tsan", makefile)
        self.assertIn("devcontainer-swift-profile.XXXXXX", makefile)
        self.assertIn(
            'LLVM_PROFILE_FILE="$$TEST_BIN_PATH/codecov/devcontainer-tests-%m-%p.profraw"',
            makefile,
        )
        self.assertEqual(makefile.count("--build-tests"), 4)
        self.assertEqual(
            makefile.count("--product devcontainer-engine"),
            4,
        )
        self.assertEqual(
            makefile.count("DEVCONTAINER_ENGINE_TEST_EXECUTABLE="),
            4,
        )
        self.assertEqual(
            makefile.count("$(SWIFT_TEST_RUNNER_FLAGS)"),
            4,
        )
        self.assertEqual(
            makefile.count("Tools/ci/run-swift-testing-bundle.sh"),
            4,
        )
        self.assertEqual(
            makefile.count("devcontainerPackageTests.xctest/Contents/MacOS"),
            4,
        )
        self.assertEqual(makefile.count("--enable-code-coverage"), 5)
        self.assertEqual(makefile.count("--sanitize=address"), 3)
        self.assertEqual(makefile.count("--sanitize=thread"), 3)

    def test_hosted_swift_jobs_pin_xcode_and_bound_reporter_output(self) -> None:
        swift_workflows = (
            "ci.yml",
            "codeql.yml",
            "docs.yml",
            "homebrew.yml",
            "quality.yml",
            "sonar.yml",
            "stable-release-gate.yml",
        )
        developer_dir = (
            "DEVELOPER_DIR: /Applications/Xcode_26.6.app/Contents/Developer"
        )
        for name in swift_workflows:
            contents = (WORKFLOWS / name).read_text(encoding="utf-8")
            self.assertIn(developer_dir, contents, name)

        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn(
            "SWIFT_TEST_RUNNER_FLAGS ?= --no-parallel",
            makefile,
        )
        self.assertNotIn("$(SWIFT) test", makefile)

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

    def test_parity_comparison_survives_failed_lanes(self) -> None:
        contents = (WORKFLOWS / "parity.yml").read_text(encoding="utf-8")
        compare = contents[contents.index("  compare:\n"):]

        self.assertIn("    if: ${{ always() }}\n", compare)
        self.assertIn("          status=0\n", compare)
        self.assertIn(
            "compare_results.py .build/parity || status=1",
            compare,
        )
        self.assertIn(
            "compare_results.py .build/parity/vscode || status=1",
            compare,
        )
        self.assertIn(
            "validate_manifest.py --release || status=1",
            compare,
        )
        self.assertIn('          exit "${status}"\n', compare)

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
        self.assertIn(
            'formula_path="${PWD}/homebrew-tap/Formula/${formula}.rb"',
            contents,
        )
        self.assertIn(
            'test_tap="stephenlclarke/devcontainer-release-ci-${GITHUB_RUN_ID}"',
            contents,
        )
        self.assertIn("export HOMEBREW_NO_AUTO_UPDATE=1", contents)
        self.assertIn('brew tap-new --no-git "${test_tap}"', contents)
        self.assertIn('brew install --formula "${test_tap}/${formula}"', contents)
        self.assertNotIn('brew tap "${tap}" "${PWD}/homebrew-tap"', contents)
        self.assertNotIn('brew untap "stephenlclarke/tap"', contents)

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
