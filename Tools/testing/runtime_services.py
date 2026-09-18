"""Released API service lifecycle under the caller's lease and durable host guard.

Only the metadata/Engine negotiation case uses this adapter initially. It does
not install kernels, pull images, start guest workloads or modify provider keys.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import time

from host_runtime import deadline, require_api_service
from runtime_probe import probe_api, probe_diagnostics, require_probe_stopped
from service_journal import ServiceJournal, digest
from service_switch import API, BASE_SERVICES, Launchd, ServiceSwitch, snapshot


class ProcessSurvivors(ValueError):
    """Expected asynchronous shutdown is incomplete, never a passing cleanup."""


def require_owned_volume(volume: Path) -> dict:
    """launchd rejects SSD plists when the volume ignores file ownership."""
    result = subprocess.run(["/usr/sbin/diskutil", "info", "-plist", str(volume)],
                            check=True, capture_output=True, timeout=5, env={"PATH": "/usr/bin:/bin"})
    info = plistlib.loads(result.stdout)
    if info.get("GlobalPermissionsEnabled") is not True or info.get("MountPoint") != str(volume) or info.get("Internal") is not False:
        raise ValueError("Released services require ownership enforcement on the enrolled external SSD")
    uuid = info.get("VolumeUUID")
    if not isinstance(uuid, str) or not uuid:
        raise ValueError("SSD volume identity is unavailable")
    return {"uuid": uuid, "mount": str(volume), "ownersEnabled": True}


def wait_stopped(probe, seconds: float = 15):
    """Wait only for process/registration disappearance, not a failed case retry."""
    with deadline(seconds):
        while True:
            try:
                probe()
                return
            except ProcessSurvivors:
                time.sleep(0.05)


def process_inventory() -> dict[int, dict]:
    result = subprocess.run(["/bin/ps", "-axo", "pid=,ppid=,pgid=,lstart=,comm="], check=True, capture_output=True,
                            timeout=5, env={"PATH": "/usr/bin:/bin"})
    processes = {}
    for line in result.stdout.decode().splitlines():
        fields = line.split(maxsplit=8)
        if len(fields) != 9 or any(not value.isdigit() for value in fields[:3]):
            raise ValueError("Incomplete host process identity")
        pid, parent, group = map(int, fields[:3])
        if pid <= 0 or pid in processes:
            raise ValueError("Ambiguous host process identity")
        processes[pid] = {"pid": pid, "parent": parent, "group": group,
                          "started": " ".join(fields[3:8]), "program": fields[8]}
    return processes


def process_programs() -> list[str]:
    return [item["program"] for item in process_inventory().values()]


def capture_owned_processes(launchd, prior: list[dict]) -> list[dict]:
    roots = {item["label"]: pid for item in prior if (pid := launchd.process_id(item["label"])) is not None}
    processes = process_inventory()
    if not set(roots.values()) <= processes.keys():
        raise ValueError("Service processes changed during ownership capture")
    owners = {}
    for label, root in roots.items():
        owned = {root}
        while True:
            expanded = owned | {pid for pid, item in processes.items() if item["parent"] in owned}
            if expanded == owned:
                break
            owned = expanded
        for pid in owned:
            owners.setdefault(pid, []).append(label)
    return [dict(processes[pid], labels=sorted(owners[pid])) for pid in sorted(owners)]


def require_idle(programs: list[str]) -> None:
    # Do not stop an active Actions job or a user CLI/guest workload. Listener
    # jobs may be suspended by the explicitly scoped service transaction.
    busy = {"Runner.Worker", "container", "compose", "docker", "docker-compose", "colima",
            "container-runtime-linux", "com.apple.Virtualization.VirtualMachine"}
    if any(Path(program).name in busy for program in programs):
        raise ValueError("Active worker, container command or guest prevents runtime selection")


def require_captured_processes_stopped(launchd, services, captured, *, allow_registered=False):
    """An unchanged plist alone cannot authorize a surviving old process."""
    current = process_inventory()
    allowed = {}
    if allow_registered:
        registered = [item for item in services if launchd.inspect(item["label"]) ==
                      {key: item[key] for key in ("label", "path", "program")}]
        allowed = {item["pid"]: item for item in capture_owned_processes(launchd, registered)}
    for prior in captured:
        actual = current.get(prior["pid"])
        # exec/setsid do not end ownership; a new start identity establishes PID reuse.
        if actual is not None and actual["started"] == prior["started"]:
            owner = allowed.get(prior["pid"])
            if (owner is not None and all(owner[key] == actual[key] for key in ("started", "program"))
                    and set(owner["labels"]) & set(prior["labels"])):
                continue
            raise ProcessSurvivors("An owned original service process survived removal")


def authorised_roots(launchd: Launchd, home: Path) -> dict[str, Path]:
    """Known family services only; never derive authority from a loaded plist."""
    roots = {label: home / "Library/Application Support/com.apple.container" for label in BASE_SERVICES}
    agents = home / "Library/LaunchAgents"
    for label in launchd.labels():
        if label in {"homebrew.mxcl.devcontainer", "sh.brew.container", "com.stephenlclarke.container-family-ci"} or any(
                label.startswith(f"actions.runner.stephenlclarke-{repository}.")
                for repository in ("devcontainer", "container-compose", "container-build")):
            roots[label] = agents
    return roots


def selected_definition(root: Path, executable: Path) -> Path:
    """Mirror stock SystemStart's service contract, omitting guest provisioning."""
    if not root.is_absolute() or root.resolve() != root or not root.is_dir():
        raise ValueError("Selected runtime needs a canonical owned root")
    if not executable.is_absolute() or executable.resolve() != executable or not executable.is_file():
        raise ValueError("Selected runtime needs a canonical prepared executable")
    app = root / "container"
    app.mkdir(mode=0o700)
    logs = root / "container-logs"
    logs.mkdir(mode=0o700)
    environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(root),
                   "TMPDIR": str(root), "TMP": str(root), "TEMP": str(root),
                   "CONTAINER_APP_ROOT": str(app), "CONTAINER_INSTALL_ROOT": str(executable.parent.parent),
                   "CONTAINER_LOG_ROOT": str(logs)}
    definition = {"Label": API, "ProgramArguments": [str(executable), "start"],
                  "EnvironmentVariables": environment, "RunAtLoad": True,
                  "LimitLoadToSessionType": ["Aqua", "Background", "System"], "MachServices": {API: True}}
    path = root / "selected-apiserver.plist"
    with path.open("xb") as output:
        output.write(plistlib.dumps(definition))
        output.flush()
        os.fsync(output.fileno())
    return path


class ControlledRuntime:
    """Select one released service, then restore originals before guard removal.

    The caller has already marked root ownership and acquired the shared lease.
    `journal_parent` is private retained INTERNAL storage, checked against the
    disposable SSD device here. Logs/state/selected plists stay in `root`.
    """

    def __init__(self, root: Path, owner: dict, executable: Path, journal_parent: Path, *,
                 launchd=None, home: Path | None = None):
        if owner.get("root") != str(root) or journal_parent.stat().st_dev == root.stat().st_dev:
            raise ValueError("Runtime journal must retain this owner on a separate internal volume")
        self.root, self.owner, self.executable = root, owner, executable
        self.journal_parent = journal_parent
        self.launchd, self.home = launchd or Launchd(), home or Path.home()
        self.switch = None
        self.journal = None
        self.service = None
        self.original_processes = []

    def start(self):
        prior = snapshot(self.launchd, authorised_roots(self.launchd, self.home))
        self.original_processes = capture_owned_processes(self.launchd, prior)
        self.require_idle_before_selection(prior)
        definition = selected_definition(self.root, self.executable)
        path = self.journal_parent / (digest(str(self.root).encode()) + ".sqlite")
        self.journal = ServiceJournal(path, self.owner, create=True)
        self.journal.put("runtime-context.json", json.dumps(
            {"apiExecutable": str(self.executable)}, sort_keys=True).encode())
        self.journal.put("original-processes.plist", plistlib.dumps(self.original_processes))
        self.switch = ServiceSwitch(self.launchd, prior, self.root, self.journal.put)
        self.require_idle_before_selection(prior)
        self.switch.prepare()
        # A listener/helper that survived removal may not overlap this lane.
        wait_stopped(self.require_workers_stopped)
        self.switch.install(definition)
        with deadline(25):
            while True:
                current = self.launchd.inspect(API)
                if current != {"label": API, "path": str(definition), "program": str(self.executable)}:
                    raise ValueError("Selected API service changed before readiness")
                try:
                    self.service = require_api_service(self.executable)
                    break
                except ValueError:
                    # Registration identity is checked above; wait only for
                    # launchd to report this selected job running, not a retry
                    # of an Engine conformance request.
                    time.sleep(0.05)
        self.journal.put("service-started.plist", plistlib.dumps(self.service))
        probe_api(self.root, self.executable.parent / "container", self.journal, self.verify)
        self.journal.put("service-ready.plist", plistlib.dumps(self.service))

    def require_idle_before_selection(self, prior):
        # Homebrew's registered one-shot `container system start` is itself an
        # authorised service to quiesce, not an unrelated user CLI. Exempt only
        # its captured root process; never exempt active Actions workers, child
        # workloads, or another CLI with the same executable name.
        administrative = set()
        for item in prior:
            if item["label"] == "sh.brew.container":
                arguments = plistlib.loads(item["payload"]).get("ProgramArguments", [])
                if arguments[1:] == ["system", "start"]:
                    pid = self.launchd.process_id(item["label"])
                    if pid is not None:
                        administrative.add(pid)
        current = process_inventory()
        exempt = {item["pid"] for item in self.original_processes if item["pid"] in administrative
                  and item["pid"] in current and current[item["pid"]]["started"] == item["started"]
                  and current[item["pid"]]["program"] == item["program"]}
        require_idle([item["program"] for pid, item in current.items() if pid not in exempt])

    def require_workers_stopped(self):
        if self.launchd.labels() & {item["label"] for item in self.switch.prior}:
            raise ProcessSurvivors("Original service registrations are still being removed")
        # A captured administrative CLI is still ours while it exits. Classify
        # its live identity before applying the unrelated-command idle gate.
        self.require_original_processes_stopped()
        programs = process_programs()
        require_idle(programs)
        outgoing = {Path(item["program"]).resolve() for item in self.switch.prior if item["label"] in BASE_SERVICES}
        if any(Path(program).name in {"Runner.Listener", "devcontainer-engine"} or Path(program).resolve() in outgoing
               for program in programs):
            raise ProcessSurvivors("An outgoing provider, runtime consumer or CI listener survived service removal")

    def require_original_processes_stopped(self, *, allow_registered=False):
        require_captured_processes_stopped(self.launchd, self.switch.prior if self.switch else [],
                                          self.original_processes, allow_registered=allow_registered)

    def verify(self):
        if self.service is None or require_api_service(self.executable) != self.service:
            raise ValueError("Selected API service changed during the case")

    def restore(self):
        if self.switch is None:
            return
        require_probe_stopped(self.journal.records())
        self.switch.restore(before_originals=lambda: wait_stopped(self.require_selected_stopped))

    def require_selected_stopped(self):
        install = self.executable.parent.parent
        unregistered = {Path(item["program"]).resolve() for item in self.switch.prior
                        if item["label"] in BASE_SERVICES and self.launchd.inspect(item["label"]) is None}
        if any(Path(program).is_relative_to(install) or Path(program).is_relative_to(self.root)
               or Path(program).resolve() in unregistered
               for program in process_programs()):
            raise ProcessSurvivors("Runtime processes survived service removal")
        self.require_original_processes_stopped(allow_registered=True)

    def receipt(self) -> dict:
        return self.journal.receipt() if self.journal is not None else {"status": "not-started"}

    def preserve_logs(self):
        """Keep bounded service diagnostics privately; they may contain secrets."""
        if self.journal is None:
            return
        probe_diagnostics(self.root, self.journal)
        # Stock SystemStart leaves stdio to launchd and supplies LogRoot for
        # service-owned file logging. launchd cannot open SSD stdio here
        # (EX_CONFIG), even when the selected process itself can use the disk.
        for name in ("container-apiserver.log", "container-core-images.log", "container-machine-apiserver.log"):
            path = self.root / "container-logs" / name
            if path.is_symlink() or path.parent.resolve() != path.parent:
                raise ValueError("Selected API log path changed")
            if not path.exists():
                continue
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                    raise ValueError("Selected API log ownership changed")
                stream.seek(max(0, info.st_size - 64 * 1024))
                self.journal.put("service-" + name, stream.read(64 * 1024))
