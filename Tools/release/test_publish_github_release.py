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
        prerelease: bool = True,
        remote_assets: str = "",
        download_content: str = "",
        remote_assets_after_upload: str = (
            "package.tar.gz\npackage.tar.gz.sha256\n"
        ),
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
                '    if [[ "${2:-}" != --silent ]]; then\n'
                "      printf "
                f"'{{\"tag_name\":\"%s\",\"draft\":false,\"prerelease\":"
                f"{'true' if prerelease else 'false'}}}\\n' "
                '"$RELEASE_TAG"\n'
                "    fi\n"
                "    exit 0\n"
                "  fi\n"
                "  printf 'HTTP 404: Not Found\\n' >&2\n"
                "  exit 1\n"
                "fi\n"
                'if [[ "${1:-}" == release && "${2:-}" == view ]]; then\n'
                '  if [[ -f "$GH_UPLOAD_MARKER" ]]; then\n'
                '    printf "%s" "$REMOTE_ASSETS_AFTER_UPLOAD"\n'
                "  else\n"
                '    printf "%s" "$REMOTE_ASSETS"\n'
                "  fi\n"
                "  exit 0\n"
                "fi\n"
                'if [[ "${1:-}" == release && "${2:-}" == download ]]; then\n'
                "  pattern=''\n"
                "  directory=''\n"
                "  while (( $# > 0 )); do\n"
                '    case "$1" in\n'
                '      --pattern) pattern="$2"; shift 2 ;;\n'
                '      --dir) directory="$2"; shift 2 ;;\n'
                "      *) shift ;;\n"
                "    esac\n"
                "  done\n"
                '  if [[ -n "$DOWNLOAD_CONTENT" ]]; then\n'
                '    printf "%s" "$DOWNLOAD_CONTENT" > "$directory/$pattern"\n'
                "  else\n"
                '    case "$pattern" in\n'
                '      package.tar.gz) printf "archive" > "$directory/$pattern" ;;\n'
                '      package.tar.gz.sha256) printf "checksum\\n" > "$directory/$pattern" ;;\n'
                "    esac\n"
                "  fi\n"
                "  exit 0\n"
                "fi\n"
                'if [[ "${1:-}" == release && "${2:-}" == upload ]]; then\n'
                '  : > "$GH_UPLOAD_MARKER"\n'
                "  exit 0\n"
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
                "REMOTE_ASSETS": remote_assets,
                "REMOTE_ASSETS_AFTER_UPLOAD": remote_assets_after_upload,
                "DOWNLOAD_CONTENT": download_content,
                "GH_UPLOAD_MARKER": str(root / "upload.marker"),
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

    def test_existing_final_stable_release_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment, gh_trace, git_trace = self.fixture(
                Path(temporary_directory),
                release_exists=True,
                prerelease=False,
            )
            result = self.run_publisher(
                environment,
                "stable-stage",
                "1.2.3",
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("not a staged prerelease", result.stderr)
            self.assertNotIn("release create", gh_trace.read_text(encoding="utf-8"))
            self.assertFalse(git_trace.exists())

    def test_new_stable_stage_uses_verified_tag_and_prerelease(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment, gh_trace, git_trace = self.fixture(
                Path(temporary_directory),
                release_exists=False,
            )
            result = self.run_publisher(
                environment,
                "stable-stage",
                "1.2.3",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            trace = gh_trace.read_text(encoding="utf-8")
            self.assertIn("release create 1.2.3", trace)
            self.assertIn("--verify-tag", trace)
            self.assertIn("--prerelease", trace)
            self.assertIn("--latest=false", trace)
            self.assertFalse(git_trace.exists())

    def test_existing_stable_stage_uploads_only_missing_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment, gh_trace, git_trace = self.fixture(
                Path(temporary_directory),
                release_exists=True,
            )
            result = self.run_publisher(
                environment,
                "stable-stage",
                "1.2.3",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            trace = gh_trace.read_text(encoding="utf-8")
            self.assertIn("release upload 1.2.3", trace)
            self.assertNotIn("--clobber", trace)
            self.assertIn("--title Release", trace)
            self.assertIn("--notes-file", trace)
            self.assertFalse(git_trace.exists())

    def test_stable_stage_reuses_only_byte_identical_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment, gh_trace, _ = self.fixture(
                Path(temporary_directory),
                release_exists=True,
                remote_assets="package.tar.gz\n",
            )
            result = self.run_publisher(environment, "stable-stage", "1.2.3")
            self.assertEqual(result.returncode, 0, result.stderr)
            trace = gh_trace.read_text(encoding="utf-8")
            self.assertIn("release download 1.2.3", trace)
            self.assertNotIn("release upload 1.2.3 package.tar.gz ", trace)
            self.assertNotIn("--clobber", trace)

    def test_stable_stage_rejects_unexpected_or_conflicting_assets(self) -> None:
        cases = (
            ("foreign.tar.gz\n", "archive", "unexpected asset"),
            ("package.tar.gz\n", "different", "conflicts with candidate"),
        )
        for remote_assets, content, message in cases:
            with self.subTest(message=message):
                with tempfile.TemporaryDirectory() as temporary:
                    environment, gh_trace, _ = self.fixture(
                        Path(temporary),
                        release_exists=True,
                        remote_assets=remote_assets,
                        download_content=content,
                    )
                    result = self.run_publisher(
                        environment, "stable-stage", "1.2.3"
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(message, result.stderr)
                    trace = gh_trace.read_text(encoding="utf-8")
                    self.assertNotIn("release edit", trace)
                    self.assertNotIn("--clobber", trace)

    def test_stable_stage_validates_all_existing_assets_before_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment, gh_trace, _ = self.fixture(
                Path(temporary_directory),
                release_exists=True,
                remote_assets="package.tar.gz.sha256\n",
                download_content="different",
            )
            result = self.run_publisher(environment, "stable-stage", "1.2.3")
            self.assertEqual(result.returncode, 1)
            self.assertIn("conflicts with candidate", result.stderr)
            trace = gh_trace.read_text(encoding="utf-8")
            self.assertNotIn("release upload", trace)
            self.assertNotIn("release edit", trace)

    def test_stable_stage_revalidates_remote_assets_after_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment, gh_trace, _ = self.fixture(
                Path(temporary_directory),
                release_exists=True,
                remote_assets_after_upload=(
                    "package.tar.gz\npackage.tar.gz.sha256\nforeign.tar.gz\n"
                ),
            )
            result = self.run_publisher(environment, "stable-stage", "1.2.3")
            self.assertEqual(result.returncode, 1)
            self.assertIn("unexpected asset", result.stderr)
            trace = gh_trace.read_text(encoding="utf-8")
            self.assertIn("release upload", trace)
            self.assertEqual(trace.count("release view"), 2)
            self.assertNotIn("release edit", trace)

    def test_stable_finalize_promotes_staged_prerelease(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment, gh_trace, git_trace = self.fixture(
                Path(temporary_directory),
                release_exists=True,
            )
            result = self.run_publisher(
                environment,
                "stable-finalize",
                "1.2.3",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            trace = gh_trace.read_text(encoding="utf-8")
            self.assertIn("release edit 1.2.3", trace)
            self.assertIn("--prerelease=false", trace)
            self.assertIn("--latest", trace)
            self.assertFalse(git_trace.exists())


if __name__ == "__main__":
    unittest.main()
