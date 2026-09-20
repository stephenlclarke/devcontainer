"""Recover completed E04 Docker builds, never resubmit an unknown operation."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3

from build_images import BuildImages
from case_evidence import canonical, digest, validate_identity
from docker_vm import DockerVM, command_record, environment, pid_roles, private_root, scoped_processes, start_arguments, verify_pid_files
from guest_fixture import GuestFixture
from guest_runtime import GUEST_API_VERSION
from host_runtime import deadline
from released_docker import admit_docker
from runtime_services import require_idle


def recovery_inputs(retained: Path, ssd: Path, owner: dict) -> dict:
    """Reauthenticate the original release closure offline, not a newer runtime."""
    repository = Path(__file__).parents[2]
    names = ("docker-oracle.lock.json", "docker-cli.lock.json", "guest-images.lock.json")
    locks = [json.loads((repository / "Tools/bazel" / name).read_text()) for name in names]
    pins = json.loads((repository / "Tests/Parity/manifest.json").read_text())["referencePins"]["docker"]
    # The caller has already checked the private evidence file and owner record.
    with closing(sqlite3.connect((retained / "runtime-cases.sqlite").as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT bytes,sha256 FROM artifacts WHERE case_id=? AND name='admission.json'",
                         (validate_identity(owner["identity"]),)).fetchone()
    if row is None or digest(row[0]) != row[1]:
        raise ValueError("Docker recovery admission is missing or corrupt")
    inputs = admit_docker(locks[0], locks[1], pins, locks[2], ssd, retained,
                         fixture=owner["identity"]["fixture"], repository=repository)
    if canonical(inputs) != row[0]:
        raise ValueError("Docker recovery release inputs differ from the original case")
    return inputs


def verify_running(vm: DockerVM) -> None:
    """No command retries, unrelated processes, replaced tools or PID adoption."""
    private_root(vm.root, vm.owner)
    records = vm.journal.records()
    expected = {"owner": vm.owner, "tools": vm.tools, "environment": environment(vm.root, vm.tools),
                "start": start_arguments(vm.tools, vm.root, vm.owner["identity"]["fixture"]), "socket": str(vm.socket)}
    if records.get("docker-plan.json") != canonical(expected):
        raise ValueError("Docker recovery plan differs from its original admission")
    if "docker-vm-stop-intent.json" in records:
        raise ValueError("Interrupted Docker shutdown needs explicit reconciliation")
    current = vm.inventory()
    # A running owned VZ VM necessarily has a system XPC worker outside the
    # Lima process tree. Do not adopt or signal it; only the authenticated
    # Colima profile may stop its VM. Other client/runner work still blocks.
    require_idle([item["program"] for item in current.values()
                  if Path(item["program"]).name != "com.apple.Virtualization.VirtualMachine"])
    for name, data in records.items():
        if not command_record(name, "-intent.json"):
            continue
        stem = name.removesuffix("-intent.json")
        exited = json.loads(records.get(stem + "-exit.json", b"null"))
        process = json.loads(records.get(stem + "-process.json", b"null"))
        if (not isinstance(exited, dict) or type(exited.get("code")) is not int or
                (name.startswith("docker-") and exited["code"] != 0) or
                not isinstance(process, dict) or type(process.get("pid")) is not int or process["pid"] <= 0):
            raise ValueError("Docker recovery command lacks a successful durable exit")
        if any(item["pid"] == process["pid"] or item.get("group") == process["pid"] for item in current.values()):
            raise ValueError("Docker recovery command process remains")
    captured = json.loads(records.get("docker-vm-pids.json", b"null"))
    pids = verify_pid_files(vm.root, vm.tools, current)
    if set(pids) != set(pid_roles(vm.root, vm.tools)) or pids != captured:
        raise ValueError("Docker recovery PID ownership changed")
    previous = json.loads(records.get("docker-vm-processes.json", b"{}"))
    scoped = scoped_processes(vm.root, vm.tools, current)
    if not scoped or any(previous.get(pid) != item for pid, item in scoped.items()):
        raise ValueError("Docker recovery has an unrecorded or changed process")
    vm.verify()


def recover_completed_build(retained: Path, ssd: Path, owner: dict, guard, journal, *, apply: bool) -> dict:
    """Caller holds the family lease and has verified the exact case evidence."""
    key = validate_identity(owner["identity"])
    if owner["identity"]["fixture"] != "E04-image-build":
        raise ValueError("Docker VM has no verified shutdown receipt; reconciliation required")
    inputs = recovery_inputs(retained, ssd, owner)
    vm = DockerVM(Path(owner["root"]), owner, inputs["tools"], inputs["pins"], journal)
    workload = inputs["workload"]["image"]
    client = GuestFixture(vm.socket, key, workload["manifest"], GUEST_API_VERSION, journal)
    images = BuildImages(client, journal, key, workload["repository"] + "@" + workload["manifest"])
    with deadline(45):
        verify_running(vm)
        outputs = images.recovery_plan() + images.intermediate_plan()
    if not apply:
        return {"status": "ready-to-clean-completed-docker-build", "caseID": key,
                "changed": False, "images": outputs}
    # Revalidate at the mutation boundary. The sealed failed case is untouched;
    # only its separate private journal gains recovery receipts.
    with deadline(150):
        if json.loads(guard.path.read_bytes()) != owner or recovery_inputs(retained, ssd, owner) != inputs:
            raise ValueError("Docker recovery ownership or inputs changed")
        verify_running(vm)
        journal.put("e04-recovery-authorized.json", canonical({"caseID": key, "cleanupOnly": True}))
        images.recovery_plan()
        images.cleanup()
        vm.stop()
    # The common closed-VM path verifies shutdown again before root removal.
    from recover_runtime import recover_closed_docker
    return recover_closed_docker(retained, owner, guard, apply=True)


def recover_completed_devcontainer(retained: Path, ssd: Path, owner: dict, guard, journal, *, apply: bool) -> dict:
    """Reconcile completed CLI commands; never replay up/exec or alter results."""
    from devcontainer_reference import DevcontainerReference, FIXTURE
    from devcontainer_build_reference import DevcontainerBuildReference, FIXTURE as BUILD_FIXTURE
    from devcontainer_users_reference import DevcontainerUsersReference, FIXTURE as USERS_FIXTURE
    from devcontainer_lifecycle_reference import DevcontainerLifecycleReference, FIXTURE as LIFECYCLE_FIXTURE
    from devcontainer_features_reference import DevcontainerFeaturesReference, FIXTURE as FEATURES_FIXTURE
    from devcontainer_ports_reference import DevcontainerPortsReference, FIXTURE as PORTS_FIXTURE
    from devcontainer_reuse_reference import DevcontainerReuseReference, FIXTURE as REUSE_FIXTURE
    from devcontainer_compose_reference import DevcontainerComposeReference, FIXTURE as COMPOSE_FIXTURE
    from devcontainer_dependencies_reference import DevcontainerDependenciesReference, FIXTURE as DEPENDENCIES_FIXTURE
    from devcontainer_resources_reference import DevcontainerResourcesReference, FIXTURE as RESOURCES_FIXTURE
    selected = owner["identity"]["fixture"]
    if selected not in {FIXTURE, BUILD_FIXTURE, USERS_FIXTURE, LIFECYCLE_FIXTURE, FEATURES_FIXTURE, PORTS_FIXTURE, REUSE_FIXTURE, COMPOSE_FIXTURE, DEPENDENCIES_FIXTURE, RESOURCES_FIXTURE}:
        raise ValueError("Not a devcontainer recovery transaction")
    key = validate_identity(owner["identity"])
    inputs = recovery_inputs(retained, ssd, owner)
    vm = DockerVM(Path(owner["root"]), owner, inputs["tools"], inputs["pins"], journal)
    adapter = {FIXTURE: DevcontainerReference, BUILD_FIXTURE: DevcontainerBuildReference,
               USERS_FIXTURE: DevcontainerUsersReference, LIFECYCLE_FIXTURE: DevcontainerLifecycleReference,
               FEATURES_FIXTURE: DevcontainerFeaturesReference, PORTS_FIXTURE: DevcontainerPortsReference,
               REUSE_FIXTURE: DevcontainerReuseReference, COMPOSE_FIXTURE: DevcontainerComposeReference,
               DEPENDENCIES_FIXTURE: DevcontainerDependenciesReference, RESOURCES_FIXTURE: DevcontainerResourcesReference}[selected]
    fixture = adapter(vm, inputs, owner)
    with deadline(45):
        verify_running(vm)
        identifier = fixture.recovery_plan()
    if not apply:
        return {"status": "ready-to-clean-completed-devcontainer", "caseID": key,
                "changed": False, "container": identifier}
    with deadline(150):
        if json.loads(guard.path.read_bytes()) != owner or recovery_inputs(retained, ssd, owner) != inputs:
            raise ValueError("D01 recovery ownership or inputs changed")
        verify_running(vm)
        journal.put(selected.split("-", 1)[0].lower() + "-recovery-authorized.json",
                    canonical({"caseID": key, "cleanupOnly": True}))
        fixture.remove_owned()
        vm.stop()
    from recover_runtime import recover_closed_docker
    return recover_closed_docker(retained, owner, guard, apply=True)
