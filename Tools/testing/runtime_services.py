"""Own released-provider services and private credential HOME for qualification.

This adapter journals and restores provider service definitions and their
process-scoped environment. Callers own guest workloads and provider resources.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import tempfile
import time
from typing import Callable, Mapping

from host_runtime import deadline, require_api_service
from private_keychain import require_keychain_stopped
from runtime_probe import probe_api, probe_diagnostics, require_probe_stopped
from service_journal import ServiceJournal, digest
from service_switch import API, BASE_SERVICES, Launchd, ServiceSwitch, canonical_file, snapshot


PROVIDER_HELPER_LAYOUT = {
    "com.apple.container.container-core-images": (
        "container-core-images",
        "container-core-images",
        "com.apple.container.core.container-core-images",
    ),
    "com.apple.container.machine-apiserver": (
        "machine-apiserver",
        "machine-apiserver",
        "com.apple.container.core.machine-apiserver",
    ),
}
PROVIDER_HELPER_ENVIRONMENT = frozenset(
    {"CONTAINER_APP_ROOT", "CONTAINER_INSTALL_ROOT", "CONTAINER_LOG_ROOT"}
)
PROVIDER_HELPER_QUIESCENCE = {
    "status": "running",
    "containerCount": 0,
    "resourceCount": 0,
    "guestCount": 0,
    "clientCount": 0,
}
UNSCOPED_PROVIDER_GATEWAY = "io.github.stephenlclarke.container.engine"
PROVIDER_GATEWAY_SOCKET_ROOT = Path("/private/tmp")


def require_unscoped_provider_gateway_absent(launchd: Launchd, *, uid: int | None = None,
                                             temporary_root: Path | None = None) -> None:
    """Reject the fork's unscoped gateway label or public socket before switching services."""
    if launchd.inspect(UNSCOPED_PROVIDER_GATEWAY) is not None:
        raise ValueError("Unaccounted provider gateway prevents private API selection")
    temporary_root = temporary_root or PROVIDER_GATEWAY_SOCKET_ROOT
    owner = os.geteuid() if uid is None else uid
    if type(owner) is not int or owner < 0 or not temporary_root.is_absolute():
        raise ValueError("Provider gateway socket authority is invalid")
    if not temporary_root.exists() and not temporary_root.is_symlink():
        return
    if temporary_root.is_symlink() or temporary_root.resolve() != temporary_root or not temporary_root.is_dir():
        raise ValueError("Provider gateway temporary root is not canonical")
    gateway_root = temporary_root / f"container-engine-{owner}"
    if gateway_root.is_symlink():
        raise ValueError("Unaccounted provider gateway socket root exists")
    if gateway_root.exists():
        info = gateway_root.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != owner
                or stat.S_IMODE(info.st_mode) != 0o700 or gateway_root.resolve() != gateway_root):
            raise ValueError("Unaccounted provider gateway socket root exists")
    socket_path = gateway_root / "docker.sock"
    try:
        socket_path.lstat()
    except FileNotFoundError:
        return
    raise ValueError("Unaccounted provider gateway socket prevents private API selection")


def _require_canonical_owned_parent(path: Path, root: Path) -> None:
    """Reject symlinked helper paths and parents below the transaction root."""
    if (not root.is_absolute() or root.resolve() != root or root.is_symlink()
            or not root.is_dir() or not path.is_absolute() or not path.is_relative_to(root)
            or path.resolve() != path):
        raise ValueError("Generated provider helper path is not canonical under its owner")
    current = root
    for component in path.relative_to(root).parts[:-1]:
        current = current / component
        if current.is_symlink() or not current.is_dir() or current.resolve() != current:
            raise ValueError("Generated provider helper parent is unsafe")
    info = path.parent.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError("Generated provider helper parent is not privately owned")


def _validate_provider_helper_definition(definition: dict, label: str, path: Path,
                                        program: Path, provider_root: Path,
                                        runtime_root: Path) -> None:
    """Validate exact generated helper identity and its only admitted roots."""
    environment = definition.get("EnvironmentVariables")
    arguments = definition.get("ProgramArguments")
    directory = PROVIDER_HELPER_LAYOUT[label][0]
    mach_service = PROVIDER_HELPER_LAYOUT[label][2]
    if (definition.get("Label") != label
            or set(definition) != {"Label", "EnvironmentVariables", "LimitLoadToSessionType",
                                   "MachServices", "ProgramArguments", "RunAtLoad"}
            or not isinstance(arguments, list) or not arguments
            or arguments[0] != str(program)
            or definition.get("Program") is not None
            or not isinstance(environment, dict)
            or set(environment) != PROVIDER_HELPER_ENVIRONMENT
            or environment != {
                "CONTAINER_APP_ROOT": str(runtime_root / "container") + "/",
                "CONTAINER_INSTALL_ROOT": str(provider_root) + "/",
                "CONTAINER_LOG_ROOT": str(runtime_root / "container-logs"),
            }
            or definition.get("RunAtLoad") is not False
            or definition.get("LimitLoadToSessionType") != ["Aqua", "Background", "System"]
            or definition.get("MachServices") != {mach_service: True}
            or path.parts[-3:-1] != ("plugin-state", directory)
            or not path.name == "service.plist"):
        raise ValueError("Generated provider helper definition is not the admitted contract")


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
    expires = time.monotonic() + seconds
    with deadline(seconds):
        while True:
            try:
                probe()
                return
            except ProcessSurvivors:
                remaining = expires - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Owned runtime processes did not stop before the deadline")
                time.sleep(min(0.05, remaining))


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


def sha256_file(path: Path) -> str:
    """Hash an admitted immutable executable without retaining its contents."""
    digest_value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest_value.update(block)
    return digest_value.hexdigest()


def atomic_replace_private_file(path: Path, expected: bytes, replacement: bytes,
                                *, mode: int, owner: int) -> None:
    """Replace one checked file atomically inside its existing owned directory."""
    if (path.is_symlink() or path.resolve() != path or not path.parent.is_dir()
            or path.parent.is_symlink() or path.parent.resolve() != path.parent):
        raise ValueError("Generated service definition path is not canonical")
    current = canonical_file(path)
    if current != expected:
        raise ValueError("Generated service definition changed before replacement")
    info = path.lstat()
    parent_info = path.parent.stat()
    if (info.st_uid != owner or parent_info.st_uid != owner
            or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or parent_info.st_mode & 0o022):
        raise ValueError("Generated service definition ownership changed")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".service-definition-", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        if temporary.is_symlink() or temporary.resolve() != temporary:
            raise ValueError("Temporary service definition path changed")
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


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
    """Create the selected API service under the case's private runtime roots."""
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


def verify_selected_api(root: Path, executable: Path, expected_pid: int,
                        definition_sha256: str, launchd=None) -> dict:
    """Re-admit the exact private API service before released guest provisioning."""
    if (not root.is_absolute() or root.resolve() != root or root.is_symlink()
            or not root.is_dir() or root.stat().st_uid != os.getuid()
            or stat.S_IMODE(root.stat().st_mode) != 0o700):
        raise ValueError("Selected API HOME must be a canonical private owner directory")
    if (not executable.is_absolute() or executable.resolve() != executable or executable.is_symlink()
            or not executable.is_file() or not os.access(executable, os.X_OK)
            or type(expected_pid) is not int or expected_pid <= 0
            or re.fullmatch(r"[0-9a-f]{64}", definition_sha256) is None):
        raise ValueError("Selected API executable or process identity is invalid")
    path = root / "selected-apiserver.plist"
    info = path.lstat()
    payload = canonical_file(path)
    if (path.is_symlink() or path.resolve() != path or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o022
            or info.st_dev != root.stat().st_dev
            or hashlib.sha256(payload).hexdigest() != definition_sha256):
        raise ValueError("Selected API definition differs from its admitted private bytes")
    definition = plistlib.loads(payload)
    expected_environment = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(root),
        "TMPDIR": str(root), "TMP": str(root), "TEMP": str(root),
        "CONTAINER_APP_ROOT": str(root / "container"),
        "CONTAINER_INSTALL_ROOT": str(executable.parent.parent),
        "CONTAINER_LOG_ROOT": str(root / "container-logs"),
    }
    if (set(definition) != {"Label", "ProgramArguments", "EnvironmentVariables",
                            "RunAtLoad", "LimitLoadToSessionType", "MachServices"}
            or definition.get("Label") != API
            or definition.get("ProgramArguments") != [str(executable), "start"]
            or definition.get("EnvironmentVariables") != expected_environment
            or definition.get("RunAtLoad") is not True
            or definition.get("LimitLoadToSessionType") != ["Aqua", "Background", "System"]
            or definition.get("MachServices") != {API: True}):
        raise ValueError("Selected API definition is not the private runtime contract")
    launchd = launchd or Launchd()
    if launchd.inspect(API) != {"label": API, "path": str(path), "program": str(executable)}:
        raise ValueError("Selected API launchd registration differs from its private definition")
    if launchd.process_id(API) != expected_pid:
        raise ValueError("Selected API process differs from its admitted PID")
    service = require_api_service(executable)
    if service.get("pid") != expected_pid:
        raise ValueError("Selected API executable process differs from its admitted PID")
    process = process_inventory().get(expected_pid)
    if not isinstance(process, dict) or process.get("program") != str(executable):
        raise ValueError("Selected API PID no longer runs the admitted executable")
    return {"status": "running", "pid": expected_pid, "definitionSHA256": definition_sha256}


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
        self.original_api_file_metadata = None
        self.provider_helper_originals = None
        self.provider_helper_selected = None
        self.start_phase = "not-started"
        self.host_mutation_started = False

    def start(self, *, prepare_home=None):
        self.start_phase = "preflight"
        require_unscoped_provider_gateway_absent(self.launchd)
        self.start_phase = "service-snapshot"
        prior = snapshot(self.launchd, authorised_roots(self.launchd, self.home))
        api_original = next(item for item in prior if item["label"] == API)
        api_path = Path(api_original["path"])
        api_info = api_path.lstat()
        if (api_path.is_symlink() or api_path.resolve() != api_path
                or not stat.S_ISREG(api_info.st_mode) or api_info.st_uid != os.getuid()
                or api_info.st_nlink != 1):
            raise ValueError("Original API service definition metadata is unsafe")
        self.original_api_file_metadata = {
            "path": str(api_path),
            "mode": stat.S_IMODE(api_info.st_mode),
            "uid": api_info.st_uid,
            "atimeNS": api_info.st_atime_ns,
            "mtimeNS": api_info.st_mtime_ns,
            "device": api_info.st_dev,
            "inode": api_info.st_ino,
        }
        self.original_processes = capture_owned_processes(self.launchd, prior)
        self.require_idle_before_selection(prior)
        self.start_phase = "private-state-setup"
        definition = selected_definition(self.root, self.executable)
        path = self.journal_parent / (digest(str(self.root).encode()) + ".sqlite")
        self.journal = ServiceJournal(path, self.owner, create=True)
        self.journal.put("runtime-context.json", json.dumps(
            {"apiExecutable": str(self.executable)}, sort_keys=True).encode())
        self.journal.put("original-api-file-meta.json", json.dumps(
            self.original_api_file_metadata, sort_keys=True).encode())
        self.journal.put("original-processes.plist", plistlib.dumps(self.original_processes))
        self.switch = ServiceSwitch(self.launchd, prior, self.root, self.journal.put)
        self.require_idle_before_selection(prior)
        self.start_phase = "service-switch-prepare"
        self.host_mutation_started = True
        self.switch.prepare()
        # A listener/helper that survived removal may not overlap this lane.
        wait_stopped(self.require_workers_stopped)
        # The enhanced provider stores its handoff key during API startup.
        # Prepare the isolated HOME only after durable recovery and quiescence,
        # but before launchd can start any selected runtime consumer.
        if prepare_home is not None:
            self.start_phase = "private-keychain-setup"
            prepare_home(self.journal)
        self.start_phase = "selected-api-install"
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
        self.start_phase = "ready"

    def failure_disposition(self) -> dict:
        """Report whether this start call could have changed host services."""
        if self.host_mutation_started:
            return {"status": "uncertain", "phase": self.start_phase,
                    "hostMutationStarted": True}
        return {"status": "not-started", "phase": self.start_phase,
                "hostMutationStarted": False}

    def retain_primary_failure(self, error: BaseException) -> dict:
        """Retain typed bounded failure detail privately, even before journal creation."""
        message = str(error)
        truncated = len(message) > 8192
        payload = json.dumps({
            "schemaVersion": 1,
            "phase": self.start_phase,
            "hostMutationStarted": self.host_mutation_started,
            "exceptionType": f"{type(error).__module__}.{type(error).__qualname__}",
            "message": message[:8192],
            "messageTruncated": truncated,
        }, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        if self.journal is not None:
            try:
                self.journal.put("primary-runtime-failure.json", payload)
                return {"location": "private-journal", "sha256": hashlib.sha256(payload).hexdigest()}
            except BaseException:
                # Keep the exact primary exception available even if journal
                # retention itself failed; the owned case root remains private.
                pass
        path = self.root / "primary-runtime-failure.json"
        root_info = self.root.lstat()
        if (self.root.is_symlink() or self.root.resolve() != self.root
                or not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.getuid()
                or stat.S_IMODE(root_info.st_mode) != 0o700):
            raise ValueError("Private startup failure root changed")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                descriptor = -1
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            directory_fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        return {"location": "private-case-root", "sha256": hashlib.sha256(payload).hexdigest()}

    def propagate_provider_helper_home(
        self,
        provider_root: Path,
        expected_programs: Mapping[str, Mapping[str, str | Path]],
        require_quiescent: Callable[[], Mapping[str, object]],
    ) -> dict:
        """Restart only authenticated provider helpers with the case HOME."""
        if (self.journal is None or self.switch is None or self.service is None
                or self.provider_helper_originals is not None):
            raise ValueError("Provider helper HOME propagation is not ready")
        if (not provider_root.is_absolute() or provider_root.resolve() != provider_root
                or provider_root.is_symlink() or not provider_root.is_dir()):
            raise ValueError("Provider install root is not canonical")
        if set(expected_programs) != set(PROVIDER_HELPER_LAYOUT):
            raise ValueError("Trusted helper executable inventory is incomplete")

        selected = []
        original_payloads = {}
        selected_payloads = {}
        for label, (directory, executable_name, _mach_service) in PROVIDER_HELPER_LAYOUT.items():
            trusted = expected_programs[label]
            if set(trusted) != {"program", "sha256"}:
                raise ValueError("Trusted helper executable identity is malformed")
            program = Path(trusted["program"])
            expected_program = (
                provider_root / "libexec/container/plugins" / directory
                / "bin" / executable_name
            )
            if (program != expected_program or not program.is_absolute()
                    or program.resolve() != program or program.is_symlink()
                    or not program.is_file() or not os.access(program, os.X_OK)
                    or re.fullmatch(r"[0-9a-f]{64}", str(trusted["sha256"])) is None
                    or sha256_file(program) != trusted["sha256"]):
                raise ValueError("Provider helper executable differs from its admitted package")
            service_path = self.root / "container/plugin-state" / directory / "service.plist"
            _require_canonical_owned_parent(service_path, self.root)
            job = self.launchd.inspect(label)
            expected_job = {"label": label, "path": str(service_path), "program": str(program)}
            if job != expected_job:
                raise ValueError("Generated provider helper registration differs from its owned package")
            payload = canonical_file(service_path)
            definition = plistlib.loads(payload)
            _validate_provider_helper_definition(
                definition, label, service_path, program, provider_root, self.root
            )
            arguments = definition["ProgramArguments"]
            expected_arguments = [str(program), "start"]
            if label.endswith("machine-apiserver"):
                expected_arguments.extend([
                    "--resources",
                    str(provider_root / "libexec/container/plugins/machine-apiserver/resources"),
                ])
            if arguments != expected_arguments:
                raise ValueError("Generated provider helper arguments are not admitted")
            selected_definition = dict(definition)
            environment = dict(definition["EnvironmentVariables"])
            environment.update({"HOME": str(self.root), "TMPDIR": str(self.root),
                                "TMP": str(self.root), "TEMP": str(self.root)})
            selected_definition["EnvironmentVariables"] = environment
            selected_payload = plistlib.dumps(selected_definition)
            original_payloads[label] = payload
            selected_payloads[label] = selected_payload
            selected.append({
                "label": label,
                "path": str(service_path),
                "program": str(program),
                "payload": payload,
                "selectedPayload": selected_payload,
                "mode": stat.S_IMODE(service_path.stat().st_mode),
                "pid": self.launchd.process_id(label),
            })

        proof = require_quiescent()
        if (not isinstance(proof, Mapping)
                or set(proof) != set(PROVIDER_HELPER_QUIESCENCE)
                or proof.get("status") != "running"
                or any(type(proof.get(key)) is not int or proof[key] != 0
                       for key in ("containerCount", "resourceCount", "guestCount", "clientCount"))):
            raise ValueError("Provider helpers may change HOME only while the provider is empty")
        proof_payload = json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()
        self.journal.put("provider-home-quiescence.json", proof_payload)
        manifest = [{key: entry[key] for key in ("label", "path", "program", "mode")}
                    for entry in selected]
        self.journal.put("provider-helper-originals.plist", plistlib.dumps(manifest))
        for index, entry in enumerate(selected):
            self.journal.put(f"provider-helper-original-{index:04d}.plist", entry["payload"])
            self.journal.put(f"provider-helper-selected-{index:04d}.plist", entry["selectedPayload"])

        captured = capture_owned_processes(self.launchd, selected)
        self.provider_helper_originals = selected
        self.provider_helper_selected = selected_payloads
        for entry in selected:
            current = self.launchd.inspect(entry["label"])
            expected_job = {key: entry[key] for key in ("label", "path", "program")}
            if current != expected_job or canonical_file(Path(entry["path"])) != entry["payload"]:
                raise ValueError("Generated provider helper changed before restart")
            self.switch.record("bootout-provider-helper", entry["label"])
            self.launchd.bootout(entry["label"])
            if self.launchd.inspect(entry["label"]) is not None:
                raise ValueError("Provider helper remained registered after stop")
        wait_stopped(lambda: require_captured_processes_stopped(
            self.launchd, selected, captured
        ))
        for entry in selected:
            service_path = Path(entry["path"])
            atomic_replace_private_file(
                service_path, entry["payload"], entry["selectedPayload"],
                mode=entry["mode"], owner=os.getuid(),
            )
        for entry in selected:
            service_path = Path(entry["path"])
            self.switch.record("bootstrap-provider-helper", entry["label"])
            self.launchd.bootstrap(service_path)
            current = self.launchd.inspect(entry["label"])
            expected_job = {key: entry[key] for key in ("label", "path", "program")}
            payload = canonical_file(service_path)
            definition = plistlib.loads(payload)
            if (current != expected_job or payload != entry["selectedPayload"]
                    or definition.get("EnvironmentVariables", {}).get("HOME") != str(self.root)
                    or definition.get("EnvironmentVariables", {}).get("TMPDIR") != str(self.root)
                    or definition.get("EnvironmentVariables", {}).get("TMP") != str(self.root)
                    or definition.get("EnvironmentVariables", {}).get("TEMP") != str(self.root)):
                raise ValueError("Provider helper private HOME registration did not verify")
        return {"status": "private-home-ready", "helpers": len(selected)}

    def restore_provider_helper_definitions(self) -> dict:
        """Restore exact generated helper bytes before the selected runtime is removed."""
        if self.provider_helper_originals is None:
            return {"status": "not-needed", "helpers": 0}
        selected = self.provider_helper_originals
        still_registered = [entry for entry in selected
                            if self.launchd.inspect(entry["label"]) is not None]
        captured = capture_owned_processes(self.launchd, still_registered)
        for entry in selected:
            service_path = Path(entry["path"])
            current = self.launchd.inspect(entry["label"])
            if current is not None:
                expected_job = {key: entry[key] for key in ("label", "path", "program")}
                if (current != expected_job
                        or canonical_file(service_path) not in {
                            entry["payload"], entry["selectedPayload"]
                        }):
                    raise ValueError("Foreign provider helper prevents private HOME restoration")
                self.switch.record("bootout-provider-helper-selected", entry["label"])
                self.launchd.bootout(entry["label"])
                if self.launchd.inspect(entry["label"]) is not None:
                    raise ValueError("Private HOME provider helper remained registered")
        wait_stopped(lambda: require_captured_processes_stopped(
            self.launchd, selected, captured
        ))
        for entry in selected:
            service_path = Path(entry["path"])
            current_payload = canonical_file(service_path)
            if current_payload == entry["payload"]:
                continue
            if current_payload != entry["selectedPayload"]:
                raise ValueError("Generated provider helper definition changed before restoration")
            self.switch.record("restore-provider-helper-definition", entry["label"])
            atomic_replace_private_file(
                service_path, entry["selectedPayload"], entry["payload"],
                mode=entry["mode"], owner=os.getuid(),
            )
            if canonical_file(service_path) != entry["payload"]:
                raise ValueError("Generated provider helper definition restoration differs")
        self.provider_helper_originals = None
        self.provider_helper_selected = None
        return {"status": "restored", "helpers": len(selected)}

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
        if any(Path(program).resolve() in outgoing for program in programs):
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
        require_keychain_stopped(self.journal.records())
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
        # Keep stdio under launchd and retain bounded service-owned logs from
        # the private LogRoot. launchd cannot open SSD stdio here (EX_CONFIG),
        # even when the selected process itself can use the disk.
        directory = self.root / "container-logs"
        if directory.resolve() != directory:
            raise ValueError("Selected API log path changed")
        names = ["container-apiserver.log", "container-core-images.log", "container-machine-apiserver.log"]
        # Dedicated and shared-sandbox workers hold the exit/I/O diagnostics;
        # retain only bounded logs under this transaction's disposable root.
        if directory.exists():
            workers = []
            for path in directory.iterdir():
                if re.fullmatch(r"container-runtime-linux-[A-Za-z0-9_-]{1,128}\.log", path.name):
                    workers.append(path.name)
                    if len(workers) > 64:
                        raise ValueError("Too many selected worker logs")
            names.extend(sorted(workers))
        for name in names:
            path = directory / name
            if path.is_symlink() or path.parent.resolve() != path.parent:
                raise ValueError("Selected API log path changed")
            if not path.exists():
                continue
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                    raise ValueError("Selected API log ownership changed")
                stream.seek(max(0, info.st_size - 64 * 1024))
                key = "service-" + name
                if name.startswith("container-runtime-linux-"):
                    # UUID/shared names exceed the journal's 64-character key
                    # limit; retain the original name privately, never truncate it.
                    key = "worker-" + digest(name.encode())[:48]
                    self.journal.put(key + ".name", name.encode())
                    key += ".log"
                self.journal.put(key, stream.read(64 * 1024))
