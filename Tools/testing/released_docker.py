"""The downloaded Docker lane for the same direct Engine fixture contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import shutil
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "bazel"))

from case_evidence import CaseStore, canonical, digest, run_case, validate_identity
from docker_vm import DockerVM, private_root
from engine_probe import engine_negotiation, request
from guest_runtime import FIXTURES, ReleasedGuest
from host_runtime import HostGuard, cancellation, deadline, runtime_lease
from prepare_docker_cli import prepare_cli
from prepare_guest_images import require_image
from prepare_releases import require_retained
from release_inputs import validate_lock
from runtime_services import require_owned_volume
from service_journal import ServiceJournal


def admit_docker(oracle_lock: dict, cli_lock: dict, pins: dict, images: dict, scratch: Path, retained: Path) -> dict:
    expected = {"abiosoft/colima": "v0.10.3", "lima-vm/lima": "v2.2.0", "abiosoft/colima-core": "v0.10.4"}
    assets = validate_lock(oracle_lock)
    if len(assets) != 3 or {asset["repository"]: asset["tag"] for asset in assets} != expected:
        raise ValueError("Docker VM requires the reviewed released tool/image closure")
    prepared = [require_retained(asset, retained / "release-objects" / asset["sha256"],
                                retained / "prepared-releases", retained / "prepared-receipts") for asset in assets]
    client = prepare_cli(cli_lock, pins, scratch, retained, offline=True)
    tools = dict(client["executables"])
    for item in prepared:
        tools.update(item["executables"])
        tools.update(item.get("files", {}))
    matches = [image for image in images.get("images", []) if image.get("name") == "alpine-workload"]
    if len(matches) != 1:
        raise ValueError("Docker workload image is missing or ambiguous")
    return {"assets": prepared, "client": client, "tools": tools, "pins": pins,
            "workload": require_image(matches[0], retained / "guest-images")}


class DockerCase:
    def __init__(self, store, identity, inputs, parent, revalidate, guard, journal_parent):
        self.store, self.identity, self.inputs, self.parent = store, identity, inputs, parent
        self.revalidate, self.guard, self.journal_parent = revalidate, guard, journal_parent
        self.root, self.owner, self.vm, self.guest = None, None, None, None
        self.requests = []

    def setup(self):
        self.root = Path(tempfile.mkdtemp(dir=self.parent, prefix="docker-"))
        self.owner = {"identity": self.identity, "root": str(self.root)}
        (self.root / "owner.json").write_bytes(canonical(self.owner))
        self.store.attach(self.identity, "owner.json", canonical(self.owner))
        self.store.attach(self.identity, "admission.json", canonical(self.inputs))
        self.guard.begin(self.owner)
        journal = ServiceJournal(self.journal_parent / (digest(canonical(self.owner)) + ".sqlite"), self.owner, create=True)
        self.vm = DockerVM(self.root, self.owner, self.inputs["tools"], self.inputs["pins"], journal)
        self.vm.start()
        if self.identity["fixture"] in FIXTURES:
            self.vm.command("docker-workload-load", [self.inputs["tools"]["docker"], "--host", "unix://" + str(self.vm.socket),
                            "image", "load", "--input", self.inputs["workload"]["path"]], timeout=60)
            identifier = self.inputs["workload"]["image"]["config"]
            status, data = request(self.vm.socket, "GET", "/images/" + identifier + "/json")
            if status != 200 or json.loads(data).get("Id") != identifier:
                raise ValueError("Docker did not load the exact admitted workload image")
            self.guest = ReleasedGuest(self.inputs, self.identity["fixture"], self.root / "workspace", self.owner,
                                       self.vm, "", self.vm.socket, observe=self.requests.append)
        if self.revalidate() != self.inputs:
            raise ValueError("Docker release inputs changed during setup")

    def operation(self):
        self.vm.verify()
        if self.guest is not None:
            return self.guest.operation()
        with deadline(45):
            return engine_negotiation(self.vm.socket, observe=self.requests.append)

    def cleanup(self):
        if self.guest is not None:
            try:
                self.guest.cleanup()
            finally:
                self.store.attach(self.identity, "requests.json", canonical(self.requests))
        if self.vm is not None:
            try:
                self.vm.stop()
            finally:
                self.store.attach(self.identity, "docker-journal.json", canonical(self.vm.journal.receipt()))
                self.store.attach(self.identity, "requests.json", canonical(self.requests))
        if self.revalidate() != self.inputs:
            raise ValueError("Docker release inputs changed during execution")
        if self.root is not None:
            private_root(self.root, self.owner)
            shutil.rmtree(self.root)
        if self.owner is not None:
            self.guard.clear(self.owner)
        return {"status": "passed", "remainingOwnedResources": []}


def run_docker(args):
    # Import shared evidence/runtime constants lazily to avoid a module cycle.
    from released_engine import SSD, SSD_VOLUME, RETAINED, write_junit
    if args.candidate_invocation:
        raise ValueError("Docker reference uses released binaries, not a product candidate invocation")
    repository = Path(__file__).parents[2]
    names = ("docker-oracle.lock.json", "docker-cli.lock.json", "guest-images.lock.json")
    locks = [json.loads((repository / "Tools/bazel" / name).read_text()) for name in names]
    pins = json.loads((repository / "Tests/Parity/manifest.json").read_text())["referencePins"]["docker"]
    expected = {key: str(value).lower() for key, value in json.loads(
        (repository / f"Tests/Parity/fixtures/{args.fixture}/contract.json").read_text())["expected"].items()}
    volume = require_owned_volume(SSD_VOLUME)
    if SSD.resolve() != SSD or RETAINED.resolve() != RETAINED or SSD.stat().st_dev == RETAINED.stat().st_dev:
        raise ValueError("Docker oracle requires separate canonical SSD and retained storage")
    parent, journal_parent = SSD / "live", RETAINED / "private-runtime"
    parent.mkdir(mode=0o700, exist_ok=True)
    journal_parent.mkdir(mode=0o700, exist_ok=True)
    if parent.resolve() != parent or journal_parent.resolve() != journal_parent:
        raise ValueError("Symlinked Docker oracle scratch or journal directory")
    guard = HostGuard(RETAINED / "runtime-admission.json")
    with runtime_lease(Path(f"/private/tmp/container-compose-runtime-{os.getuid()}.lock"), guard), cancellation():
        def revalidate():
            if require_owned_volume(SSD_VOLUME) != volume:
                raise ValueError("Docker oracle SSD identity changed")
            return admit_docker(locks[0], locks[1], pins, locks[2], SSD, RETAINED)
        inputs = revalidate()
        harness = {str(path.relative_to(repository)): digest(path.read_bytes()) for path in [
            *sorted(Path(__file__).parent.glob("*.py")), *[repository / "Tools/bazel" / name for name in
            ("prepare_releases.py", "prepare_docker_cli.py", "prepare_guest_images.py", "release_inputs.py", "oci_image_layout.py")]]}
        identity = {"campaign": args.campaign, "fixture": args.fixture, "lane": "docker",
                    "contractSHA256": digest(canonical(expected)), "harnessSHA256": digest(canonical(harness)),
                    "releaseSetSHA256": digest(canonical({"dockerInputs": locks, "pins": pins})),
                    "runtimeSHA256": digest(canonical({"inputs": inputs, "volume": volume,
                                                       "machine": platform.machine(), "os": platform.mac_ver()[0]}))}
        validate_identity(identity)
        store = CaseStore(RETAINED / "runtime-cases.sqlite")
        case = DockerCase(store, identity, inputs, parent, revalidate, guard, journal_parent)
        result = run_case(store, identity, expected, case.setup, case.operation, case.cleanup)
        scope = "released-docker-case-only"
        if os.environ.get("XML_OUTPUT_FILE"):
            write_junit(identity, result, Path(os.environ["XML_OUTPUT_FILE"]), scope)
        print(canonical({"identity": identity, "result": result, "scope": scope}).decode())
        raise SystemExit(0 if result["status"] == "passed" else 1)
