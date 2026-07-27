"""Tests for the portable SwiftLint baseline transformation."""

from __future__ import annotations

import unittest
from pathlib import Path

from swiftlint_baseline import ROOT_TOKEN, rewrite_baseline


class SwiftLintBaselineTests(unittest.TestCase):
    def test_canonical_and_checkout_paths_round_trip(self) -> None:
        document = [
            {
                "violation": {
                    "location": {
                        "file": (
                            "file:///old/checkout/"
                            "Sources/DevContainerModel/BuildInfo.swift"
                        )
                    }
                }
            }
        ]

        canonical = rewrite_baseline(document, root=None)
        self.assertEqual(
            canonical[0]["violation"]["location"]["file"],
            (
                f"file:///{ROOT_TOKEN}/"
                "Sources/DevContainerModel/BuildInfo.swift"
            ),
        )
        resolved = rewrite_baseline(canonical, root=Path("/new/checkout"))
        self.assertEqual(
            resolved[0]["violation"]["location"]["file"],
            (
                "file:///new/checkout/"
                "Sources/DevContainerModel/BuildInfo.swift"
            ),
        )

    def test_non_first_party_paths_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not first-party"):
            rewrite_baseline(
                [
                    {
                        "violation": {
                            "location": {
                                "file": "file:///checkout/.build/vendor.swift"
                            }
                        }
                    }
                ],
                root=None,
            )


if __name__ == "__main__":
    unittest.main()
