"""Tests for strict signing and sanitized notarization evidence."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parent
SIGNING_SCRIPT = TOOLS / "sign-and-notarize.sh"
EVIDENCE_TOOL = TOOLS / "write-notarization-evidence.py"
SUBMISSION_ID = "01234567-89ab-cdef-0123-456789abcdef"


class SigningToolTests(unittest.TestCase):
    def write_executable(self, path: Path, body: str) -> None:
        path.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n" + body,
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def make_stage(self, root: Path) -> Path:
        stage = root / "stage" / "devcontainer-1.2.3"
        binaries = [
            stage / "bin" / "devcontainer",
            stage / "bin" / "devcontainer-compose",
            stage / "bin" / "devcontainer-engine",
            (
                stage
                / "libexec"
                / "container"
                / "plugins"
                / "devcontainer"
                / "bin"
                / "devcontainer"
            ),
        ]
        for binary in binaries:
            binary.parent.mkdir(parents=True, exist_ok=True)
            self.write_executable(binary, "exit 0\n")
        return stage

    def make_fake_tools(self, root: Path) -> tuple[Path, Path]:
        fake_bin = root / "fake-bin"
        fake_bin.mkdir()
        trace = root / "codesign.trace"
        self.write_executable(
            fake_bin / "codesign",
            'printf "%s\\n" "$*" >> "$SIGNING_TRACE"\n',
        )
        self.write_executable(
            fake_bin / "ditto",
            'output="${@: -1}"\nprintf "notary archive" > "$output"\n',
        )
        self.write_executable(
            fake_bin / "xcrun",
            (
                'printf \'{"id":"'
                + SUBMISSION_ID
                + '","status":"Accepted","private":"discard-me"}\\n\'\n'
            ),
        )
        return fake_bin, trace

    def test_signing_covers_every_executable_and_retains_sanitized_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            stage = self.make_stage(root)
            fake_bin, trace = self.make_fake_tools(root)
            evidence = root / "notarization.json"
            environment = os.environ.copy()
            environment.update(
                {
                    "DEVCONTAINER_NOTARY_PROFILE": "fixture-profile",
                    "DEVCONTAINER_SIGNING_IDENTITY": "Developer ID Application: Fixture",
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                    "SIGNING_TRACE": str(trace),
                }
            )

            subprocess.run(
                [str(SIGNING_SCRIPT), str(stage), str(evidence)],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )

            trace_lines = trace.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(trace_lines), 8)
            for executable in (
                "devcontainer",
                "devcontainer-compose",
                "devcontainer-engine",
            ):
                expected = 4 if executable == "devcontainer" else 2
                self.assertEqual(
                    sum(
                        Path(line.split()[-1]).name == executable
                        for line in trace_lines
                    ),
                    expected,
                )
            value = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(value["id"], SUBMISSION_ID)
            self.assertEqual(value["status"], "Accepted")
            self.assertRegex(value["archiveSHA256"], r"^[0-9a-f]{64}$")
            self.assertNotIn("private", value)
            self.assertNotIn("fixture-profile", evidence.read_text(encoding="utf-8"))

    def test_missing_release_credentials_fail_before_signing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            stage = self.make_stage(root)
            result = subprocess.run(
                [str(SIGNING_SCRIPT), str(stage), str(root / "evidence.json")],
                env={"PATH": os.environ["PATH"]},
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("DEVCONTAINER_SIGNING_IDENTITY", result.stderr)

    def test_notary_rejection_does_not_create_acceptance_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "notary.json"
            output = root / "evidence.json"
            source.write_text(
                json.dumps({"id": SUBMISSION_ID, "status": "Invalid"}),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(EVIDENCE_TOOL),
                    "--input",
                    str(source),
                    "--archive-sha256",
                    "0" * 64,
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
