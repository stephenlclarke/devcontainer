# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Regress full-profile orchestration, source changes and retained failures."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import compiled_consumers as consumers


class CompiledConsumerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = self.directory / "repo"
        self.root.mkdir()
        self.output = self.directory / "evidence"
        self.source = {"commit": "a" * 40, "dirty": False}
        self.calls = []

    def execute(self, command, root, output, timeout):
        self.calls.append(command)
        invocation = output / "invocation"
        invocation.mkdir()
        (invocation / "inputs-before.json").write_text(json.dumps(self.source))
        (invocation / "aquery.stdout.log").write_text("{}")
        (output / "stderr.log").write_text(str(invocation))
        (output / "stdout.log").write_text("/Volumes/SSD/cf/bazel/output/fixture" if command[1] == "info" else "{}")
        return {"exitCode": 0, "elapsedNS": 1}

    def prove(self, build, query, raw, output_base, execution_root, profile, output, root):
        self.assertEqual(raw, query / "aquery.stdout.log")
        self.assertEqual(raw.parent.parent.name, profile + "-aquery")
        self.assertEqual(execution_root, output_base / "execroot/_main")
        output.write_text(json.dumps({"profile": profile, "releaseQualified": False}))

    def patches(self):
        for name, value in (("snapshot", lambda root: self.source.copy()),
                            ("execute", self.execute), ("prove", self.prove),
                            ("invocation_from_log", lambda path: Path(path.read_text()))):
            selected = patch.object(consumers, name, side_effect=value)
            selected.start()
            self.addCleanup(selected.stop)

    def test_two_profiles_use_exact_same_build_and_query_configuration(self):
        self.patches()
        result = consumers.run(self.root, self.output, ("stock", "enhanced"), 30)
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["releaseQualified"])
        self.assertEqual(len(self.calls), 6)
        for offset, profile in ((0, "stock"), (3, "enhanced")):
            expected = ["--config=" + profile, "--config=release", "--config=prebuilt-container-sdk"]
            self.assertEqual(self.calls[offset][2:5], expected)
            self.assertEqual(self.calls[offset + 1][2:5], expected)
            self.assertEqual(self.calls[offset][-1], "//:products")
            self.assertEqual(self.calls[offset + 1][-2:], ["deps(//:products)", "--output=jsonproto"])

    def test_failed_second_profile_preserves_first_proof_and_stops(self):
        self.patches()
        original = self.execute

        def fail(command, root, output, timeout):
            result = original(command, root, output, timeout)
            if "--config=enhanced" in command:
                result["exitCode"] = 2
            return result

        with patch.object(consumers, "execute", side_effect=fail):
            with self.assertRaisesRegex(ValueError, "enhanced compiled consumer build failed"):
                consumers.run(self.root, self.output, ("stock", "enhanced"), 30)
        result = json.loads((self.output / "consumers.json").read_text())
        self.assertEqual(result["profiles"]["stock"]["status"], "passed")
        self.assertEqual(result["profiles"]["enhanced"]["status"], "failed")
        self.assertEqual(len(self.calls), 4)
        self.assertTrue((self.output / "stock-compiled-consumer.json").is_file())

    def test_source_change_stops_before_query_and_seals_failure(self):
        self.patches()
        changed = {**self.source, "commit": "b" * 40}
        with patch.object(consumers, "snapshot", side_effect=[self.source, self.source, changed]):
            with self.assertRaisesRegex(ValueError, "source changed during"):
                consumers.run(self.root, self.output, ("stock",), 30)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(json.loads((self.output / "consumers.json").read_text())["status"], "failed")


if __name__ == "__main__":
    unittest.main()
