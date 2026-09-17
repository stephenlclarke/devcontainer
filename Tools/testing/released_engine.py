"""One released Engine negotiation case with reversible, isolated service selection."""

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
from prepare_releases import require_prepared
from release_inputs import validate_lock
from case_evidence import CaseStore, canonical, digest, run_case, validate_identity
from engine_probe import engine_negotiation, request
from host_runtime import HostGuard, OwnedProcess, cancellation, deadline, runtime_lease
from runtime_services import ControlledRuntime, require_owned_volume


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


def admit(lock: dict, lane: str, retained: Path, ssd: Path) -> list[dict]:
    return [require_prepared(asset, retained / "release-objects" / asset["sha256"],
                             ssd / "prepared-releases", retained / "prepared-receipts")
            for asset in release_selection(lock, lane)]


def version(command: Path) -> str:
    result = subprocess.run([str(command), "--version"], check=True, capture_output=True, timeout=10,
                            env={"PATH": "/usr/bin:/bin", "TMPDIR": str(SSD / "tmp")})
    if not result.stdout or len(result.stdout) > 4096 or result.stderr:
        raise ValueError("Unexpected released version output")
    return result.stdout.decode().strip()


def write_junit(identity: dict, result: dict, path: Path) -> None:
    failed = result["status"] != "passed"
    suite = ET.Element("testsuite", name="released-engine", tests="1", failures=str(int(failed)), errors="0", skipped="0")
    case = ET.SubElement(suite, "testcase", name=identity["fixture"], classname=identity["lane"],
                         time=str(sum(result["durationsNS"].values()) / 1e9))
    properties = ET.SubElement(case, "properties")
    for name, value in {**identity, **result["durationsNS"]}.items():
        ET.SubElement(properties, "property", name=name, value=str(value))
    if failed:
        ET.SubElement(case, "failure", message=result["status"]).text = "\n".join(result["errors"])
    ET.SubElement(case, "system-out").text = canonical({"identity": identity, "result": result}).decode()
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


class ReleasedCase:
    def __init__(self, store: CaseStore, identity: dict, releases: list[dict], parent: Path, revalidate, guard: HostGuard,
                 *, runtime_factory=None):
        self.store, self.identity, self.releases = store, identity, releases
        self.parent, self.revalidate = parent, revalidate
        self.root = None
        self.output = None
        self.child = OwnedProcess()
        self.guard, self.owner = guard, None
        self.requests = []
        self.runtime_factory, self.runtime = runtime_factory, None

    def setup(self):
        self.root = Path(tempfile.mkdtemp(dir=self.parent, prefix="case-"))
        self.socket = self.root / "engine.sock"
        owner = canonical({"identity": self.identity, "root": str(self.root)})
        (self.root / "owner.json").write_bytes(owner)
        # Durable ownership intent precedes launching any external process.
        self.store.attach(self.identity, "owner.json", owner)
        self.owner = json.loads(owner)
        self.guard.begin(self.owner)
        if self.runtime_factory is not None:
            self.runtime = self.runtime_factory(self.root, self.owner)
            self.runtime.start()
            self.store.attach(self.identity, "api-service.json", canonical(self.runtime.service))
        self.output = (self.root / "engine.log").open("xb")
        engine = self.releases[0]["executables"]["devcontainer-engine"]
        container = self.releases[1]["executables"]["container"]
        self.store.attach(self.identity, "process-intent.json", canonical({"root": str(self.root), "program": engine}))
        self.child.start([engine, "--container", container, "--socket", str(self.socket),
                          "--state", str(self.root / "state.sqlite")], self.root, self.output)
        self.store.attach(self.identity, "process.json", canonical({"pid": self.child.process.pid, "root": str(self.root)}))
        self.child.wait_ready(lambda: request(self.socket, "GET", "/_ping", timeout=1) == (200, b"OK"))

    def operation(self):
        with deadline(45):
            return engine_negotiation(self.socket, observe=self.requests.append)

    def cleanup(self):
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
                    self.store.attach(self.identity, "engine.log", (self.root / "engine.log").read_bytes())
            if self.runtime is not None and self.runtime.service is not None:
                self.runtime.verify()
            # Detect executable replacement before claiming successful cleanup.
            if self.revalidate() != self.releases:
                raise ValueError("Released runtime inputs changed during execution")
        finally:
            # A diagnostics/retention failure must not prevent restoring the
            # operator's services after the child has verifiably stopped.
            if stopped and self.runtime is not None:
                try:
                    self.runtime.restore()
                finally:
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
    parser.add_argument("--lane", required=True, choices=["apple-stock", "container-compose"])
    args = parser.parse_args()
    os.umask(0o077)
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("Released Apple cases require Apple silicon macOS")
    repository = Path(__file__).parents[2]
    lock = json.loads((repository / "Tools/bazel/releases.lock.json").read_text())
    expected = {key: str(value).lower() for key, value in json.loads(
        (repository / f"Tests/Parity/fixtures/{FIXTURE}/contract.json").read_text())["expected"].items()}
    harness = {str(path.relative_to(repository)): digest(path.read_bytes()) for path in [
        *sorted(Path(__file__).parent.glob("*.py")),
        repository / "Tools/bazel/prepare_releases.py", repository / "Tools/bazel/release_inputs.py"]}
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
        releases = admit(lock, args.lane, RETAINED, SSD)
        api_server = Path(releases[1]["executables"]["container-apiserver"])
        runtime = {"releases": releases, "machine": platform.machine(), "os": platform.mac_ver()[0],
                   "scratchVolume": volume,
                   "apiProgram": str(api_server), "serviceSelection": "released-private-root-v1",
                   "versions": [version(Path(releases[0]["executables"]["devcontainer"])),
                                version(Path(releases[1]["executables"]["container"]))]}
        identity = {"campaign": args.campaign, "fixture": FIXTURE, "lane": args.lane,
                    "contractSHA256": digest(canonical(expected)), "harnessSHA256": digest(canonical(harness)),
                    "releaseSetSHA256": digest(canonical(lock)), "runtimeSHA256": digest(canonical(runtime))}
        validate_identity(identity)
        store = CaseStore(RETAINED / "runtime-cases.sqlite")
        def revalidate():
            if require_owned_volume(SSD_VOLUME) != volume:
                raise ValueError("SSD ownership or volume identity changed during execution")
            return admit(lock, args.lane, RETAINED, SSD)

        def runtime_factory(root, owner):
            journal_parent = RETAINED / "private-runtime"
            journal_parent.mkdir(mode=0o700, exist_ok=True)
            return ControlledRuntime(root, owner, api_server, journal_parent)

        case = ReleasedCase(store, identity, releases, parent, revalidate, guard, runtime_factory=runtime_factory)
        result = run_case(store, identity, expected, case.setup, case.operation, case.cleanup)
        if os.environ.get("XML_OUTPUT_FILE"):
            write_junit(identity, result, Path(os.environ["XML_OUTPUT_FILE"]))
        print(canonical({"identity": identity, "result": result, "scope": "released-engine-negotiation-only"}).decode())
        raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
