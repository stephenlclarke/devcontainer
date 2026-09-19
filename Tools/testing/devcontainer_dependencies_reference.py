"""C02 dependency health/DNS with generation-bound three-service cleanup."""

import json
from pathlib import Path
import re

from case_evidence import canonical
from devcontainer_compose_reference import DevcontainerComposeReference, PROJECT_LABEL, SERVICE_LABEL
from devcontainer_reference import DevcontainerReference, IMAGE, created_id


FIXTURE = "C02-compose-dependencies"
DATABASE_IMAGE = "python:3.13-alpine@sha256:399babc8b49529dabfd9c922f2b5eea81d611e4512e3ed250d75bd2e7683f4b0"
SERVICES = ("app", "helper", "database")


def fixture_inputs(repository: Path) -> dict:
    root = repository / "Tests/Parity/fixtures" / FIXTURE
    values = {key: (root / path).read_text() for key, path in (
        ("configuration", ".devcontainer/devcontainer.json"), ("probe", "probe.sh"), ("compose", "compose.yaml"))}
    configuration = json.loads(values["configuration"])
    if (configuration.get("dockerComposeFile") != "../compose.yaml" or configuration.get("service") != "app" or
            configuration.get("runServices") != ["app", "database", "helper"] or
            configuration.get("shutdownAction") != "stopCompose" or
            values["compose"].count(IMAGE) != 2 or values["compose"].count(DATABASE_IMAGE) != 1):
        raise ValueError("C02 dependency fixture or image pins changed")
    return values


class DevcontainerDependenciesReference(DevcontainerComposeReference):
    fixture = FIXTURE
    keys = {"dependency_dns", "dependency_health", "run_service"}
    commands = (*DevcontainerReference.commands, "devcontainer-dependency-pull")

    def __init__(self, vm, inputs, owner, *, observe=None):
        super().__init__(vm, inputs, owner, observe=observe)
        self.project = "cf-c02-" + self.owner[:32]
        self.network_name = self.project + "_default"
        self.plan["project"] = self.project

    def prepare(self):
        if self.project_inventory("containers") or self.project_inventory("networks") or self.network(self.network_name):
            raise ValueError("C02 project already exists")
        DevcontainerReference.prepare(self)
        self.journal.put("c02-project-intent.json", canonical(self.plan))

    def prepare_image(self):
        super().prepare_image()
        self.vm.command("devcontainer-dependency-pull", [self.inputs["tools"]["docker"], "--host",
                        "unix://" + str(self.socket), "image", "pull", "--platform=linux/arm64", DATABASE_IMAGE], timeout=120)

    def service_identity(self, value, role):
        """Bind sibling ownership to project, workspace, role, image and incarnation."""
        config = value.get("Config", {})
        labels = config.get("Labels", {})
        identifier = value.get("Id")
        image = DATABASE_IMAGE if role == "database" else IMAGE
        admitted = self.inputs["dependencyWorkload" if role == "database" else "workload"]["image"]
        allowed = {admitted["manifest"], admitted["config"], image.split("@", 1)[1]}
        if (role not in SERVICES or not isinstance(identifier, str) or re.fullmatch(self.id_pattern, identifier) is None or
                not isinstance(labels, dict) or labels.get(PROJECT_LABEL) != self.project or labels.get(SERVICE_LABEL) != role or
                labels.get("com.docker.compose.project.working_dir") != str(self.workspace) or
                config.get("Image") != image or value.get("Image") not in allowed or
                not isinstance(value.get("Name"), str) or not value["Name"].startswith("/") or
                not isinstance(value.get("Created"), str) or not value["Created"]):
            raise ValueError("C02 service ownership or incarnation is invalid")
        if role == "app" and self.owned(value) != identifier:
            raise ValueError("C02 app identity changed")
        return {"Id": identifier, "Name": value["Name"], "Created": value["Created"], "Image": value["Image"],
                "Config": {key: config.get(key) for key in ("Image", "Labels", "Cmd", "Entrypoint")}}

    def service_inventory(self):
        result = {}
        for item in self.project_inventory("containers"):
            identifier = item.get("Id")
            if not isinstance(identifier, str) or re.fullmatch(self.id_pattern, identifier) is None:
                raise ValueError("C02 inventory lacks a complete ID")
            value = self.inspect(identifier)
            role = value.get("Config", {}).get("Labels", {}).get(SERVICE_LABEL) if isinstance(value, dict) else None
            if role not in SERVICES or role in result or value.get("Id") != identifier:
                raise ValueError("C02 project inventory contains unknown or duplicate services")
            result[role] = self.service_identity(value, role)
        if len({item["Id"] for item in result.values()}) != len(result):
            raise ValueError("C02 project contains duplicate identities")
        return result

    def up_identity(self, output):
        identifier = created_id(output, self.id_pattern)
        services = self.service_inventory()
        if set(services) != set(SERVICES) or services["app"]["Id"] != identifier:
            raise ValueError("C02 requires exactly the selected app and two dependencies")
        network = self.network(self.network_name)
        if network is None:
            raise ValueError("C02 network disappeared")
        identity = self.network_identity(network)
        if ([item.get("Id") for item in self.project_inventory("networks")] != [identity["Id"]] or
                set(network["Containers"]) != {item["Id"] for item in services.values()}):
            raise ValueError("C02 network membership differs from the three-service project")
        self.journal.put("c02-project-created.json", canonical({"services": services, "network": identity}))
        for role, item in services.items():
            inspected = self.inspect(item["Id"])
            state = inspected.get("State", {})
            # Keep diagnostics separate from the stable ownership receipt and
            # omit environment values, which may contain credentials.
            self.journal.put("c02-" + role + "-network-state.json", canonical({
                "Id": item["Id"], "State": state, "NetworkSettings": inspected.get("NetworkSettings"),
                "NetworkMode": inspected.get("HostConfig", {}).get("NetworkMode")}))
            if state.get("Status") != "running" or (role == "database" and state.get("Health", {}).get("Status") != "healthy"):
                raise ValueError("C02 selected services are not running and healthy")
        return identifier

    def execute(self):
        result = super().execute()
        # Read only after the original probe: another exec could itself trigger
        # hosts reconciliation and change the behavior under investigation.
        try:
            actual = self.find()
            if actual is None or self.owned(actual) != self.identifier:
                raise ValueError("C02 diagnostic target changed")
            if actual.get("State", {}).get("Status") != "running":
                self.journal.put("c02-app-hosts-status.json", canonical({"skipped": "container-not-running"}))
                return result
            status, payload = self.call("GET", f"/containers/{self.identifier}/archive?path=/etc/hosts",
                                        total_timeout=5)
            if status == 200:
                self.journal.put("c02-app-hosts.tar", payload)
            self.journal.put("c02-app-hosts-status.json", canonical({"status": status}))
        except Exception as error:
            # A bounded optional diagnostic must never replace probe results.
            self.journal.put("c02-app-hosts-status.json", canonical({"error": type(error).__name__}))
        return result

    def recovery_plan(self):
        records = self.journal.records()
        # Check command completion before any resource mutation, including when
        # the CLI failed before its aggregate creation receipt was recorded.
        if "devcontainer-plan.json" not in records:
            return None
        if records["devcontainer-plan.json"] != canonical(self.plan) or self.vm.uncertain:
            raise ValueError("C02 process or plan ownership is uncertain")
        for command in self.commands:
            if command + "-intent.json" in records and command + "-exit.json" not in records:
                raise ValueError("C02 command completion is unknown")
        services = self.service_inventory()
        actual = self.network(self.network_name)
        networks = self.project_inventory("networks")
        known = json.loads(records.get("c02-project-created.json", b"null"))
        if known is None:
            if services or actual is not None or networks or "devcontainer-up-intent.json" in records:
                raise ValueError("C02 creation outcome is unobserved; preserve quarantine")
            return None
        if records.get("c02-project-intent.json") != canonical(self.plan):
            raise ValueError("C02 project intent changed")
        removed = records.get("c02-project-removed.json")
        if removed is not None and (removed != canonical({"project": self.project, "absent": True}) or services or actual or networks):
            raise ValueError("C02 closed project reappeared or closure changed")
        for role in SERVICES:
            expected = known["services"][role]
            closed = records.get("c02-" + role + "-removed.json")
            if closed is not None and closed != canonical({"id": expected["Id"], "absent": True}):
                raise ValueError("C02 service closure receipt changed")
            if role in services:
                if services[role] != expected or closed is not None:
                    raise ValueError("C02 service was replaced or reappeared")
            elif self.inspect(expected["Id"]) is not None:
                raise ValueError("C02 known service survived with changed ownership")
        network = known["network"]
        if actual is not None:
            if (self.network_identity(actual) != network or
                    set(actual["Containers"]) - {item["Id"] for item in services.values()} or
                    [item.get("Id") for item in networks] != [network["Id"]]):
                raise ValueError("C02 network ownership or membership changed")
        elif networks or self.network(network["Id"]) is not None:
            raise ValueError("C02 network survived with changed ownership")
        return known

    def remove_owned(self):
        known = self.recovery_plan()
        if "devcontainer-plan.json" not in self.journal.records():
            return
        if known is not None:
            for role in SERVICES:
                self.recovery_plan()
                expected = known["services"][role]
                if self.inspect(expected["Id"]) is not None:
                    self.journal.put("c02-" + role + "-delete.json", canonical(expected))
                    status, _ = self.call("DELETE", "/containers/" + expected["Id"] + "?force=true&v=true")
                    if status not in (204, 404) or self.inspect(expected["Id"]) is not None:
                        raise ValueError("C02 service removal is unverified")
                self.journal.put("c02-" + role + "-removed.json", canonical({"id": expected["Id"], "absent": True}))
            self.recovery_plan()
            network = known["network"]
            actual = self.network(network["Id"])
            if actual is not None:
                if actual["Containers"]:
                    raise ValueError("C02 network remains attached")
                self.journal.put("c02-network-delete.json", canonical(network))
                status, _ = self.call("DELETE", "/networks/" + network["Id"])
                if status not in (204, 404):
                    raise ValueError("C02 network removal failed")
            if self.network(network["Id"]) or self.network(self.network_name):
                raise ValueError("C02 network remains after deletion")
        if self.project_inventory("containers") or self.project_inventory("networks"):
            raise ValueError("C02 project residue remains")
        self.journal.put("c02-project-removed.json", canonical({"project": self.project, "absent": True}))
        self.journal.put("devcontainer-removed.json", canonical({"verifiedAbsent": True}))
