"""D07 repeated up, replacement, persistent counters and owned cleanup."""

import json
from pathlib import Path

from case_evidence import canonical
from devcontainer_reference import DevcontainerReference, IMAGE, WORKSPACE, observations
from network_volume_probe import Resource
from host_runtime import deadline


FIXTURE = "D07-reuse-cleanup"
VOLUME = "dcparity-d07-reuse"
COMMANDS = ("devcontainer-reuse", "devcontainer-reuse-exec", "devcontainer-rebuild", "devcontainer-rebuild-exec")
UP_COMMANDS = {"devcontainer-up", "devcontainer-reuse", "devcontainer-rebuild"}


def fixture_inputs(repository: Path) -> dict:
    root = repository / "Tests/Parity/fixtures" / FIXTURE
    result = {key: (root / path).read_text() for key, path in (
        ("configuration", ".devcontainer/devcontainer.json"), ("probe", "probe.sh"))}
    config = json.loads(result["configuration"])
    if (config.get("image") != IMAGE or config.get("remoteUser") != "root" or
            config.get("overrideCommand") is not True or
            config.get("mounts") != [f"source={VOLUME},target=/cache,type=volume"]):
        raise ValueError("D07 configuration, volume or image pin changed")
    return result


class ReuseVolume(Resource):
    """Retain the fixture's fixed name only inside the exclusively owned engine."""

    def __init__(self, client):
        super().__init__(client, client.journal, client.owner, "volume")
        self.name, self.prefix = VOLUME, "d07-volume"
        self.intent["Name"] = self.name

    def verify(self):
        known = self.journal.records().get(self.prefix + "-created.json")
        actual = self.inspect(self.name)
        if known is None or actual is None or canonical(self.identity(actual)) != known:
            raise ValueError("D07 volume identity changed")


class DevcontainerReuseReference(DevcontainerReference):
    fixture = FIXTURE
    keys = {"create_count", "start_count"}
    commands = (*DevcontainerReference.commands, *COMMANDS)

    def __init__(self, vm, inputs, owner, *, observe=None):
        super().__init__(vm, inputs, owner, observe=observe)
        self.volume = ReuseVolume(self)

    def prepare(self):
        super().prepare()
        self.volume.create()

    def owned(self, value):
        identifier = super().owned(value)
        if sum(mount.get("Type") == "volume" and mount.get("Name") == VOLUME and
               mount.get("Destination") == "/cache" for mount in value["Mounts"] if isinstance(mount, dict)) != 1:
            raise ValueError("D07 volume mount changed")
        self.volume.verify()
        return identifier

    def counters(self, name):
        output = self.vm.command(name, self.arguments("exec") + ["--", "/bin/sh", WORKSPACE + "/probe.sh"],
                                 timeout=60, separate_output=True)
        return observations(output, self.keys)

    def operation(self):
        # Three bounded up/exec pairs plus ownership and removal probes.
        with deadline(570):
            return self.execute()

    def next_up(self, name, *, rebuild=False):
        arguments = self.arguments("up") + ["--user-data-folder", str(self.vm.root / "devcontainer-data")]
        if rebuild:
            arguments.append("--remove-existing-container")
        output = self.vm.command(name, arguments, timeout=120, separate_output=True)
        identifier = self.up_identity(output)
        self.journal.put(name + "-result.json", canonical({"id": identifier}))
        actual = self.find()
        if actual is None or self.owned(actual) != identifier:
            raise ValueError("D07 CLI result does not match owned inventory")
        return identifier

    def execute(self):
        first_counts = super().execute()
        first = self.identifier
        if first_counts != {"create_count": "1", "start_count": "1"}:
            raise ValueError("D07 initial hooks did not run exactly once")
        second = self.next_up("devcontainer-reuse")
        reused_counts = self.counters("devcontainer-reuse-exec")
        if second != first or reused_counts != first_counts:
            raise ValueError("D07 reuse changed identity or reran hooks")
        third = self.next_up("devcontainer-rebuild", rebuild=True)
        if third == first or self.inspect(first) is not None:
            raise ValueError("D07 rebuild did not replace the first container")
        final = self.counters("devcontainer-rebuild-exec")
        if final != {"create_count": "2", "start_count": "2"}:
            raise ValueError("D07 rebuild did not preserve volume counters")
        self.remove_owned()
        return {**final, "reused": "true", "rebuilt": "true", "hooks_idempotent_on_reuse": "true", "cleanup": "true"}

    def recovery_plan(self):
        records = self.journal.records()
        if "devcontainer-plan.json" not in records:
            return None
        if records["devcontainer-plan.json"] != canonical(self.plan) or self.vm.uncertain:
            raise ValueError("D07 has uncertain process or plan ownership")
        for command in self.commands:
            if command + "-intent.json" in records and command + "-exit.json" not in records:
                raise ValueError("D07 command completion is unknown")
        names = ("devcontainer-created.json", "devcontainer-reuse-result.json",
                 "devcontainer-rebuild-result.json", "d07-delete.json")
        known = {json.loads(records[name])["id"] for name in names if name in records}
        actual = self.find()
        identifier = self.owned(actual) if actual is not None else None
        if "devcontainer-rebuild-intent.json" in records and "devcontainer-rebuild-result.json" not in records:
            previous = json.loads(records.get("devcontainer-created.json", b"null"))
            deleting = json.loads(records.get("d07-delete.json", b"null"))
            observed = {"id": identifier} if identifier is not None else deleting
            if observed is None or observed == previous:
                raise ValueError("D07 rebuild outcome has no observed replacement; preserve quarantine")
        for previous in known - {identifier}:
            if self.inspect(previous) is not None:
                raise ValueError("D07 earlier container survived or changed ownership")
        if identifier is not None:
            if "devcontainer-up-intent.json" not in records or "devcontainer-removed.json" in records:
                raise ValueError("D07 container lacks intent or reappeared after removal")
            if identifier not in known:
                latest = "devcontainer-rebuild" if "devcontainer-rebuild-intent.json" in records else "devcontainer-up"
                receipt = "devcontainer-rebuild-result.json" if latest.endswith("rebuild") else "devcontainer-created.json"
                if receipt in records:
                    raise ValueError("D07 observed container was replaced")
            return identifier
        if not known and "devcontainer-up-intent.json" in records:
            raise ValueError("D07 has no observed creation outcome; preserve quarantine")
        return None

    def remove_owned(self):
        identifier = self.recovery_plan()
        if identifier is not None:
            self.journal.put("d07-delete.json", canonical({"id": identifier}))
            status, _ = self.call("DELETE", f"/containers/{identifier}?force=true&v=true")
            if status not in (204, 404) or self.inspect(identifier) is not None or self.find() is not None:
                raise ValueError("D07 container removal did not complete")
        self.volume.cleanup()
        if "devcontainer-plan.json" in self.journal.records():
            self.journal.put("devcontainer-removed.json", canonical({"verifiedAbsent": True}))
