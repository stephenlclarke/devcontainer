"""D01 through the pinned official CLI inside an exclusively owned Docker VM."""

from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import quote

from case_evidence import canonical, digest
from guest_fixture import GuestFixture, OWNER_LABEL
from guest_runtime import GUEST_API_VERSION
from host_runtime import deadline


FIXTURE = "D01-image-config"
IMAGE = "alpine:3.22@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce"
WORKSPACE = "/workspaces/devcontainer-parity"
KEYS = {"environment", "workspace", "post_create", "uid"}


def fixture_inputs(repository: Path) -> dict:
    root = repository / "Tests/Parity/fixtures" / FIXTURE
    configuration = (root / ".devcontainer/devcontainer.json").read_bytes()
    probe = (root / "probe.sh").read_bytes()
    if json.loads(configuration).get("image") != IMAGE:
        raise ValueError("D01 reference image pin changed")
    return {"configuration": configuration.decode(), "probe": probe.decode()}


def created_id(payload: bytes) -> str:
    result = json.loads(payload)
    if not isinstance(result, dict):
        raise ValueError("CLI up must report one result object")
    identifier = result.get("containerId")
    if (result.get("outcome") != "success" or not isinstance(identifier, str) or
            re.fullmatch(r"[0-9a-f]{64}", identifier) is None):
        raise ValueError("CLI up did not return a successful immutable container ID")
    return identifier


def observations(payload: bytes) -> dict[str, str]:
    result = {}
    for line in payload.decode().splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in KEYS or key in result:
            raise ValueError("D01 probe returned unexpected or duplicate output")
        result[key] = value
    if set(result) != KEYS:
        raise ValueError("D01 probe did not return every observation")
    return result


class DevcontainerReference(GuestFixture):
    """Reuse bounded HTTP probes, never the legacy build/global-context runner.

    The enclosing DockerCase owns the entire private VM and host lease. CLI
    commands are journalled by DockerVM. A missing command exit is uncertainty,
    not permission to delete a potentially still-creating resource.
    """

    def __init__(self, vm, inputs: dict, owner: dict, *, observe=None):
        token = digest(canonical(owner))
        image = inputs["workload"]["image"]["manifest"]
        super().__init__(vm.socket, token, image, GUEST_API_VERSION, vm.journal, observe=observe)
        self.vm, self.inputs = vm, inputs
        self.workspace = vm.root / "workspace" / FIXTURE
        self.plan = {"owner": token, "workspace": str(self.workspace), "image": IMAGE,
                     "inputsSHA256": digest(canonical(inputs["devcontainers"])),
                     "fixtureSHA256": digest(canonical(inputs["devcontainerFixture"]))}

    def setup(self):
        with deadline(180):
            self.prepare()

    def prepare(self):
        if "devcontainer-plan.json" in self.journal.records():
            raise ValueError("D01 has already been attempted; reconcile instead of retrying")
        self.journal.put("devcontainer-plan.json", canonical(self.plan))
        self.workspace.mkdir(mode=0o700)
        (self.workspace / ".devcontainer").mkdir(mode=0o700)
        (self.workspace / ".devcontainer/devcontainer.json").write_text(self.inputs["devcontainerFixture"]["configuration"])
        (self.workspace / "probe.sh").write_text(self.inputs["devcontainerFixture"]["probe"])
        # Keep the checked-in index-digest reference unchanged. The private
        # daemon fetches that exact published image; no product/image is built.
        self.vm.command("devcontainer-image-pull", [self.inputs["tools"]["docker"], "--host",
                        "unix://" + str(self.socket), "image", "pull", "--platform=linux/arm64", IMAGE], timeout=120)
        if self.find() is not None:
            raise ValueError("D01 owner label already exists before CLI execution")

    def arguments(self, command: str) -> list[str]:
        tools = self.inputs["devcontainers"]
        arguments = ["/usr/bin/env", "DOCKER_HOST=unix://" + str(self.socket), tools["node"], tools["cli"], command,
                "--docker-path", self.inputs["tools"]["docker"], "--workspace-folder", str(self.workspace),
                "--id-label", OWNER_LABEL + "=" + self.owner]
        # JSON mode wraps exec's raw output into stderr log events. Only up
        # uses it; exec retains the ordinary stdout contract of the old fixture.
        return arguments + (["--log-format", "json"] if command == "up" else [])

    def owned(self, value: dict) -> str:
        identifier = value.get("Id")
        config = value.get("Config", {})
        images = self.inputs["workload"]["image"]
        permitted = {images["manifest"], images["config"], IMAGE.split("@", 1)[1]}
        mounts = value.get("Mounts", [])
        if (not isinstance(identifier, str) or re.fullmatch(r"[0-9a-f]{64}", identifier) is None or
                not isinstance(config, dict) or not isinstance(config.get("Labels"), dict) or
                config["Labels"].get(OWNER_LABEL) != self.owner or config.get("Image") != IMAGE or
                value.get("Image") not in permitted or not isinstance(mounts, list) or
                sum(isinstance(mount, dict) and mount.get("Type") == "bind" and
                    mount.get("Source") == str(self.workspace) and mount.get("Destination") == WORKSPACE
                    for mount in mounts) != 1):
            raise ValueError("D01 resource ownership, image or workspace changed")
        return identifier

    def find(self):
        filters = quote(canonical({"label": [OWNER_LABEL + "=" + self.owner]}).decode(), safe="")
        status, payload = self.call("GET", "/containers/json?all=true&filters=" + filters)
        values = json.loads(payload)
        if status != 200 or not isinstance(values, list) or len(values) > 1:
            raise ValueError("D01 owner inventory is invalid or ambiguous")
        if not values:
            return None
        identifier = values[0].get("Id") if isinstance(values[0], dict) else None
        if not isinstance(identifier, str) or re.fullmatch(r"[0-9a-f]{64}", identifier) is None:
            raise ValueError("D01 inventory lacks an immutable ID")
        actual = self.inspect(identifier)
        if actual is None or self.owned(actual) != identifier:
            raise ValueError("D01 inventory and inspection disagree")
        return actual

    def operation(self):
        with deadline(195):
            return self.execute()

    def execute(self):
        if self.journal.records().get("devcontainer-plan.json") != canonical(self.plan):
            raise ValueError("D01 plan identity changed")
        output = self.vm.command("devcontainer-up", self.arguments("up") + ["--user-data-folder",
                                 str(self.vm.root / "devcontainer-data")], timeout=120, separate_output=True)
        self.identifier = created_id(output)
        self.journal.put("devcontainer-created.json", canonical({"id": self.identifier}))
        actual = self.find()
        if actual is None or self.owned(actual) != self.identifier:
            raise ValueError("CLI result does not match the owned D01 resource")
        output = self.vm.command("devcontainer-exec", self.arguments("exec") + ["--", "/bin/sh", WORKSPACE + "/probe.sh"],
                                 timeout=60, separate_output=True)
        return observations(output)

    def recovery_plan(self):
        """Read-only ownership admission, also used immediately before deletion."""
        records = self.journal.records()
        if "devcontainer-plan.json" not in records:
            return None
        if records["devcontainer-plan.json"] != canonical(self.plan) or self.vm.uncertain:
            raise ValueError("D01 has uncertain process or plan ownership")
        for command in ("devcontainer-image-pull", "devcontainer-up", "devcontainer-exec"):
            if command + "-intent.json" in records and command + "-exit.json" not in records:
                raise ValueError("D01 command completion is unknown")
        known = json.loads(records.get("devcontainer-created.json", b"null"))
        deleting = json.loads(records.get("devcontainer-delete-intent.json", b"null"))
        if known is not None and deleting is not None and known != deleting:
            raise ValueError("D01 creation and deletion identities disagree")
        known = known or deleting
        actual = self.find()
        if actual is not None:
            identifier = self.owned(actual)
            if ((known is not None and known != {"id": identifier}) or
                    "devcontainer-up-intent.json" not in records or "devcontainer-removed.json" in records):
                raise ValueError("D01 inventory was replaced or lacks a creation intent")
            return identifier
        elif known is not None:
            if self.inspect(known["id"]) is not None:
                raise ValueError("Known D01 container survived under changed ownership")
        elif "devcontainer-up-intent.json" in records:
            raise ValueError("D01 has no observed creation outcome; preserve quarantine")
        return None

    def cleanup(self):
        with deadline(45):
            self.remove_owned()

    def remove_owned(self):
        identifier = self.recovery_plan()
        if identifier is not None:
            self.journal.put("devcontainer-delete-intent.json", canonical({"id": identifier}))
            status, _ = self.call("DELETE", f"/containers/{identifier}?force=true&v=true")
            if status not in (204, 404) or self.inspect(identifier) is not None or self.find() is not None:
                raise ValueError("D01 removal did not complete")
        if "devcontainer-plan.json" not in self.journal.records():
            return
        self.journal.put("devcontainer-removed.json", canonical({"verifiedAbsent": True}))
