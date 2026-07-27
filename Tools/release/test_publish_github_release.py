"""Tests for immutable stable and staged Current GitHub publication."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parent
PUBLISHER = TOOLS / "publish-github-release.sh"
COMMIT = "0123456789abcdef0123456789abcdef01234567"


class GitHubReleasePublisherTests(unittest.TestCase):
    def write_executable(self, path: Path, body: str) -> None:
        path.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n" + body,
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def fixture(
        self,
        root: Path,
        *,
        release_exists: bool,
    ) -> tuple[dict[str, str], Path, Path]:
        fake_bin = root / "bin"
        fake_bin.mkdir()
        gh_trace = root / "gh.trace"
        git_trace = root / "git.trace"
        self.write_executable(
            fake_bin / "gh",
            (
                'printf "%s\\n" "$*" >> "$GH_TRACE"\n'
                'if [[ "${1:-}" == api ]]; then\n'
                f"  if [[ {'1' if release_exists else '0'} == 1 ]]; then\n"
                "    printf '{}\\n'\n"
                "    exit 0\n"
                "  fi\n"
                "  printf 'HTTP 404: Not Found\\n' >&2\n"
                "  exit 1\n"
                "fi\n"
            ),
        )
        self.write_executable(
            fake_bin / "git",
            'printf "%s\\n" "$*" >> "$GIT_TRACE"\n',
        )
        asset = root / "package.tar.gz"
        checksum = root / "package.tar.gz.sha256"
        notes = root / "notes.md"
        manifest = root / "assets.txt"
        asset.write_bytes(b"archive")
        checksum.write_text("checksum\n", encoding="utf-8")
        notes.write_text("# Release\n", encoding="utf-8")
        manifest.write_text(f"{asset}\n{checksum}\n", encoding="utf-8")
        environment = os.environ.copy()
        environment.update(
            {
                "GH": str(fake_bin / "gh"),
                "GH_TRACE": str(gh_trace),
                "GIT": str(fake_bin / "git"),
                "GIT_TRACE": str(git_trace),
                "PUBLISH_SHA": COMMIT,
                "RELEASE_ASSETS_FILE": str(manifest),
                "RELEASE_NOTES_FILE": str(notes),
                "RELEASE_REPOSITORY": "stephenlclarke/devcontainer",
                "RELEASE_TITLE": "Release",
            }
        )
        return environment, gh_trace, git_trace

    def run_publisher(
        self,
        environment: dict[str, str],
        mode: str,
        tag: str,
    ) -> subprocess.CompletedProcess[str]:
        environment["RELEASE_TAG"] = tag
        return subprocess.run(
            [str(PUBLISHER), mode],
            env=environment,
            capture_output=True,
            text=True,
        )

    def test_first_current_stage_creates_tag_and_prerelease(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment, gh_trace, git_trace = self.fixture(
                Path(temporary_directory),
                release_exists=False,
            )
            result = self.run_publisher(environment, "current-stage", "current")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("release create current", gh_trace.read_text(encoding="utf-8"))
            trace = git_trace.read_text(encoding="utf-8")
            self.assertIn(f"tag --no-sign --force current {COMMIT}", trace)
            self.assertIn("push --force origin refs/tags/current", trace)

    def test_later_current_stage_preserves_source_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment, gh_trace, git_trace = self.fixture(
                Path(temporary_directory),
                release_exists=True,
            )
            result = self.run_publisher(environment, "current-stage", "current")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("release upload current", gh_trace.read_text(encoding="utf-8"))
            self.assertFalse(git_trace.exists())

    def test_current_finalize_moves_tag_after_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment, gh_trace, git_trace = self.fixture(
                Path(temporary_directory),
                release_exists=True,
            )
            result = self.run_publisher(environment, "current-finalize", "current")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("release edit current", gh_trace.read_text(encoding="utf-8"))
            self.assertIn("push --force origin", git_trace.read_text(encoding="utf-8"))

    def test_existing_stable_release_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment, gh_trace, git_trace = self.fixture(
                Path(temporary_directory),
                release_exists=True,
            )
            result = self.run_publisher(environment, "stable", "1.2.3")
            self.assertEqual(result.returncode, 1)
            self.assertIn("immutable", result.stderr)
            self.assertNotIn("release create", gh_trace.read_text(encoding="utf-8"))
            self.assertFalse(git_trace.exists())

    def test_new_stable_release_uses_existing_verified_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment, gh_trace, git_trace = self.fixture(
                Path(temporary_directory),
                release_exists=False,
            )
            result = self.run_publisher(environment, "stable", "1.2.3")
            self.assertEqual(result.returncode, 0, result.stderr)
            trace = gh_trace.read_text(encoding="utf-8")
            self.assertIn("release create 1.2.3", trace)
            self.assertIn("--verify-tag", trace)
            self.assertFalse(git_trace.exists())


if __name__ == "__main__":
    unittest.main()
