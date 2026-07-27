"""Regression tests for the Compose-style README header and quality badges."""

from __future__ import annotations

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

    def test_complete_sonar_badge_set_is_present(self) -> None:
        expected_metrics = {
            "alert_status",
            "bugs",
            "code_smells",
            "coverage",
            "duplicated_lines_density",
            "ncloc",
            "reliability_rating",
            "security_rating",
            "sqale_index",
            "sqale_rating",
            "vulnerabilities",
        }
        for metric in expected_metrics:
            self.assertIn(
                "project=stephenlclarke_devcontainer&metric=" + metric,
                README,
            )

    def test_delivery_and_project_badges_are_present(self) -> None:
        for marker in (
            "actions/workflows/codeql.yml/badge.svg?branch=main",
            "actions/workflows/ci.yml/badge.svg?branch=main",
            "actions/workflows/docs.yml/badge.svg?branch=main",
            "img.shields.io/github/v/release/stephenlclarke/devcontainer",
            "license-Apache--2.0",
            "page_id=stephenlclarke.devcontainer",
        ):
            self.assertIn(marker, README)


if __name__ == "__main__":
    unittest.main()
