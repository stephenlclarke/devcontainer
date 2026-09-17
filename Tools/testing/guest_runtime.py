"""Admit and provision released guest data only inside the selected private runtime."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).parents[1] / "bazel"))
from case_evidence import canonical, digest
from guest_fixture import GuestFixture
from host_runtime import OwnedProcess, deadline
from lifecycle_probe import COMMAND, lifecycle
from prepare_guest_images import require_image, validate_image
from prepare_releases import require_retained
from release_inputs import sha256, validate_lock


FIXTURES = {"E02-container-lifecycle", "E05-archive-copy"}


def admit_guest(kernel_lock: dict, image_lock: dict, lane: str, retained: Path) -> dict:
    """Missing enhanced inputs fail before any host-service or store mutation."""
    names = {"apple-stock": "stock-vminit", "container-compose": "enhanced-vminit"}
    if lane not in names:
        raise ValueError("Unsupported guest provider lane")
    images = image_lock.get("images")
    if image_lock.get("schemaVersion") != 1 or not isinstance(images, list):
        raise ValueError("Invalid guest image lock")
    for image in images:
        validate_image(image)
    by_name = {image["name"]: image for image in images}
    if len(by_name) != len(images) or any(name not in by_name for name in (names[lane], "alpine-workload")):
        raise ValueError("Exact provider initialization/workload image is missing or ambiguous")
    init_reference = ("ghcr.io/apple/containerization/vminit:0.45.0" if lane == "apple-stock" else
                      "ghcr.io/stephenlclarke/containerization/vminit:7e066a3101bc84fa0f7231daf6a03aa9ef62a567")
    if by_name[names[lane]]["reference"] != init_reference:
        raise ValueError("Initialization reference differs from the selected released provider")
    assets = validate_lock(kernel_lock)
    if len(assets) != 1 or (assets[0]["repository"], assets[0]["tag"], assets[0]["name"]) != (
            "kata-containers/kata-containers", "3.32.0", "kata-static-3.32.0-arm64.tar.zst"):
        raise ValueError("Guest runtime requires the reviewed recommended kernel")
    asset = assets[0]
    kernel = require_retained(asset, retained / "release-objects" / asset["sha256"],
                              retained / "prepared-releases", retained / "prepared-receipts")
    return {"kernel": kernel,
            "initialization": require_image(by_name[names[lane]], retained / "guest-images"),
            "workload": require_image(by_name["alpine-workload"], retained / "guest-images")}


class ReleasedGuest:
    """Own setup commands and guest cleanup before the Engine/provider is stopped."""

    def __init__(self, inputs: dict, fixture: str, root: Path, owner: dict, runtime, container: str, socket: Path,
                 *, observe=None):
        if fixture not in FIXTURES:
            raise ValueError("Unsupported released guest fixture")
        self.inputs, self.fixture, self.root = inputs, fixture, root
        self.owner, self.runtime, self.container, self.socket = owner, runtime, container, socket
        self.observe, self.guest, self.commands = observe, None, []
        self.pending_logs = {}

    def retain_logs(self):
        """A bounded private snapshot is required before disposable logs can go."""
        journal = self.runtime.journal
        for name, path in list(self.pending_logs.items()):
            stopped = json.loads(journal.records().get(name + "-stopped.json", b"null"))
            if not isinstance(stopped, dict) or stopped.get("verifiedStopped") is not True:
                raise ValueError("Provisioning log is still live; retain its root for reconciliation")
            with path.open("rb") as incoming:
                data = incoming.read(1024**2 + 1)
            snapshot = data[:1024**2]
            journal.put(name + ".log", snapshot)
            journal.put(name + "-log.json", canonical({"bytes": len(snapshot), "sha256": digest(snapshot),
                        "truncated": len(data) > len(snapshot)}))
            del self.pending_logs[name]

    def command(self, name: str, arguments: list[str]):
        """Journal before spawning; do not retry uncertain commands or expose their logs."""
        journal = self.runtime.journal
        journal.put(name + "-intent.json", canonical({"arguments": arguments}))
        child = OwnedProcess()
        self.commands.append(child)
        started = time.monotonic_ns()
        log = self.root / (name + ".log")
        try:
            with log.open("xb") as output:
                self.pending_logs[name] = log
                try:
                    self.runtime.verify()
                    child.start([self.container, *arguments], self.root, output,
                                provider_install=Path(self.container).parent.parent)
                    journal.put(name + "-process.json", canonical({"pid": child.process.pid}))
                    code = child.process.wait(timeout=60)
                    if code != 0:
                        raise RuntimeError("Released guest provisioning command failed")
                finally:
                    child.stop()
                    journal.put(name + "-stopped.json", canonical({"verifiedStopped": True,
                                "durationNS": time.monotonic_ns() - started}))
        finally:
            self.retain_logs()
        self.runtime.verify()

    def provision(self):
        self.runtime.journal.put("guest-inputs.json", canonical(self.inputs))
        with deadline(190):
            kernel = Path(self.inputs["kernel"]["files"]["kernel"])
            self.command("guest-kernel", ["system", "kernel", "set", "--arch", "arm64", "--binary", str(kernel)])
            # KernelSet copies into the private provider store and selects this
            # default symlink. Check the installed bytes before any guest boots.
            installed = self.root / "container/kernels/vmlinux"
            selected = self.root / "container/kernels/default.kernel-arm64"
            if (installed.resolve() != installed or selected.resolve() != installed or
                    sha256(installed) != sha256(kernel)):
                raise ValueError("Private runtime kernel does not match its admitted release")
            for name in ("initialization", "workload"):
                self.command("guest-" + name, ["image", "load", "--input", self.inputs[name]["path"]])
        self.runtime.journal.put("guest-provisioned.json", canonical({"inputsSHA256": digest(canonical(self.inputs))}))

    def operation(self):
        self.runtime.verify()
        command = COMMAND if self.fixture == "E02-container-lifecycle" else ("sleep", "300")
        self.guest = GuestFixture(self.socket, digest(canonical(self.owner["identity"])),
                                  self.inputs["workload"]["image"]["config"], "1.54", self.runtime.journal,
                                  command=command, observe=self.observe)
        with deadline(90):
            if self.fixture == "E02-container-lifecycle":
                return lifecycle(self.guest)
            self.guest.setup()
            return self.guest.archive(observe=self.observe)

    def cleanup(self):
        # The caller must not tear down the Engine/provider or delete its state
        # if cleanup is uncertain: keep the existing quarantine for recovery.
        for child in self.commands:
            child.stop()
        self.retain_logs()
        if self.guest is not None:
            self.runtime.verify()
            with deadline(45):
                return self.guest.cleanup()
        return {"status": "passed", "remainingOwnedResources": []}


def require_guest_cleanup(records: dict[str, bytes]) -> None:
    """Legacy recovery cannot silently discard a guest it never reconciled."""
    if "container-intent.json" in records:
        intent = json.loads(records["container-intent.json"])
        removed = json.loads(records.get("container-removed.json", b"null"))
        if removed != {"name": intent["name"], "absent": True}:
            raise ValueError("Guest resource needs explicit reconciliation before service recovery")
    for name in ("guest-kernel", "guest-initialization", "guest-workload"):
        if name + "-intent.json" in records:
            stopped = json.loads(records.get(name + "-stopped.json", b"null"))
            if not isinstance(stopped, dict) or stopped.get("verifiedStopped") is not True:
                raise ValueError("Guest provisioning process needs explicit reconciliation")
            metadata = json.loads(records.get(name + "-log.json", b"null"))
            payload = records.get(name + ".log")
            if (not isinstance(metadata, dict) or payload is None or
                    metadata != {"bytes": len(payload), "sha256": digest(payload),
                                 "truncated": metadata.get("truncated")} or type(metadata.get("truncated")) is not bool):
                raise ValueError("Guest provisioning diagnostics must be retained before recovery")
