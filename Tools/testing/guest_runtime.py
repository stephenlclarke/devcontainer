"""Admit and provision released guest data only inside the selected private runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
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
from exec_probe import exec_streams
from network_volume_probe import NetworkVolumeFixture
from engine_probe import request
from build_fixture import BuildFixture
from build_runtime import ReleasedBuilder, admit_builder


FIXTURES = {"E02-container-lifecycle", "E03-exec-streams", "E04-image-build", "E05-archive-copy", "E06-network-volume"}
PROVISION_STEPS = ("guest-kernel", "guest-initialization", "guest-workload")
GUEST_API_VERSION = "1.53"


def require_guest_api(socket: Path) -> dict:
    """Use the same claimed API contract in every lane, not the oracle maximum."""
    status, payload = request(socket, "GET", "/version")
    value = json.loads(payload)
    if status != 200 or not isinstance(value, dict):
        raise ValueError("Engine API range is unavailable")
    versions = [value.get(key) for key in ("MinAPIVersion", "ApiVersion")]
    if any(not isinstance(item, str) or re.fullmatch(r'1\.[0-9]{2}', item) is None for item in versions):
        raise ValueError("Engine API range is malformed")
    minimum, maximum = [tuple(map(int, item.split('.'))) for item in versions]
    if not minimum <= tuple(map(int, GUEST_API_VERSION.split('.'))) <= maximum:
        raise ValueError("Engine does not support the fixed guest API contract")
    return {"requested": GUEST_API_VERSION, "minimum": versions[0], "maximum": versions[1]}


def diagnostic_snapshot(path: Path) -> tuple[bytes, bytes]:
    """Read a bounded, singly owned regular log; never follow a substituted path."""
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("Provisioning log path must be canonical")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as incoming:
        info = os.fstat(incoming.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_nlink != 1 or info.st_mode & 0o022):
            raise ValueError("Provisioning log must remain a singly owned regular file")
        data = incoming.read(1024**2 + 1)
    payload = data[:1024**2]
    return payload, canonical({"bytes": len(payload), "sha256": digest(payload), "truncated": len(data) > len(payload)})


def admit_guest(kernel_lock: dict, image_lock: dict, lane: str, retained: Path, *, builder_lock=None) -> dict:
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
    result = {"kernel": kernel,
              "initialization": require_image(by_name[names[lane]], retained / "guest-images"),
              "workload": require_image(by_name["alpine-workload"], retained / "guest-images")}
    if builder_lock is not None:
        result["builder"] = admit_builder(builder_lock, lane, retained)
    return result


class ReleasedGuest:
    """Own setup commands and guest cleanup before the Engine/provider is stopped."""

    def __init__(self, inputs: dict, fixture: str, root: Path, owner: dict, runtime, container: str, socket: Path,
                 *, observe=None, image_id: str | None = None):
        if fixture not in FIXTURES:
            raise ValueError("Unsupported released guest fixture")
        self.inputs, self.fixture, self.root = inputs, fixture, root
        self.owner, self.runtime, self.container, self.socket = owner, runtime, container, socket
        self.observe, self.guest, self.commands = observe, None, []
        self.builder = None
        self.pending_logs = {}
        image = inputs["workload"]["image"]
        self.image_id = image["config"] if image_id is None else image_id
        if self.image_id not in {image["config"], image.get("manifest")}:
            raise ValueError("Runtime image identity is outside the admitted OCI closure")

    def retain_logs(self):
        """A bounded private snapshot is required before disposable logs can go."""
        journal = self.runtime.journal
        for name, path in list(self.pending_logs.items()):
            stopped = json.loads(journal.records().get(name + "-stopped.json", b"null"))
            if not isinstance(stopped, dict) or stopped.get("verifiedStopped") is not True:
                raise ValueError("Provisioning log is still live; retain its root for reconciliation")
            payload, metadata = diagnostic_snapshot(path)
            journal.put(name + ".log", payload)
            journal.put(name + "-log.json", metadata)
            del self.pending_logs[name]

    def command(self, name: str, arguments: list[str], *, timeout=60):
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
                    code = child.process.wait(timeout=timeout)
                    if code != 0:
                        raise RuntimeError("Released guest provisioning command failed")
                finally:
                    child.stop()
                    journal.put(name + "-stopped.json", canonical({"verifiedStopped": True,
                                "durationNS": time.monotonic_ns() - started}))
        finally:
            self.retain_logs()
        self.runtime.verify()
        if name.startswith("guest-builder-list-") and json.loads(journal.records()[name + "-log.json"])["truncated"]:
            raise ValueError("Provisioning output exceeds the retained diagnostic bound")
        return journal.records()[name + ".log"]

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
        if self.fixture == "E04-image-build":
            self.builder = ReleasedBuilder(self.inputs["builder"], self.root, self.runtime.journal, self.command)
            with deadline(300):
                self.builder.provision()
        self.runtime.journal.put("guest-provisioned.json", canonical({"inputsSHA256": digest(canonical(self.inputs))}))

    def operation(self):
        self.runtime.verify()
        self.runtime.journal.put("guest-api.json", canonical(require_guest_api(self.socket)))
        if self.fixture == "E04-image-build":
            if self.container and self.builder is None:
                raise ValueError("Apple image builds require an admitted private builder")
            image = self.inputs["workload"]["image"]
            self.guest = BuildFixture(self.socket, digest(canonical(self.owner["identity"])), self.image_id,
                                      GUEST_API_VERSION, self.runtime.journal,
                                      image["repository"] + "@" + image["manifest"], observe=self.observe,
                                      before_submit=self.builder.verify_for_build if self.builder else None,
                                      private_named_cleanup=bool(self.container))
            with deadline(390):
                return self.guest.operation()
        if self.fixture == "E06-network-volume":
            self.guest = NetworkVolumeFixture(self.socket, digest(canonical(self.owner["identity"])),
                                              self.image_id, GUEST_API_VERSION, self.runtime.journal,
                                              self.root, observe=self.observe)
            with deadline(180):
                return self.guest.operation()
        command = COMMAND if self.fixture == "E02-container-lifecycle" else ("sleep", "300")
        if self.fixture == "E03-exec-streams":
            command = ("sleep", "600")
        self.guest = GuestFixture(self.socket, digest(canonical(self.owner["identity"])),
                                  self.image_id, GUEST_API_VERSION, self.runtime.journal,
                                  command=command, observe=self.observe)
        with deadline(360 if self.fixture == "E03-exec-streams" else 90):
            if self.fixture == "E02-container-lifecycle":
                return lifecycle(self.guest)
            self.guest.setup()
            if self.fixture == "E03-exec-streams":
                return exec_streams(self.guest)
            return self.guest.archive(observe=self.observe)

    def cleanup(self):
        # The caller must not tear down the Engine/provider or delete its state
        # if cleanup is uncertain: keep the existing quarantine for recovery.
        for child in self.commands:
            child.stop()
        self.retain_logs()
        result = {"status": "passed", "remainingOwnedResources": []}
        if self.guest is not None:
            self.runtime.verify()
            with deadline(45):
                result = self.guest.cleanup()
        if self.builder is not None:
            self.runtime.verify()
            with deadline(130):
                self.builder.cleanup()
        return result


def require_guest_resources_stopped(records: dict[str, bytes]) -> list[str]:
    """Legacy recovery cannot silently discard a guest it never reconciled."""
    for prefix in ("e04-images", "e04-builder"):
        if prefix + "-intent.json" in records:
            removed = json.loads(records.get(prefix + "-removed.json", b"null"))
            expected = {"absent": True}
            if prefix == "e04-builder":
                expected["intentSHA256"] = digest(records[prefix + "-intent.json"])
            if removed != expected:
                raise ValueError("Image-build resources need explicit reconciliation before service recovery")
    if "network-volume-intent.json" in records:
        removed = json.loads(records.get("network-volume-removed.json", b"null"))
        if removed != {"intentSHA256": digest(records["network-volume-intent.json"]), "absent": True}:
            raise ValueError("Network/volume resources need explicit reconciliation before service recovery")
    if "container-intent.json" in records:
        intent = json.loads(records["container-intent.json"])
        removed = json.loads(records.get("container-removed.json", b"null"))
        if removed != {"name": intent["name"], "absent": True}:
            raise ValueError("Guest resource needs explicit reconciliation before service recovery")
    return require_guest_commands_stopped(records)


def require_guest_commands_stopped(records: dict[str, bytes]) -> list[str]:
    """Check helper closure independently of the workload resource lifecycle."""
    steps = []
    builder_steps = [name.removesuffix("-intent.json") for name in records
                     if name.startswith("guest-builder-") and name.endswith("-intent.json")]
    for name in (*PROVISION_STEPS, *sorted(builder_steps)):
        if name + "-intent.json" in records:
            stopped = json.loads(records.get(name + "-stopped.json", b"null"))
            if not isinstance(stopped, dict) or stopped.get("verifiedStopped") is not True:
                raise ValueError("Guest provisioning process needs explicit reconciliation")
            steps.append(name)
    return steps


def require_diagnostic(records: dict[str, bytes], name: str):
    metadata = json.loads(records.get(name + "-log.json", b"null"))
    payload = records.get(name + ".log")
    if (not isinstance(metadata, dict) or payload is None or len(payload) > 1024**2 or
            metadata != {"bytes": len(payload), "sha256": digest(payload), "truncated": metadata.get("truncated")} or
            type(metadata.get("truncated")) is not bool):
        raise ValueError("Guest provisioning diagnostics must be retained before recovery")


def require_guest_cleanup(records: dict[str, bytes]) -> None:
    for name in require_guest_resources_stopped(records):
        require_diagnostic(records, name)


def guest_diagnostic_plan(root: Path, records: dict[str, bytes]) -> dict[str, bytes]:
    """Plan missing snapshots only after all recorded guest processes are stopped.

    Report mode is read-only. The recovery caller has authenticated the case,
    root, process absence and service registrations before committing this plan.
    """
    plan = {}
    for name in require_guest_resources_stopped(records):
        names = (name + ".log", name + "-log.json")
        if all(key in records for key in names):
            require_diagnostic(records, name)
            continue
        payload, metadata = diagnostic_snapshot(root / names[0])
        for key, data in zip(names, (payload, metadata)):
            if key in records:
                if records[key] != data:
                    raise ValueError("Partial provisioning diagnostic changed; refusing replacement")
            else:
                plan[key] = data
    return plan
