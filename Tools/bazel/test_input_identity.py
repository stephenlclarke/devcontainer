"""Source mutation detection separate from action-cache scheduling."""

import os
import json
from pathlib import Path
import tempfile
import subprocess
import unittest

from input_identity import file_identity, source_identity, tooling_identity, verify


class InputIdentityTests(unittest.TestCase):
    def test_parent_components_cannot_hide_an_intermediate_directory_link(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            root = Path(directory)
            self.git(root, "init")
            (root / ".gitignore").write_text("Generated/Hop\n")
            (root / "Generated").mkdir()
            (root / "Generated/A.swift").write_text("lexical target\n")
            (root / "Else/Child").mkdir(parents=True)
            (root / "Else/A.swift").write_text("actual target\n")
            (root / "Generated/Hop").symlink_to("../Else/Child")
            (root / "Sources").mkdir()
            (root / "Sources/Linked.swift").symlink_to("../Generated/Hop/../A.swift")
            self.git(root, "add", "--all")
            self.commit(root)
            self.assertTrue(source_identity(root)["dirty"])

    def test_indirect_source_links_cannot_attest_a_clean_commit(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            root = Path(directory)
            self.git(root, "init")
            (root / ".gitignore").write_text("Generated/\n")
            for name in ("A.swift", "B.swift"):
                (root / name).write_text(name)
            intermediate = root / "Generated" / "Current.swift"
            intermediate.parent.mkdir()
            intermediate.symlink_to("../A.swift")
            source = root / "Sources" / "Linked.swift"
            source.parent.mkdir()
            source.symlink_to("../Generated/Current.swift")
            self.git(root, "add", "--all")
            self.commit(root)
            self.assertTrue(source_identity(root)["dirty"])
            intermediate.unlink()
            intermediate.symlink_to("../B.swift")
            self.assertTrue(source_identity(root)["dirty"])

    def test_committed_link_cannot_attest_an_ignored_untracked_target(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            root = Path(directory)
            self.git(root, "init")
            (root / ".gitignore").write_text("Generated/\n")
            target = root / "Generated" / "Hidden.swift"
            target.parent.mkdir()
            target.write_text("not committed\n")
            source = root / "Sources" / "Linked.swift"
            source.parent.mkdir()
            source.symlink_to("../Generated/Hidden.swift")
            self.git(root, "add", "--all")
            self.commit(root)
            self.assertTrue(source_identity(root)["dirty"])
            self.git(root, "add", "--force", str(target))
            self.commit(root)
            self.assertFalse(source_identity(root)["dirty"])

    def test_commit_comparison_tracks_bytes_links_modes_and_deletions(self) -> None:
        for object_format in ("sha1", "sha256"):
            with self.subTest(format=object_format), tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
                root = Path(directory)
                self.git(root, "init", "--object-format=" + object_format)
                source = root / "source.swift"
                source.write_text("committed\n")
                link = root / "linked.swift"
                link.symlink_to("source.swift")
                self.git(root, "add", "--all")
                self.commit(root)
                self.assertFalse(source_identity(root)["dirty"])
                self.git(root, "config", "core.filemode", "false")
                source.chmod(0o755)
                self.assertTrue(source_identity(root)["dirty"])
                source.chmod(0o644)
                self.assertFalse(source_identity(root)["dirty"])
                source.write_text("modified\n")
                self.assertTrue(source_identity(root)["dirty"])
                source.write_text("committed\n")
                link.unlink()
                self.assertTrue(source_identity(root)["dirty"])

    def test_index_flags_cannot_attest_a_clean_source(self) -> None:
        for flag in ("--assume-unchanged", "--skip-worktree"):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
                root = Path(directory)
                self.git(root, "init")
                source = root / "source.swift"
                source.write_text("committed\n")
                self.git(root, "add", "source.swift")
                self.commit(root)
                self.git(root, "update-index", flag, "source.swift")
                source.write_text("modified\n")
                self.assertTrue(source_identity(root)["dirty"])

    def test_ignored_source_inputs_are_hashed_and_dirty(self) -> None:
        for exclusion in (".gitignore", ".git/info/exclude", "global-excludes"):
            with self.subTest(exclusion=exclusion), tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
                root = Path(directory)
                self.git(root, "init")
                (root / exclusion).write_text("Sources/\n")
                if exclusion == "global-excludes":
                    self.git(root, "config", "core.excludesFile", str(root / exclusion))
                self.git(root, "add", "--all")
                self.commit(root)
                self.assertFalse(source_identity(root)["dirty"])
                source = root / "Sources" / "Module.docc" / "Hidden.md"
                source.parent.mkdir(parents=True)
                source.write_text("# Uncommitted\n")
                before = source_identity(root)
                self.assertTrue(before["dirty"])
                self.assertIn("Sources/Module.docc/Hidden.md", before["files"])
                source.write_text("# Changed\n")
                with self.assertRaisesRegex(ValueError, "Sources changed"):
                    verify(before, source_identity(root))

    @staticmethod
    def git(root: Path, *arguments: str) -> None:
        subprocess.run(["/usr/bin/git", "-C", str(root), *arguments], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def commit(self, root: Path) -> None:
        self.git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "fixture")

    def test_hidden_untracked_sources_still_make_identity_dirty(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            root = Path(directory)

            def git(*arguments: str) -> None:
                subprocess.run(["/usr/bin/git", "-C", str(root), *arguments], check=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            git("init")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "fixture")
            git("config", "status.showUntrackedFiles", "no")
            self.assertFalse(source_identity(root)["dirty"])
            source = root / "Sources" / "New.swift"
            source.parent.mkdir()
            source.write_text("public struct New {}\n")
            identity = source_identity(root)
            self.assertIn("Sources/New.swift", identity["files"])
            self.assertTrue(identity["dirty"])

    def test_external_tooling_requires_exact_consumer_lock(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            root = Path(directory)
            tools = root / "shared"
            tools.mkdir()
            (tools / "run.sh").write_text("fixture")
            consumer = root / "consumer"
            lock = consumer / "Tools/bazel/workflow-tooling.json"
            lock.parent.mkdir(parents=True)
            expected = {"run.sh": file_identity(tools / "run.sh", tools)}
            lock.write_text(json.dumps(expected))
            self.assertEqual(tooling_identity(tools, consumer), expected)
            (tools / "run.sh").write_text("changed")
            with self.assertRaisesRegex(ValueError, "reviewed lock"):
                tooling_identity(tools, consumer)

    def test_tooling_mutations_invalidate_evidence(self) -> None:
        before = {"commit": "a", "files": {}, "tooling": {"run.sh": "original"}}
        verify(before, before)
        with self.assertRaisesRegex(ValueError, "tooling changed"):
            verify(before, dict(before, tooling={"run.sh": "changed"}))

    def test_modes_and_linked_bytes_are_inputs(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            root = Path(directory).resolve()
            source = root / "source"
            source.write_text("first")
            first = file_identity(source, root)
            source.chmod(0o700)
            self.assertNotEqual(first, file_identity(source, root))
            link = root / "link"
            link.symlink_to(source.name)
            before = file_identity(link, root)
            source.write_text("second")
            self.assertNotEqual(before, file_identity(link, root))
            (root / "outside").symlink_to("/etc/hosts")
            with self.assertRaises(ValueError):
                file_identity(root / "outside", root)
            self.assertEqual(file_identity(root / "missing", root), {"deleted": True})
            with self.assertRaises(ValueError):
                file_identity(root, root)

    def test_unchanged_and_generated_lock_updates(self) -> None:
        before = {"commit": "a", "files": {"main.swift": "one", "MODULE.bazel.lock": "old"}}
        verify(before, before)
        verify(before, {"commit": "a", "files": {"main.swift": "one", "MODULE.bazel.lock": "new"}})

    def test_edits_additions_deletions_and_head_changes_fail(self) -> None:
        before = {"commit": "a", "files": {"main.swift": "one"}}
        for after in [
            {"commit": "b", "files": before["files"]},
            {"commit": "a", "files": {"main.swift": "two"}},
            {"commit": "a", "files": {}},
            {"commit": "a", "files": {"main.swift": "one", "new.swift": "new"}},
        ]:
            with self.subTest(after=after), self.assertRaises(ValueError):
                verify(before, after)


if __name__ == "__main__":
    unittest.main()
