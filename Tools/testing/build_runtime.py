"""Own a released Apple builder in the already leased, isolated provider root.

The stock builder ID is fixed to buildkit. Never use this adapter against a
user store: its caller verifies the exclusive private runtime before every
command. Build outputs must be reconciled before stopping this worker. Cache
data is removed only with the whole private root after provider shutdown.
"""

import json
import os
from pathlib import Path
import sys
import socket
import re
import stat

sys.path.insert(0, str(Path(__file__).parents[1] / "bazel"))

from build_probe import unique_object
from case_evidence import canonical, digest
from prepare_guest_images import require_image, validate_image


def native_blob(root, descriptor):
    """Authenticate bounded metadata from the pinned provider's private store."""
    identifier = descriptor.get("digest")
    size = descriptor.get("size")
    if (not isinstance(identifier, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", identifier) is None or
            type(size) is not int or not 0 < size <= 1024**2):
        raise ValueError("Invalid native builder metadata descriptor")
    path = root / "container/content/blobs/sha256" / identifier.split(":")[1]
    if path.resolve() != path:
        raise ValueError("Native builder metadata path is not canonical")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as incoming:
        info = os.fstat(incoming.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size != size:
            raise ValueError("Native builder metadata is not a bounded owned file")
        payload = incoming.read(size + 1)
    if len(payload) != size or "sha256:" + digest(payload) != identifier:
        raise ValueError("Native builder metadata content differs from its digest")
    value = json.loads(payload, object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError("Native builder metadata is not an object")
    return value


def require_native_image(root, descriptor, image):
    """Stock wraps a leaf in an index; verify the graph, never equate its digests."""
    index = native_blob(root, descriptor)
    leaves = index.get("manifests")
    if (descriptor.get("mediaType") != "application/vnd.oci.image.index.v1+json" or
            index.get("schemaVersion") != 2 or not isinstance(leaves, list) or len(leaves) != 1 or
            not isinstance(leaves[0], dict) or leaves[0].get("digest") != image["manifest"]):
        raise ValueError("Native builder index does not resolve to the admitted leaf")
    manifest = native_blob(root, leaves[0])
    config_descriptor = manifest.get("config", {})
    if config_descriptor.get("digest") != image["config"]:
        raise ValueError("Native builder manifest does not resolve to the admitted config")
    config = native_blob(root, config_descriptor)
    if (config.get("os"), config.get("architecture")) != ("linux", "arm64"):
        raise ValueError("Native builder config is not linux-arm64")


def host_build_dns(path=Path("/etc/resolv.conf")):
    """Match the pinned product's hostBuildDNSArguments, retaining first order."""
    try:
        contents = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    seen, arguments = set(), []
    for line in contents.splitlines():
        fields = line.split()
        if len(fields) < 2 or fields[0] != "nameserver" or fields[1] in seen:
            continue
        address = fields[1]
        valid = False
        for family, value in ((socket.AF_INET, address), (socket.AF_INET6, address.split('%', 1)[0])):
            try:
                socket.inet_pton(family, value)
                valid = True
            except OSError:
                continue
        if valid:
            seen.add(address)
            arguments.extend(["--dns", address])
    return arguments


def admit_builder(lock, lane, retained):
    expected = {
        "apple-stock": ("stock-builder", "ghcr.io/apple/container-builder-shim/builder:0.13.1"),
        "container-compose": ("enhanced-builder", "ghcr.io/stephenlclarke/container-builder-shim/builder:current-34334330512-5373d9b4363c"),
    }
    if lane not in expected or lock.get("schemaVersion") != 1 or not isinstance(lock.get("images"), list):
        raise ValueError("Invalid released builder selection")
    for image in lock["images"]:
        validate_image(image)
    images = {image["name"]: image for image in lock["images"]}
    name, reference = expected[lane]
    if len(images) != len(lock["images"]) or name not in images or images[name]["reference"] != reference:
        raise ValueError("Builder differs from the selected published provider")
    return dict(require_image(images[name], retained / "guest-images"), dnsArguments=host_build_dns())


class ReleasedBuilder:
    def __init__(self, inputs, root, journal, command):
        self.inputs, self.root, self.journal, self.command = inputs, root, journal, command
        image = inputs["image"]
        validate_image(image)
        self.reference = image["reference"]
        self.dns = inputs["dnsArguments"]
        self.config = root / "container/config/config.toml"
        # Stock resolves local images by stored reference, not descriptor
        # digest. This private tag was loaded from the admitted leaf archive;
        # its native index must authenticate that immutable leaf and config.
        self.payload = ('[build]\nimage = "' + self.reference + '"\nrosetta = false\n').encode()
        self.intent = {"inputs": inputs, "root": str(root), "configSHA256": digest(self.payload)}

    def inventory(self):
        records = self.journal.records()
        index = 0
        while f"guest-builder-list-{index}-intent.json" in records:
            index += 1
        payload = self.command(f"guest-builder-list-{index}", ["list", "--all", "--format", "json"])
        value = json.loads(payload, object_pairs_hook=unique_object)
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ValueError("Builder inventory is malformed")
        return value

    def owned(self, value):
        config = value.get("configuration", {})
        image = config.get("image", {})
        labels = config.get("labels", {})
        mounts = config.get("mounts", [])
        exports = str(self.root / "container/builder")
        if (config.get("id") != "buildkit" or image.get("reference") != self.reference or
                labels.get("com.apple.container.plugin") != "builder" or
                labels.get("com.apple.container.resource.role") != "builder" or
                not any(m.get("source") == exports and m.get("destination") == "/var/lib/container-builder-shim/exports"
                        for m in mounts if isinstance(m, dict))):
            raise ValueError("Builder identity or private exports mount differs; refusing mutation")
        require_native_image(self.root, image.get("descriptor", {}), self.inputs["image"])
        return {"configurationSHA256": digest(canonical(config)), "id": "buildkit"}

    def verify_configuration(self):
        if self.config.resolve() != self.config or self.config.read_bytes() != self.payload:
            raise ValueError("Private builder configuration changed")

    def verify(self):
        self.verify_configuration()
        values = self.inventory()
        if len(values) != 1:
            raise ValueError("Private runtime must contain exactly its owned builder")
        identity = self.owned(values[0])
        expected = self.journal.records().get("e04-builder-created.json")
        if expected is not None and expected != canonical(identity):
            raise ValueError("Builder configuration changed after creation")
        return values[0], identity

    def verify_for_build(self):
        if host_build_dns() != self.dns:
            raise ValueError("Host build DNS changed since admission; refusing worker recreation")
        return self.verify()

    def provision(self):
        if "e04-builder-intent.json" in self.journal.records():
            raise ValueError("Builder provisioning already attempted; reconcile instead")
        if self.inventory():
            raise ValueError("Private runtime already contains containers; refusing builder adoption")
        self.journal.put("e04-builder-intent.json", canonical(self.intent))
        self.config.parent.mkdir(mode=0o700)
        with self.config.open("xb") as output:
            output.write(self.payload)
            output.flush()
            os.fsync(output.fileno())
        self.command("guest-builder-image", ["image", "load", "--input", self.inputs["path"]])
        if host_build_dns() != self.dns:
            raise ValueError("Host build DNS changed before builder start")
        self.command("guest-builder-start", ["builder", "start", *self.dns], timeout=180)
        # A successful CLI exit is required. Killing a timed-out start process
        # does not prove the asynchronous runtime finished creating its worker.
        self.journal.put("e04-builder-start-completed.json", canonical({"completed": True}))
        value, identity = self.verify()
        if value.get("status", {}).get("state") != "running":
            raise ValueError("Builder did not reach running state")
        self.journal.put("e04-builder-created.json", canonical(identity))

    def cleanup(self):
        records = self.journal.records()
        if "e04-builder-intent.json" not in records:
            return
        if records["e04-builder-intent.json"] != canonical(self.intent):
            raise ValueError("Builder cleanup belongs to another transaction")
        if "guest-builder-start-intent.json" not in records:
            if self.inventory():
                raise ValueError("Unstarted builder appeared; preserve runtime")
        else:
            if "e04-builder-start-completed.json" not in records:
                raise ValueError("Builder start completion is uncertain; preserve runtime")
            if "e04-builder-removed.json" in records:
                if self.inventory():
                    raise ValueError("Removed builder reappeared; preserve runtime")
                return
            # A lost deletion response is reconciled by absence, never by
            # repeating a potentially completed delete or adopting a new ID.
            if "guest-builder-delete-intent.json" in records:
                self.verify_configuration()
                if self.inventory():
                    raise ValueError("Builder deletion is unresolved; preserve runtime")
                self.journal.put("e04-builder-removed.json", canonical({"intentSHA256": digest(canonical(self.intent)), "absent": True}))
                return
            value, _ = self.verify()
            if value.get("status", {}).get("state") == "running":
                self.command("guest-builder-stop", ["builder", "stop"])
            value, _ = self.verify()
            if value.get("status", {}).get("state") != "stopped":
                raise ValueError("Builder has not stopped; preserve runtime")
            self.command("guest-builder-delete", ["builder", "delete"])
            if self.inventory():
                raise ValueError("Builder container residue remains; preserve runtime")
        self.journal.put("e04-builder-removed.json", canonical({"intentSHA256": digest(canonical(self.intent)), "absent": True}))
