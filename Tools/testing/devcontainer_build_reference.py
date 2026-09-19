"""Dockerfile configuration through the same pinned CLI and private Docker VM."""

import json
from pathlib import Path
import re
from urllib.parse import quote

from devcontainer_reference import DevcontainerReference, IMAGE


FIXTURE = "D02-dockerfile-config"


def fixture_inputs(repository: Path) -> dict:
    root = repository / "Tests/Parity/fixtures" / FIXTURE
    result = {key: (root / path).read_text() for key, path in (
        ("configuration", ".devcontainer/devcontainer.json"), ("probe", "probe.sh"), ("dockerfile", "Dockerfile"))}
    build = json.loads(result["configuration"]).get("build")
    if (build != {"dockerfile": "../Dockerfile", "context": "..", "target": "development",
                  "args": {"PARITY_BUILD_ARG": "from-devcontainer"}} or
            result["dockerfile"].splitlines()[0] != "FROM " + IMAGE + " AS development"):
        raise ValueError("D02 build configuration or base image pin changed")
    return result


class DevcontainerBuildReference(DevcontainerReference):
    fixture = FIXTURE
    keys = {"build_arg", "build_target", "post_create", "workspace"}
    up_timeout = 240
    metadata_fields = ("postCreateCommand", "remoteUser", "overrideCommand")

    def write_workspace(self):
        super().write_workspace()
        (self.workspace / "Dockerfile").write_text(self.inputs["devcontainerFixture"]["dockerfile"])

    def arguments(self, command: str) -> list[str]:
        # Docker's global plugin directories are outside the admitted closure.
        # Pin the official CLI to its legacy Engine build path for this oracle;
        # the Dockerfile and its observations remain unchanged.
        arguments = super().arguments(command)
        arguments.insert(2, "DOCKER_BUILDKIT=0")
        return arguments + (["--buildkit", "never"] if command == "up" else [])

    def inspect_image(self, reference):
        if not isinstance(reference, str) or not 1 <= len(reference) <= 512:
            raise ValueError("D02 image reference is invalid")
        status, payload = self.call("GET", "/images/" + quote(reference, safe="") + "/json")
        value = json.loads(payload)
        if status != 200 or not isinstance(value, dict):
            raise ValueError("D02 image inspection failed")
        return value

    def owned(self, value):
        identifier = self.owned_workspace(value)
        built = self.inspect_image(value["Config"].get("Image"))
        base = self.inspect_image(IMAGE)
        image_id = value.get("Image")
        base_layers = base.get("RootFS", {}).get("Layers")
        built_layers = built.get("RootFS", {}).get("Layers")
        configuration = json.loads(self.inputs["devcontainerFixture"]["configuration"])
        metadata = json.loads(built.get("Config", {}).get("Labels", {}).get("devcontainer.metadata", "null"))
        expected = {name: configuration[name] for name in self.metadata_fields}
        admitted = self.inputs["workload"]["image"]
        if (not isinstance(image_id, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None or
                built.get("Id") != image_id or metadata != [expected] or
                base.get("Id") not in {admitted["manifest"], admitted["config"], IMAGE.split("@", 1)[1]} or
                not isinstance(base_layers, list) or not base_layers or
                not isinstance(built_layers, list) or len(built_layers) <= len(base_layers) or
                built_layers[:len(base_layers)] != base_layers or
                any(not isinstance(layer, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", layer) is None
                    for layer in built_layers)):
            raise ValueError("D02 built image identity, base ancestry or metadata changed")
        return identifier
