"""Tests for immutable stable and commit-addressed Current publication."""

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
    def test_release_creation_uses_bounded_reconciliation_helper(self) -> None:
        contents = PUBLISHER.read_text(encoding="utf-8")

        self.assertIn('"$SELF_DIRECTORY/create-github-release-draft.sh"', contents)
        self.assertIn("RELEASE_GITHUB_RETRY_ATTEMPTS", contents)
        self.assertIn("recreate_release_draft", contents)
        self.assertNotIn('"$GH" release create', contents)

    def test_help_lists_every_supported_mode(self) -> None:
        result = subprocess.run(
            [str(PUBLISHER), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for mode in (
            "current-stage",
            "current-finalize",
            "stable-stage",
            "stable-finalize",
            "stable-promote",
        ):
            with self.subTest(mode=mode):
                self.assertIn(mode, result.stdout)

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

            if args[:2] == ["attestation", "verify"]:
                artifact = Path(args[2]).name
                if os.environ["ATTESTATION_FAILURE"] == artifact:
                    print("attestation rejected", file=sys.stderr)
                    raise SystemExit(1)
                raise SystemExit(0)

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
                if state.startswith("published") and os.environ["DOWNLOAD_CONTENT"]:
                    content = os.environ["DOWNLOAD_CONTENT"].encode()
                else:
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
                    latest = (
                        tag
                        if Path(os.environ["GH_LATEST_MARKER"]).exists()
                        else os.environ["LATEST_TAG"]
                    )
                    if "--jq" in args:
                        print(latest)
                    else:
                        print(json.dumps({"tag_name": latest}))
                    raise SystemExit(0)
                if "/releases/tags/" in endpoint:
                    if state == "missing":
                        print("HTTP 404: Not Found", file=sys.stderr)
                        raise SystemExit(1)
                    document = {
                        "tag_name": tag,
                        "draft": state in {"draft", "current-draft"},
                        "prerelease": state in {
                            "current-draft", "published-current"
                        },
                        "immutable": (
                            state.startswith("published")
                            and os.environ["PUBLISHED_IMMUTABLE"] == "true"
                        ),
                    }
                    print(json.dumps(document))
                    raise SystemExit(0)
                raise SystemExit(0)

            if args[:2] == ["release", "create"]:
                if "--draft" in args:
                    created = (
                        "current-draft"
                        if any(
                            value in {"--prerelease", "--prerelease=true"}
                            for value in args
                        )
                        else "draft"
                    )
                    state_path.write_text(created, encoding="utf-8")
                else:
                    created = "published-current" if "--prerelease" in args else "published"
                    state_path.write_text(created, encoding="utf-8")
                raise SystemExit(0)

            if args[:2] == ["release", "delete"]:
                state_path.write_text("missing", encoding="utf-8")
                Path(os.environ["GH_UPLOAD_MARKER"]).unlink(missing_ok=True)
                raise SystemExit(0)

            if args[:2] == ["release", "upload"]:
                Path(os.environ["GH_UPLOAD_MARKER"]).touch()
                raise SystemExit(0)

            if args[:2] == ["release", "edit"]:
                if "--draft=false" in args:
                    published = (
                        "published-current"
                        if "--prerelease" in args
                        else "published"
                    )
                    state_path.write_text(published, encoding="utf-8")
                if "--latest" in args:
                    Path(os.environ["GH_LATEST_MARKER"]).touch()
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
                if requested == "isDraft,tagName,targetCommitish":
                    print(json.dumps({
                        "isDraft": state in {"draft", "current-draft"},
                        "tagName": tag,
                        "targetCommitish": os.environ["PUBLISH_SHA"],
                    }))
                    raise SystemExit(0)
                if requested == (
                    "isDraft,isPrerelease,tagName,targetCommitish,name"
                ):
                    print(json.dumps({
                        "isDraft": state in {"draft", "current-draft"},
                        "isPrerelease": state == "current-draft",
                        "tagName": tag,
                        "targetCommitish": os.environ["PUBLISH_SHA"],
                        "name": os.environ["RELEASE_TITLE"],
                    }))
                    raise SystemExit(0)
                if requested == "assets" and "--jq" in args:
                    print("\n".join(names()))
                    raise SystemExit(0)
                if requested == "isDraft,assets":
                    print(json.dumps({
                        "isDraft": state in {"draft", "current-draft"},
                        "assets": assets(),
                    }))
                    raise SystemExit(0)
                if requested == "isImmutable" and "--jq" in args:
                    print(str(
                        state.startswith("published")
                        and os.environ["PUBLISHED_IMMUTABLE"] == "true"
                    ).lower())
                    raise SystemExit(0)
                notes = Path(os.environ["RELEASE_NOTES_FILE"]).read_text(
                    encoding="utf-8"
                )
                print(json.dumps({
                    "isDraft": False,
                    "isImmutable": os.environ["PUBLISHED_IMMUTABLE"] == "true",
                    "isPrerelease": state == "published-current",
                    "tagName": tag,
                    # GitHub documents target_commitish as the source used only
                    # when it creates a missing tag. Existing signed stable tags
                    # commonly report the default branch here, so the publisher
                    # authenticates the actual remote tag target independently.
                    "targetCommitish": "main",
                    "name": os.environ["PUBLISHED_TITLE"],
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
            '  tag_target="$REMOTE_TAG"\n'
            '  if [[ -e "$GIT_LOOKUP_MARKER" && '
            '-n "$REMOTE_TAG_AFTER_FIRST_LOOKUP" ]]; then\n'
            '    tag_target="$REMOTE_TAG_AFTER_FIRST_LOOKUP"\n'
            "  fi\n"
            '  : > "$GIT_LOOKUP_MARKER"\n'
            '  if [[ -n "$tag_target" ]]; then\n'
            '    printf "%s\\trefs/tags/%s^{}\\n" "$tag_target" "$RELEASE_TAG"\n'
            "  fi\n"
            "fi\n",
        )
        recovery_trace = root / "recovery-verifier.trace"
        self.write_executable(
            fake_bin / "verify-recovered-assets",
            'printf "%s\\n" "$*" >> "$RECOVERY_VERIFIER_TRACE"\n'
            'if [[ "$RECOVERY_VERIFIER_FAILURE" == true ]]; then\n'
            '  printf "recovered package verification failed\\n" >&2\n'
            "  exit 1\n"
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
                "ATTESTATION_FAILURE": "",
                "CHECKSUM": str(checksum),
                "DOWNLOAD_CONTENT": download_content,
                "GH": str(fake_bin / "gh"),
                "GH_STATE": str(state_file),
                "GH_TRACE": str(gh_trace),
                "GH_UPLOAD_MARKER": str(root / "upload.marker"),
                "GH_LATEST_MARKER": str(root / "latest.marker"),
                "GIT": str(fake_bin / "git"),
                "GIT_LOOKUP_MARKER": str(root / "git-lookup.marker"),
                "GIT_TRACE": str(git_trace),
                "IMMUTABLE_SETTING": str(immutable_setting).lower(),
                "PUBLISHED_IMMUTABLE": str(published_immutable).lower(),
                "PUBLISHED_TITLE": "Release",
                "PUBLISH_SHA": COMMIT,
                "RELEASE_ASSETS_FILE": str(manifest),
                "RELEASE_NOTES_FILE": str(notes),
                "RELEASE_REPOSITORY": "stephenlclarke/devcontainer",
                "RELEASE_RECOVERY_VERIFIER": str(
                    fake_bin / "verify-recovered-assets"
                ),
                "RELEASE_SIGNER_WORKFLOW": (
                    "stephenlclarke/devcontainer/"
                    ".github/workflows/prebuilt-binaries.yml"
                ),
                "RELEASE_SOURCE_REF": "refs/heads/main",
                "RELEASE_TITLE": "Release",
                "REMOTE_ASSETS": remote_assets,
                "REMOTE_ASSETS_AFTER_UPLOAD": remote_assets_after_upload,
                "REMOTE_TAG": remote_tag,
                "REMOTE_TAG_AFTER_FIRST_LOOKUP": "",
                "SERVER_DIGEST_MISMATCH": "",
                "LATEST_TAG": "1.2.3",
                "RELEASE_IMMUTABILITY_ATTEMPTS": "1",
                "RELEASE_IMMUTABILITY_DELAY_SECONDS": "0",
                "RECOVERY_VERIFIER_FAILURE": "false",
                "RECOVERY_VERIFIER_TRACE": str(recovery_trace),
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

    def test_current_stage_and_finalize_publish_commit_addressed_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, gh_trace, git_trace = self.fixture(Path(temporary))
            tag = f"current-{COMMIT}"
            result = self.run_publisher(environment, "current-stage", tag)
            self.assertEqual(result.returncode, 0, result.stderr)
            trace = gh_trace.read_text()
            self.assertIn(f"release create {tag}", trace)
            self.assertIn("--draft", trace)
            self.assertIn("--prerelease", trace)
            self.assertIn("ls-remote --tags", git_trace.read_text())

            result = self.run_publisher(environment, "current-finalize", tag)
            self.assertEqual(result.returncode, 0, result.stderr)
            trace = gh_trace.read_text()
            self.assertIn(f"release edit {tag}", trace)
            self.assertIn("--draft=false", trace)
            self.assertIn("--prerelease", trace)
            self.assertIn("isDraft,isImmutable", trace)

    def test_current_rejects_moving_or_mismatched_tags(self) -> None:
        for tag in ("current", f"current-{'f' * 40}"):
            with self.subTest(tag=tag), tempfile.TemporaryDirectory() as temporary:
                environment, gh_trace, _ = self.fixture(Path(temporary))
                result = self.run_publisher(environment, "current-stage", tag)
                self.assertEqual(result.returncode, 2)
                self.assertIn("commit-addressed tag", result.stderr)
                self.assertFalse(gh_trace.exists())

    def test_current_rejects_preexisting_tag_with_different_target(self) -> None:
        cases = (
            ("current-stage", "missing"),
            ("current-finalize", "current-draft"),
        )
        for mode, state in cases:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                environment, gh_trace, _ = self.fixture(
                    Path(temporary),
                    state=state,
                    remote_tag="f" * 40,
                    remote_assets="package.tar.gz\npackage.tar.gz.sha256\n",
                )
                result = self.run_publisher(
                    environment, mode, f"current-{COMMIT}"
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn("Current release tag target mismatch", result.stderr)
                trace = gh_trace.read_text()
                self.assertNotIn("release create", trace)
                self.assertNotIn("release edit", trace)

    def test_current_stage_allows_github_to_create_absent_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, gh_trace, _ = self.fixture(
                Path(temporary), remote_tag=""
            )
            tag = f"current-{COMMIT}"
            result = self.run_publisher(environment, "current-stage", tag)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"release create {tag}", gh_trace.read_text())

    def test_current_finalize_rechecks_tag_immediately_before_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, gh_trace, _ = self.fixture(
                Path(temporary),
                state="current-draft",
                remote_assets="package.tar.gz\npackage.tar.gz.sha256\n",
            )
            environment["REMOTE_TAG_AFTER_FIRST_LOOKUP"] = "f" * 40
            result = self.run_publisher(
                environment, "current-finalize", f"current-{COMMIT}"
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("Current release tag target mismatch", result.stderr)
            self.assertNotIn("release edit", gh_trace.read_text())

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

    def test_stable_stage_replaces_foreign_or_conflicting_private_draft(
        self,
    ) -> None:
        cases = (
            ("foreign.tar.gz\n", ""),
            ("package.tar.gz\n", "different"),
        )
        for remote_assets, content in cases:
            with self.subTest(assets=remote_assets), tempfile.TemporaryDirectory() as temporary:
                environment, gh_trace, _ = self.fixture(
                    Path(temporary),
                    state="draft",
                    remote_assets=remote_assets,
                    download_content=content,
                )
                result = self.run_publisher(environment, "stable-stage", "1.2.3")
                self.assertEqual(result.returncode, 0, result.stderr)
                trace = gh_trace.read_text()
                self.assertIn("release delete 1.2.3", trace)
                self.assertIn("release create 1.2.3", trace)
                self.assertIn("release upload 1.2.3", trace)
                self.assertNotIn("--draft=false", trace)

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

    def test_stable_finalize_publishes_without_promoting_latest(self) -> None:
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
            self.assertIn("--latest=false", trace)
            self.assertIn("isDraft,isImmutable", trace)
            self.assertNotIn("releases/latest --jq .tag_name", trace)

    def test_stable_promote_marks_verified_release_latest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, gh_trace, _ = self.fixture(
                Path(temporary),
                state="published",
                remote_assets="package.tar.gz\npackage.tar.gz.sha256\n",
            )
            environment["LATEST_TAG"] = "1.2.2"
            result = self.run_publisher(environment, "stable-promote", "1.2.3")
            self.assertEqual(result.returncode, 0, result.stderr)
            trace = gh_trace.read_text()
            self.assertIn("release edit 1.2.3", trace)
            self.assertIn("--latest", trace)
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
            self.assertIn("release download 1.2.3", trace)
            self.assertIn("isDraft,isImmutable", trace)
            self.assertNotIn("release create", trace)
            self.assertNotIn("release edit", trace)

    def test_published_recovery_replaces_new_notarized_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, gh_trace, _ = self.fixture(
                Path(temporary),
                state="published",
                remote_assets="package.tar.gz\npackage.tar.gz.sha256\n",
                download_content="published-bytes",
            )
            result = self.run_publisher(environment, "stable-stage", "1.2.3")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                Path(environment["ARCHIVE"]).read_text(encoding="utf-8"),
                "published-bytes",
            )
            self.assertEqual(
                Path(environment["CHECKSUM"]).read_text(encoding="utf-8"),
                "published-bytes",
            )
            self.assertEqual(
                gh_trace.read_text().count("release download 1.2.3"),
                2,
            )
            trace = gh_trace.read_text()
            self.assertEqual(trace.count("attestation verify"), 2)
            self.assertIn(
                "--signer-workflow stephenlclarke/devcontainer/"
                ".github/workflows/prebuilt-binaries.yml",
                trace,
            )
            self.assertIn(f"--source-digest {COMMIT}", trace)
            self.assertIn("--source-ref refs/heads/main", trace)
            recovery_trace = Path(environment["RECOVERY_VERIFIER_TRACE"])
            self.assertEqual(
                recovery_trace.read_text(encoding="utf-8").strip().split()[1],
                COMMIT,
            )

    def test_published_recovery_rejects_untrusted_provenance_before_replacement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, gh_trace, _ = self.fixture(
                Path(temporary),
                state="published",
                remote_assets="package.tar.gz\npackage.tar.gz.sha256\n",
                download_content="published-bytes",
            )
            environment["ATTESTATION_FAILURE"] = "package.tar.gz"
            result = self.run_publisher(environment, "stable-stage", "1.2.3")
            self.assertEqual(result.returncode, 1)
            self.assertIn("attestation rejected", result.stderr)
            self.assertEqual(Path(environment["ARCHIVE"]).read_bytes(), b"archive")
            self.assertEqual(
                Path(environment["CHECKSUM"]).read_text(encoding="utf-8"),
                "checksum\n",
            )
            self.assertIn("attestation verify", gh_trace.read_text())
            self.assertFalse(Path(environment["RECOVERY_VERIFIER_TRACE"]).exists())

    def test_published_recovery_rejects_failed_package_authority_before_replacement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, _, _ = self.fixture(
                Path(temporary),
                state="published",
                remote_assets="package.tar.gz\npackage.tar.gz.sha256\n",
                download_content="published-bytes",
            )
            environment["RECOVERY_VERIFIER_FAILURE"] = "true"
            result = self.run_publisher(environment, "stable-stage", "1.2.3")
            self.assertEqual(result.returncode, 1)
            self.assertIn("recovered package verification failed", result.stderr)
            self.assertEqual(Path(environment["ARCHIVE"]).read_bytes(), b"archive")
            self.assertEqual(
                Path(environment["CHECKSUM"]).read_text(encoding="utf-8"),
                "checksum\n",
            )

    def test_published_current_is_recovered_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, gh_trace, _ = self.fixture(
                Path(temporary),
                state="published-current",
                remote_assets="package.tar.gz\npackage.tar.gz.sha256\n",
                download_content="published-current-bytes",
            )
            tag = f"current-{COMMIT}"
            result = self.run_publisher(environment, "current-stage", tag)
            self.assertEqual(result.returncode, 0, result.stderr)
            trace = gh_trace.read_text()
            self.assertIn(f"release download {tag}", trace)
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
            self.assertIn("release tag target mismatch", result.stderr)
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
            self.assertIn("immutable", result.stderr)

    def test_stable_recovery_rejects_changed_metadata_or_assets(self) -> None:
        cases = (
            ("PUBLISHED_TITLE", "Different", "title"),
            ("SERVER_DIGEST_MISMATCH", "package.tar.gz", "digest mismatch"),
        )
        for variable, value, message in cases:
            with self.subTest(variable=variable), tempfile.TemporaryDirectory() as temporary:
                environment, _, _ = self.fixture(
                    Path(temporary),
                    state="published",
                    remote_assets="package.tar.gz\npackage.tar.gz.sha256\n",
                )
                environment[variable] = value
                result = self.run_publisher(
                    environment, "stable-finalize", "1.2.3"
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)


if __name__ == "__main__":
    unittest.main()
