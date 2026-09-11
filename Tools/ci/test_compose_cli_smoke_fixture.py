"""Tests for the policy-valid hosted Compose smoke fixture."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


FIXTURE = Path(__file__).with_name("compose-cli-smoke-fixture.sh")


class ComposeCLISmokeFixtureTests(unittest.TestCase):
    def run_fixture(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(FIXTURE), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_provenance_probe_identifies_native_container_compose(self) -> None:
        result = self.run_fixture("version", "--format", "json")

        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["source"], "stephenlclarke/container-compose")
        self.assertRegex(value["version"], r"^[0-9]+\.[0-9]+\.[0-9]+$")
        self.assertRegex(value["commit"], r"^[0-9a-f]{40}$")

    def test_user_version_command_retains_smoke_marker(self) -> None:
        result = self.run_fixture("version")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"Version": "fixture"})

    def test_other_commands_fail_closed(self) -> None:
        result = self.run_fixture("up")

        self.assertEqual(result.returncode, 64)
        self.assertIn("expected: version", result.stderr)


if __name__ == "__main__":
    unittest.main()
