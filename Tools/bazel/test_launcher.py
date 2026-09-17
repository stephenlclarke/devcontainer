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
    def test_shared_launcher_remains_absolute_after_changing_workspaces(self) -> None:
        result = subprocess.run(
            ["/bin/bash", "-c", 'source ./run.sh; cd /; /bin/bash -c \'source "$1"; printf "%s" "$SELF_PATH"\' test "$SELF_PATH"'],
            cwd=SCRIPT.parent, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, str(SCRIPT.resolve()))

    def test_test_wrapper_declares_only_validated_ssd_scratch(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            for temporary, status in [(directory, 0), ("/tmp", 2)]:
                result = subprocess.run(
                    ["/bin/bash", str(SCRIPT.with_name("ssd-test-runner.sh")),
                     "/usr/bin/printenv", "DEVCONTAINER_TEST_SCRATCH_ROOT"],
                    env={"PATH": "/usr/bin:/bin", "TEST_TMPDIR": temporary},
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, status)
                if status == 0:
                    self.assertEqual(result.stdout.strip(), "/Volumes/SSD/cf/bazel/")

    def test_test_wrapper_rejects_scratch_symlink_outside_ssd(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            alias = Path(directory) / "escape"
            alias.symlink_to("/private/tmp", target_is_directory=True)
            result = subprocess.run(
                ["/bin/bash", str(SCRIPT.with_name("ssd-test-runner.sh")), "/usr/bin/true"],
                env={"PATH": "/usr/bin:/bin", "TEST_TMPDIR": str(alias)}, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 2)

    def test_test_wrapper_marks_wrapped_process_as_bazel_even_without_inherited_marker(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            result = subprocess.run(
                ["/bin/bash", str(SCRIPT.with_name("ssd-test-runner.sh")), "/usr/bin/printenv", "BAZEL_TEST"],
                env={"PATH": "/usr/bin:/bin", "TEST_TMPDIR": directory}, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "1")

    def test_legacy_integration_entry_point_selects_manifest_opt_in(self) -> None:
        result = subprocess.run(["/usr/bin/make", "-n", "test-integration"], cwd=SCRIPT.parents[2],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DEVCONTAINER_HOST_INTEGRATION=1", result.stdout)
        self.assertNotIn("DEVCONTAINER_RUN_HOST_INTEGRATION", result.stdout)

    def test_real_argument_assembly_handles_empty_arrays_on_system_bash(self) -> None:
        for command, targets in [("query", ["//:product"]), ("info", []), ("coverage", ["//:unit", "--config=stock"])]:
            with self.subTest(command=command):
                result = subprocess.run(
                    ["/bin/bash", "-c", 'source "$1"; shift; clean_environment() { printf "%s\\0" "$@"; }; run_bazel /repo "$1" /invocation /pinned-bazel stock "${@:2}"',
                     "test", str(SCRIPT), command, *targets],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                args = result.stdout.split("\0")[:-1]
                self.assertEqual(args.count("--config=stock"), 1)
                self.assertIn(command, args)
                self.assertEqual(args[0], "/usr/bin/python3" if command == "coverage" else "/pinned-bazel")
                self.assertEqual(any(a.startswith("--disk_cache=") for a in args), command == "coverage")
                self.assertEqual(any(a.startswith("--test_tmpdir=") for a in args), command == "coverage")

    def test_build_environment_excludes_credentials_and_shell_hooks(self) -> None:
        result = subprocess.run(
            ["/bin/bash", "-c", 'source "$1"; export UNRELATED_SECRET=fixture-secret BASH_ENV=/does/not/exist PYTHONPATH=/untrusted; clean_environment /usr/bin/env',
             "test", str(SCRIPT)], capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        values = dict(line.split("=", 1) for line in result.stdout.splitlines())
        self.assertEqual(set(values), {"HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "TMPDIR", "TMP", "TEMP", "DEVELOPER_DIR", "PYTHONDONTWRITEBYTECODE", "DEVCONTAINER_HOST_INTEGRATION"})
        self.assertEqual(values["PATH"], "/usr/bin:/bin:/usr/sbin:/sbin")

    def test_host_opt_in_is_preserved_only_as_a_boolean(self) -> None:
        for value, expected in [("1", 0), ("0", 0), ("not-a-boolean", 2)]:
            result = subprocess.run(
                ["/bin/bash", "-c", 'source "$1"; export DEVCONTAINER_HOST_INTEGRATION="$2"; clean_environment /usr/bin/printenv DEVCONTAINER_HOST_INTEGRATION',
                 "test", str(SCRIPT), value], capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, expected)
            if expected == 0:
                self.assertEqual(result.stdout.strip(), value)

    def test_runtime_profile_is_one_coherent_selection(self) -> None:
        self.assertEqual(invoke("runtime_profile").stdout.strip(), "enhanced")
        self.assertEqual(invoke("runtime_profile", "--config=stock", "--config=asan").stdout.strip(), "stock")
        self.assertEqual(invoke("runtime_profile", "--config=stock", "--config=enhanced").returncode, 2)
        self.assertEqual(invoke("runtime_profile", "--config", "stock").returncode, 2)
        self.assertEqual(invoke("validate_arguments", "--define=runtime_profile=stock").returncode, 2)

    def test_profile_expansion_is_not_duplicated(self) -> None:
        result = invoke("execution_arguments", "--config=stock", "//:unit", "--config=asan", "--test_arg=two words", "--config=stock")
        self.assertEqual(result.stdout.split("\0"), ["//:unit", "--config=asan", "--test_arg=two words", ""])

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
            "--override_module=rules_swift=/tmp/local", "--override_repository=lib=/tmp/local",
            "--lockfile_mode=off", "--registry=https://example.invalid", "--noenable_bzlmod",
        ]:
            with self.subTest(argument=argument):
                self.assertEqual(invoke("validate_arguments", argument).returncode, 2)

    def test_equivalent_qualification_labels(self) -> None:
        for label in ["bazel_qualification", ":bazel_qualification", "//:bazel_qualification"]:
            self.assertEqual(invoke("is_qualification_label", label).returncode, 0)
        self.assertNotEqual(invoke("is_qualification_label", "//other:bazel_qualification").returncode, 0)

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

    def test_workspace_prefix_rejects_missing_or_relative_paths(self) -> None:
        for arguments in [("--workspace",), ("--workspace", "relative", "build")]:
            result = subprocess.run(["/bin/bash", str(SCRIPT), *arguments], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("Bazel evidence:", result.stderr)


if __name__ == "__main__":
    unittest.main()
