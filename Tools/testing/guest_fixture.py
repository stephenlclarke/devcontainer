"""Journalled disposable-container ownership for direct-HTTP guest fixtures.

The caller admits the socket/runtime/image closure, holds the host lease and
supplies a private durable journal and whole-phase deadlines. No implicit pull,
build, Docker CLI, daemon startup or broad cleanup is performed here.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import time
from urllib.parse import quote

from archive_probe import archive_copy
from case_evidence import canonical
from engine_probe import request


OWNER_LABEL = "devcontainer.parity.case"


class GuestFixture:
    """Create once; reconcile uncertain creation/deletion by exact ID and owner."""

    id_pattern = r"[0-9a-f]{64}"

    def __init__(self, socket: Path, owner: str, image: str, api_version: str, journal,
                 *, command: tuple[str, ...] = ("sleep", "300"), network="none", mounts=(), aliases=(), observe=None):
        if (re.fullmatch(r"[0-9a-f]{64}", owner) is None or
                re.fullmatch(r"sha256:[0-9a-f]{64}", image) is None or
                re.fullmatch(r"[0-9]+\.[0-9]+", api_version) is None or
                not socket.is_absolute() or socket.resolve() != socket):
            raise ValueError("Guest fixture requires immutable case/image identities and API version")
        if (not isinstance(command, tuple) or not command or len(command) > 32 or
                any(not isinstance(item, str) or not item or "\0" in item for item in command)):
            raise ValueError("Guest fixture requires an explicit command tuple")
        if (not isinstance(network, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", network) is None or
                not isinstance(mounts, tuple) or any(not isinstance(mount, dict) for mount in mounts) or
                not isinstance(aliases, tuple) or any(not isinstance(alias, str) or
                    re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", alias) is None for alias in aliases)):
            raise ValueError("Guest network/mount configuration is invalid")
        self.socket, self.journal = socket, journal
        self.observe = observe
        self.owner, self.image, self.version = owner, image, api_version
        self.name = "cf-test-" + owner[:32]
        self.intent = {"name": self.name, "image": image, "labels": {OWNER_LABEL: owner},
                       "socket": str(socket), "apiVersion": api_version, "command": list(command)}
        self.configuration = json.loads(canonical({"network": network, "mounts": mounts, "aliases": aliases}))
        if network != "none" or mounts or aliases:
            self.intent["networkMounts"] = self.configuration
        self.identifier = None

    def call(self, method: str, route: str, body=None, *, timeout=5, total_timeout=None):
        event = {"method": method, "route": f"/v{self.version}{route}"}
        started = time.monotonic_ns()
        try:
            options = {"total_timeout": total_timeout} if total_timeout is not None else {}
            status, payload = request(self.socket, method, event["route"],
                                      canonical(body) if body is not None else None, timeout=timeout, **options)
            event["status"] = status
            return status, payload
        except (Exception, KeyboardInterrupt) as error:
            event["error"] = type(error).__name__
            raise
        finally:
            event["durationNS"] = time.monotonic_ns() - started
            if self.observe is not None:
                self.observe(event)

    def inspect(self, resource: str):
        status, payload = self.call("GET", f"/containers/{resource}/json")
        value = json.loads(payload)
        if status == 404 and isinstance(value, dict) and isinstance(value.get("message"), str):
            return None
        if status != 200 or not isinstance(value, dict):
            raise ValueError("Cannot inspect guest resource identity")
        return value

    def owned(self, value: dict) -> str:
        identifier = value.get("Id")
        config = value.get("Config")
        labels = config.get("Labels") if isinstance(config, dict) else None
        if (not isinstance(identifier, str) or re.fullmatch(self.id_pattern, identifier) is None or
                value.get("Name") != "/" + self.name or not isinstance(config, dict) or
                not isinstance(labels, dict) or labels.get(OWNER_LABEL) != self.owner or config.get("Image") != self.image or
                config.get("Cmd") != self.intent["command"] or
                value.get("Image") != self.image):
            raise ValueError("Guest resource ownership or image changed; refusing mutation")
        return identifier

    def setup(self):
        self.create()
        self.start()

    def create(self):
        if "container-intent.json" in self.journal.records():
            raise ValueError("Guest creation already attempted; reconcile instead of retrying")
        status, payload = self.call("GET", f"/images/{self.image}/json")
        if status != 200 or json.loads(payload).get("Id") != self.image:
            raise ValueError("Pinned guest image is not prepared; no implicit pull")
        if self.inspect(self.name) is not None:
            raise ValueError("Fixture name already exists; refusing adoption")
        self.journal.put("container-intent.json", canonical(self.intent))
        body = {"Image": self.image, "Cmd": self.intent["command"], "Labels": self.intent["labels"],
                "HostConfig": {"AutoRemove": False, "NetworkMode": self.configuration["network"]}}
        if self.configuration["mounts"]:
            body["HostConfig"]["Mounts"] = self.configuration["mounts"]
        if self.configuration["aliases"]:
            body["NetworkingConfig"] = {"EndpointsConfig": {
                self.configuration["network"]: {"Aliases": self.configuration["aliases"]}}}
        status, payload = self.call("POST", f"/containers/create?name={self.name}", self.creation_body(body))
        value = json.loads(payload)
        identifier = value.get("Id") if isinstance(value, dict) else None
        if status != 201 or not isinstance(identifier, str) or re.fullmatch(self.id_pattern, identifier) is None:
            raise ValueError("Guest creation failed or returned an invalid ID")
        # Retain the returned ID before further requests; uncertain responses
        # are recoverable by the already-journalled unique name and labels.
        self.journal.put("container-created.json", canonical({"id": identifier}))
        actual = self.inspect(identifier)
        if actual is None or self.owned(actual) != identifier:
            raise ValueError("Created guest is not the admitted resource")
        self.identifier = identifier
        return actual

    def creation_body(self, body: dict) -> dict:
        """Subclasses can project additional journal-bound resource settings."""
        return body

    def start(self):
        if self.identifier is None:
            raise ValueError("Guest start requires a verified created ID")
        identifier = self.identifier
        actual = self.inspect(identifier)
        if actual is None or self.owned(actual) != identifier:
            raise ValueError("Guest identity changed before start")
        status, _ = self.call("POST", f"/containers/{identifier}/start")
        if status != 204:
            raise ValueError("Guest start failed")
        actual = self.inspect(identifier)
        if actual is None or self.owned(actual) != identifier or actual.get("State", {}).get("Status") != "running":
            raise ValueError("Guest did not enter running state")

    def remove(self):
        """Exercise ordinary removal, separately from forceful failure cleanup."""
        if self.identifier is None:
            raise ValueError("Guest removal requires a verified created ID")
        actual = self.inspect(self.identifier)
        if actual is None or self.owned(actual) != self.identifier:
            raise ValueError("Guest identity changed before removal")
        self.journal.put("container-delete-intent.json", canonical({"id": self.identifier}))
        status, _ = self.call("DELETE", f"/containers/{self.identifier}?v=true")
        if status != 204 or self.inspect(self.identifier) is not None:
            raise ValueError("Ordinary guest removal failed")

    def archive(self, *, observe=None):
        if self.identifier is None:
            raise ValueError("Archive fixture requires a successfully started guest")
        actual = self.inspect(self.identifier)
        if actual is None or self.owned(actual) != self.identifier or actual.get("State", {}).get("Status") != "running":
            raise ValueError("Archive guest identity/state changed")
        return archive_copy(self.socket, self.identifier, self.version, observe=observe)

    def cleanup(self):
        records = self.journal.records()
        intent = records.get("container-intent.json")
        if intent is None:
            return {"status": "passed", "remainingOwnedResources": []}
        if intent != canonical(self.intent):
            raise ValueError("Guest journal belongs to another fixture")
        created = json.loads(records["container-created.json"]) if "container-created.json" in records else None
        deleting = json.loads(records["container-delete-intent.json"]) if "container-delete-intent.json" in records else None
        if created is not None and deleting is not None and created != deleting:
            raise ValueError("Guest creation and deletion identities disagree")
        known = created or deleting
        actual = self.inspect(self.name)
        if actual is not None:
            identifier = self.owned(actual)
            if known is not None and known != {"id": identifier}:
                raise ValueError("Fixture name now identifies a different guest")
            if "container-removed.json" in records:
                raise ValueError("A removed fixture resource reappeared")
            self.journal.put("container-delete-intent.json", canonical({"id": identifier}))
            # Remove by immutable ID, never by a name another actor can reuse.
            status, _ = self.call("DELETE", f"/containers/{identifier}?force=true&v=true")
            if status not in {204, 404}:
                raise ValueError("Guest deletion failed")
            if self.inspect(identifier) is not None or self.inspect(self.name) is not None:
                raise ValueError("Guest remains after deletion")
        elif known is not None:
            identifier = known.get("id")
            if not isinstance(identifier, str) or re.fullmatch(self.id_pattern, identifier) is None:
                raise ValueError("Guest journal has invalid created identity")
            if self.inspect(identifier) is not None:
                raise ValueError("Guest was renamed; refusing unverified cleanup")
        else:
            # A request may still be executing server-side after a client
            # timeout. A momentary 404 is not proof that it created nothing.
            raise ValueError("Uncertain creation has no observed identity; runtime reconciliation required")
        # A create response can be lost before the ID is journalled. The name
        # alone cannot prove absence if that guest was subsequently renamed.
        filters = quote(canonical({"label": [OWNER_LABEL + "=" + self.owner]}).decode(), safe="")
        status, payload = self.call("GET", "/containers/json?all=true&filters=" + filters)
        if status != 200 or json.loads(payload) != []:
            raise ValueError("Owned guest residue remains or cannot be enumerated")
        self.journal.put("container-removed.json", canonical({"name": self.name, "absent": True}))
        return {"status": "passed", "remainingOwnedResources": []}
