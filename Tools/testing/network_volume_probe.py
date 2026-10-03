"""E06 network/volume semantics with journalled multi-resource ownership."""

import json
from pathlib import Path
import re
from urllib.parse import quote

from case_evidence import canonical, digest
from exec_probe import execute
from guest_fixture import GuestFixture, OWNER_LABEL


class ScopedJournal:
    """Give each resource its own records inside the same durable transaction log."""

    def __init__(self, journal, scope: str):
        if scope not in {"app", "peer", "reader"}:
            raise ValueError("Unknown E06 journal scope")
        self.journal, self.prefix = journal, "e06-" + scope + "-"

    def records(self):
        return {name[len(self.prefix):]: data for name, data in self.journal.records().items()
                if name.startswith(self.prefix)}

    def put(self, name, data):
        self.journal.put(self.prefix + name, data)


class Resource:
    """Own one network or volume; never prune, force-delete or adopt foreign state."""

    def __init__(self, client, journal, owner: str, kind: str):
        if kind not in {"network", "volume"}:
            raise ValueError("Unsupported fixture resource")
        self.client, self.journal, self.owner, self.kind = client, journal, owner, kind
        self.name = "cf-test-" + owner[:32] + "-" + kind
        self.prefix = "e06-" + kind
        self.intent = {"Name": self.name, "Driver": "bridge" if kind == "network" else "local",
                       "Labels": {OWNER_LABEL: owner}}

    def inspect(self, identifier):
        status, payload = self.client.call("GET", f"/{self.kind}s/{identifier}")
        value = json.loads(payload)
        if status == 404 and isinstance(value, dict) and isinstance(value.get("message"), str):
            return None
        if status != 200 or not isinstance(value, dict):
            raise ValueError("Cannot inspect fixture " + self.kind)
        return value

    def identity(self, value):
        if (value.get("Name") != self.name or not isinstance(value.get("Driver"), str) or not value["Driver"] or
                not isinstance(value.get("Labels"), dict) or value["Labels"].get(OWNER_LABEL) != self.owner):
            raise ValueError("Fixture resource ownership changed")
        # Preserve the observed driver, not a normalized value: released Apple
        # adapters expose their plugin here. E06 asserts inspection, not spelling.
        fields = ("Id", "Created") if self.kind == "network" else ("CreatedAt", "Mountpoint")
        if any(not isinstance(value.get(key), str) or not value[key] for key in fields):
            raise ValueError("Fixture resource omitted stable identity")
        if self.kind == "network" and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value["Id"]) is None:
            raise ValueError("Invalid fixture network ID")
        return {key: value[key] for key in ("Name", "Driver", "Labels", *fields)}

    def create(self):
        if self.prefix + "-intent.json" in self.journal.records():
            raise ValueError("Resource creation already attempted")
        if self.inspect(self.name) is not None:
            raise ValueError("Fixture resource name already exists")
        self.journal.put(self.prefix + "-intent.json", canonical(self.intent))
        status, payload = self.client.call("POST", f"/{self.kind}s/create", self.intent)
        value = json.loads(payload)
        if status != 201 or not isinstance(value, dict):
            raise ValueError("Fixture resource creation failed")
        if self.kind == "volume":
            observed = self.identity(value)
        else:
            identifier = value.get("Id")
            if not isinstance(identifier, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", identifier) is None:
                raise ValueError("Network creation returned an invalid ID")
            # Preserve the returned ID before any follow-up could fail.
            self.journal.put(self.prefix + "-created-id.json", canonical({"id": identifier}))
            actual = self.inspect(identifier)
            if actual is None or actual.get("Id") != identifier:
                raise ValueError("New network identity cannot be verified")
            observed = self.identity(actual)
        self.journal.put(self.prefix + "-created.json", canonical(observed))
        return observed

    def cleanup(self):
        records = self.journal.records()
        intent = records.get(self.prefix + "-intent.json")
        if intent is None:
            return
        if intent != canonical(self.intent):
            raise ValueError("Resource journal belongs to another case")
        known = json.loads(records.get(self.prefix + "-created.json", b"null"))
        deleting = json.loads(records.get(self.prefix + "-delete.json", b"null"))
        known_id = json.loads(records.get(self.prefix + "-created-id.json", b"null"))
        if known is not None and deleting is not None and known != deleting:
            raise ValueError("Resource creation/deletion identities disagree")
        known = known or deleting
        actual = self.inspect(self.name)
        if actual is not None:
            observed = self.identity(actual)
            if (known is not None and known != observed or
                    known_id is not None and known_id != {"id": actual.get("Id")} or
                    self.prefix + "-removed.json" in records):
                raise ValueError("Fixture resource was replaced or reappeared")
            if self.kind == "network" and actual.get("Containers") != {}:
                raise ValueError("Fixture network still has attached containers")
            self.journal.put(self.prefix + "-delete.json", canonical(observed))
            identifier = actual["Id"] if self.kind == "network" else self.name
            status, _ = self.client.call("DELETE", f"/{self.kind}s/{identifier}")
            if status not in {204, 404} or self.inspect(identifier) is not None or self.inspect(self.name) is not None:
                raise ValueError("Fixture resource deletion is unverified")
        elif known is None and known_id is None:
            raise ValueError("Resource creation is uncertain; preserve runtime")
        elif self.kind == "network":
            identifier = known["Id"] if known is not None else known_id["id"]
            if self.inspect(identifier) is not None:
                raise ValueError("Fixture network was renamed")
        filters = quote(canonical({"label": [OWNER_LABEL + "=" + self.owner]}).decode(), safe="")
        status, payload = self.client.call("GET", f"/{self.kind}s?filters=" + filters)
        value = json.loads(payload)
        empty = value == [] if self.kind == "network" else (
            isinstance(value, dict) and "Volumes" in value and value["Volumes"] in ([], None) and not value.get("Warnings"))
        if status != 200 or not empty:
            raise ValueError("Fixture resource residue remains")
        self.journal.put(self.prefix + "-removed.json", canonical({"name": self.name, "absent": True}))


class NetworkVolumeFixture:
    def __init__(self, socket: Path, owner: str, image: str, version: str, journal, root: Path, *, observe=None):
        self.journal, self.root = journal, root
        self.intent = {"owner": owner, "image": image, "socket": str(socket), "apiVersion": version, "root": str(root)}
        self.client = GuestFixture(socket, owner, image, version, journal, observe=observe)
        self.network = Resource(self.client, journal, owner, "network")
        self.volume = Resource(self.client, journal, owner, "volume")
        self.guests = []
        for role in ("app", "peer", "reader"):
            mounts = [{"Type": "volume", "Source": self.volume.name, "Target": "/data"}] if role != "peer" else []
            if role == "app":
                mounts.extend([{"Type": "bind", "Source": str(root / "readonly-input"), "Target": "/input", "ReadOnly": True},
                               {"Type": "tmpfs", "Target": "/scratch"}])
            guest = GuestFixture(socket, digest((owner + ":" + role).encode()), image, version, ScopedJournal(journal, role),
                                 network=self.network.name if role != "reader" else "none", mounts=tuple(mounts),
                                 aliases=("app",) if role == "app" else (), observe=observe)
            self.guests.append(guest)

    def operation(self):
        if "network-volume-intent.json" in self.journal.records():
            raise ValueError("Network/volume fixture already attempted")
        self.journal.put("network-volume-intent.json", canonical(self.intent))
        bind = self.root / "readonly-input"
        bind.mkdir(mode=0o700)
        (bind / "input.txt").write_bytes(b"read-only\n")
        self.network.create()
        self.volume.create()
        app, peer, reader = self.guests
        app.setup()
        peer.setup()
        dns = execute(peer, "dns", ["ping", "-c", "1", "app"])
        written = execute(app, "write", ["sh", "-c", "printf volume-data >/data/value"])
        reader.setup()
        persisted = execute(reader, "persisted", ["cat", "/data/value"])
        original = execute(app, "original", ["cat", "/input/input.txt"])
        readonly = execute(app, "readonly", ["sh", "-c", "printf changed >/input/input.txt"])
        temporary = execute(app, "tmpfs", ["sh", "-c", "printf scratch >/scratch/value && cat /scratch/value"])
        inspected = app.inspect(app.identifier)
        if inspected is None or app.owned(inspected) != app.identifier:
            raise ValueError("Application identity changed during mount observations")
        mounts = inspected.get("Mounts", [])
        tmpfs = isinstance(mounts, list) and any(isinstance(mount, dict) and mount.get("Type") == "tmpfs" and
                                                mount.get("Destination") == "/scratch" for mount in mounts)
        network = self.network.inspect(self.network.name)
        volume = self.volume.inspect(self.volume.name)
        values = {"bind_read_only": original == (b"read-only\n", b"", 0) and readonly[2] != 0 and
                                    (bind / "input.txt").read_bytes() == b"read-only\n",
                  "network_dns": dns[2] == 0, "network_inspect": network is not None,
                  "volume_inspect": volume is not None, "tmpfs": tmpfs and temporary[0] == b"scratch" and temporary[2] == 0,
                  "volume_persistence": written[2] == 0 and persisted[0] == b"volume-data" and persisted[2] == 0}
        return {key: str(value).lower() for key, value in values.items()}

    def cleanup(self):
        intent = self.journal.records().get("network-volume-intent.json")
        if intent is None:
            return {"status": "passed", "remainingOwnedResources": []}
        if intent != canonical(self.intent):
            raise ValueError("Network/volume journal belongs to another fixture")
        # Reverse dependency order; a refused guest cleanup keeps its providers.
        for guest in reversed(self.guests):
            guest.cleanup()
        self.network.cleanup()
        self.volume.cleanup()
        self.journal.put("network-volume-removed.json", canonical({"intentSHA256": digest(intent), "absent": True}))
        return {"status": "passed", "remainingOwnedResources": []}
