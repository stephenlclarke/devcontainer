"""Focused safety tests for the thin Bazel launcher, not a build coordinator."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("run.sh")
UUID = "884BCCCF-5C0C-4A9B-B412-04FD1C1A6895"


def invoke(function: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Source the guarded entry point and invoke one real shell policy function."""
    return subprocess.run(
        ["/bin/bash", "-c", 'source "$1"; shift; "$@"', "test", str(SCRIPT), function, *args],
        capture_output=True,
        text=True,
        check=False,
    )


class LauncherTests(unittest.TestCase):
    def test_valid_external_volume(self) -> None:
        self.assertEqual(invoke("validate_volume", UUID, UUID, "/Volumes/SSD", "false").returncode, 0)

    def test_unenrolled_replaced_internal_or_alias_volume_is_rejected(self) -> None:
        for expected, actual, mount, internal in [
            ("", UUID, "/Volumes/SSD", "false"),
            ("invalid", UUID, "/Volumes/SSD", "false"),
            (UUID, "00000000-0000-0000-0000-000000000000", "/Volumes/SSD", "false"),
            (UUID, UUID, "/Volumes/SSD-alias", "false"),
            (UUID, UUID, "/Volumes/SSD", "true"),
        ]:
            with self.subTest(expected=expected, actual=actual, mount=mount, internal=internal):
                self.assertEqual(invoke("validate_volume", expected, actual, mount, internal).returncode, 2)

    def test_storage_environment_and_remote_overrides_are_rejected(self) -> None:
        for argument in [
            "--output_base=/tmp", "--disk_cache=/tmp", "--repository_cache=/tmp",
            "--test_tmpdir=/tmp", "--sandbox_base=/tmp", "--bazelrc=/tmp/rc",
            "--build_event_json_file=/tmp/events", "--action_env=TMPDIR=/tmp",
            "--test_env=TMPDIR=/tmp", "--remote_cache=https://example.invalid",
            "--host_jvm_args=-Djava.io.tmpdir=/tmp", "--profile=/tmp/profile",
            "--symlink_prefix=/tmp/", "--repo_env=TMPDIR=/tmp",
        ]:
            with self.subTest(argument=argument):
                self.assertEqual(invoke("validate_arguments", argument).returncode, 2)

    def test_normal_target_selection_and_test_output_are_allowed(self) -> None:
        self.assertEqual(invoke("validate_arguments", "//:bazel_qualification", "--test_output=all", "--config=asan").returncode, 0)

    def test_directory_creation_rejects_symlink_escapes_and_files(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            root = Path(directory).resolve()
            target = root / "nested" / "owned"
            self.assertEqual(invoke("ensure_directory", str(target)).returncode, 0)
            self.assertTrue(target.is_dir())
            self.assertEqual(invoke("ensure_directory", str(target)).returncode, 0)
            (root / "escape").symlink_to(target, target_is_directory=True)
            self.assertEqual(invoke("ensure_directory", str(root / "escape" / "child")).returncode, 2)
            self.assertFalse((target / "child").exists())
            (root / "file").write_text("not a directory", encoding="utf-8")
            self.assertEqual(invoke("ensure_directory", str(root / "file" / "child")).returncode, 2)
            self.assertEqual(invoke("ensure_directory", str(root) + "/../escape").returncode, 2)
            self.assertEqual(invoke("ensure_directory", "relative/path").returncode, 2)

    def test_cached_tool_bytes_are_authenticated_and_symlinks_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            root = Path(directory).resolve()
            tool = root / "tool"
            tool.write_bytes(b"expected tool")
            digest = hashlib.sha256(tool.read_bytes()).hexdigest()
            self.assertEqual(invoke("verify_digest", str(tool), digest).returncode, 0)
            tool.write_bytes(b"changed")
            self.assertNotEqual(invoke("verify_digest", str(tool), digest).returncode, 0)
            (root / "link").symlink_to(tool)
            self.assertNotEqual(invoke("verify_digest", str(root / "link"), digest).returncode, 0)
            self.assertNotEqual(invoke("verify_digest", str(root / "missing"), digest).returncode, 0)

    def test_help_and_invalid_commands_do_not_bootstrap(self) -> None:
        for argument, status in [("--help", 0), ("clean", 2), ("release", 2)]:
            result = subprocess.run(["/bin/bash", str(SCRIPT), argument], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, status)
            self.assertIn("Usage:", result.stdout + result.stderr)
            self.assertNotIn("Bazel evidence:", result.stderr)


if __name__ == "__main__":
    unittest.main()
