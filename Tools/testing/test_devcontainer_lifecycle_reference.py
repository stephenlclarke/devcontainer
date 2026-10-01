"""D04 reuses image ownership and does not synthesize lifecycle observations."""

import json
from pathlib import Path
import unittest

from devcontainer_lifecycle_reference import DevcontainerLifecycleReference, FIXTURE, HOOKS, fixture_inputs
from devcontainer_reference import IMAGE, WORKSPACE
import test_devcontainer_reference as reference_tests


EXPECTED = {"host_hook": "initialize", "order": "onCreate,updateContent,postCreate,postStart,postAttach"}


class LifecycleReferenceTests(reference_tests.ReferenceTests):
    def reopen(self):
        self.inputs["devcontainerFixture"] = fixture_inputs(Path(__file__).parents[2])
        return DevcontainerLifecycleReference(self.vm, self.inputs, self.owner)

    def command(self, name, arguments, **kwargs):
        result = super().command(name, arguments, **kwargs)
        return "".join(key + "=" + value + "\n" for key, value in EXPECTED.items()).encode() if name == "devcontainer-exec" else result

    def test_full_contract_uses_exact_pins_isolated_paths_and_verified_cleanup(self):
        self.assertEqual(self.start(), EXPECTED)
        self.assertEqual(self.fixture.workspace.name, FIXTURE)
        self.assertEqual([item[0] for item in self.commands],
                         ["devcontainer-image-pull", "devcontainer-up", "devcontainer-exec"])
        self.assertEqual([item[2] for item in self.commands], [120, 120, 60])
        self.assertEqual(self.commands[0][1][-1], IMAGE)
        self.assertEqual(self.commands[2][1][-3:], ["--", "/bin/sh", WORKSPACE + "/probe.sh"])
        config = self.fixture.workspace / ".devcontainer/devcontainer.json"
        self.assertEqual(config.read_text(), self.inputs["devcontainerFixture"]["configuration"])
        self.assertFalse((self.fixture.workspace / ".lifecycle-host").exists())
        self.fixture.cleanup()
        self.assertIsNone(self.server.guest)
        self.reopen().cleanup()

    def test_hook_configuration_drift_is_rejected_before_runtime_work(self):
        root = self.root / "repository/Tests/Parity/fixtures" / FIXTURE
        (root / ".devcontainer").mkdir(parents=True)
        (root / "probe.sh").write_text(self.inputs["devcontainerFixture"]["probe"])
        original = json.loads(self.inputs["devcontainerFixture"]["configuration"])
        changes = [(hook, value) for hook in HOOKS for value in (None, "", [])]
        changes += [("image", "alpine:latest"), ("remoteUser", "vscode"), ("overrideCommand", False)]
        for key, value in changes:
            (root / ".devcontainer/devcontainer.json").write_text(json.dumps(dict(original, **{key: value})))
            with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, "pin changed"):
                fixture_inputs(self.root / "repository")


if __name__ == "__main__":
    unittest.main()
