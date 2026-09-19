"""Seven unchanged observations and non-root-readable public workspace only."""

import json
from pathlib import Path
import unittest

from devcontainer_users_reference import DevcontainerUsersReference, FIXTURE, METADATA_FIELDS, fixture_inputs
import test_devcontainer_build_reference as build_tests


EXPECTED = {"container_env": "container-value", "expanded_env": "container-value", "home": "/home/vscode",
            "post_create": "user-post-create", "remote_env": "remote-value", "uid": "1000", "user": "vscode"}


class UsersReferenceTests(build_tests.BuildReferenceTests):
    def reopen(self):
        super().reopen()
        self.inputs["devcontainerFixture"] = fixture_inputs(Path(__file__).parents[2])
        config = json.loads(self.inputs["devcontainerFixture"]["configuration"])
        self.server.images["generated:test"]["Config"]["Labels"]["devcontainer.metadata"] = json.dumps(
            [{key: config[key] for key in METADATA_FIELDS}])
        return DevcontainerUsersReference(self.vm, self.inputs, self.owner)

    def command(self, name, arguments, **kwargs):
        result = super().command(name, arguments, **kwargs)
        return "".join(key + "=" + value + "\n" for key, value in EXPECTED.items()).encode() if name == "devcontainer-exec" else result

    def test_full_contract_uses_exact_pins_isolated_paths_and_verified_cleanup(self):
        self.assertEqual(self.start(), EXPECTED)
        self.assertEqual(self.fixture.workspace.name, FIXTURE)
        self.assertEqual([entry[2] for entry in self.commands], [120, 240, 60])
        self.assertIn("DOCKER_BUILDKIT=0", self.commands[1][1])
        self.assertIn("--buildkit", self.commands[1][1])
        self.assertNotIn("--buildkit", self.commands[2][1])
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.fixture.workspace.stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.fixture.workspace / ".devcontainer").stat().st_mode & 0o777, 0o755)
        for name in ("Dockerfile", "probe.sh", ".devcontainer/devcontainer.json"):
            self.assertEqual((self.fixture.workspace / name).stat().st_mode & 0o777, 0o644)
        self.assertEqual((self.fixture.workspace / "Dockerfile").read_text(), self.inputs["devcontainerFixture"]["dockerfile"])
        self.fixture.cleanup()
        self.assertIsNone(self.server.guest)

    def test_build_inputs_bind_dockerfile_and_configuration(self):
        root = self.root / "repository/Tests/Parity/fixtures" / FIXTURE
        (root / ".devcontainer").mkdir(parents=True)
        (root / "probe.sh").write_text(self.inputs["devcontainerFixture"]["probe"])
        config = json.loads(self.inputs["devcontainerFixture"]["configuration"])
        for field, value in (("containerUser", "root"), ("remoteUser", "root"), ("updateRemoteUserUID", True),
                             ("build", {"dockerfile": "../foreign", "context": ".."})):
            (root / "Dockerfile").write_text(self.inputs["devcontainerFixture"]["dockerfile"])
            (root / ".devcontainer/devcontainer.json").write_text(json.dumps(dict(config, **{field: value})))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "pin changed"):
                fixture_inputs(self.root / "repository")
        (root / ".devcontainer/devcontainer.json").write_text(json.dumps(config))
        (root / "Dockerfile").write_text("FROM unpinned:latest\n")
        with self.assertRaisesRegex(ValueError, "pin changed"):
            fixture_inputs(self.root / "repository")


if __name__ == "__main__":
    unittest.main()
