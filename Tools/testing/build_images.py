"""E04 image ownership transaction, independent of builder execution.

The caller must admit the released runtime/builder, hold its exclusive lease,
and enforce whole-phase deadlines. This component never submits a build or
prunes caches. Uncertain build completion preserves the runtime for recovery.
"""

import json
from urllib.parse import quote

from build_probe import build_context, build_output, owned_image, tag_for, unique_object
from case_evidence import canonical, digest
from guest_fixture import OWNER_LABEL


class BuildImages:
    """Journal both output names before mutation; remove only verified identities."""

    def __init__(self, client, journal, owner: str, base: str):
        self.client, self.journal, self.owner = client, journal, owner
        self.intent = {"owner": owner, "base": base, "image": client.image,
                       "socket": str(client.socket), "apiVersion": client.version,
                       "contexts": {self.role(failing): digest(build_context(base, owner, failing=failing))
                                    for failing in (False, True)}}

    @staticmethod
    def role(failing: bool) -> str:
        return "failed" if failing else "built"

    def key(self, failing: bool, event: str) -> str:
        return f"e04-{self.role(failing)}-{event}.json"

    def records(self):
        records = self.journal.records()
        if records.get("e04-images-intent.json") != canonical(self.intent):
            raise ValueError("Build image journal belongs to another transaction")
        return records

    def inspect(self, reference: str):
        route = "/images/" + quote(reference, safe="") + "/json"
        status, payload = self.client.call("GET", route)
        value = json.loads(payload, object_pairs_hook=unique_object)
        if status == 404 and isinstance(value, dict) and isinstance(value.get("message"), str):
            return None
        if status != 200 or not isinstance(value, dict):
            raise ValueError("Cannot inspect build image")
        return value

    def prepare(self):
        if "e04-images-intent.json" in self.journal.records():
            raise ValueError("Build preparation already attempted; reconcile instead")
        base = self.inspect(self.intent["base"])
        references = {self.intent["base"]}
        # Docker displays official Hub images without docker.io/library.
        # This is the same repository and exact digest, not a foreign alias,
        # digest-only match or normalization of a parity observation.
        if self.intent["base"].startswith("docker.io/library/"):
            references.add(self.intent["base"].removeprefix("docker.io/library/"))
        if (base is None or base.get("Id") != self.intent["image"] or
                not isinstance(base.get("RepoDigests"), list) or
                not any(reference in base["RepoDigests"] for reference in references)):
            raise ValueError("Digest-pinned build base is not prepared")
        for failing in (False, True):
            if self.inspect(tag_for(self.owner, failing=failing)) is not None:
                raise ValueError("Build output tag already exists; refusing adoption")
        self.journal.put("e04-images-intent.json", canonical(self.intent))

    def start(self, *, failing: bool = False):
        records = self.records()
        key = self.key(failing, "started")
        if key in records or self.key(failing, "removed") in records or "e04-images-removed.json" in records:
            raise ValueError("Build already attempted or transaction closed")
        if self.inspect(tag_for(self.owner, failing=failing)) is not None:
            raise ValueError("Build output appeared before submission")
        # The caller must commit this record BEFORE sending the build request.
        self.journal.put(key, canonical({"contextSHA256": self.intent["contexts"][self.role(failing)]}))

    def response(self, status: int, payload: bytes, *, failing: bool = False):
        records = self.records()
        if self.key(failing, "started") not in records or "e04-images-removed.json" in records:
            raise ValueError("Build response requires an open submitted transaction")
        if self.key(failing, "completed") in records:
            raise ValueError("Build response already recorded")
        result = build_output(status, payload)
        # Only a complete, bounded, valid HTTP response establishes completion.
        # It does NOT establish the semantic success/failure phase of E04.
        self.journal.put(f"e04-{self.role(failing)}-response.jsonl", payload)
        self.journal.put(self.key(failing, "completed"), canonical({
            "status": status, "responseSHA256": digest(payload),
            "progress": result.progress, "failed": result.failed, "records": result.records}))
        actual = self.inspect(tag_for(self.owner, failing=failing))
        if actual is not None:
            self.journal.put(self.key(failing, "created"), canonical(self.identity(actual, failing)))
        return result

    def identity(self, value, failing):
        observed = owned_image(value, self.owner, failing=failing)
        if observed["id"] == self.intent["image"]:
            raise ValueError("Build output aliases the admitted base image")
        if value.get("RepoDigests") not in (None, []):
            raise ValueError("Build output acquired a repository digest; refusing deletion")
        return observed

    def known_identity(self, failing: bool, records):
        created = json.loads(records.get(self.key(failing, "created"), b"null"))
        deleting = json.loads(records.get(self.key(failing, "delete"), b"null"))
        if created is not None and deleting is not None and created != deleting:
            raise ValueError("Build creation and deletion identities disagree")
        return created or deleting

    def remove_one(self, failing: bool, records):
        tag = tag_for(self.owner, failing=failing)
        known = self.known_identity(failing, records)
        actual = self.inspect(tag)
        if actual is None:
            if known is not None and self.inspect(known["id"]) is not None:
                raise ValueError("Build output lost its tag but still exists")
            if self.key(failing, "removed") not in records:
                self.journal.put(self.key(failing, "removed"), canonical({"absent": True}))
            return
        if (self.key(failing, "completed") not in records or
                self.key(failing, "removed") in records or "e04-images-removed.json" in records):
            raise ValueError("Unsubmitted or removed build output appeared")
        observed = self.identity(actual, failing)
        if known is not None and known != observed:
            raise ValueError("Build output identity changed during cleanup")
        by_id = self.inspect(observed["id"])
        if by_id is None or self.identity(by_id, failing) != observed:
            raise ValueError("Build tag and ID disagree")
        self.journal.put(self.key(failing, "delete"), canonical(observed))
        # No force deletion, parent pruning or daemon-wide image/cache cleanup.
        status, _ = self.client.call("DELETE", "/images/" + quote(observed["id"], safe="") +
                                     "?force=false&noprune=true")
        if status not in {200, 404} or self.inspect(observed["id"]) is not None or self.inspect(tag) is not None:
            raise ValueError("Build output deletion is unverified")
        self.journal.put(self.key(failing, "removed"), canonical(observed))

    def cleanup(self):
        records = self.records()
        for failing in (False, True):
            if self.key(failing, "started") in records and self.key(failing, "completed") not in records:
                raise ValueError("Build completion is uncertain; preserve runtime")
        for failing in (False, True):
            self.remove_one(failing, records)
        filters = quote(canonical({"label": [OWNER_LABEL + "=" + self.owner]}).decode(), safe="")
        status, payload = self.client.call("GET", "/images/json?all=true&filters=" + filters)
        if status != 200 or json.loads(payload, object_pairs_hook=unique_object) != []:
            raise ValueError("Owned build image residue remains; preserve runtime")
        # This closes only image outputs, never builder caches or containers.
        self.journal.put("e04-images-removed.json", canonical({"absent": True}))
