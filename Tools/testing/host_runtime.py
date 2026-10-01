"""Host lease and bounded owned-process lifetime for individual Bazel test cases."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import time

from case_evidence import canonical, digest


def cleanup_receipt(owner: dict, root: Path) -> bytes:
    """Bind interrupted root removal to its original filesystem incarnation."""
    info = root.stat()
    # Birth time survives partial deletion; ctime does not. Include it to reject
    # replacement directories even if an inode is later reused.
    identity = {"device": info.st_dev, "inode": info.st_ino,
                "birthtimeNS": getattr(info, "st_birthtime_ns", int(info.st_birthtime * 1e9))}
    return canonical({"ownerSHA256": digest(canonical(owner)), "rootIdentity": identity})


def require_api_service(executable: Path) -> dict:
    """Reject inherited/mixed providers before a released case touches XPC.

    Stock Apple uses this fixed per-user service name, irrespective of HOME.
    Retain only identity fields, never launchctl's environment or full output.
    This admission check does not install, start, stop or repair any service.
    """
    if not executable.is_absolute() or executable.resolve() != executable:
        raise ValueError("API service executable must be a canonical released path")
    service = f"gui/{os.getuid()}/com.apple.container.apiserver"
    result = subprocess.run(["/bin/launchctl", "print", service], capture_output=True, timeout=5,
                            env={"PATH": "/usr/bin:/bin"}, check=False)
    if result.returncode != 0:
        raise ValueError("Selected released API service is not registered")
    output = result.stdout.decode("utf-8", errors="strict")
    def field(key):
        values = re.findall(r"^\t" + key + r" = ([^\n]+)$", output, re.MULTILINE)
        if len(values) != 1:
            raise ValueError("Selected released API service identity is incomplete")
        return values[0]

    program = field("program")
    if program != str(executable):
        raise ValueError("Registered API service is not the selected released provider")
    if field("state") != "running":
        raise ValueError("Selected released API service is not running")
    pid = field("pid")
    if not pid.isdigit() or int(pid) <= 0:
        raise ValueError("Selected released API service is not running")
    # launchd reports running while xpcproxy is still preparing exec. Its
    # configured program is not evidence that the selected binary has started.
    process = subprocess.run(["/bin/ps", "-p", pid, "-o", "comm="], capture_output=True, timeout=5,
                             env={"PATH": "/usr/bin:/bin"}, check=False)
    if process.returncode != 0 or process.stdout.decode("utf-8", errors="strict").strip() != str(executable):
        raise ValueError("Selected released API executable has not started")
    return {"service": service, "program": program, "pid": int(pid)}


@contextmanager
def runtime_lease(path: Path, guard=None):
    """Share Compose's flock/lockf inode; never unlink it or inherit ownership flags."""
    if not path.is_absolute() or path.parent.resolve() != path.parent:
        raise ValueError("Runtime lease requires a canonical parent")
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o022:
            raise ValueError("Runtime lease is not a private single-owner file")
        # A busy runtime is an admission failure, not a test execution or timeout.
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if guard is not None:
            guard.check()
        yield
    finally:
        os.close(descriptor)


class HostGuard:
    """Durable quarantine spans cases/campaigns and survives worker termination."""

    def __init__(self, path: Path):
        if path.parent.resolve() != path.parent:
            raise ValueError("Runtime admission guard requires canonical storage")
        self.path = path

    def sync_directory(self):
        descriptor = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def check(self):
        if self.path.exists() or self.path.is_symlink():
            raise ValueError("Runtime is quarantined: reconcile the previous owned case before admission")

    def begin(self, owner: dict):
        with self.path.open("x") as output:
            json.dump(owner, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        self.sync_directory()

    def clear(self, owner: dict):
        if self.path.is_symlink() or json.loads(self.path.read_text()) != owner:
            raise ValueError("Runtime quarantine ownership changed")
        self.path.unlink()
        self.sync_directory()


@contextmanager
def deadline(seconds: float):
    """Bound a whole phase, including a peer that drips data before socket timeout."""
    if seconds <= 0 or signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise ValueError("Deadline requires positive duration and no existing alarm")

    def expired(_number, _frame):
        raise TimeoutError("Host case phase exceeded its deadline")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@contextmanager
def cancellation():
    """Let Bazel cancellation enter the ordinary cleanup-and-seal path."""
    def interrupted(_number, _frame):
        raise KeyboardInterrupt()

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


class OwnedProcess:
    def __init__(self):
        self.process = None
        self.spawn_pending = False

    def start(self, arguments: list[str], root: Path, output, *, provider_install: Path | None = None,
              errors=None, stdin=subprocess.DEVNULL, runtime_socket: Path | None = None) -> None:
        if self.process is not None or self.spawn_pending:
            raise ValueError("Case already owns a process")
        environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(root),
                       "TMPDIR": str(root), "TMP": str(root), "TEMP": str(root), "LANG": "en_US.UTF-8"}
        if provider_install is not None:
            if not provider_install.is_absolute() or provider_install.resolve() != provider_install:
                raise ValueError("Provider install root must be canonical")
            environment.update(CONTAINER_APP_ROOT=str(root / "container"),
                               CONTAINER_INSTALL_ROOT=str(provider_install),
                               CONTAINER_LOG_ROOT=str(root / "container-logs"))
        if runtime_socket is not None:
            if not runtime_socket.is_absolute() or runtime_socket.resolve() != runtime_socket:
                raise ValueError("Runtime socket must be canonical")
            environment.update(DOCKER_HOST="unix://" + str(runtime_socket),
                               CONTAINER_COMPOSE_ENGINE_SOCKET=str(runtime_socket))
            if provider_install is not None:
                container = str(provider_install / "bin/container")
                environment.update(CONTAINER_COMPOSE_CONTAINER=container, CONTAINER_BIN=container)
        # Popen can be interrupted after fork but before returning the handle.
        # An uncertain launch is quarantined, never interpreted as no child.
        self.spawn_pending = True
        self.process = subprocess.Popen(
            arguments, cwd=root, stdin=stdin, stdout=output,
            stderr=subprocess.STDOUT if errors is None else errors,
            start_new_session=True, close_fds=True,
            env=environment)
        self.spawn_pending = False

    def identity(self) -> dict:
        """Capture an incarnation while the original, unreaped child handle exists."""
        from runtime_services import process_inventory
        if self.process is None or self.process.poll() is not None:
            raise ValueError("Cannot capture an absent or exited child")
        pid = self.process.pid
        value = process_inventory().get(pid)
        if (value is None or value["parent"] != os.getpid() or value["group"] != pid or
                value["program"] != self.process.args[0] or self.process.poll() is not None):
            raise ValueError("Child incarnation changed during capture")
        return dict(value, arguments=list(self.process.args))

    def wait_ready(self, probe, seconds: float = 20) -> None:
        if self.process is None:
            raise ValueError("No process has been started")
        end = time.monotonic() + seconds
        with deadline(seconds):
            while time.monotonic() < end:
                if self.process.poll() is not None:
                    raise RuntimeError("Released service exited before readiness")
                try:
                    if probe():
                        return
                except (ConnectionError, FileNotFoundError):
                    # Readiness only: no conformance operation is retried here.
                    pass
                time.sleep(0.02)
        raise TimeoutError("Released service did not become ready")

    def stop(self, seconds: float = 5) -> None:
        if self.process is None:
            if self.spawn_pending:
                raise RuntimeError("Process creation was interrupted; child ownership is uncertain")
            return
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                # The child can exit between poll and signal; it is still ours
                # to reap, not a reason to report nonexistent leaked resources.
                pass
            try:
                self.process.wait(timeout=seconds)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=seconds)
        # Descendants may outlive their leader. Never report verified cleanup
        # while they exist; the durable host guard then blocks later admission.
        try:
            os.killpg(self.process.pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            # The macOS test sandbox can reject a probe of descendants after
            # their leader exits. This is not absence: let bounded cleanup wait
            # for ESRCH without signalling an already-reaped process group.
            raise RuntimeError("Owned service process group disappearance is not yet verifiable") from None
        raise RuntimeError("Owned service process group still has descendants")
