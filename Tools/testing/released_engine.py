"""Released Engine cases with reversible, isolated service and guest selection."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).parents[1] / "bazel"))
from prepare_releases import require_retained
from prepare_candidate import COMPOSE_PRODUCTS, admit_candidate, SCOPE as CANDIDATE_SCOPE
from private_keychain import run_keychain
from release_inputs import validate_lock
from case_evidence import CaseStore, canonical, contract_observations, digest, run_case, validate_identity
from campaign_identity import published_fingerprints
from compose_foreground_probe import FIXTURES as COMPOSE_FOREGROUND_FIXTURES
from engine_probe import engine_negotiation, request
from host_runtime import HostGuard, OwnedProcess, cancellation, deadline, runtime_lease
from runtime_services import ControlledRuntime, require_owned_volume
from guest_runtime import FIXTURES, GUEST_API_VERSION, ReleasedGuest, admit_guest, diagnostic_snapshot


SSD = Path("/Volumes/SSD/cf/bazel")
SSD_VOLUME = Path("/Volumes/SSD")
RETAINED = Path.home() / "Library/Application Support/ContainerFamily/retained/workflow"
FIXTURE = "E01-engine-negotiation"


def release_selection(lock: dict, lane: str) -> list[dict]:
    assets = validate_lock(lock)
    runtime = ("apple/container", "container-1.4.1-installer-signed.pkg") if lane == "apple-stock" else (
        "stephenlclarke/container-compose", "container-release-arm64.tar.gz")
    required = [("stephenlclarke/devcontainer", "devcontainer-release-arm64.tar.gz"), runtime]
    selected = []
    for repository, name in required:
        matches = [asset for asset in assets if (asset["repository"], asset["name"]) == (repository, name)]
        if len(matches) != 1:
            raise ValueError("Required released runtime is missing or ambiguous")
        selected.append(matches[0])
    if lane not in {"apple-stock", "container-compose"} or selected[0]["tag"] != "1.0.1":
        raise ValueError("This released-service adapter requires reviewed devcontainer 1.0.1 arguments")
    return selected


def admit(lock: dict, lane: str, retained: Path, candidate: str | None = None) -> list[dict]:
    assets = release_selection(lock, lane)
    local = [admit_candidate(retained, candidate, "stock" if lane == "apple-stock" else "enhanced")] if candidate else []
    return local + [require_retained(asset, retained / "release-objects" / asset["sha256"],
                             retained / "prepared-releases", retained / "prepared-receipts")
                    for asset in (assets[1:] if candidate else assets)]


def fixture_guest_inputs(inputs: dict, fixture: str, candidate: dict, repository: Path, compose=None) -> dict:
    """Devcontainer cases consume authenticated bundles, never global tools."""
    if fixture in COMPOSE_FOREGROUND_FIXTURES:
        if (not isinstance(compose, dict) or compose.get("scope") != CANDIDATE_SCOPE or
                compose.get("productFamily") != "container-compose" or
                compose.get("runtimeProfile") not in {"stock", "enhanced"} or
                compose.get("runtimeProfile") != candidate.get("runtimeProfile") or
                set(compose.get("executables", {})) != COMPOSE_PRODUCTS):
            raise ValueError("Compose fixture requires an admitted matching native Compose candidate")
        return {**inputs, "composeCandidate": compose}
    if fixture not in {"C02-compose-dependencies", "C01-compose-service", "D01-image-config", "D02-dockerfile-config", "D03-users-environment", "D04-lifecycle-hooks", "D05-features", "D06-ports", "D07-reuse-cleanup"}:
        return inputs
    if fixture in {"C01-compose-service", "C02-compose-dependencies"}:
        from devcontainer_compose_reference import fixture_inputs
        if fixture == "C02-compose-dependencies":
            from devcontainer_dependencies_reference import fixture_inputs
        if (not isinstance(compose, dict) or compose.get("scope") != CANDIDATE_SCOPE or
                compose.get("productFamily") != "container-compose" or
                compose.get("runtimeProfile") not in {"stock", "enhanced"} or
                compose.get("runtimeProfile") != candidate.get("runtimeProfile") or
                set(compose.get("executables", {})) != COMPOSE_PRODUCTS):
            raise ValueError("Compose fixture requires an admitted matching native Compose candidate")
        inputs = {**inputs, "composeCandidate": compose}
    elif fixture == "D07-reuse-cleanup":
        from devcontainer_reuse_reference import fixture_inputs
    elif fixture == "D06-ports":
        from devcontainer_ports_reference import fixture_inputs
    elif fixture == "D05-features":
        from devcontainer_features_reference import fixture_inputs
    elif fixture == "D04-lifecycle-hooks":
        from devcontainer_lifecycle_reference import fixture_inputs
    elif fixture == "D03-users-environment":
        from devcontainer_users_reference import fixture_inputs
    elif fixture == "D02-dockerfile-config":
        from devcontainer_build_reference import fixture_inputs
    else:
        from devcontainer_reference import fixture_inputs
    required = {"devcontainer", "devcontainer-docker", "devcontainer-compose", "devcontainer-engine", "reference-node"}
    if candidate.get("scope") != CANDIDATE_SCOPE or set(candidate.get("executables", {})) != required:
        raise ValueError("Devcontainer fixture requires an admitted private-runtime candidate archive")
    return {**inputs, "devcontainerCandidate": candidate, "devcontainerFixture": fixture_inputs(repository)}


def version(command: Path) -> str:
    result = subprocess.run([str(command), "--version"], check=True, capture_output=True, timeout=10,
                            env={"PATH": "/usr/bin:/bin", "TMPDIR": str(SSD / "tmp")})
    if not result.stdout or len(result.stdout) > 4096 or result.stderr:
        raise ValueError("Unexpected released version output")
    return result.stdout.decode().strip()


def release_set_identity(lock: dict, guest_locks, candidate: dict | None) -> str:
    published = [lock, guest_locks] if guest_locks else lock
    # Comparison intentionally ignores per-lane runtime fingerprints. Bind the
    # development-only scope here so it cannot mix with a published campaign.
    inputs = {"scope": CANDIDATE_SCOPE, "publishedInputs": published, "candidate": candidate} if candidate else published
    return digest(canonical(inputs))


def write_junit(identity: dict, result: dict, path: Path, scope: str = "released-engine-case-only") -> None:
    failed = result["status"] != "passed"
    suite = ET.Element("testsuite", name="released-engine", tests="1", failures=str(int(failed)), errors="0", skipped="0")
    case = ET.SubElement(suite, "testcase", name=identity["fixture"], classname=identity["lane"],
                         time=str(sum(result["durationsNS"].values()) / 1e9))
    properties = ET.SubElement(case, "properties")
    for name, value in {**identity, **result["durationsNS"], "scope": scope}.items():
        ET.SubElement(properties, "property", name=name, value=str(value))
    if failed:
        ET.SubElement(case, "failure", message=result["status"]).text = "\n".join(result["errors"])
    ET.SubElement(case, "system-out").text = canonical({"identity": identity, "result": result}).decode()
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


class ReleasedCase:
    def __init__(self, store: CaseStore, identity: dict, releases: list[dict], parent: Path, revalidate, guard: HostGuard,
                 *, runtime_factory=None, guest_inputs=None, admission=None):
        self.store, self.identity, self.releases = store, identity, releases
        self.parent, self.revalidate = parent, revalidate
        self.root = None
        self.output = None
        self.child = OwnedProcess()
        self.guard, self.owner = guard, None
        self.requests = []
        self.runtime_factory, self.runtime = runtime_factory, None
        self.guest_inputs, self.guest = guest_inputs, None
        self.admission = admission
        self.keychain_intent = False

    def prepare_home(self, journal):
        """Provision the private default keychain before either API or Engine startup."""
        self.store.attach(self.identity, "keychain-intent.json", canonical({"home": str(self.root)}))
        self.keychain_intent = True
        self.store.attach(self.identity, "keychain-ready.json", canonical(run_keychain(self.root, "create", journal)))

    def setup(self):
        self.root = Path(tempfile.mkdtemp(dir=self.parent, prefix="case-"))
        self.socket = self.root / "engine.sock"
        owner = canonical({"identity": self.identity, "root": str(self.root)})
        (self.root / "owner.json").write_bytes(owner)
        # Durable ownership intent precedes launching any external process.
        self.store.attach(self.identity, "owner.json", owner)
        if self.admission is not None:
            self.store.attach(self.identity, "admission.json", canonical(self.admission))
        self.owner = json.loads(owner)
        self.guard.begin(self.owner)
        if self.runtime_factory is not None:
            self.runtime = self.runtime_factory(self.root, self.owner)
            self.runtime.start(prepare_home=self.prepare_home)
            self.store.attach(self.identity, "api-service.json", canonical(self.runtime.service))
        else:
            self.prepare_home(None)
        if self.guest_inputs is not None:
            if self.runtime is None:
                raise ValueError("Guest setup requires the controlled private runtime")
            self.guest = ReleasedGuest(self.guest_inputs, self.identity["fixture"], self.root, self.owner,
                                       self.runtime, self.releases[1]["executables"]["container"], self.socket,
                                       observe=self.requests.append)
            self.guest.provision()
            if self.revalidate() != self.releases:
                raise ValueError("Released runtime inputs changed during provisioning")
        self.output = (self.root / "engine.log").open("xb")
        engine = self.releases[0]["executables"]["devcontainer-engine"]
        container = self.releases[1]["executables"]["container"]
        self.store.attach(self.identity, "process-intent.json", canonical({"root": str(self.root), "program": engine}))
        self.child.start([engine, "--container", container, "--socket", str(self.socket),
                          "--state", str(self.root / "state.sqlite")], self.root, self.output,
                         provider_install=Path(container).parent.parent)
        self.store.attach(self.identity, "process.json", canonical({"pid": self.child.process.pid, "root": str(self.root)}))
        self.store.attach(self.identity, "process-incarnation.json", canonical(self.child.identity()))
        self.child.wait_ready(lambda: request(self.socket, "GET", "/_ping", timeout=1) == (200, b"OK"))
        if self.guest is not None and self.identity["fixture"] in {"C02-compose-dependencies", "C01-compose-service", "D01-image-config", "D02-dockerfile-config", "D03-users-environment", "D04-lifecycle-hooks", "D05-features", "D06-ports", "D07-reuse-cleanup"}:
            self.guest.setup_devcontainer()

    def operation(self):
        if self.guest is not None:
            return self.guest.operation()
        with deadline(45):
            return engine_negotiation(self.socket, observe=self.requests.append)

    def cleanup(self):
        if self.guest is not None:
            # Uncertain guest deletion leaves the Engine/provider running and
            # the host quarantined; stopping them first would lose reconciliation.
            try:
                self.guest.cleanup()
            finally:
                self.store.attach(self.identity, "requests.json", canonical(self.requests))
        stopped = False
        try:
            try:
                self.child.stop()
                stopped = True
            finally:
                # A failed stop still needs its diagnostics sealed before
                # run_case completes; a surviving process has only a snapshot.
                if self.output is not None:
                    self.output.close()
                self.store.attach(self.identity, "process-cleanup.json", canonical({"verifiedStopped": stopped}))
                self.store.attach(self.identity, "requests.json", canonical(self.requests))
                if self.output is not None:
                    payload, metadata = diagnostic_snapshot(self.root / "engine.log")
                    self.store.attach(self.identity, "engine.log", payload)
                    self.store.attach(self.identity, "engine-log.json", metadata)
            if self.runtime is not None and self.runtime.service is not None:
                self.runtime.verify()
            # Detect executable replacement before claiming successful cleanup.
            if self.revalidate() != self.releases:
                raise ValueError("Released runtime inputs changed during execution")
        finally:
            # A diagnostics/retention failure must not prevent restoring the
            # operator's services after the child has verifiably stopped.
            if stopped:
                try:
                    if self.runtime is not None:
                        self.runtime.restore()
                    if self.keychain_intent:
                        # Both API and Engine consume this keychain. Preserve
                        # it if either shutdown is uncertain, as recovery does.
                        self.store.attach(self.identity, "keychain-cleanup.json", canonical(run_keychain(
                            self.root, "delete", self.runtime.journal if self.runtime else None)))
                finally:
                    if self.runtime is not None:
                        self.runtime.preserve_logs()
                        self.store.attach(self.identity, "service-journal.json", canonical(self.runtime.receipt()))
        if self.root is not None:
            owner = json.loads((self.root / "owner.json").read_text())
            if self.root.is_symlink() or owner != {"identity": self.identity, "root": str(self.root)}:
                raise ValueError("Case root ownership changed; preserving evidence")
            shutil.rmtree(self.root)
        if self.owner is not None:
            self.guard.clear(self.owner)
        return {"status": "passed", "remainingOwnedResources": []}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--lane", required=True, choices=["docker", "apple-stock", "container-compose"])
    parser.add_argument("--fixture", choices=[FIXTURE, *sorted(FIXTURES)], default=FIXTURE)
    parser.add_argument("--candidate-invocation", help="prepared local candidate; NOT published-release qualification")
    parser.add_argument("--compose-candidate-invocation", help="prepared matching native Compose candidate for C01/C02/E09/E10/E11/E12")
    args = parser.parse_args()
    os.umask(0o077)
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("Released Engine cases require Apple silicon macOS")
    compose_fixtures = {"C01-compose-service", "C02-compose-dependencies", *COMPOSE_FOREGROUND_FIXTURES}
    if args.compose_candidate_invocation and (args.fixture not in compose_fixtures or args.lane == "docker"):
        raise ValueError("Native Compose candidate is only valid for native C01/C02/E09/E10/E11/E12")
    if args.lane == "docker":
        from released_docker import run_docker
        run_docker(args)
        return
    if args.fixture in compose_fixtures and not args.compose_candidate_invocation:
        raise ValueError("Compose fixture requires a prepared native Compose candidate; no runtime changes made")
    if args.fixture in {"C02-compose-dependencies", "C01-compose-service", "D01-image-config", "D02-dockerfile-config", "D03-users-environment", "D04-lifecycle-hooks", "D05-features", "D06-ports", "D07-reuse-cleanup"} and not args.candidate_invocation:
        raise ValueError("Devcontainer fixture requires a verified private-runtime candidate; no runtime changes made")
    repository = Path(__file__).parents[2]
    lock = json.loads((repository / "Tools/bazel/releases.lock.json").read_text())
    guest_locks = None
    builder_lock = None
    if args.fixture in FIXTURES:
        guest_locks = [json.loads((repository / "Tools/bazel" / name).read_text())
                       for name in ("guest-kernel.lock.json", "guest-images.lock.json")]
        if args.fixture in {"E04-image-build", "D02-dockerfile-config", "D03-users-environment", "D05-features"}:
            builder_lock = json.loads((repository / "Tools/bazel/builder-images.lock.json").read_text())
    expected = contract_observations(json.loads(
        (repository / f"Tests/Parity/fixtures/{args.fixture}/contract.json").read_text())["expected"])
    fingerprints = published_fingerprints(repository)
    for root in (SSD, RETAINED):
        if not root.is_dir() or root.resolve() != root:
            raise ValueError("Missing or symlinked enrolled storage")
    if SSD.stat().st_dev == RETAINED.stat().st_dev:
        raise ValueError("Scratch and retained evidence must use separate volumes")
    volume = require_owned_volume(SSD_VOLUME)
    parent = SSD / "live"
    parent.mkdir(exist_ok=True)
    if parent.resolve() != parent:
        raise ValueError("Symlinked case scratch")
    # The legacy Compose lock pathname is an IPC coordination point, not a
    # payload/temp directory. Keeping the same inode serializes both workflows.
    guard = HostGuard(RETAINED / "runtime-admission.json")
    with runtime_lease(Path(f"/private/tmp/container-compose-runtime-{os.getuid()}.lock"), guard), cancellation():
        releases = admit(lock, args.lane, RETAINED, args.candidate_invocation)
        def selected_compose():
            if not args.compose_candidate_invocation:
                return None
            return admit_candidate(RETAINED, args.compose_candidate_invocation,
                                   "stock" if args.lane == "apple-stock" else "enhanced", "container-compose")
        compose = selected_compose()
        guest_inputs = admit_guest(*guest_locks, args.lane, RETAINED, builder_lock=builder_lock,
                                   fixture=args.fixture) if guest_locks is not None else None
        if guest_inputs is not None:
            guest_inputs = fixture_guest_inputs(guest_inputs, args.fixture, releases[0], repository, compose)
        api_server = Path(releases[1]["executables"]["container-apiserver"])
        runtime = {"releases": releases, "machine": platform.machine(), "os": platform.mac_ver()[0],
                   "scratchVolume": volume,
                   "apiProgram": str(api_server), "serviceSelection": "released-private-root-v1",
                   "versions": [version(Path(releases[0]["executables"]["devcontainer"])),
                                version(Path(releases[1]["executables"]["container"]))]}
        if guest_inputs is not None:
            runtime["guestInputs"] = guest_inputs
            runtime["guestAPIVersion"] = GUEST_API_VERSION
        identity = {"campaign": args.campaign, "fixture": args.fixture, "lane": args.lane,
                    "contractSHA256": digest(canonical(expected)), **fingerprints,
                    "runtimeSHA256": digest(canonical(runtime))}
        if args.candidate_invocation:
            candidates = {"devcontainer": releases[0], "compose": compose} if compose else releases[0]
            identity["releaseSetSHA256"] = release_set_identity(fingerprints, None, candidates)
        validate_identity(identity)
        store = CaseStore(RETAINED / "runtime-cases.sqlite")
        def revalidate():
            if require_owned_volume(SSD_VOLUME) != volume:
                raise ValueError("SSD ownership or volume identity changed during execution")
            if guest_locks is not None:
                current = admit_guest(*guest_locks, args.lane, RETAINED, builder_lock=builder_lock, fixture=args.fixture)
                if fixture_guest_inputs(current, args.fixture, releases[0], repository, selected_compose()) != guest_inputs:
                    raise ValueError("Released guest inputs changed during execution")
            return admit(lock, args.lane, RETAINED, args.candidate_invocation)

        def runtime_factory(root, owner):
            journal_parent = RETAINED / "private-runtime"
            journal_parent.mkdir(mode=0o700, exist_ok=True)
            return ControlledRuntime(root, owner, api_server, journal_parent)

        scope = CANDIDATE_SCOPE if args.candidate_invocation else "released-engine-case-only"
        admission = {"scope": scope, "releaseLock": lock, "guestLocks": guest_locks,
                     "builderLock": builder_lock, "runtime": runtime}
        case = ReleasedCase(store, identity, releases, parent, revalidate, guard, runtime_factory=runtime_factory,
                            guest_inputs=guest_inputs, admission=admission)
        result = run_case(store, identity, expected, case.setup, case.operation, case.cleanup)
        if os.environ.get("XML_OUTPUT_FILE"):
            write_junit(identity, result, Path(os.environ["XML_OUTPUT_FILE"]), scope)
        print(canonical({"identity": identity, "result": result, "scope": scope}).decode())
        raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
