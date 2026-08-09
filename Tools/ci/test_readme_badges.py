"""Regression tests for the Compose-style README header and quality badges."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
README = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")


class ReadmeBadgeTests(unittest.TestCase):
    def test_icon_uses_the_compose_header_geometry(self) -> None:
        self.assertIn(
            '<img align="left" hspace="20" '
            'src="docs/images/devcontainer-icon.png" width="147"',
            README,
        )
        self.assertIn('<br clear="left" />', README)

    def test_complete_supported_sonar_badge_set_is_present(self) -> None:
        expected_metrics = {
            "alert_status",
            "bugs",
            "code_smells",
            "coverage",
            "duplicated_lines_density",
            "ncloc",
            "security_rating",
            "sqale_rating",
        }
        actual_metrics = re.findall(
            r"project=stephenlclarke_devcontainer&amp;metric=([a-z_]+)",
            README,
        )
        self.assertEqual(set(actual_metrics), expected_metrics)
        self.assertEqual(len(actual_metrics), len(expected_metrics))

    def test_workflow_and_project_badges_are_present(self) -> None:
        for marker in (
            "actions/workflows/ci.yml/badge.svg?branch=main",
            "actions/workflows/codeql.yml/badge.svg?branch=main",
            "actions/workflows/docs.yml/badge.svg?branch=main",
            "actions/workflows/homebrew.yml/badge.svg?branch=main",
            "actions/workflows/prebuilt-binaries.yml/badge.svg?branch=main",
            "page_id=stephenlclarke.devcontainer",
        ):
            self.assertIn(marker, README)

    def test_reproducible_live_demo_is_linked(self) -> None:
        self.assertIn("## See it work", README)
        self.assertIn("docs/images/devcontainer-demo.gif", README)
        self.assertTrue((REPOSITORY_ROOT / "docs/devcontainer-demo.tape").is_file())
        self.assertTrue(
            (REPOSITORY_ROOT / "Tools/release/record-vhs-live-demo.sh").is_file()
        )


if __name__ == "__main__":
    unittest.main()
