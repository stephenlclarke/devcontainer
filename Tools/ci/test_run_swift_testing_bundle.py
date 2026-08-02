"""Tests for direct execution of a prebuilt Swift Testing bundle."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parent
RUNNER = TOOLS / "run-swift-testing-bundle.sh"


class SwiftTestingBundleRunnerTests(unittest.TestCase):
    def make_executable(self, path: Path, body: str) -> Path:
        path.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n" + body,
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def test_loads_absolute_bundle_with_explicit_swift_testing_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            bundle = self.make_executable(root / "PackageTests", "exit 0\n")
            helper = self.make_executable(
                root / "swiftpm-testing-helper",
                "printf 'args=%s\\n' \"$*\"\n",
            )
            platform = root / "MacOSX.platform"
            (platform / "Developer/Library/Frameworks").mkdir(parents=True)
            (platform / "Developer/Library/PrivateFrameworks").mkdir()
            (platform / "Developer/usr/lib").mkdir(parents=True)
            environment = os.environ.copy()
            environment.update(
                {
                    "SWIFT_TEST_HELPER": str(helper),
                    "SWIFT_TEST_PLATFORM_PATH": str(platform),
                    "DYLD_FRAMEWORK_PATH": "/existing/frameworks",
                    "DYLD_LIBRARY_PATH": "/existing/libraries",
                }
            )

            result = subprocess.run(
                [str(RUNNER), str(bundle), "--no-parallel"],
                env=environment,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                f"args=--test-bundle-path {bundle} --no-parallel "
                "--testing-library swift-testing",
                result.stdout,
            )

    def test_configures_xcode_framework_and_library_search_paths(self) -> None:
        contents = RUNNER.read_text(encoding="utf-8")

        self.assertIn(
            'export DYLD_FRAMEWORK_PATH="$FRAMEWORK_PATH',
            contents,
        )
        self.assertIn(
            'export DYLD_LIBRARY_PATH="$LIBRARY_PATH',
            contents,
        )
        self.assertIn("/Developer/Library/PrivateFrameworks", contents)
        self.assertIn("/Developer/usr/lib", contents)

    def test_resolves_requested_sanitizer_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            bundle = self.make_executable(root / "PackageTests", "exit 0\n")
            helper = self.make_executable(
                root / "swiftpm-testing-helper",
                "printf 'args=%s\\n' \"$*\"\n",
            )
            clang = subprocess.run(
                ["xcrun", "--find", "clang"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            platform = root / "MacOSX.platform"
            (platform / "Developer/Library/Frameworks").mkdir(parents=True)
            environment = os.environ.copy()
            environment.update(
                {
                    "SWIFT_TEST_CLANG": clang,
                    "SWIFT_TEST_HELPER": str(helper),
                    "SWIFT_TEST_PLATFORM_PATH": str(platform),
                }
            )

            result = subprocess.run(
                [str(RUNNER), str(bundle), "--sanitize=address"],
                env=environment,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--sanitize=address", result.stdout)

    def test_rejects_conflicting_sanitizers(self) -> None:
        result = subprocess.run(
            [
                str(RUNNER),
                "/tmp/unused-PackageTests",
                "--sanitize=address",
                "--sanitize=thread",
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 64)
        self.assertIn("Only one Swift sanitizer", result.stderr)

    def test_rejects_relative_bundle_path(self) -> None:
        result = subprocess.run(
            [str(RUNNER), "PackageTests"],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 64)
        self.assertIn("must be absolute", result.stderr)

    def test_rejects_missing_bundle_before_toolchain_discovery(self) -> None:
        result = subprocess.run(
            [str(RUNNER), "/tmp/definitely-missing-PackageTests"],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 66)
        self.assertIn("does not exist", result.stderr)


if __name__ == "__main__":
    unittest.main()
