"""C03 env_file, network aliases and a Compose-created named volume.

Reuse C02's generation-bound service/network transaction. Its journal keys keep
their original names for recovery compatibility; volume ownership is separate.
"""

import json
from pathlib import Path
from urllib.parse import quote

from case_evidence import canonical
from devcontainer_compose_reference import PROJECT_LABEL
from devcontainer_dependencies_reference import DevcontainerDependenciesReference
from devcontainer_reference import DevcontainerReference, IMAGE


FIXTURE = "C03-compose-resources"
VOLUME_LABEL = "com.docker.compose.volume"


def fixture_inputs(repository: Path) -> dict:
    root = repository / "Tests/Parity/fixtures" / FIXTURE
    values = {key: (root / path).read_text() for key, path in (
        ("configuration", ".devcontainer/devcontainer.json"), ("probe", "probe.sh"),
        ("compose", "compose.yaml"), ("environment", "fixture.env"))}
    configuration = json.loads(values["configuration"])
    if (configuration.get("dockerComposeFile") != "../compose.yaml" or configuration.get("service") != "app" or
            configuration.get("runServices") != ["app", "peer"] or configuration.get("overrideCommand") is not True or
            configuration.get("shutdownAction") != "stopCompose" or values["compose"].count(IMAGE) != 2 or
            values["environment"] != "PARITY_ENV_FILE=compose-env-file\n"):
        raise ValueError("C03 resource fixture or image pins changed")
    return values


class DevcontainerResourcesReference(DevcontainerDependenciesReference):
    fixture = FIXTURE
    keys = {"env_file", "named_volume", "network_alias", "network_peer"}
    services = ("app", "peer")
    network_key = "parity"
    commands = DevcontainerReference.commands

    def __init__(self, vm, inputs, owner, *, observe=None):
        super().__init__(vm, inputs, owner, observe=observe)
        self.project = "cf-c03-" + self.owner[:32]
        self.network_name = self.project + "_parity"
        self.volume_name = self.project + "_parity-cache"
        self.plan.update(project=self.project, volume=self.volume_name)

    def write_workspace(self):
        super().write_workspace()
        (self.workspace / "fixture.env").write_text(self.inputs["devcontainerFixture"]["environment"])

    def prepare_image(self):
        DevcontainerReference.prepare_image(self)

    def volume(self):
        status, payload = self.call("GET", "/volumes/" + quote(self.volume_name, safe=""))
        value = json.loads(payload)
        if status == 404 and isinstance(value, dict) and isinstance(value.get("message"), str):
            return None
        if status != 200 or not isinstance(value, dict):
            raise ValueError("C03 volume inspection failed")
        return value

    def volume_inventory(self):
        filters = quote(canonical({"label": [PROJECT_LABEL + "=" + self.project]}).decode(), safe="")
        status, payload = self.call("GET", "/volumes?filters=" + filters)
        value = json.loads(payload)
        if (status != 200 or not isinstance(value, dict) or value.get("Warnings") or "Volumes" not in value or
                value["Volumes"] is not None and not isinstance(value["Volumes"], list)):
            raise ValueError("C03 volume inventory is invalid")
        return value["Volumes"] or []

    def volume_identity(self, value):
        labels = value.get("Labels")
        if (value.get("Name") != self.volume_name or not isinstance(labels, dict) or
                labels.get(PROJECT_LABEL) != self.project or labels.get(VOLUME_LABEL) != "parity-cache" or
                labels.get("devcontainer.parity") != "C03" or
                any(not isinstance(value.get(key), str) or not value[key]
                    for key in ("CreatedAt", "Driver", "Mountpoint", "Scope"))):
            raise ValueError("C03 volume ownership or incarnation changed")
        return {key: value.get(key) for key in ("Name", "Labels", "CreatedAt", "Driver", "Mountpoint", "Scope", "Options")}

    def prepare(self):
        if self.volume() is not None or self.volume_inventory():
            raise ValueError("C03 volume already exists")
        # Capture absence before any CLI could create the named volume.
        self.journal.put("c03-volume-intent.json", canonical(self.plan))
        super().prepare()

    def up_identity(self, output):
        identifier = super().up_identity(output)
        actual = self.volume()
        if actual is None or self.volume_inventory() != [actual]:
            raise ValueError("C03 requires exactly its declared volume")
        self.journal.put("c03-volume-created.json", canonical(self.volume_identity(actual)))
        return identifier

    def owned(self, value):
        identifier = super().owned(value)
        if sum(isinstance(item, dict) and item.get("Type") == "volume" and
               item.get("Name") == self.volume_name and item.get("Destination") == "/cache"
               for item in value["Mounts"]) != 1:
            raise ValueError("C03 app volume mount changed")
        return identifier

    def verify_volume(self):
        records = self.journal.records()
        intent = records.get("c03-volume-intent.json")
        if intent is None:
            return
        if intent != canonical(self.plan):
            raise ValueError("C03 volume intent changed")
        actual, inventory = self.volume(), self.volume_inventory()
        known = records.get("c03-volume-created.json")
        deleting = records.get("c03-volume-delete.json")
        if deleting is not None and deleting != known:
            raise ValueError("C03 volume deletion identity changed")
        removed = records.get("c03-volume-removed.json")
        if removed is not None and removed != canonical({"name": self.volume_name, "absent": True}):
            raise ValueError("C03 volume closure changed")
        if actual is not None:
            if removed is not None or known is None or canonical(self.volume_identity(actual)) != known or inventory != [actual]:
                raise ValueError("C03 volume was replaced, unrecorded or joined by another volume")
        elif inventory or known is None and "devcontainer-up-intent.json" in records:
            raise ValueError("C03 volume creation or absence is uncertain")

    def recovery_plan(self):
        self.verify_volume()
        return super().recovery_plan()

    def remove_owned(self):
        # Check volume identity before deleting any service, not just at the end.
        super().remove_owned()
        records = self.journal.records()
        if "c03-volume-intent.json" not in records:
            return
        self.verify_volume()
        if self.volume() is not None:
            self.journal.put("c03-volume-delete.json", records["c03-volume-created.json"])
            status, _ = self.call("DELETE", "/volumes/" + quote(self.volume_name, safe=""))
            if status not in (204, 404) or self.volume() is not None:
                raise ValueError("C03 volume deletion is unverified")
        if self.volume_inventory():
            raise ValueError("C03 volume residue remains")
        self.journal.put("c03-volume-removed.json", canonical({"name": self.volume_name, "absent": True}))
