"""Shared published campaign identity; no downloads or runtime work."""

import os
from pathlib import Path
import tempfile
import unittest

from campaign_identity import PREPARATION_HELPERS, RELEASE_INPUTS, RUNTIME_HELPERS, published_fingerprints
from case_evidence import canonical, compare_cases, digest


class CampaignIdentityTests(unittest.TestCase):
    def test_activation_and_background_resolution_are_in_runtime_fingerprint(self):
        self.assertTrue({"native_activation.py", "background_items.py"} <= set(RUNTIME_HELPERS))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "Tools/testing").mkdir(parents=True)
        for name in (*RELEASE_INPUTS, *["Tools/bazel/" + name for name in PREPARATION_HELPERS],
                     *["Tools/testing/" + name for name in RUNTIME_HELPERS]):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"{}\n")

    def test_same_closure_compares_distinct_runtime_lanes(self):
        fingerprints = published_fingerprints(self.root)
        records = []
        for index, lane in enumerate(("docker", "apple-stock", "container-compose")):
            identity = dict(fingerprints, campaign="published", fixture="E01-engine-negotiation", lane=lane,
                            contractSHA256=digest(canonical({"ping": "true"})), runtimeSHA256=str(index) * 64)
            result = {"status": "passed", "observations": {"ping": "true"}, "errors": [],
                      "cleanup": {"status": "passed", "remainingOwnedResources": []},
                      "durationsNS": {"setup": 1, "operation": 100, "cleanup": 1}}
            records.append({"identity": identity, "result": result})
        comparison = compare_cases(records, {"ping": "true"})
        self.assertTrue(comparison["functionalParity"])
        self.assertFalse(comparison["timingQualified"])

    def test_every_lane_lock_and_helper_invalidates_the_shared_identity(self):
        before = published_fingerprints(self.root)
        for name in RELEASE_INPUTS:
            path = self.root / name
            path.write_bytes(b'{"changed":true}\n')
            self.assertNotEqual(published_fingerprints(self.root)["releaseSetSHA256"], before["releaseSetSHA256"])
            path.write_bytes(b"{}\n")
        for name in (*["Tools/bazel/" + name for name in PREPARATION_HELPERS],
                     *["Tools/testing/" + name for name in RUNTIME_HELPERS]):
            path = self.root / name
            path.write_bytes(b"# changed\n")
            self.assertNotEqual(published_fingerprints(self.root)["harnessSHA256"], before["harnessSHA256"])
            path.write_bytes(b"{}\n")
        self.assertEqual(published_fingerprints(self.root), before)

    def test_missing_cross_lane_lock_cannot_silently_narrow_comparison(self):
        (self.root / RELEASE_INPUTS[1]).unlink()
        with self.assertRaises(FileNotFoundError):
            published_fingerprints(self.root)

    def test_runfiles_and_resolved_workspace_have_identical_execution_closure(self):
        runfiles = self.root / "runfiles"
        for name in (*RELEASE_INPUTS, *["Tools/bazel/" + name for name in PREPARATION_HELPERS],
                     *["Tools/testing/" + name for name in RUNTIME_HELPERS]):
            path = runfiles / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(self.root / name)
        (self.root / "Tools/testing/test_extra.py").write_bytes(b"# not executed by the runtime\n")
        self.assertEqual(published_fingerprints(self.root), published_fingerprints(runfiles))
        (runfiles / "Tools/testing/docker_vm.py").unlink()
        with self.assertRaises(FileNotFoundError):
            published_fingerprints(runfiles)


if __name__ == "__main__":
    unittest.main()
