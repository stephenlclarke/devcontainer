"""Journalled launchd replacement primitives; callers own the family runtime lease.

The caller must quiesce cooperating workers and verify an idle host before using
this boundary. No broad `container system stop`, key deletion or state migration
is performed. Every mutation is preceded by a durable caller-owned journal event.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess


API = "com.apple.container.apiserver"
BASE_SERVICES = {API, "com.apple.container.container-core-images", "com.apple.container.machine-apiserver"}


def canonical_file(path: Path) -> bytes:
    """Read a non-aliased, user-owned launch definition without printing it."""
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("Service definition path is not canonical")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_nlink != 1 or info.st_mode & 0o022 or info.st_size > 1024 * 1024):
            raise ValueError("Service definition is not a private owned regular file")
        payload = stream.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            raise ValueError("Service definition grew beyond its size limit")
        return payload


class Launchd:
    """Narrow launchctl interface; failures never include environment or stderr."""

    def __init__(self):
        self.domain = f"gui/{os.getuid()}"

    def command(self, *arguments):
        return subprocess.run(["/bin/launchctl", *arguments], capture_output=True,
                              timeout=10, env={"PATH": "/usr/bin:/bin"}, check=False)

    def labels(self) -> set[str]:
        result = self.command("list")
        if result.returncode != 0:
            raise RuntimeError("Cannot enumerate launchd jobs")
        rows = [line.split() for line in result.stdout.decode().splitlines()[1:]]
        if any(len(row) != 3 for row in rows):
            raise ValueError("Malformed launchd job inventory")
        return {row[2] for row in rows}

    def inspect(self, label: str) -> dict | None:
        if re.fullmatch(r"[A-Za-z0-9._-]+", label) is None:
            raise ValueError("Invalid launchd job label")
        result = self.command("print", f"{self.domain}/{label}")
        if result.returncode == 113:  # launchctl: service not found
            return None
        if result.returncode != 0:
            raise RuntimeError("Cannot inspect launchd job")
        output = result.stdout.decode()
        fields = {}
        for key in ("path", "program"):
            values = re.findall(r"^\t" + key + r" = ([^\n]+)$", output, re.MULTILINE)
            if len(values) != 1:
                raise ValueError("Ambiguous launchd job identity")
            fields[key] = values[0]
        return {"label": label, **fields}

    def bootout(self, label: str):
        if self.command("bootout", f"{self.domain}/{label}").returncode != 0:
            raise RuntimeError("Launchd job removal failed")

    def process_id(self, label: str) -> int | None:
        if re.fullmatch(r"[A-Za-z0-9._-]+", label) is None:
            raise ValueError("Invalid launchd job label")
        result = self.command("print", f"{self.domain}/{label}")
        if result.returncode != 0:
            raise RuntimeError("Cannot capture launchd process ownership")
        values = re.findall(r"^\tpid = ([0-9]+)$", result.stdout.decode(), re.MULTILINE)
        if not values:
            return None
        if len(values) != 1 or int(values[0]) <= 0:
            raise ValueError("Ambiguous launchd process ownership")
        return int(values[0])

    def bootstrap(self, path: Path):
        if self.command("bootstrap", self.domain, str(path)).returncode != 0:
            raise RuntimeError("Launchd job registration failed")


def snapshot(launchd: Launchd, allowed_roots: dict[str, Path]) -> list[dict]:
    """Reject unknown runtime jobs before capturing every explicitly scoped job.

    `allowed_roots` must be selected from the operator's known service roots,
    not inferred from an untrusted job's own path. Snapshot bytes can contain
    secrets and belong only in private retained storage, never public evidence.
    """
    labels = launchd.labels()
    runtime = {label for label in labels if label.startswith("com.apple.container.")}
    if not runtime <= BASE_SERVICES or not runtime <= allowed_roots.keys():
        raise ValueError("Unaccounted runtime jobs or workloads prevent service switching")
    snapshots = []
    # Stop consumers/workers before the runtime that they could reconnect to.
    for label in sorted(labels & allowed_roots.keys(), key=lambda value: (value in BASE_SERVICES, value)):
        root = allowed_roots[label]
        if not root.is_absolute() or root.resolve() != root or root == Path("/"):
            raise ValueError("Invalid authorised service root")
        current = launchd.inspect(label)
        if current is None:
            raise ValueError("Service inventory changed during snapshot")
        path = Path(current["path"])
        if not path.is_relative_to(root):
            raise ValueError("Service definition is outside its authorised root")
        payload = canonical_file(path)
        definition = plistlib.loads(payload)
        arguments = definition.get("ProgramArguments", [])
        program = definition.get("Program") or (arguments[0] if arguments else None)
        if definition.get("Label") != label or program != current["program"]:
            raise ValueError("Loaded job and service definition disagree")
        snapshots.append({**current, "sha256": hashlib.sha256(payload).hexdigest(), "payload": payload})
    return snapshots


class ServiceSwitch:
    """Replace scoped jobs and restore their original definitions without edits.

    `journal(name, bytes)` must durably and immutably retain each event before
    returning. Constructing the switch has no effects. After any interrupted or
    uncertain mutation, restore can be retried against these exact snapshots;
    a foreign job or changed original plist remains a hard failure.
    """

    def __init__(self, launchd: Launchd, prior: list[dict], owned_root: Path, journal):
        if not owned_root.is_absolute() or owned_root.resolve() != owned_root or owned_root == Path("/"):
            raise ValueError("Service switch requires a canonical owned root")
        self.launchd, self.prior, self.root, self.journal = launchd, prior, owned_root, journal
        self.sequence = 0
        self.recorded = False

    @classmethod
    def recover(cls, launchd, prior, owned_root, journal, last_sequence):
        """Resume from authenticated private journal inputs, never guessed state."""
        if type(last_sequence) is not int or last_sequence < 0:
            raise ValueError("Invalid service journal sequence")
        recovered = cls(launchd, prior, owned_root, journal)
        recovered.sequence = last_sequence
        recovered.recorded = True
        return recovered

    def record(self, operation: str, label: str):
        self.sequence += 1
        self.journal(f"service-event-{self.sequence:04d}.txt", f"{operation}\n{label}\n".encode())

    def check_original(self, original: dict):
        payload = canonical_file(Path(original["path"]))
        if hashlib.sha256(payload).hexdigest() != original["sha256"] or payload != original["payload"]:
            raise ValueError("Original service definition changed; refusing automatic restore")

    def prepare(self):
        # Journal all originals before the first removal, including unchanged
        # definitions needed to reconcile an interrupted bootstrap later.
        if self.recorded:
            raise ValueError("Service switch was already prepared")
        runtime = {label for label in self.launchd.labels() if label.startswith("com.apple.container.")}
        if runtime != {item["label"] for item in self.prior if item["label"].startswith("com.apple.container.")}:
            raise ValueError("Runtime inventory changed before preparation")
        for index, original in enumerate(self.prior):
            self.check_original(original)
            self.journal(f"service-original-{index:04d}.plist", original["payload"])
        manifest = [{key: value for key, value in item.items() if key != "payload"} for item in self.prior]
        self.journal("service-originals.plist", plistlib.dumps(manifest))
        self.recorded = True
        parent_removed = False
        for original in self.prior:
            current = self.launchd.inspect(original["label"])
            if current is None and parent_removed and original["label"] in BASE_SERVICES - {API}:
                self.record("absent-after-parent-removal", original["label"])
                continue
            expected = {key: original[key] for key in ("label", "path", "program")}
            if current != expected:
                raise ValueError("Service changed before removal")
            self.check_original(original)
            self.record("bootout-original", original["label"])
            self.launchd.bootout(original["label"])
            parent_removed = parent_removed or original["label"] == API

    def install(self, definition: Path):
        if not self.recorded or not definition.is_relative_to(self.root):
            raise ValueError("Service installation requires a prepared owned definition")
        payload = canonical_file(definition)
        job = plistlib.loads(payload)
        label = job.get("Label")
        if label != API or any(value.startswith("com.apple.container.") for value in self.launchd.labels()):
            raise ValueError("Selected API service name is not available")
        self.journal("service-selected.plist", payload)
        self.record("bootstrap-selected", label)
        self.launchd.bootstrap(definition)
        arguments = job.get("ProgramArguments", [])
        expected = {"label": label, "path": str(definition),
                    "program": job.get("Program") or (arguments[0] if arguments else None)}
        if canonical_file(definition) != payload or self.launchd.inspect(label) != expected:
            raise ValueError("Selected service registration differs from its journalled definition")

    def restore(self, *, before_originals=None):
        if not self.recorded:
            return
        # Validate *all* originals and survivors before any restoration mutation.
        for original in self.prior:
            self.check_original(original)
        prior = {item["label"]: item for item in self.prior}
        self.remove_owned(self.owned_survivors(prior))
        # Do not register originals until all selected runtime jobs are gone.
        remaining = self.launchd.labels()
        if any(label.startswith("com.apple.container.") and label not in prior for label in remaining):
            raise ValueError("Selected runtime still has unreconciled jobs")
        if before_originals is not None:
            before_originals()
        self.restore_originals()
        self.record("restored", "all")

    def owned_survivors(self, prior: dict) -> list[dict]:
        labels = self.launchd.labels()
        selected = {label for label in labels if label.startswith("com.apple.container.")} | (labels & prior.keys())
        owned = []
        for label in sorted(selected):
            current = self.launchd.inspect(label)
            if current is None:
                raise ValueError("Service inventory changed during restoration")
            original = prior.get(label)
            if original is not None and current == {key: original[key] for key in ("label", "path", "program")}:
                continue
            path = Path(current["path"])
            if path.resolve() != path or not path.is_relative_to(self.root):
                raise ValueError("Foreign service prevents restoration")
            owned.append(current)
        return owned

    def remove_owned(self, owned: list[dict]):
        # Stop the parent before its plugin jobs so it cannot register more.
        owned.sort(key=lambda item: (item["label"] != API, item["label"]))
        for current in owned:
            actual = self.launchd.inspect(current["label"])
            if actual is None:
                continue  # The owned parent may have removed its own plugin.
            if actual != current:
                raise ValueError("Owned service changed before removal")
            self.record("bootout-selected", current["label"])
            self.launchd.bootout(current["label"])

    def restore_originals(self):
        # Restore plugin registrations before the parent, and consumers last.
        priorities = {label: 0 for label in BASE_SERVICES}
        priorities[API] = 1
        ordered = sorted(self.prior, key=lambda item: (priorities.get(item["label"], 2), item["label"]))
        for original in ordered:
            current = self.launchd.inspect(original["label"])
            expected = {key: original[key] for key in ("label", "path", "program")}
            if current is None:
                self.check_original(original)
                self.record("restore-original", original["label"])
                self.launchd.bootstrap(Path(original["path"]))
                current = self.launchd.inspect(original["label"])
            if current != expected:
                raise ValueError("Original service restoration is not verified")
