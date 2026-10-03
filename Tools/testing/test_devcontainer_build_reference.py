"""Unmodified Dockerfile fixture and strict generated-image ownership proof."""

import copy
import json
from pathlib import Path
import unittest

from devcontainer_build_reference import DevcontainerBuildReference, FIXTURE, fixture_inputs
from devcontainer_reference import IMAGE, WORKSPACE
import test_devcontainer_reference as reference_tests


PROBE = (b"build_arg=from-devcontainer\nbuild_target=development\n"
         b"post_create=dockerfile-post-create\nworkspace=/workspaces/devcontainer-parity\n")


class BuildReferenceTests(reference_tests.ReferenceTests):
    def reopen(self):
        self.inputs["devcontainerFixture"] = fixture_inputs(Path(__file__).parents[2])
        configuration = json.loads(self.inputs["devcontainerFixture"]["configuration"])
        metadata = [{key: configuration[key] for key in ("postCreateCommand", "remoteUser", "overrideCommand")}]
        self.server.images = {
            IMAGE: {"Id": self.inputs["workload"]["image"]["manifest"], "RootFS": {"Layers": ["sha256:" + "1" * 64]}},
            "generated:test": {"Id": "sha256:" + "e" * 64,
                               "RootFS": {"Layers": ["sha256:" + "1" * 64, "sha256:" + "2" * 64]},
                               "Config": {"Labels": {"devcontainer.metadata": json.dumps(metadata)}}},
        }
        return DevcontainerBuildReference(self.vm, self.inputs, self.owner)

    def guest(self):
        value = super().guest()
        value["Config"]["Image"] = "generated:test"
        value["Image"] = "sha256:" + "e" * 64
        return value

    def command(self, name, arguments, **kwargs):
        result = super().command(name, arguments, **kwargs)
        return PROBE if name == "devcontainer-exec" else result

    def test_full_contract_uses_exact_pins_isolated_paths_and_verified_cleanup(self):
        self.assertEqual(self.start(), {"build_arg": "from-devcontainer", "build_target": "development",
                                       "post_create": "dockerfile-post-create", "workspace": WORKSPACE})
        self.assertEqual(self.fixture.workspace.name, FIXTURE)
        self.assertEqual((self.fixture.workspace / "Dockerfile").read_text(), self.inputs["devcontainerFixture"]["dockerfile"])
        self.assertEqual([entry[2] for entry in self.commands], [120, 240, 60])
        self.assertEqual(self.commands[0][1][-1], IMAGE)
        self.assertIn("DOCKER_BUILDKIT=0", self.commands[1][1])
        mode = self.commands[1][1].index("--buildkit")
        self.assertEqual(self.commands[1][1][mode + 1], "never")
        self.assertNotIn("--buildkit", self.commands[2][1])
        self.fixture.cleanup()
        self.assertIsNone(self.server.guest)
        self.assertIn("devcontainer-removed.json", self.vm.journal.records())

    def test_built_image_and_pinned_base_ancestry_cannot_change(self):
        self.start()
        original = copy.deepcopy(self.server.images)
        mutations = [
            lambda images: images["generated:test"].update(Id="sha256:" + "3" * 64),
            lambda images: images[IMAGE].update(Id="sha256:" + "3" * 64),
            lambda images: images[IMAGE]["RootFS"].update(Layers=[]),
            lambda images: images["generated:test"]["RootFS"].update(Layers=["sha256:" + "1" * 64]),
            lambda images: images["generated:test"]["RootFS"].update(Layers=["sha256:" + "3" * 64, "sha256:" + "2" * 64]),
            lambda images: images["generated:test"]["RootFS"].update(Layers=["sha256:" + "1" * 64, "invalid"]),
            lambda images: images["generated:test"]["Config"]["Labels"].update({"devcontainer.metadata": "[]"}),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.server.images = copy.deepcopy(original)
                mutate(self.server.images)
                with self.assertRaisesRegex(ValueError, "image identity"):
                    self.fixture.cleanup()
                self.assertIsNotNone(self.server.guest)
        self.server.images = original
        self.fixture.cleanup()

    def test_missing_or_invalid_image_reference_is_never_adopted(self):
        self.start()
        for reference in (None, "", "a" * 513):
            with self.subTest(reference=reference), self.assertRaisesRegex(ValueError, "reference"):
                self.fixture.inspect_image(reference)
        with self.assertRaisesRegex(ValueError, "inspection"):
            self.fixture.inspect_image("absent")

    def test_build_inputs_bind_dockerfile_and_configuration(self):
        root = self.root / "repository/Tests/Parity/fixtures" / FIXTURE
        (root / ".devcontainer").mkdir(parents=True)
        (root / "Dockerfile").write_text("FROM unpinned:latest\n")
        (root / "probe.sh").write_text("probe")
        (root / ".devcontainer/devcontainer.json").write_text(self.inputs["devcontainerFixture"]["configuration"])
        with self.assertRaisesRegex(ValueError, "pin changed"):
            fixture_inputs(self.root / "repository")


if __name__ == "__main__":
    unittest.main()
