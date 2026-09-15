"""Contract tests for the combined Swift test and executable coverage pipeline."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class CoveragePipelineTests(unittest.TestCase):
    def test_coverage_helpers_are_executable(self) -> None:
        for name in ("run-cli-coverage.sh", "export-swift-coverage.sh"):
            path = REPOSITORY_ROOT / "Tools" / "coverage" / name
            self.assertTrue(path.is_file(), name)
            self.assertTrue(os.access(path, os.X_OK), name)

    def test_makefile_instruments_and_exports_both_cli_products(self) -> None:
        makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertGreaterEqual(makefile.count("--enable-code-coverage"), 3)
        self.assertIn("--product devcontainer\n", makefile)
        self.assertIn("--product devcontainer-compose\n", makefile)
        self.assertIn("Tools/coverage/run-cli-coverage.sh", makefile)
        self.assertIn("Tools/coverage/export-swift-coverage.sh", makefile)
        exporter = (
            REPOSITORY_ROOT / "Tools" / "coverage" / "export-swift-coverage.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("*PackageTests", exporter)
        self.assertIn("*Tests.xctest/Contents/MacOS/*Tests", exporter)
        self.assertIn('LLVM_COV_COMMAND+=( -object "$test_binary" )', exporter)
        self.assertIn("--lcov-output coverage.lcov", makefile)
        self.assertIn('--changed-since "$(SWIFT_COVERAGE_BASE)"', makefile)

    def test_exporter_supports_one_test_binary_with_nounset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            bin_directory = root / "debug"
            profile_directory = bin_directory / "codecov"
            test_binary = bin_directory / "DevcontainerPackageTests.xctest/Contents/MacOS/DevcontainerPackageTests"
            profile_directory.mkdir(parents=True)
            test_binary.parent.mkdir(parents=True)
            (profile_directory / "default.profraw").touch()
            test_binary.touch()
            for name in ("devcontainer", "devcontainer-compose", "devcontainer-docker"):
                executable = bin_directory / name
                executable.touch()
                executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

            llvm_profdata = root / "llvm-profdata"
            llvm_profdata.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "while (( $# > 0 )); do\n"
                "  if [[ \"$1\" == \"-o\" ]]; then touch \"$2\"; exit 0; fi\n"
                "  shift\n"
                "done\n"
                "exit 64\n",
                encoding="utf-8",
            )
            llvm_profdata.chmod(llvm_profdata.stat().st_mode | stat.S_IXUSR)
            llvm_cov = root / "llvm-cov"
            llvm_cov.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "printf '{\"data\":[]}'\n",
                encoding="utf-8",
            )
            llvm_cov.chmod(llvm_cov.stat().st_mode | stat.S_IXUSR)

            environment = os.environ.copy()
            environment.update(
                {
                    "SWIFT_LLVM_PROFDATA": str(llvm_profdata),
                    "SWIFT_LLVM_COV": str(llvm_cov),
                }
            )
            result = subprocess.run(
                [
                    str(REPOSITORY_ROOT / "Tools/coverage/export-swift-coverage.sh"),
                    str(bin_directory),
                ],
                env=environment,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (profile_directory / "devcontainer.json").read_text(encoding="utf-8"),
                '{"data":[]}',
            )

    def test_compose_fixture_has_immutable_release_provenance(self) -> None:
        fixture = (REPOSITORY_ROOT / "Tools" / "coverage" / "run-cli-coverage.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('"source":"stephenlclarke/container-compose"', fixture)
        self.assertIn('"commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"', fixture)

    def test_sonar_excludes_only_non_executable_registration_files(self) -> None:
        properties = (
            REPOSITORY_ROOT / "sonar-project.properties"
        ).read_text(encoding="utf-8")
        exclusions = next(
            line.removeprefix("sonar.coverage.exclusions=").split(",")
            for line in properties.splitlines()
            if line.startswith("sonar.coverage.exclusions=")
        )
        self.assertEqual(
            exclusions,
            [
                "Sources/DevContainerCLI/DevContainerCommand.swift",
                "Sources/DevContainerCore/DevContainerProject.swift",
            ],
        )

    def test_sonar_requires_an_exact_clean_head(self) -> None:
        makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
        coverage_recipe = makefile.split("coverage:\n", 1)[1].split(
            "\ncoverage-check:", 1
        )[0]
        self.assertIn("git status --porcelain --untracked-files=all", coverage_recipe)
        self.assertIn("Coverage evidence requires a clean worktree", coverage_recipe)
        self.assertIn(".build/sonar-coverage-revision", makefile)
        self.assertIn('coverage_version" != "$$head_version', makefile)
        self.assertIn("SONAR_PROJECT_VERSION must match checked-out HEAD", makefile)
        self.assertIn('-Dsonar.projectVersion="$$sonar_project_version"', makefile)
        self.assertIn('-Dsonar.pullrequest.key="$${SONAR_PULL_REQUEST_KEY}"', makefile)


if __name__ == "__main__":
    unittest.main()
