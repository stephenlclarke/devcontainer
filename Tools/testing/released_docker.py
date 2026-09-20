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

from case_evidence import CaseStore, canonical, contract_observations, digest, run_case, validate_identity
from campaign_identity import published_fingerprints
from docker_vm import DockerVM, private_root
from devcontainer_reference import DevcontainerReference, FIXTURE as DEVCONTAINER_FIXTURE, fixture_inputs
from devcontainer_build_reference import DevcontainerBuildReference, FIXTURE as BUILD_FIXTURE, fixture_inputs as build_fixture_inputs
from devcontainer_users_reference import DevcontainerUsersReference, FIXTURE as USERS_FIXTURE, fixture_inputs as users_fixture_inputs
from devcontainer_lifecycle_reference import DevcontainerLifecycleReference, FIXTURE as LIFECYCLE_FIXTURE, fixture_inputs as lifecycle_fixture_inputs
from devcontainer_features_reference import DevcontainerFeaturesReference, FIXTURE as FEATURES_FIXTURE, fixture_inputs as features_fixture_inputs
from devcontainer_reuse_reference import DevcontainerReuseReference, FIXTURE as REUSE_FIXTURE, fixture_inputs as reuse_fixture_inputs
from devcontainer_compose_reference import DevcontainerComposeReference, FIXTURE as COMPOSE_FIXTURE, fixture_inputs as compose_fixture_inputs
from devcontainer_dependencies_reference import DevcontainerDependenciesReference, FIXTURE as DEPENDENCIES_FIXTURE, fixture_inputs as dependencies_fixture_inputs
from devcontainer_ports_reference import DevcontainerPortsReference, FIXTURE as PORTS_FIXTURE, fixture_inputs as ports_fixture_inputs
from engine_probe import engine_negotiation, request
from guest_runtime import FIXTURES, GUEST_API_VERSION, ReleasedGuest
from host_runtime import HostGuard, cancellation, cleanup_receipt, deadline, runtime_lease
from prepare_docker_cli import prepare_cli
from prepare_devcontainers_cli import prepare_cli as prepare_devcontainers
from prepare_guest_images import require_image
from prepare_releases import require_retained
from release_inputs import validate_lock
from runtime_services import require_owned_volume
from service_journal import ServiceJournal


def admit_docker(oracle_lock: dict, cli_lock: dict, pins: dict, images: dict, scratch: Path, retained: Path,
                 *, fixture=None, repository=None) -> dict:
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
    workload = {FEATURES_FIXTURE: "ubuntu-workload", PORTS_FIXTURE: "python-workload"}.get(fixture, "alpine-workload")
    matches = [image for image in images.get("images", []) if image.get("name") == workload]
    if len(matches) != 1:
        raise ValueError("Docker workload image is missing or ambiguous")
    result = {"assets": prepared, "client": client, "tools": tools, "pins": pins,
              "workload": require_image(matches[0], retained / "guest-images")}
    if fixture == DEPENDENCIES_FIXTURE:
        dependencies = [image for image in images.get("images", []) if image.get("name") == "python-workload"]
        if len(dependencies) != 1:
            raise ValueError("C02 dependency image is missing or ambiguous")
        result["dependencyWorkload"] = require_image(dependencies[0], retained / "guest-images")
    if fixture in {COMPOSE_FIXTURE, DEPENDENCIES_FIXTURE, "E09-compose-foreground"}:
        compose_lock = json.loads((repository / "Tools/bazel/releases.lock.json").read_text())
        compose_assets = [asset for asset in validate_lock(compose_lock) if asset["repository"] == "docker/compose"]
        if (len(compose_assets) != 1 or compose_assets[0]["tag"] != "v" + pins["composeVersion"] or
                compose_assets[0]["name"] != "docker-compose-darwin-aarch64"):
            raise ValueError("C01 requires the pinned published Docker Compose release")
        asset = compose_assets[0]
        result["compose"] = require_retained(asset, retained / "release-objects" / asset["sha256"],
                                             retained / "prepared-releases", retained / "prepared-receipts")
    if fixture in {DEVCONTAINER_FIXTURE, BUILD_FIXTURE, USERS_FIXTURE, LIFECYCLE_FIXTURE, FEATURES_FIXTURE, PORTS_FIXTURE, REUSE_FIXTURE, COMPOSE_FIXTURE, DEPENDENCIES_FIXTURE}:
        reference_lock = json.loads((repository / "Tools/bazel/devcontainers-cli.lock.json").read_text())
        reference = json.loads((repository / "Tests/Parity/manifest.json").read_text())["referencePins"]["devcontainersCli"]
        result["devcontainers"] = prepare_devcontainers(reference_lock, reference, scratch, retained, offline=True)
        readers = {DEVCONTAINER_FIXTURE: fixture_inputs, BUILD_FIXTURE: build_fixture_inputs,
                   USERS_FIXTURE: users_fixture_inputs, LIFECYCLE_FIXTURE: lifecycle_fixture_inputs,
                   FEATURES_FIXTURE: features_fixture_inputs, PORTS_FIXTURE: ports_fixture_inputs, REUSE_FIXTURE: reuse_fixture_inputs,
                   COMPOSE_FIXTURE: compose_fixture_inputs, DEPENDENCIES_FIXTURE: dependencies_fixture_inputs}
        result["devcontainerFixture"] = readers[fixture](repository)
    return result


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
        if self.identity["fixture"] in {DEVCONTAINER_FIXTURE, BUILD_FIXTURE, USERS_FIXTURE, LIFECYCLE_FIXTURE, FEATURES_FIXTURE, PORTS_FIXTURE, REUSE_FIXTURE, COMPOSE_FIXTURE, DEPENDENCIES_FIXTURE}:
            adapter = {DEVCONTAINER_FIXTURE: DevcontainerReference, BUILD_FIXTURE: DevcontainerBuildReference,
                       USERS_FIXTURE: DevcontainerUsersReference, LIFECYCLE_FIXTURE: DevcontainerLifecycleReference,
                       FEATURES_FIXTURE: DevcontainerFeaturesReference, PORTS_FIXTURE: DevcontainerPortsReference, REUSE_FIXTURE: DevcontainerReuseReference,
                       COMPOSE_FIXTURE: DevcontainerComposeReference, DEPENDENCIES_FIXTURE: DevcontainerDependenciesReference}[self.identity["fixture"]]
            self.guest = adapter(self.vm, self.inputs, self.owner, observe=self.requests.append)
            self.guest.setup()
        elif self.identity["fixture"] in FIXTURES:
            self.vm.command("docker-workload-load", [self.inputs["tools"]["docker"], "--host", "unix://" + str(self.vm.socket),
                            "image", "load", "--input", self.inputs["workload"]["path"]], timeout=60)
            # Docker's containerd store exposes the OCI target digest as Id,
            # not the config digest used by the classic image store. Both are
            # authenticated by the retained single-platform archive's closure.
            identifier = self.inputs["workload"]["image"]["manifest"]
            status, data = request(self.vm.socket, "GET", "/v" + GUEST_API_VERSION + "/images/" + identifier + "/json")
            inspection = json.loads(data)
            self.vm.journal.put("docker-workload-inspect.json", canonical({"status": status, "inspection": inspection}))
            if (status != 200 or inspection.get("Id") != identifier or
                    inspection.get("Descriptor", {}).get("digest") != identifier or
                    inspection.get("Os") != "linux" or inspection.get("Architecture") != "arm64"):
                raise ValueError("Docker did not load the exact admitted workload image")
            self.guest = ReleasedGuest(self.inputs, self.identity["fixture"], self.root / "workspace", self.owner,
                                       self.vm, "", self.vm.socket, observe=self.requests.append, image_id=identifier)
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
            # Persist before removal: a crash during rmtree or before guard.clear
            # must remain recoverable without restarting the VM or fixture.
            authorization = cleanup_receipt(self.owner, self.root)
            self.vm.journal.put("docker-cleanup-authorized.json", authorization)
            private_root(self.root, self.owner)
            if cleanup_receipt(self.owner, self.root) != authorization:
                raise ValueError("Docker root changed before cleanup")
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
    expected = contract_observations(json.loads(
        (repository / f"Tests/Parity/fixtures/{args.fixture}/contract.json").read_text())["expected"])
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
            return admit_docker(locks[0], locks[1], pins, locks[2], SSD, RETAINED,
                                fixture=args.fixture, repository=repository)
        inputs = revalidate()
        identity = {"campaign": args.campaign, "fixture": args.fixture, "lane": "docker",
                    "contractSHA256": digest(canonical(expected)), **published_fingerprints(repository),
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
