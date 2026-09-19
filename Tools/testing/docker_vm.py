"""An isolated released Docker oracle; all VM mutations require the family lease."""

from __future__ import annotations

from contextlib import nullcontext
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import time

from case_evidence import canonical
from engine_probe import request
from guest_runtime import diagnostic_snapshot
from runtime_services import process_inventory, require_idle


PROFILE = "parity"
INSTANCE = "colima-" + PROFILE
DEVCONTAINER_COMMANDS = {"devcontainer-image-pull", "devcontainer-up", "devcontainer-exec", "devcontainer-frozen-lock"}


def command_record(name: str, suffix: str) -> bool:
    """One command inventory for retention, process recovery and root disposal."""
    return name.endswith(suffix) and (name.startswith("docker-") or name.removesuffix(suffix) in DEVCONTAINER_COMMANDS)


def private_root(root: Path, owner: dict) -> None:
    info = root.lstat()
    marker = root / "owner.json"
    if (root.resolve() != root or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
            stat.S_IMODE(info.st_mode) != 0o700 or owner.get("root") != str(root) or
            marker.resolve() != marker or not marker.is_file() or marker.stat().st_nlink != 1 or
            marker.stat().st_uid != os.getuid() or marker.stat().st_mode & 0o022 or
            json.loads(marker.read_text()) != owner):
        raise ValueError("Docker oracle root ownership changed")


def environment(root: Path, tools: dict) -> dict[str, str]:
    """Do not inherit contexts, credentials, caches, shell hooks or SSH agents."""
    binaries = [Path(tools[key]) for key in ("colima", "limactl", "docker")]
    if any(not path.is_absolute() or path.resolve() != path or not path.is_file() for path in binaries):
        raise ValueError("Docker oracle requires canonical prepared executables")
    return {"PATH": ":".join([str(path.parent) for path in binaries] + ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
            "HOME": str(root), "TMPDIR": str(root / "tmp"), "TMP": str(root / "tmp"), "TEMP": str(root / "tmp"),
            "COLIMA_HOME": str(root / "colima"), "LIMA_HOME": str(root / "lima"),
            "COLIMA_CACHE_HOME": str(root / "cache/colima"), "DOCKER_CONFIG": str(root / "docker"),
            "XDG_CONFIG_HOME": str(root / "config"), "XDG_CACHE_HOME": str(root / "cache"),
            "XDG_RUNTIME_DIR": str(root / "run"), "LANG": "en_US.UTF-8", "TERM": "dumb"}


def start_arguments(tools: dict, root: Path) -> list[str]:
    return [tools["colima"], "start", PROFILE, "--vm-type=vz", "--arch=aarch64", "--cpus=2", "--memory=2",
            "--disk=10", "--root-disk=10", "--runtime=docker", "--disk-image", tools["disk-image"],
            "--force-disk-image=false", "--activate=false", "--template=false", "--ssh-config=false",
            "--ssh-agent=false", "--binfmt=false", "--vz-rosetta=false", "--kubernetes=false",
            "--network-address=false", "--network-host-addresses=false", "--mount-inotify=false",
            "--mount", str(root / "workspace") + ":w", "--mount-type=virtiofs", "--port-forwarder=none"]


def scoped_processes(root: Path, tools: dict, inventory: dict) -> dict:
    """Include escaped SSH/usernet helpers, without retaining process arguments."""
    output = subprocess.run(["/bin/ps", "-ww", "-axo", "pid=,args="], capture_output=True, check=True, timeout=5,
                            env={"PATH": "/usr/bin:/bin"}).stdout
    if len(output) > 8 * 1024**2:
        raise ValueError("Host process arguments exceed the inventory bound")
    owned = {pid for pid, item in inventory.items() if item["program"] in {tools["colima"], tools["limactl"]}}
    for line in output.decode().splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) == 2 and fields[0].isdigit() and str(root) + "/" in fields[1]:
            pid = int(fields[0])
            if pid != os.getpid() and pid in inventory:
                owned.add(pid)
    while True:
        expanded = owned | {pid for pid, item in inventory.items() if item["parent"] in owned}
        if expanded == owned:
            return {str(pid): inventory[pid] for pid in sorted(owned)}
        owned = expanded


def process_arguments(pid: int) -> str:
    result = subprocess.run(["/bin/ps", "-ww", "-p", str(pid), "-o", "args="], check=True,
                            capture_output=True, timeout=5, env={"PATH": "/usr/bin:/bin"})
    if len(result.stdout) > 65536 or len(result.stdout.splitlines()) != 1:
        raise ValueError("Docker VM process arguments are unavailable or ambiguous")
    return result.stdout.decode().strip()


def pid_roles(root: Path, tools: dict) -> dict[str, str]:
    """Exact pinned Lima roles, not a process that merely mentions our root.

    The released built-in VZ driver runs in the hostagent process. Its second
    PID file must therefore name the very same process incarnation.
    """
    instance = root / "lima" / INSTANCE
    network = root / "lima/_networks/user-v2"
    host = " ".join([tools["limactl"], "hostagent", "--pidfile", str(instance / "ha.pid"),
                     "--socket", str(instance / "ha.sock"), "--guestagent", tools["guest-agent"]])
    host_pattern = re.escape(host) + r"(?: --progress)? " + INSTANCE
    usernet = " ".join([tools["limactl"], "usernet", "-p", str(network / "usernet_user-v2.pid"),
                        "-e", str(network / "user-v2_ep.sock"), "--listen-qemu", str(network / "user-v2_qemu.sock"),
                        "--listen", str(network / "user-v2_fd.sock"), "--subnet"])
    # The subnet is private generated configuration, not authority for ownership.
    network_pattern = re.escape(usernet) + r" [0-9.]+/[0-9]+(?: --leases [0-9a-fA-F:.,=]+)?"
    return {"lima/" + INSTANCE + "/ha.pid": host_pattern,
            "lima/" + INSTANCE + "/vz.pid": host_pattern,
            "lima/_networks/user-v2/usernet_user-v2.pid": network_pattern}


def verify_pid_files(root: Path, tools: dict, inventory: dict, *, arguments=process_arguments) -> dict:
    """Colima/Lima stop may signal PID files; authenticate them before calling it."""
    values, roles = {}, pid_roles(root, tools)
    for path in sorted((root / "lima").rglob("*.pid")):
        name = path.relative_to(root).as_posix()
        info = path.lstat()
        if (name not in roles or path.resolve() != path or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_nlink != 1 or info.st_size > 32 or info.st_mode & 0o022):
            raise ValueError("Unsafe Docker VM PID file")
        value = path.read_text().strip()
        if not value.isdigit() or int(value) <= 0 or int(value) not in inventory:
            raise ValueError("Docker VM PID file lacks a proven owned process")
        process = inventory[int(value)]
        if process["program"] != tools["limactl"] or re.fullmatch(roles[name], arguments(int(value))) is None:
            raise ValueError("Docker VM PID file does not match its exact owned process role")
        values[name] = process
    host, driver = (values.get("lima/" + INSTANCE + "/" + name) for name in ("ha.pid", "vz.pid"))
    if driver is not None and driver != host:
        raise ValueError("Docker VZ driver is not the authenticated hostagent")
    return values


def same_incarnation(first: dict, second: dict) -> bool:
    # Survives exec/reparenting; does not mistake PID reuse for a surviving VM.
    return first["pid"] == second["pid"] and first["started"] == second["started"]


def require_unreachable_socket(path: Path) -> None:
    """A listening peer is enough to refuse cleanup; never wait for HTTP data."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(1)
        try:
            connection.connect(str(path))
        except (ConnectionRefusedError, FileNotFoundError):
            return
    raise ValueError("Docker oracle socket is still reachable")


def require_closed_vm(root: Path, owner: dict, records: dict, *, inventory=None) -> None:
    """Admit cleanup recovery only after durable, verified VM shutdown.

    This never adopts a process, invokes Colima or signals a PID. An interrupted
    startup/shutdown still requires explicit reconciliation, not a guessed retry.
    """
    if records.get("docker-vm-closed.json") != canonical({"verifiedStopped": True}):
        raise ValueError("Docker VM has no verified shutdown receipt; reconciliation required")
    plan = json.loads(records.get("docker-plan.json", b"null"))
    if (not isinstance(plan, dict) or plan.get("owner") != owner or
            plan.get("socket") != str(root / "colima" / PROFILE / "docker.sock") or
            not isinstance(plan.get("tools"), dict)):
        raise ValueError("Docker recovery plan identity is missing or changed")
    tools = plan["tools"]
    if plan.get("environment") != environment(root, tools) or plan.get("start") != start_arguments(tools, root):
        raise ValueError("Docker recovery plan differs from the admitted runtime")
    current = (inventory or process_inventory)()
    require_idle([item["program"] for item in current.values()])
    if scoped_processes(root, tools, current):
        raise ValueError("Docker VM processes remain; preserving quarantine")
    for name, data in records.items():
        if name in {"docker-vm-processes.json", "docker-vm-stop-processes.json", "docker-vm-pids.json"}:
            for captured in json.loads(data).values():
                survivor = current.get(captured["pid"])
                if survivor is not None and same_incarnation(captured, survivor):
                    raise ValueError("Captured Docker VM process survived shutdown")
        elif command_record(name, "-intent.json"):
            stem = name.removesuffix("-intent.json")
            if stem + "-exit.json" not in records:
                raise ValueError("Docker VM command has no durable exit receipt")
            if stem in DEVCONTAINER_COMMANDS and (stem + ".log" not in records or stem + "-log.json" not in records):
                raise ValueError("D01 command diagnostics are not retained")
            if json.loads(data).get("separateOutput") and (stem + ".stderr.log" not in records or stem + "-stderr-log.json" not in records):
                raise ValueError("Separated command diagnostics are not retained")
            process = json.loads(records.get(stem + "-process.json", b"null"))
            if not isinstance(process, dict) or type(process.get("pid")) is not int or process["pid"] <= 0:
                raise ValueError("Docker VM command process record is missing or invalid")
            # Legacy command receipts have no start token. PID reuse therefore
            # blocks conservatively; it can never authorize a signal or deletion.
            if any(item["pid"] == process["pid"] or item.get("group") == process["pid"]
                   for item in current.values()):
                raise ValueError("Recorded Docker command or process group remains")
    require_unreachable_socket(Path(plan["socket"]))


def require_socket_paths(root: Path):
    if len(os.fsencode(root / "lima" / INSTANCE / "ssh.sock.0123456789abcdef")) >= 104:
        raise ValueError("Docker oracle root exceeds the macOS socket path limit")


def inspect_instance(payload: bytes, root: Path, *, allow_absent=False) -> dict | None:
    """The private Lima home must contain exactly the one owned instance."""
    values = [json.loads(line) for line in payload.splitlines() if line.strip()]
    if not values and allow_absent:
        return None
    if len(values) != 1 or not isinstance(values[0], dict):
        raise ValueError("Unexpected Docker oracle VM inventory")
    value = values[0]
    expected = {"name": INSTANCE, "dir": str(root / "lima" / INSTANCE), "arch": "aarch64", "vmType": "vz"}
    if any(value.get(key) != item for key, item in expected.items()) or value.get("errors"):
        raise ValueError("Docker oracle VM identity or health differs")
    if value.get("status") not in {"Running", "Stopped"}:
        raise ValueError("Docker oracle VM has uncertain state")
    return {**expected, "status": value["status"]}


def verify_engine(socket: Path, pins: dict) -> dict:
    status, data = request(socket, "GET", "/version")
    version = json.loads(data)
    expected = {"Version": pins["engineVersion"], "GitCommit": pins["engineCommit"], "ApiVersion": pins["engineApiVersion"]}
    if status != 200 or not isinstance(version, dict) or any(version.get(key) != value for key, value in expected.items()):
        raise ValueError("Running Docker Engine differs from the pinned oracle")
    if version.get("Os") != "linux" or version.get("Arch") != "arm64":
        raise ValueError("Docker oracle must be the released Linux ARM64 engine")
    return {key: version[key] for key in (*expected, "Os", "Arch")}


class DockerVM:
    """Own one disposable Colima profile, never an operator-installed instance.

    The caller holds the family runtime lease, starts quarantine before calling
    configure(), and clears it only after shutdown, retention and root removal.
    A missing command receipt is uncertainty, not permission to retry startup.
    """

    def __init__(self, root: Path, owner: dict, tools: dict, pins: dict, journal, *, inventory=process_inventory):
        self.root, self.owner, self.tools, self.pins, self.journal = root, owner, tools, pins, journal
        self.inventory = inventory
        self.env = environment(root, tools)
        self.socket = root / "colima" / PROFILE / "docker.sock"
        self.processes = []
        self.uncertain = False

    def configure(self):
        private_root(self.root, self.owner)
        require_socket_paths(self.root)
        if self.journal.records().get("docker-plan.json") is not None:
            raise ValueError("Existing Docker VM intent requires recovery, not another start")
        current = self.inventory()
        require_idle([item["program"] for item in current.values()])
        if any(Path(item["program"]).name in {"limactl", "gvproxy", "qemu-system-aarch64"} for item in current.values()):
            raise ValueError("Another VM prevents Docker oracle admission")
        plan = {"owner": self.owner, "tools": self.tools, "environment": self.env,
                "start": start_arguments(self.tools, self.root), "socket": str(self.socket)}
        self.journal.put("docker-plan.json", canonical(plan))
        for name in ("tmp", "colima", "lima", "docker", "config", "cache", "run", "workspace"):
            (self.root / name).mkdir(mode=0o700)
        config = self.root / "lima/_config"
        config.mkdir(mode=0o700)
        # Published guest bytes must not be modified by implicit apt downloads.
        # Dependency provisioning can validate the image, never install packages.
        override = {"provision": [{"mode": "dependency", "skipDefaultDependencyResolution": True,
                    "script": "#!/bin/sh\nset -eu\ncommand -v dockerd\ncommand -v iptables\ncommand -v tar\n"}]}
        (config / "override.yaml").write_bytes(canonical(override))
        self.journal.put("docker-lima-override.json", canonical(override))

    def command(self, name: str, arguments: list[str], *, timeout=60, separate_output=False) -> bytes:
        private_root(self.root, self.owner)
        if self.uncertain or name + "-intent.json" in self.journal.records():
            raise ValueError("Uncertain or repeated Docker VM command")
        self.journal.put(name + "-intent.json", canonical({"arguments": arguments, "timeout": timeout,
                                                         "separateOutput": separate_output}))
        started = time.monotonic_ns()
        path = self.root / (name + ".log")
        self.uncertain = True
        with path.open("xb") as output, ((self.root / (name + ".stderr.log")).open("xb")
                                        if separate_output else nullcontext(subprocess.STDOUT)) as errors:
            child = subprocess.Popen(arguments, cwd=self.root, env=self.env, stdin=subprocess.DEVNULL,
                                     stdout=output, stderr=errors, start_new_session=True, close_fds=True)
            self.processes.append(child)
            self.journal.put(name + "-process.json", canonical({"pid": child.pid}))
            try:
                code = child.wait(timeout=timeout)
            except BaseException:
                # Stop only this directly owned command group. Any escaped VM
                # process keeps quarantine until explicit verified VM recovery.
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=5)
                raise
        self.journal.put(name + "-exit.json", canonical({"code": code, "durationNS": time.monotonic_ns() - started}))
        self.uncertain = False
        payload, metadata = diagnostic_snapshot(path)
        stderr_truncated = (separate_output and
                            json.loads(diagnostic_snapshot(self.root / (name + ".stderr.log"))[1])["truncated"])
        if json.loads(metadata)["truncated"] or stderr_truncated:
            raise ValueError("Docker VM command output exceeded its bound")
        if code != 0:
            raise RuntimeError("Docker VM command failed; private diagnostic log retained")
        return payload

    def start(self):
        self.configure()
        self.command("docker-vm-start", start_arguments(self.tools, self.root), timeout=300)
        value = inspect_instance(self.command("docker-vm-ready", [self.tools["limactl"], "list", "--json"]), self.root)
        if value["status"] != "Running":
            raise ValueError("Docker VM did not become ready")
        self.journal.put("docker-vm-identity.json", canonical(value))
        processes = scoped_processes(self.root, self.tools, self.inventory())
        if not processes:
            raise ValueError("Running Docker VM has no authenticated process ownership")
        self.journal.put("docker-vm-processes.json", canonical(processes))
        pids = verify_pid_files(self.root, self.tools, self.inventory())
        if set(pids) != set(pid_roles(self.root, self.tools)):
            raise ValueError("Running Docker VM lacks its authenticated PID roles")
        self.journal.put("docker-vm-pids.json", canonical(pids))
        self.journal.put("docker-engine-version.json", canonical(verify_engine(self.socket, self.pins)))
        executable = self.command("docker-engine-digest", [self.tools["colima"], "--profile", PROFILE, "ssh", "--",
                                   "sha256sum", "/usr/bin/dockerd"])
        if executable.decode().split() != [self.pins["engineSHA256"], "/usr/bin/dockerd"]:
            raise ValueError("Docker Engine executable differs from the parity pin")

    def verify(self):
        private_root(self.root, self.owner)
        if "docker-engine-version.json" not in self.journal.records():
            raise ValueError("Docker Engine has not completed identity verification")
        if canonical(verify_engine(self.socket, self.pins)) != self.journal.records()["docker-engine-version.json"]:
            raise ValueError("Docker Engine identity changed")

    def stop(self):
        private_root(self.root, self.owner)
        records = self.journal.records()
        if self.uncertain:
            raise ValueError("Interrupted Docker VM command requires reconciliation")
        if "docker-vm-start-intent.json" not in records:
            return
        if "docker-vm-start-exit.json" not in records:
            raise ValueError("Docker VM startup has no durable exit receipt")
        if "docker-vm-pids.json" not in records:
            raise ValueError("Docker VM startup has no authenticated ownership capture; reconciliation required")
        inventory = self.inventory()
        current = scoped_processes(self.root, self.tools, inventory)
        pids = verify_pid_files(self.root, self.tools, inventory)
        captured = json.loads(records["docker-vm-pids.json"])
        if any(name not in captured or captured[name] != value for name, value in pids.items()):
            raise ValueError("Docker VM PID incarnation changed before shutdown")
        # Recheck identities after reading arguments/PID files, before Lima can signal.
        refreshed = self.inventory()
        if any(refreshed.get(value["pid"]) != value for value in pids.values()):
            raise ValueError("Docker VM process changed during shutdown admission")
        self.journal.put("docker-vm-stop-processes.json", canonical(current))
        self.command("docker-vm-stop", [self.tools["colima"], "stop", PROFILE], timeout=90)
        value = inspect_instance(self.command("docker-vm-stopped", [self.tools["limactl"], "list", "--json"]),
                                 self.root, allow_absent=True)
        if value is not None and value["status"] != "Stopped":
            raise ValueError("Docker VM remains running")
        groups = {child.pid for child in self.processes}
        remaining = self.inventory()
        captured_processes = {**json.loads(records.get("docker-vm-processes.json", b"{}")), **current}
        if (scoped_processes(self.root, self.tools, remaining) or
                any((str(pid) in captured_processes and same_incarnation(captured_processes[str(pid)], item)) or
                    item.get("group") in groups for pid, item in remaining.items())):
            raise ValueError("Docker VM processes remain; preserving quarantine")
        require_unreachable_socket(self.socket)
        self.retain_logs()
        self.journal.put("docker-vm-closed.json", canonical({"verifiedStopped": True}))

    def retain_logs(self):
        records = self.journal.records()
        for name in records:
            if command_record(name, "-exit.json"):
                stem = name.removesuffix("-exit.json")
                payload, metadata = diagnostic_snapshot(self.root / (stem + ".log"))
                self.journal.put(stem + ".log", payload)
                self.journal.put(stem + "-log.json", metadata)
                intent = json.loads(records.get(stem + "-intent.json", b"{}"))
                if intent.get("separateOutput"):
                    payload, metadata = diagnostic_snapshot(self.root / (stem + ".stderr.log"))
                    self.journal.put(stem + ".stderr.log", payload)
                    self.journal.put(stem + "-stderr-log.json", metadata)
