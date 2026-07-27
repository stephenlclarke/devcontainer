"""Contract tests for the combined Swift test and executable coverage pipeline."""

from __future__ import annotations

import os
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
        self.assertIn("--lcov-output coverage.lcov", makefile)
        self.assertIn('--changed-since "$(SWIFT_COVERAGE_BASE)"', makefile)

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


if __name__ == "__main__":
    unittest.main()
