"""Boundary tests for retaining already published immutable release assets."""

import json
from pathlib import Path
import tempfile
import unittest

import resume_immutable_release as resume


class ImmutableResumeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.original = json.dumps({"schemaVersion": 1, "scope": "publication-verifier-identity",
                                    "sourceCommit": "a" * 40, "verifierCommit": "b" * 40,
                                    "verifierSHA256": "c" * 64}).encode()
        (self.root / "publication-verifier.json").write_text("new operation metadata")
        (self.root / "package.tar.gz").write_bytes(b"unchanged archive")
        self.release = {"id": 123, "draft": False, "prerelease": True, "immutable": True,
                        "assets": [{"id": 1, "name": "publication-verifier.json", "size": len(self.original),
                                    "digest": "sha256:" + resume.sha(self.original)},
                                   {"id": 2, "name": "package.tar.gz", "size": len(b"unchanged archive"),
                                    "digest": "sha256:" + resume.sha(b"unchanged archive")}]}

    def check(self, source="a" * 40, verifier="c" * 64, data=None):
        return resume.authenticate(self.release, self.root, source, verifier,
                                   lambda _identifier: self.original if data is None else data)

    def test_reuses_exact_assets_and_original_metadata(self):
        result = self.check()
        self.assertTrue(result["immutableResume"])
        self.assertEqual(result["originalVerifierCommit"], "b" * 40)
        self.assertEqual((self.root / "publication-verifier.json").read_bytes(), self.original)
        self.assertEqual(len(result["assetsSHA256"]), 2)

    def test_rejects_changed_archive_or_downloaded_metadata_before_copy(self):
        for name in ("archive", "metadata"):
            with self.subTest(name=name):
                if name == "archive":
                    (self.root / "package.tar.gz").write_bytes(b"changed")
                with self.assertRaisesRegex(resume.ResumeError, "bytes differ"):
                    self.check(data=b"changed" if name == "metadata" else None)
                self.assertEqual((self.root / "publication-verifier.json").read_text(), "new operation metadata")
                (self.root / "package.tar.gz").write_bytes(b"unchanged archive")

    def test_rejects_wrong_source_or_verifier(self):
        for args in (("d" * 40, "c" * 64), ("a" * 40, "d" * 64)):
            with self.subTest(args=args), self.assertRaisesRegex(resume.ResumeError, "selected source or code"):
                self.check(*args)

    def test_rejects_extra_missing_duplicate_or_symlinked_asset(self):
        original = list(self.release["assets"])
        for entries in (original[:1], original + original[:1], original + [{"name": "foreign"}]):
            self.release["assets"] = entries
            with self.assertRaisesRegex(resume.ResumeError, "inventory"):
                self.check()
        self.release["assets"] = original
        (self.root / "package.tar.gz").unlink()
        (self.root / "package.tar.gz").symlink_to(self.root / "publication-verifier.json")
        with self.assertRaisesRegex(resume.ResumeError, "aliased"):
            self.check()

    def test_initial_and_mutable_stage_use_normal_publication(self):
        self.assertFalse(resume.authenticate(None, self.root, "a" * 40, "c" * 64, None)["immutableResume"])
        self.release["immutable"] = False
        self.assertFalse(self.check()["immutableResume"])

    def test_final_release_is_never_restaged(self):
        self.release["prerelease"] = False
        with self.assertRaisesRegex(resume.ResumeError, "staged prerelease"):
            self.check()


if __name__ == "__main__":
    unittest.main()
