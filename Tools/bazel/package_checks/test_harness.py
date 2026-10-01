"""Package fixture cleanup must never discard files beneath an uncertain child."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_archive


class CleanupTests(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ["TEST_TMPDIR"])
        self.addCleanup(temporary.cleanup)
        parent = Path(temporary.name)
        package = parent / "package"
        package.mkdir()
        for name, value in (("base", package), ("root", package), ("preserve_package", False)):
            self.enter_patch(patch.object(test_archive.ArchiveTests, name, value, create=True))
        self.enter_patch(patch.dict(os.environ, {"TEST_TMPDIR": str(parent)}))
        case = test_archive.ArchiveTests("test_main_help_exposes_installed_command_tree")
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case, package

    def enter_patch(self, context):
        value = context.start()
        self.addCleanup(context.stop)
        return value

    def test_uncertain_child_preserves_home_and_package(self):
        case, package = self.fixture()
        with patch.object(test_archive, "run", side_effect=RuntimeError("cleanup is unverified")):
            with self.assertRaisesRegex(RuntimeError, "cleanup is unverified"):
                case.invoke("bin/devcontainer", "--help")
        case.doCleanups()
        test_archive.ArchiveTests.cleanup_package()
        self.assertTrue(package.is_dir())
        self.assertTrue(case.home.is_dir())
        self.assertTrue((case.home / "stdout").is_file())

    def test_verified_child_allows_both_roots_to_be_removed(self):
        case, package = self.fixture()
        with patch.object(test_archive, "run", return_value=0):
            case.invoke("bin/devcontainer", "--help")
        case.doCleanups()
        test_archive.ArchiveTests.cleanup_package()
        self.assertFalse(package.exists())
        self.assertFalse(case.home.exists())
