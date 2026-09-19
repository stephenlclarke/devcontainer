"""C01 service configuration with one private Compose project and exact cleanup."""

import json
from pathlib import Path
import re
from urllib.parse import quote

from case_evidence import canonical
from devcontainer_reference import DevcontainerReference


FIXTURE = "C01-compose-service"
PROJECT_LABEL = "com.docker.compose.project"
SERVICE_LABEL = "com.docker.compose.service"
NETWORK_LABEL = "com.docker.compose.network"


def fixture_inputs(repository: Path) -> dict:
    root = repository / "Tests/Parity/fixtures" / FIXTURE
    result = {key: (root / path).read_text() for key, path in (
        ("configuration", ".devcontainer/devcontainer.json"), ("probe", "probe.sh"), ("compose", "compose.yaml"))}
    configuration = json.loads(result["configuration"])
    if (configuration.get("dockerComposeFile") != "../compose.yaml" or configuration.get("service") != "app" or
            configuration.get("runServices") != ["app"] or configuration.get("shutdownAction") != "stopCompose"):
        raise ValueError("C01 service contract changed")
    return result


class DevcontainerComposeReference(DevcontainerReference):
    fixture = FIXTURE
    keys = {"compose_env", "post_create", "workspace"}

    def __init__(self, vm, inputs, owner, *, observe=None):
        super().__init__(vm, inputs, owner, observe=observe)
        self.project = "cf-c01-" + self.owner[:32]
        self.network_name = self.project + "_default"
        self.plan.update(project=self.project, compose=inputs["compose"]["executables"]["docker-compose"])

    def write_workspace(self):
        super().write_workspace()
        (self.workspace / "compose.yaml").write_text(self.inputs["devcontainerFixture"]["compose"])

    def project_inventory(self, collection):
        filters = quote(canonical({"label": [PROJECT_LABEL + "=" + self.project]}).decode(), safe="")
        path = "/containers/json?all=true&" if collection == "containers" else "/networks?"
        status, payload = self.call("GET", path + "filters=" + filters)
        result = json.loads(payload)
        if status != 200 or not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
            raise ValueError("C01 project inventory is invalid")
        return result

    def network(self, identifier):
        status, payload = self.call("GET", "/networks/" + quote(identifier, safe=""))
        value = json.loads(payload)
        if status == 404 and isinstance(value, dict) and isinstance(value.get("message"), str):
            return None
        if status != 200 or not isinstance(value, dict):
            raise ValueError("C01 network inspection failed")
        return value

    def network_identity(self, value):
        labels = value.get("Labels", {})
        identifier = value.get("Id")
        if (not isinstance(identifier, str) or re.fullmatch(self.id_pattern, identifier) is None or
                value.get("Name") != self.network_name or not isinstance(labels, dict) or
                labels.get(PROJECT_LABEL) != self.project or labels.get(NETWORK_LABEL) != "default" or
                not isinstance(value.get("Created"), str) or not value["Created"] or
                not isinstance(value.get("Containers"), dict)):
            raise ValueError("C01 network ownership changed")
        return {key: value[key] for key in ("Id", "Name", "Labels", "Created")}

    def prepare(self):
        if self.project_inventory("containers") or self.project_inventory("networks") or self.network(self.network_name):
            raise ValueError("C01 project already exists")
        super().prepare()
        self.journal.put("c01-project-intent.json", canonical(self.plan))

    def arguments(self, command):
        arguments = super().arguments(command)
        arguments.insert(2, "COMPOSE_PROJECT_NAME=" + self.project)
        return arguments + ["--docker-compose-path", self.inputs["compose"]["executables"]["docker-compose"]]

    def owned(self, value):
        identifier = super().owned(value)
        labels = value["Config"]["Labels"]
        if labels.get(PROJECT_LABEL) != self.project or labels.get(SERVICE_LABEL) != "app":
            raise ValueError("C01 service ownership changed")
        return identifier

    def capture_network(self, identifier):
        networks = self.project_inventory("networks")
        if len(networks) != 1:
            raise ValueError("C01 requires exactly one project network")
        actual = self.network(self.network_name)
        if actual is None:
            raise ValueError("C01 project network disappeared")
        identity = self.network_identity(actual)
        if networks[0].get("Id") != identity["Id"] or set(actual["Containers"]) != {identifier}:
            raise ValueError("C01 network inventory or membership differs")
        self.journal.put("c01-network-created.json", canonical(identity))

    def up_identity(self, output):
        identifier = super().up_identity(output)
        if [item.get("Id") for item in self.project_inventory("containers")] != [identifier]:
            raise ValueError("C01 project has unexpected services")
        # Capture network identity before exec, so a failed probe remains recoverable.
        self.capture_network(identifier)
        return identifier

    def recovery_plan(self):
        records = self.journal.records()
        if "c01-project-intent.json" not in records:
            return
        if records["c01-project-intent.json"] != canonical(self.plan):
            raise ValueError("C01 project intent changed")
        identifier = super().recovery_plan()
        values = self.project_inventory("containers")
        if [value.get("Id") for value in values] != ([identifier] if identifier else []):
            raise ValueError("C01 project service inventory changed")
        actual = self.network(self.network_name)
        known = json.loads(records.get("c01-network-created.json", b"null"))
        removed = records.get("c01-project-removed.json")
        if removed is not None:
            if removed != canonical({"project": self.project, "absent": True}):
                raise ValueError("C01 project closure receipt changed")
            if actual is not None or self.project_inventory("networks"):
                raise ValueError("C01 network reappeared after project cleanup")
        if actual is not None:
            identity = self.network_identity(actual)
            if known is None or identity != known or set(actual["Containers"]) - ({identifier} if identifier else set()):
                raise ValueError("C01 network creation or membership is uncertain")
        elif known is None or self.network(known["Id"]) is not None:
            raise ValueError("C01 network absence is uncertain")
        if [value.get("Id") for value in self.project_inventory("networks")] != ([known["Id"]] if actual else []):
            raise ValueError("C01 network inventory changed")
        return identifier

    def remove_owned(self):
        self.recovery_plan()
        records = self.journal.records()
        if "c01-project-intent.json" not in records:
            return
        known = json.loads(records["c01-network-created.json"])
        actual = self.network(self.network_name)
        # Delete only the authenticated service; never invoke broad Compose down.
        super().remove_owned()
        if actual is not None:
            actual = self.network(known["Id"])
            if actual is None or self.network_identity(actual) != known or actual["Containers"]:
                raise ValueError("C01 network changed or remains attached")
            self.journal.put("c01-network-delete.json", canonical(known))
            status, _ = self.call("DELETE", "/networks/" + known["Id"])
            if status not in (204, 404) or self.network(known["Id"]) or self.network(self.network_name):
                raise ValueError("C01 network removal unverified")
        if self.project_inventory("containers") or self.project_inventory("networks"):
            raise ValueError("C01 project residue remains")
        self.journal.put("c01-project-removed.json", canonical({"project": self.project, "absent": True}))
