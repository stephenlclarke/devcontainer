"""Focused safety tests for the thin Bazel launcher, not a build coordinator."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
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


def ad_hoc_fixture(directory: str, source: str) -> Path:
    """Give a disposable executable the same ad-hoc signing class as Bazel."""
    executable = Path(directory) / Path(source).name
    # Apple platform signatures cannot be launched from a copied path.
    shutil.copy(source, executable)
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(executable)],
                   capture_output=True, check=True)
    return executable


class LauncherTests(unittest.TestCase):
    def test_coverage_gate_routes_exact_inventory_without_building(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            repo = Path(directory)
            policy = repo / "Tools/bazel/evidence-policy.json"
            policy.parent.mkdir(parents=True)
            policy.write_text("{}")
            subprocess.run(["/usr/bin/git", "init", "-q", str(repo)], check=True)
            subprocess.run(["/usr/bin/git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["/usr/bin/git", "-C", str(repo), "-c", "user.name=Fixture", "-c",
                            "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"], check=True)
            commit = subprocess.check_output(["/usr/bin/git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()

            def report(*arguments):
                return subprocess.run(
                    ["/bin/bash", "-c", 'source "$1"; shift; clean_environment() { printf "%s\\0" "$@"; }; export_coverage_report "$@"',
                     "test", str(SCRIPT), str(repo), "stock", "invocation", *arguments], capture_output=True, text=True, check=False)

            for inventory in ("unit", "unit-cli"):
                result = report("--minimum-percent", "90", f"--inventory={inventory}", "--config=stock")
                self.assertEqual(result.returncode, 0, result.stderr)
                args = result.stdout.split("\0")[:-1]
                self.assertEqual(args[2:], ["invocation", "--minimum-percent", "90", "--expected-commit", commit,
                                           "--expected-profile", "stock", "--expected-inventory", inventory, "--expected-policy", str(policy)])
            for arguments in [("--inventory=unit-cli",), ("--minimum-percent", "90", "--inventory=other"),
                              ("--minimum-percent", "90", "--inventory=unit", "--inventory=unit-cli")]:
                result = report(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
            policy.write_text("changed")
            self.assertEqual(report("--minimum-percent", "90").returncode, 2)
            self.assertEqual(report().returncode, 0)

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

    def test_standalone_wrapper_preserves_arguments_status_and_removes_copy(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            executable = ad_hoc_fixture(directory, "/bin/bash")
            before = hashlib.sha256(executable.read_bytes()).hexdigest()
            for arguments, status, output in [(["-c", 'printf "%s\\n" "$BAZEL_TEST"'], 0, "1\n"),
                                               (["-c", 'printf "%s\\n" "$1" "$2"', "fixture",
                                                 "first arg", "second $arg"], 0, "first arg\nsecond $arg\n"),
                                               (["-c", 'printf "%s\\n" "$0"'], 0, None),
                                               (["-c", "exit 7"], 7, "")]:
                result = subprocess.run(
                    ["/bin/bash", str(SCRIPT.with_name("ssd-test-runner.sh")), str(executable), *arguments],
                    env={"PATH": "/usr/bin:/bin", "TEST_TMPDIR": directory, "DEVCONTAINER_TEST_STANDALONE": "1"},
                    capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, status, result.stderr)
                if output is None:
                    copied_path = Path(result.stdout.strip())
                    self.assertEqual(copied_path.parent, Path(directory))
                    self.assertTrue(copied_path.name.startswith("provider-test."))
                    self.assertFalse(copied_path.exists())
                else:
                    self.assertEqual(result.stdout, output)
                self.assertEqual(list(Path(directory).iterdir()), [executable])
            self.assertEqual(hashlib.sha256(executable.read_bytes()).hexdigest(), before)

    def test_standalone_wrapper_removes_copy_after_group_cancellation(self) -> None:
        for termination in [signal.SIGHUP, signal.SIGINT, signal.SIGTERM]:
            with self.subTest(signal=termination), tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
                executable = ad_hoc_fixture(directory, "/bin/bash")
                ready = Path(directory) / "ready"
                child = subprocess.Popen(
                    ["/bin/bash", str(SCRIPT.with_name("ssd-test-runner.sh")), str(executable),
                     "-c", 'printf ready > "$1"; exec /bin/sleep 30', "test", str(ready)],
                    env={"PATH": "/usr/bin:/bin", "TEST_TMPDIR": directory, "DEVCONTAINER_TEST_STANDALONE": "1"},
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                try:
                    deadline = time.monotonic() + 5
                    while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(ready.exists(), "copied executable did not start")
                    self.assertEqual(len(list(Path(directory).glob("provider-test.*"))), 1)
                    os.killpg(child.pid, termination)
                    output, errors = child.communicate(timeout=5)
                    self.assertEqual(child.returncode, 128 + termination, errors)
                    self.assertEqual(output, b"")
                    self.assertEqual(sorted(Path(directory).iterdir()), sorted([executable, ready]))
                finally:
                    if child.poll() is None:
                        os.killpg(child.pid, signal.SIGKILL)
                    child.communicate(timeout=5)

    def test_standalone_wrapper_rejects_invalid_mode_and_unsigned_input(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            script = Path(directory) / "unsigned"
            script.write_text("#!/bin/sh\nprintf 'must-not-run'\n")
            script.chmod(0o700)
            for mode, arguments in [("2", ["/usr/bin/true"]), ("1", []),
                                    ("1", [str(script)]), ("1", [str(script) + ".absent"])]:
                result = subprocess.run(
                    ["/bin/bash", str(SCRIPT.with_name("ssd-test-runner.sh")), *arguments],
                    env={"PATH": "/usr/bin:/bin", "TEST_TMPDIR": directory, "DEVCONTAINER_TEST_STANDALONE": mode},
                    capture_output=True, text=True, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertEqual(list(Path(directory).iterdir()), [script])

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

    def test_test_scratch_is_stable_and_separate_for_different_workspaces(self) -> None:
        paths = []
        for repo, command in [("/repo-one", "test"), ("/repo-two", "test"), ("/repo-one", "coverage")]:
            result = subprocess.run(
                ["/bin/bash", "-c", 'source "$1"; shift; clean_environment() { printf "%s\\0" "$@"; }; run_bazel "$1" "$2" /invocation /pinned-bazel stock //Tools/bazel:package_smoke',
                 "test", str(SCRIPT), repo, command], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            arguments = result.stdout.split("\0")[:-1]
            scratch = [argument.removeprefix("--test_tmpdir=") for argument in arguments
                       if argument.startswith("--test_tmpdir=")]
            self.assertEqual(len(scratch), 1)
            paths.append(scratch[0])
        self.assertNotEqual(paths[0], paths[1])
        self.assertEqual(paths[0], paths[2])
        # Leave space for Bazel's target hash and the actual guest socket name.
        for socket in ("/bc-12345678/engine.sock", "/dcp-12345678/provider.sock"):
            self.assertLess(len(paths[0] + "/_tmp/" + "a" * 32 + socket), 104)

    def test_build_environment_excludes_credentials_and_shell_hooks(self) -> None:
        result = subprocess.run(
            ["/bin/bash", "-c", 'source "$1"; export UNRELATED_SECRET=fixture-secret BASH_ENV=/does/not/exist PYTHONPATH=/untrusted; clean_environment /usr/bin/env',
             "test", str(SCRIPT)], capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        values = dict(line.split("=", 1) for line in result.stdout.splitlines())
        self.assertEqual(set(values), {"HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "TMPDIR", "TMP", "TEMP", "DEVELOPER_DIR", "PYTHONDONTWRITEBYTECODE", "DEVCONTAINER_HOST_INTEGRATION"})
        self.assertEqual(values["PATH"], "/usr/bin:/bin:/usr/sbin:/sbin")

    def test_release_identity_comes_from_the_captured_source_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as directory:
            root = Path(directory)
            for dirty in ("false", "true", "null"):
                (root / "inputs-before.json").write_text('{"commit":"' + "a" * 40 + '","dirty":' + dirty + '}')
                result = subprocess.run(
                    ["/bin/bash", "-c", 'source "$1"; clean_environment() { printf "%s\\0" "$@"; }; run_bazel /repo build "$2" /pinned-bazel stock --config=release //:candidate_archive',
                     "test", str(SCRIPT), str(root)], capture_output=True, text=True, check=False)
                with self.subTest(dirty=dirty):
                    if dirty == "null":
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(result.stdout, "")
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertIn("--define=DEVCONTAINER_COMMIT=" + "a" * 40, result.stdout.split("\0"))
                        self.assertIn("--define=DEVCONTAINER_BUILD_LANE=candidate", result.stdout.split("\0"))
                        self.assertIn("--define=DEVCONTAINER_SOURCE_DIRTY=" + dirty, result.stdout.split("\0"))

    def test_release_info_diagnostic_does_not_require_a_build_snapshot(self) -> None:
        result = subprocess.run(
            ["/bin/bash", "-c", 'source "$1"; clean_environment() { printf "%s\\0" "$@"; }; execute_invocation /repo info /nonexistent-invocation /pinned-bazel stock --config=release execution_root',
             "test", str(SCRIPT)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--config=release", result.stdout.split("\0"))
        self.assertNotIn("DEVCONTAINER_COMMIT", result.stdout)

    def test_indirect_targets_and_source_identity_overrides_are_rejected(self) -> None:
        for arguments in [("--target_pattern_file=/tmp/targets",), ("--target_pattern_file", "/tmp/targets"),
                          ("--define=DEVCONTAINER_COMMIT=forged",), ("--define", "DEVCONTAINER_COMMIT=forged"),
                          ("--define=DEVCONTAINER_BUILD_LANE=release",), ("--define", "DEVCONTAINER_BUILD_LANE=release"),
                          ("--define=DEVCONTAINER_SOURCE_DIRTY=false",), ("--define", "DEVCONTAINER_SOURCE_DIRTY=false")]:
            with self.subTest(arguments=arguments):
                self.assertEqual(invoke("validate_arguments", *arguments).returncode, 2)

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
            "--flagfile=/tmp/options", "--flagfile", "--flagfile=",
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
