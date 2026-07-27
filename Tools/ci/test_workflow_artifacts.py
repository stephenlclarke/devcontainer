"""Regression tests for GitHub Actions artifact evidence contracts."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
STEP_BOUNDARY = re.compile(r"^ {6}- name:", re.MULTILINE)


class WorkflowArtifactTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
