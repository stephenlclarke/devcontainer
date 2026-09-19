"""D04 host initialization and ordered guest hooks through the pinned CLI."""

import json
from pathlib import Path

from devcontainer_reference import DevcontainerReference, IMAGE


FIXTURE = "D04-lifecycle-hooks"
HOOKS = ("initializeCommand", "onCreateCommand", "updateContentCommand",
         "postCreateCommand", "postStartCommand", "postAttachCommand")


def fixture_inputs(repository: Path) -> dict:
    root = repository / "Tests/Parity/fixtures" / FIXTURE
    result = {key: (root / path).read_text() for key, path in (
        ("configuration", ".devcontainer/devcontainer.json"), ("probe", "probe.sh"))}
    config = json.loads(result["configuration"])
    if (config.get("image") != IMAGE or config.get("remoteUser") != "root" or
            config.get("overrideCommand") is not True or
            any(not isinstance(config.get(key), str) or not config[key] for key in HOOKS)):
        raise ValueError("D04 lifecycle configuration or image pin changed")
    return result


class DevcontainerLifecycleReference(DevcontainerReference):
    """Keep the image owner, command deadlines and cleanup-only recovery intact."""

    fixture = FIXTURE
    keys = {"host_hook", "order"}
