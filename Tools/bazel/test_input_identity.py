"""Source mutation detection separate from action-cache scheduling."""

import os
import json
from pathlib import Path
import tempfile
import unittest

from input_identity import file_identity, tooling_identity, verify


class InputIdentityTests(unittest.TestCase):
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
