"""Tests for the release parity quiet-host gate."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "Tools" / "parity" / "require-quiet-host.sh"


class QuietHostTests(unittest.TestCase):
    def make_tool(self, root: Path, name: str, body: str) -> Path:
        path = root / name
        path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def environment(
        self,
        root: Path,
        *,
        load: str = "{ 1.00 0.50 0.25 }",
        process_status: int = 1,
    ) -> dict[str, str]:
        pgrep_output = "printf '4242\\n'\n" if process_status == 0 else ""
        pgrep = self.make_tool(
            root,
            "pgrep",
            f"{pgrep_output}exit {process_status}",
        )
        pmset = self.make_tool(root, "pmset", "printf 'thermal level: 0\\n'")
        ps = self.make_tool(root, "ps", "printf 'PID PPID CPU MEM ELAPSED COMM\\n'")
        sleep = self.make_tool(root, "sleep", "exit 0")
        sysctl = self.make_tool(
            root,
            "sysctl",
            "if [ \"$2\" = hw.ncpu ]; then printf '8\\n'; "
            f"else printf '%s\\n' '{load}'; fi",
        )
        return {
            **os.environ,
            "DEVCONTAINER_QUIET_HOST_PGREP_BIN": str(pgrep),
            "DEVCONTAINER_QUIET_HOST_PMSET_BIN": str(pmset),
            "DEVCONTAINER_QUIET_HOST_PS_BIN": str(ps),
            "DEVCONTAINER_QUIET_HOST_SLEEP_BIN": str(sleep),
            "DEVCONTAINER_QUIET_HOST_SYSCTL_BIN": str(sysctl),
            "DEVCONTAINER_QUIET_HOST_WAIT_SECONDS": "0",
            "DEVCONTAINER_QUIET_HOST_POLL_SECONDS": "1",
        }

    def test_records_successful_quiet_host_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "evidence"
            result = subprocess.run(
                [str(SCRIPT), str(evidence)],
                check=False,
                capture_output=True,
                text=True,
                env=self.environment(root),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = (evidence / "quiet-host.tsv").read_text(encoding="utf-8")
            self.assertIn("result\tquiet", summary)
            self.assertIn("oneMinuteLoad\t1.00", summary)
            self.assertIn("loadLimit\t2.000", summary)
            self.assertIn("loadPolicy\tmin(logicalCPUs/4,2.0)", summary)
            self.assertTrue((evidence / "thermal-1.txt").is_file())
            self.assertTrue((evidence / "processes-1.txt").is_file())

    def test_help_describes_the_required_evidence_directory(self) -> None:
        result = subprocess.run(
            [str(SCRIPT), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("require-quiet-host.sh EVIDENCE_DIRECTORY", result.stdout)

    def test_rejects_high_load(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = subprocess.run(
                [str(SCRIPT), str(root / "evidence")],
                check=False,
                capture_output=True,
                text=True,
                env=self.environment(root, load="{ 2.01 0.50 0.25 }"),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("host did not become quiet", result.stderr)

    def test_failed_rerun_invalidates_an_old_success_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "evidence"
            evidence.mkdir()
            receipt = evidence / "quiet-host.tsv"
            receipt.write_text("result\tquiet\n", encoding="utf-8")
            result = subprocess.run(
                [str(SCRIPT), str(evidence)],
                check=False,
                capture_output=True,
                text=True,
                env=self.environment(root, load="{ 2.01 0.50 0.25 }"),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(receipt.exists())

    def test_rejects_competing_build_or_release_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = subprocess.run(
                [str(SCRIPT), str(root / "evidence")],
                check=False,
                capture_output=True,
                text=True,
                env=self.environment(root, process_status=0),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("competing build process is active", result.stderr)
            matches = (root / "evidence" / "competing-1.txt").read_text(
                encoding="utf-8"
            )
            self.assertIn("swift-build\t4242", matches)
            self.assertIn("container-family-release\t4242", matches)


if __name__ == "__main__":
    unittest.main()
