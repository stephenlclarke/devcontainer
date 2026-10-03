"""D03 non-root identity and environment through the pinned official CLI."""

import json
from pathlib import Path

from devcontainer_build_reference import DevcontainerBuildReference
from devcontainer_reference import IMAGE


FIXTURE = "D03-users-environment"
METADATA_FIELDS = ("postCreateCommand", "containerEnv", "containerUser", "remoteUser", "remoteEnv", "updateRemoteUserUID")


def fixture_inputs(repository: Path) -> dict:
    root = repository / "Tests/Parity/fixtures" / FIXTURE
    result = {key: (root / path).read_text() for key, path in (
        ("configuration", ".devcontainer/devcontainer.json"), ("probe", "probe.sh"), ("dockerfile", "Dockerfile"))}
    config = json.loads(result["configuration"])
    if (config.get("build") != {"dockerfile": "../Dockerfile", "context": ".."} or
            result["dockerfile"].splitlines()[0] != "FROM " + IMAGE or
            config.get("containerUser") != "vscode" or config.get("remoteUser") != "vscode" or
            config.get("updateRemoteUserUID") is not False or any(key not in config for key in METADATA_FIELDS)):
        raise ValueError("D03 configuration or base image pin changed")
    return result


class DevcontainerUsersReference(DevcontainerBuildReference):
    fixture = FIXTURE
    keys = {"container_env", "expanded_env", "home", "post_create", "remote_env", "uid", "user"}
    metadata_fields = METADATA_FIELDS

    def write_workspace(self):
        super().write_workspace()
        # Only checked-in public fixture files cross the non-root guest mount.
        # Private parent, logs, CLI data and credentials retain their 0700/0600 modes.
        for name in ("Dockerfile", "probe.sh", ".devcontainer/devcontainer.json"):
            (self.workspace / name).chmod(0o644)
        (self.workspace / ".devcontainer").chmod(0o755)
        self.workspace.chmod(0o755)
