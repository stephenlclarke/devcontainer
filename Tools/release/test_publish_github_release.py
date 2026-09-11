"""Tests for immutable stable and staged Current GitHub publication."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import textwrap
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

    def write_python_executable(self, path: Path, body: str) -> None:
        path.write_text(
            "#!/usr/bin/env python3\n" + textwrap.dedent(body),
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def fixture(
        self,
        root: Path,
        *,
        state: str = "missing",
        immutable_setting: bool = True,
        published_immutable: bool = True,
        remote_tag: str = COMMIT,
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
        state_file = root / "release.state"
        state_file.write_text(state, encoding="utf-8")
        self.write_python_executable(
            fake_bin / "gh",
            r'''
            import hashlib
            import json
            import os
            from pathlib import Path
            import sys

            args = sys.argv[1:]
            trace = Path(os.environ["GH_TRACE"])
            with trace.open("a", encoding="utf-8") as output:
                output.write(" ".join(args) + "\n")

            state_path = Path(os.environ["GH_STATE"])
            state = state_path.read_text(encoding="utf-8")
            tag = os.environ["RELEASE_TAG"]

            def names() -> list[str]:
                value = (
                    os.environ["REMOTE_ASSETS_AFTER_UPLOAD"]
                    if Path(os.environ["GH_UPLOAD_MARKER"]).exists()
                    else os.environ["REMOTE_ASSETS"]
                )
                return [line for line in value.splitlines() if line]

            def digest(name: str) -> str:
                candidates = {
                    Path(os.environ["ARCHIVE"]).name: Path(os.environ["ARCHIVE"]),
                    Path(os.environ["CHECKSUM"]).name: Path(os.environ["CHECKSUM"]),
                }
                path = candidates.get(name)
                if path is None:
                    return "sha256:" + "f" * 64
                content = path.read_bytes()
                if os.environ["SERVER_DIGEST_MISMATCH"] == name:
                    content += b"different"
                return "sha256:" + hashlib.sha256(content).hexdigest()

            def assets() -> list[dict[str, str]]:
                return [{"name": name, "digest": digest(name)} for name in names()]

            if args[:1] == ["api"]:
                endpoint = next(
                    (arg for arg in args[1:] if not arg.startswith("-")), ""
                )
                if endpoint.endswith("/immutable-releases"):
                    print(json.dumps({
                        "enabled": os.environ["IMMUTABLE_SETTING"] == "true"
                    }))
                    raise SystemExit(0)
                if endpoint.endswith("/releases/latest"):
                    if "--jq" in args:
                        print(tag)
                    else:
                        print(json.dumps({"tag_name": tag}))
                    raise SystemExit(0)
                if "/releases/tags/" in endpoint:
                    if state == "missing":
                        print("HTTP 404: Not Found", file=sys.stderr)
                        raise SystemExit(1)
                    document = {
                        "tag_name": tag,
                        "draft": state == "draft",
                        "prerelease": state == "current",
                        "immutable": (
                            state == "published"
                            and os.environ["PUBLISHED_IMMUTABLE"] == "true"
                        ),
                    }
                    print(json.dumps(document))
                    raise SystemExit(0)
                raise SystemExit(0)

            if args[:2] == ["release", "create"]:
                if "--draft" in args:
                    state_path.write_text("draft", encoding="utf-8")
                else:
                    state_path.write_text("current", encoding="utf-8")
                Path(os.environ["GH_UPLOAD_MARKER"]).touch()
                raise SystemExit(0)

            if args[:2] == ["release", "upload"]:
                Path(os.environ["GH_UPLOAD_MARKER"]).touch()
                raise SystemExit(0)

            if args[:2] == ["release", "edit"]:
                if "--draft=false" in args:
                    state_path.write_text("published", encoding="utf-8")
                raise SystemExit(0)

            if args[:2] == ["release", "download"]:
                pattern = args[args.index("--pattern") + 1]
                directory = Path(args[args.index("--dir") + 1])
                content = os.environ["DOWNLOAD_CONTENT"]
                if content:
                    (directory / pattern).write_text(content, encoding="utf-8")
                elif pattern == Path(os.environ["ARCHIVE"]).name:
                    (directory / pattern).write_bytes(
                        Path(os.environ["ARCHIVE"]).read_bytes()
                    )
                else:
                    (directory / pattern).write_bytes(
                        Path(os.environ["CHECKSUM"]).read_bytes()
                    )
                raise SystemExit(0)

            if args[:2] == ["release", "view"]:
                requested = args[args.index("--json") + 1]
                if requested == "assets" and "--jq" in args:
                    print("\n".join(names()))
                    raise SystemExit(0)
                if requested == "isDraft,assets":
                    print(json.dumps({"isDraft": state == "draft", "assets": assets()}))
                    raise SystemExit(0)
                notes = Path(os.environ["RELEASE_NOTES_FILE"]).read_text(
                    encoding="utf-8"
                )
                print(json.dumps({
                    "isDraft": False,
                    "isImmutable": os.environ["PUBLISHED_IMMUTABLE"] == "true",
                    "isPrerelease": False,
                    "tagName": tag,
                    "targetCommitish": os.environ["PUBLISH_SHA"],
                    "name": os.environ["RELEASE_TITLE"],
                    "body": notes,
                    "assets": assets(),
                }))
                raise SystemExit(0)

            raise SystemExit(0)
            ''',
        )
        self.write_executable(
            fake_bin / "git",
            'printf "%s\\n" "$*" >> "$GIT_TRACE"\n'
            'if [[ "${1:-}" == ls-remote ]]; then\n'
            '  printf "%s\\trefs/tags/%s^{}\\n" "$REMOTE_TAG" "$RELEASE_TAG"\n'
            "fi\n",
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
                "ARCHIVE": str(asset),
                "CHECKSUM": str(checksum),
                "DOWNLOAD_CONTENT": download_content,
                "GH": str(fake_bin / "gh"),
                "GH_STATE": str(state_file),
                "GH_TRACE": str(gh_trace),
                "GH_UPLOAD_MARKER": str(root / "upload.marker"),
                "GIT": str(fake_bin / "git"),
                "GIT_TRACE": str(git_trace),
                "IMMUTABLE_SETTING": str(immutable_setting).lower(),
                "PUBLISHED_IMMUTABLE": str(published_immutable).lower(),
                "PUBLISH_SHA": COMMIT,
                "RELEASE_ASSETS_FILE": str(manifest),
                "RELEASE_NOTES_FILE": str(notes),
                "RELEASE_REPOSITORY": "stephenlclarke/devcontainer",
                "RELEASE_TITLE": "Release",
                "REMOTE_ASSETS": remote_assets,
                "REMOTE_ASSETS_AFTER_UPLOAD": remote_assets_after_upload,
                "REMOTE_TAG": remote_tag,
                "SERVER_DIGEST_MISMATCH": "",
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

    def test_current_stage_and_finalize_preserve_mutable_channel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, gh_trace, git_trace = self.fixture(Path(temporary))
            result = self.run_publisher(environment, "current-stage", "current")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("release create current", gh_trace.read_text())
            self.assertIn(f"tag --no-sign --force current {COMMIT}", git_trace.read_text())

            result = self.run_publisher(environment, "current-finalize", "current")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("release edit current", gh_trace.read_text())

    def test_new_stable_stage_creates_private_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, gh_trace, git_trace = self.fixture(Path(temporary))
            result = self.run_publisher(environment, "stable-stage", "1.2.3")
            self.assertEqual(result.returncode, 0, result.stderr)
            trace = gh_trace.read_text()
            self.assertIn("release create 1.2.3", trace)
            self.assertIn("--verify-tag", trace)
            self.assertIn("--draft", trace)
            self.assertNotIn("--prerelease ", trace)
            self.assertIn("ls-remote --tags", git_trace.read_text())

    def test_stable_stage_reconciles_only_identical_draft_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, gh_trace, _ = self.fixture(
                Path(temporary),
                state="draft",
                remote_assets="package.tar.gz\n",
            )
            result = self.run_publisher(environment, "stable-stage", "1.2.3")
            self.assertEqual(result.returncode, 0, result.stderr)
            trace = gh_trace.read_text()
            self.assertIn("release download 1.2.3", trace)
            self.assertIn("release upload 1.2.3", trace)
            self.assertNotIn("--clobber", trace)

    def test_stable_stage_rejects_foreign_or_conflicting_draft_assets(self) -> None:
        cases = (
            ("foreign.tar.gz\n", "", "unexpected asset"),
            ("package.tar.gz\n", "different", "conflicts with candidate"),
        )
        for remote_assets, content, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temporary:
                environment, gh_trace, _ = self.fixture(
                    Path(temporary),
                    state="draft",
                    remote_assets=remote_assets,
                    download_content=content,
                )
                result = self.run_publisher(environment, "stable-stage", "1.2.3")
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
                self.assertNotIn("release edit", gh_trace.read_text())

    def test_stable_stage_rejects_changed_final_inventory_or_digest(self) -> None:
        cases = (
            (
                "package.tar.gz\npackage.tar.gz.sha256\nforeign.tar.gz\n",
                "",
                "unexpected asset",
            ),
            (
                "package.tar.gz\npackage.tar.gz.sha256\n",
                "package.tar.gz",
                "final digest changed",
            ),
        )
        for inventory, mismatched, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temporary:
                environment, gh_trace, _ = self.fixture(
                    Path(temporary),
                    state="draft",
                    remote_assets_after_upload=inventory,
                )
                environment["SERVER_DIGEST_MISMATCH"] = mismatched
                result = self.run_publisher(environment, "stable-stage", "1.2.3")
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
                self.assertNotIn("release edit", gh_trace.read_text())

    def test_stable_finalize_publishes_and_verifies_immutable_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, gh_trace, _ = self.fixture(
                Path(temporary),
                state="draft",
                remote_assets="package.tar.gz\npackage.tar.gz.sha256\n",
            )
            result = self.run_publisher(environment, "stable-finalize", "1.2.3")
            self.assertEqual(result.returncode, 0, result.stderr)
            trace = gh_trace.read_text()
            self.assertIn("--draft=false", trace)
            self.assertIn("--prerelease=false", trace)
            self.assertIn("isDraft,isImmutable", trace)
            self.assertIn("releases/latest --jq .tag_name", trace)

    def test_published_immutable_release_is_idempotently_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, gh_trace, _ = self.fixture(
                Path(temporary),
                state="published",
                remote_assets="package.tar.gz\npackage.tar.gz.sha256\n",
            )
            result = self.run_publisher(environment, "stable-stage", "1.2.3")
            self.assertEqual(result.returncode, 0, result.stderr)
            trace = gh_trace.read_text()
            self.assertIn("isDraft,isImmutable", trace)
            self.assertNotIn("release create", trace)
            self.assertNotIn("release edit", trace)

    def test_stable_publication_requires_repository_immutability(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, gh_trace, git_trace = self.fixture(
                Path(temporary), immutable_setting=False
            )
            result = self.run_publisher(environment, "stable-stage", "1.2.3")
            self.assertEqual(result.returncode, 1)
            self.assertIn("immutable releases", result.stderr)
            self.assertNotIn("release create", gh_trace.read_text())
            self.assertFalse(git_trace.exists())

    def test_stable_publication_requires_exact_remote_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, gh_trace, _ = self.fixture(
                Path(temporary), remote_tag="f" * 40
            )
            result = self.run_publisher(environment, "stable-stage", "1.2.3")
            self.assertEqual(result.returncode, 1)
            self.assertIn("stable tag target mismatch", result.stderr)
            self.assertNotIn("release create", gh_trace.read_text())

    def test_stable_recovery_rejects_mutable_published_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, _, _ = self.fixture(
                Path(temporary),
                state="published",
                published_immutable=False,
                remote_assets="package.tar.gz\npackage.tar.gz.sha256\n",
            )
            result = self.run_publisher(environment, "stable-finalize", "1.2.3")
            self.assertEqual(result.returncode, 1)
            self.assertIn("immutability", result.stderr)


if __name__ == "__main__":
    unittest.main()
