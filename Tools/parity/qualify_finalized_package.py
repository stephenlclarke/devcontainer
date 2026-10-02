#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# SPDX-License-Identifier: Apache-2.0
"""Qualify one finalized native package through the maintained 84-observation parity suite."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time


REPOSITORY = Path(__file__).resolve().parents[2]
ACCOUNT_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
DEFAULT_SSD_ROOT = Path("/Volumes/SSD/cf/bazel")
DEFAULT_RETAINED = ACCOUNT_HOME / "Library/Application Support/ContainerFamily/retained/devcontainer"
DEFAULT_WORKFLOW_RETAINED = ACCOUNT_HOME / "Library/Application Support/ContainerFamily/retained/workflow"
SSD = DEFAULT_SSD_ROOT
RETAINED = DEFAULT_RETAINED
JOURNAL_PARENT = RETAINED / "runtime-journals"
GUARD_PATH = DEFAULT_WORKFLOW_RETAINED / "runtime-admission.json"
LEASE_PATH = Path(f"/private/tmp/container-compose-runtime-{os.getuid()}.lock")
LANES = ("docker", "apple-stock", "container-compose")
COMPONENT_FIXTURE = "E13-compose-signals"
PARITY_HARNESS = (
    "Tools/parity/run_lane.py", "Tools/parity/run_vscode.py",
    "Tools/parity/compare_results.py", "Tools/parity/parity_lib.py",
    "Tools/parity/engine_fixture_routes.py", "Tools/parity/owned_guest_fixture.py",
    "Tools/parity/run_engine_fixture.py", "Tools/parity/docker_api.py",
    "Tools/parity/vscode-driver-extension/extension.js",
    "Tools/parity/vscode-driver-extension/package.json",
    "Tools/release/prepare_finalized_package.py",
    "Tools/testing/guest_runtime.py", "Tools/testing/guest_fixture.py",
    "Tools/testing/released_engine.py",
    "Tools/testing/service_journal.py", "Tools/testing/service_switch.py",
    "Tools/testing/case_evidence.py", "Tools/testing/host_runtime.py",
    "Tools/testing/lifecycle_probe.py", "Tools/testing/exec_probe.py",
    "Tools/testing/archive_probe.py", "Tools/testing/network_volume_probe.py",
    "Tools/testing/build_fixture.py", "Tools/testing/build_runtime.py",
    "Tools/testing/fault_probe.py", "Tools/testing/attachment_probe.py",
    "Tools/testing/foreground_probe.py", "Tools/testing/initial_terminal_probe.py",
    "Tools/testing/compose_foreground_probe.py", "Tools/testing/compose_terminal_probe.py",
    "Tools/testing/json_file_oracle.py", "Tools/testing/private_keychain.py",
    "Tools/testing/engine_probe.py", "Tools/bazel/prepare_guest_images.py",
    "Tools/bazel/prepare_releases.py", "Tools/bazel/release_inputs.py",
    "Tools/bazel/oci_image_layout.py",
    "Tools/testing/build_images.py", "Tools/testing/build_probe.py",
    "Tools/testing/runtime_services.py", "Tools/testing/runtime_probe.py",
    "Tools/testing/background_items.py", "Tools/testing/devcontainer_candidate.py",
    "Tools/testing/devcontainer_build_reference.py",
    "Tools/testing/devcontainer_compose_reference.py",
    "Tools/testing/devcontainer_dependencies_reference.py",
    "Tools/testing/devcontainer_features_reference.py",
    "Tools/testing/devcontainer_lifecycle_reference.py",
    "Tools/testing/devcontainer_ports_reference.py",
    "Tools/testing/devcontainer_reference.py",
    "Tools/testing/devcontainer_resources_reference.py",
    "Tools/testing/devcontainer_reuse_reference.py",
    "Tools/testing/devcontainer_users_reference.py",
)
CONTROLLER = Path(__file__).resolve()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_enrolled_ssd(args: argparse.Namespace) -> None:
    """Read the maintained enrolled-volume identity before creating any SSD evidence."""
    consumer_path = args.repository / "Tools/release/consume-native-finalized-package.py"
    spec = importlib.util.spec_from_file_location("native_parity_ssd_consumer", consumer_path)
    if spec is None or spec.loader is None:
        raise ValueError("maintained SSD identity verifier is unavailable")
    consumer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(consumer)
    identity = consumer.SSD_IDENTITY
    if not identity.is_file() or identity.is_symlink() or identity.stat().st_uid != os.getuid():
        raise ValueError("trusted enrolled SSD identity file is missing or unsafe")
    expected_uuid = identity.read_text(encoding="ascii").strip().upper()
    result = subprocess.run(["/usr/sbin/diskutil", "info", "-plist", "/Volumes/SSD"],
                            capture_output=True, timeout=10,
                            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}, check=True)
    consumer.validate_ssd_identity(expected_uuid, result.stdout)
    if not args.ssd_root.is_relative_to(Path("/Volumes/SSD")):
        raise ValueError("SSD evidence root must remain beneath the enrolled /Volumes/SSD mount")


def validate_guest_asset_retained_root(root: Path) -> Path:
    """Keep immutable guest archives separate from finalized-package receipts."""
    if (not root.is_absolute() or root.resolve(strict=True) != root or not root.is_dir()
            or root.stat().st_dev != ACCOUNT_HOME.stat().st_dev or root.stat().st_uid != os.getuid()
            or root.stat().st_mode & 0o777 != 0o700):
        raise ValueError("guest image and kernel inputs must use private internal workflow storage")
    return root


def load_run_lane(repository: Path):
    """Load the selected checkout's run_lane module by its exact source path."""
    path = repository / "Tools/parity/run_lane.py"
    spec = importlib.util.spec_from_file_location("native_qualification_run_lane", path)
    if spec is None or spec.loader is None:
        raise ValueError("maintained parity runner is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if Path(module.__file__).resolve() != path.resolve(strict=True):
        raise ValueError("parity helper resolved outside the selected repository")
    return module


def load_finalized_package_helper(repository: Path):
    """Load only the provenance reader from the selected maintained checkout."""
    path = repository / "Tools/release/prepare_finalized_package.py"
    spec = importlib.util.spec_from_file_location("native_qualification_finalized_package", path)
    if spec is None or spec.loader is None:
        raise ValueError("maintained finalized-package helper is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if Path(module.__file__).resolve() != path.resolve(strict=True):
        raise ValueError("finalized-package helper resolved outside the selected repository")
    return module


def run(command: list[str], *, env: dict[str, str], cwd: Path = REPOSITORY,
        timeout: int = 60, capture: bool = False,
        check: bool = True) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(command, cwd=cwd, env=env, text=True,
                               stdout=subprocess.PIPE if capture else None,
                               stderr=subprocess.PIPE if capture else None,
                               start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        terminate_process_group(process)
        raise RuntimeError(f"{Path(command[0]).name} timed out after {timeout}s") from error
    except BaseException:
        terminate_process_group(process)
        raise
    result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "").strip()[-4000:]
        raise RuntimeError(f"{Path(command[0]).name} exited {result.returncode}: {detail}")
    return result


def command_outcome(command: list[str], *, env: dict[str, str], timeout: int,
                    capture_directory: Path) -> subprocess.CompletedProcess[str]:
    """Run one maintained suite, preserving ordinary failure for later cleanup."""
    capture_directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    stdout_path = capture_directory / "stdout.log"
    stderr_path = capture_directory / "stderr.log"
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        process = subprocess.Popen(command, cwd=REPOSITORY, env=env, text=True,
                                   stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            terminate_process_group(process)
            write_json(capture_directory / "controller-command.json", {
                "executable": Path(command[0]).name, "exitCode": process.returncode,
                "timedOut": True, "timeoutSeconds": timeout})
            raise RuntimeError(f"{Path(command[0]).name} timed out after {timeout}s") from error
        except BaseException:
            terminate_process_group(process)
            raise
    outcome = subprocess.CompletedProcess(command, process.returncode, "", "")
    write_json(capture_directory / "controller-command.json", {
        "executable": Path(command[0]).name, "exitCode": outcome.returncode,
        "timedOut": False, "stdoutBytes": stdout_path.stat().st_size,
        "stderrBytes": stderr_path.stat().st_size})
    return outcome


def docker_buildx_version(path: Path) -> str:
    """Read the explicitly admitted Buildx executable's version without invoking Docker."""
    result = run([str(path), "version"], env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                 timeout=10, capture=True)
    matches = re.findall(r"(?:^|\s)v(\d+\.\d+\.\d+)(?:\s|$)", result.stdout)
    if len(matches) != 1:
        raise ValueError("Docker Buildx executable did not report one parseable semantic version")
    return matches[0]


def admit_docker_buildx(path: Path, pins: dict[str, str]) -> tuple[str, str]:
    """Authenticate Buildx bytes before executing its read-only version probe."""
    if (not path.is_absolute() or path.resolve(strict=True) != path
            or not path.is_file() or not os.access(path, os.X_OK)):
        raise ValueError("Docker Buildx must be an executable at a canonical absolute path")
    digest = sha256(path)
    if digest != pins.get("buildxSHA256"):
        raise ValueError("Docker Buildx bytes differ from the checked-in parity pins")
    version = docker_buildx_version(path)
    if version != pins.get("buildxVersion"):
        raise ValueError("Docker Buildx version differs from the checked-in parity pins")
    return version, digest


def run_suite_pair(cli_runner, vscode_runner, cli_cleanup, vscode_cleanup, *, component_fixture=None):
    """Run the full CLI/V01 pair, or the explicitly scoped CLI component only."""
    cli_result = cli_runner()
    if not cli_cleanup():
        raise RuntimeError("CLI cleanup is incomplete; V01 cannot safely start")
    if component_fixture is not None:
        if component_fixture != COMPONENT_FIXTURE:
            raise ValueError("Unsupported parity component fixture")
        return cli_result, None, cli_result.returncode == 0
    vscode_result = vscode_runner()
    if not vscode_cleanup():
        raise RuntimeError("V01 cleanup is incomplete")
    passed = cli_result.returncode == 0 and vscode_result.returncode == 0
    return cli_result, vscode_result, passed


def selected_fixture_environment(environment: dict[str, str], fixture: str | None) -> dict[str, str]:
    """Select one maintained CLI fixture only for the explicit component mode."""
    if fixture is None:
        return environment
    if fixture != COMPONENT_FIXTURE:
        raise ValueError("Unsupported parity component fixture")
    return {**environment, "DEVCONTAINER_PARITY_FIXTURES": fixture}


def compare_component_results(evidence: Path, fixture: str) -> dict:
    """Run the maintained exact comparator against the single requested fixture."""
    if fixture != COMPONENT_FIXTURE:
        raise ValueError("Unsupported parity component fixture")
    parity_directory = REPOSITORY / "Tools/parity"
    path = parity_directory / "compare_results.py"
    if path.resolve(strict=True) != path:
        raise ValueError("Parity comparator path is not canonical")
    sys.path.insert(0, str(parity_directory))
    try:
        spec = importlib.util.spec_from_file_location("native_component_compare_results", path)
        if spec is None or spec.loader is None:
            raise ValueError("Maintained parity comparator is unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        payload, matrix = module.compare(evidence, {fixture}, "cli")
    finally:
        sys.path.remove(str(parity_directory))
    write_json(evidence / "component-comparison.json", payload)
    (evidence / "component-matrix.md").write_text(matrix, encoding="utf-8")
    return payload


def write_component_result(evidence: Path, value: dict) -> Path:
    """Write a non-CAS component result without invoking qualification sealing."""
    path = evidence / "component-result.json"
    if path.exists() or path.is_symlink():
        raise ValueError("Component result already exists")
    descriptor_fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor_fd, "wb") as output:
        output.write(json.dumps(value, sort_keys=True, indent=2).encode() + b"\n")
        output.flush()
        os.fsync(output.fileno())
    return path


def component_result_payload(args: argparse.Namespace, cleanup: dict, comparison: dict,
                             host_payload: dict, provider_hashes: dict[str, str],
                             provider_inputs: dict, status: str,
                             failures: list[str]) -> dict:
    """Describe the bounded component run without qualification or publisher authority."""
    return {
        "schemaVersion": 1,
        "scope": "component-only",
        "status": status,
        "releaseAuthority": False,
        "sourceCommit": args.source_commit,
        "sourceTree": args._source_tree,
        "finalizationProvenanceSHA256": args.provenance_sha256,
        "trustedStateSHA256": args.state_sha256,
        "archiveSHA256": args._component_package_proof["archiveSHA256"],
        "fixture": COMPONENT_FIXTURE,
        "fixtureCounts": {"cliPerLane": 1, "vscodePerLane": 0,
                           "laneCount": 3, "totalLaneFixtureResults": 3},
        "vscodeStatus": "skipped",
        "comparison": {"status": comparison.get("status"),
                        "sha256": sha256(args.evidence / "component-comparison.json")
                        if (args.evidence / "component-comparison.json").is_file() else None},
        "providerSHA256": provider_hashes,
        "providerInputs": provider_inputs,
        "laneCleanup": {lane: cleanup[lane] for lane in LANES},
        "hostCleanup": {key: host_payload.get(key) for key in (
            "status", "initialColima", "finalColima", "initialServiceSetSHA256",
            "finalServiceSetSHA256", "hostGuardCleared", "restoration")},
        "failures": failures,
    }


def finalize_component_result(args: argparse.Namespace, cleanup: dict, comparison: dict,
                              host_payload: dict, provider_hashes: dict[str, str],
                              provider_inputs: dict, failures: list[str]) -> dict:
    """Persist component evidence after restoration; this path never seals a qualification."""
    complete = component_restoration_is_complete(cleanup, host_payload)
    recorded_failures = list(failures)
    if not complete and "component provider or host restoration is incomplete" not in recorded_failures:
        recorded_failures.append("component provider or host restoration is incomplete")
    if not component_comparison_is_passed(comparison, args.evidence):
        recorded_failures.append("E13 comparator evidence is incomplete or failed")
    passed = not recorded_failures
    payload = component_result_payload(
        args, cleanup, comparison, host_payload, provider_hashes, provider_inputs,
        "passed" if passed else "failed", recorded_failures)
    write_component_result(args.evidence, payload)
    return payload


def component_comparison_is_passed(comparison: dict, evidence: Path) -> bool:
    """Require the maintained comparator's exact single-fixture CLI result and file."""
    if (not isinstance(comparison, dict) or comparison.get("status") != "passed"
            or comparison.get("suite") != "cli"
            or comparison.get("expectedFixtures") != [COMPONENT_FIXTURE]
            or comparison.get("evidenceStatus") != "passed"
            or comparison.get("functionalParityStatus") != "passed"
            or comparison.get("timingStatus") != "passed"
            or comparison.get("requireZeroFunctionalDifferences") is not True):
        return False
    comparison_path = evidence / "component-comparison.json"
    try:
        return json.loads(comparison_path.read_text(encoding="utf-8")) == comparison
    except (OSError, ValueError):
        return False


def docker_stop_allowed(*, started_here: bool, suites_started: bool,
                        suite_cleanup_complete: bool) -> bool:
    """Stop only a controller-started profile after suites clean up or before suites begin."""
    return started_here and (not suites_started or suite_cleanup_complete)


def docker_restore_is_safe(*, started_here: bool, suites_started: bool,
                           suite_cleanup_complete: bool, initial: str, final: str) -> bool:
    """Require completed cleanup after suite mutation and exact Colima restoration."""
    return ((not suites_started or suite_cleanup_complete)
            and initial == final and (not started_here or final == "stopped"))


def require_host_restoration(cleanup: dict, initial_colima: str, final_colima: str,
                             guard_cleared: bool) -> None:
    """Fail closed unless all lanes and the host guard have been restored."""
    if any(cleanup.get(lane, {}).get("status") != "restored" for lane in LANES):
        raise RuntimeError("not all runtime lanes have complete restoration receipts")
    if initial_colima != final_colima or not guard_cleared:
        raise RuntimeError("host runtime state or admission guard was not restored")


def component_restoration_is_complete(cleanup: dict, host_payload: dict) -> bool:
    """Require three restored providers and an explicitly skipped V01 observation."""
    if set(cleanup) != set(LANES):
        return False
    for lane in LANES:
        row = cleanup[lane]
        if (row.get("status") != "restored" or row.get("cliCleanupComplete") is not True
                or row.get("vscodeCleanupComplete") is not False
                or row.get("vscodeStatus") != "skipped"):
            return False
    return (host_payload.get("status") == "restored"
            and host_payload.get("hostGuardCleared") is True
            and host_payload.get("initialColima") == host_payload.get("finalColima")
            and host_payload.get("initialServiceSetSHA256") == host_payload.get("finalServiceSetSHA256")
            and host_payload.get("initialServiceCount") == host_payload.get("finalServiceCount"))


def host_can_clear_guard(cleanup: dict, initial_colima: str, final_colima: str,
                         initial_services: str, final_services: str) -> bool:
    """Clear quarantine after exact restoration, including lanes never mutated."""
    return (initial_colima == final_colima and initial_services == final_services
            and all(cleanup.get(lane, {}).get("status") in {"restored", "not-started"}
                    for lane in LANES))


def validate_provider_fingerprint(path: Path, lane: str, expected: dict[str, str], manifest: dict) -> None:
    """Bind each maintained CLI/V01 observation to the selected provider bytes."""
    fingerprint = json.loads(path.read_text())
    actual = fingerprint.get("providerBinarySHA256", {})
    if lane == "docker":
        required = {}
    elif lane == "apple-stock":
        required = {"DEVCONTAINER_CONTAINER_BIN": expected["stockContainer"]}
    else:
        required = {"DEVCONTAINER_CONTAINER_BIN": expected["composeContainer"],
                    "DEVCONTAINER_COMPOSE_BIN": expected["composeProvider"]}
    if actual != required:
        raise RuntimeError(f"{lane} provider bytes in {path.name} differ from the selected executables")
    commands = fingerprint.get("commands", {})
    if lane == "apple-stock":
        output = commands.get("container", {}).get("stdout", "")
        if fingerprint.get("containerDistribution") != "apple" or not output_contains_pin(
                output, manifest["referencePins"]["appleContainer"]):
            raise RuntimeError("stock Apple runtime fingerprint differs from its checked-in version and commit")
    elif lane == "container-compose":
        output = commands.get("container", {}).get("stdout", "")
        compose_output = commands.get("containerCompose", {}).get("stdout", "")
        if (not output_contains_pin(output, manifest["referencePins"]["appleContainer"])
                or not output_contains_pin(compose_output, manifest["referencePins"]["containerCompose"])):
            raise RuntimeError("container-compose runtime fingerprint differs from its checked-in version and commit")


def output_contains_pin(output: str, pin: dict) -> bool:
    """Require the actual provider JSON output to contain the exact version and commit strings."""
    try:
        value = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        return False
    leaves = []
    def collect(item):
        if isinstance(item, dict):
            for child in item.values():
                collect(child)
        elif isinstance(item, list):
            for child in item:
                collect(child)
        elif isinstance(item, (str, int, float)):
            leaves.append(str(item))
    collect(value)
    return (pin["stableVersion"] in leaves and pin["stableCommit"] in leaves)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def descriptor(root: Path, source: Path, relative: str) -> dict[str, str]:
    """Copy one bounded regular JSON file into the immutable public-safe input closure."""
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 16 * 1024 * 1024:
        raise ValueError(f"Evidence input is not a bounded single-link regular file: {source.name}")
    payload = source.read_bytes()
    json.loads(payload)
    target = root / relative
    parent = target.parent
    chain = []
    while parent != root:
        chain.append(parent)
        parent = parent.parent
    for directory in reversed(chain):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            info = directory.lstat()
            if (not stat.S_ISDIR(info.st_mode) or directory.is_symlink()
                    or info.st_uid != os.getuid() or info.st_mode & 0o777 != 0o700):
                raise ValueError("Qualification input directory is not a private owned directory")
    with target.open("xb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    os.chmod(target, 0o600)
    return {"path": relative, "sha256": hashlib.sha256(payload).hexdigest()}


def seal_qualification(args: argparse.Namespace, evidence: Path, cleanup: dict,
                       host_payload: dict, comparisons: dict, provider_tools: dict,
                       package_proof: dict) -> tuple[Path, str]:
    """Create a bounded internal CAS receipt after all comparisons/restoration pass."""
    parent = args.qualification_directory
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if parent.is_symlink() or parent.resolve() != parent or parent.stat().st_dev != ACCOUNT_HOME.stat().st_dev:
        raise ValueError("Qualification CAS parent must be canonical internal storage")
    if parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o777 != 0o700:
        raise ValueError("Qualification CAS parent must be user-owned mode 0700")
    staging = Path(tempfile.mkdtemp(prefix=".qualification-", dir=parent))
    staging.chmod(0o700)
    try:
        inputs = staging / "inputs"
        inputs.mkdir(mode=0o700)
        references: dict[str, dict] = {}
        lane_results = {}
        for lane in LANES:
            lane_results[lane] = {}
            for suite, source_root in (("cli", evidence), ("vscode", evidence / "vscode")):
                base = source_root / lane
                item = {}
                for kind in ("results", "fingerprint"):
                    source = base / f"{kind}.json"
                    relative = f"inputs/{suite}/{lane}/{kind}.json"
                    ref = descriptor(staging, source, relative)
                    references[relative] = ref
                    item[kind] = ref
                result_value = json.loads((staging / item["results"]["path"]).read_text())
                fingerprint = staging / item["fingerprint"]["path"]
                validate_provider_fingerprint(fingerprint, lane, args._provider_hashes, args._manifest)
                item["status"] = result_value.get("status")
                if item["status"] != "passed":
                    raise ValueError(f"Cannot seal failed {lane} {suite} results")
                validate_provider_result(base / "results.json", lane, args._provider_hashes)
                if suite == "vscode":
                    security_source = base / "security-scan.json"
                    relative = f"inputs/vscode/{lane}/security-scan.json"
                    ref = descriptor(staging, security_source, relative)
                    references[relative] = ref
                    scan = json.loads((staging / relative).read_text())
                    if scan.get("status") != "passed" or scan.get("detectedNames") != []:
                        raise ValueError(f"Cannot seal failed {lane} V01 security evidence")
                    item["security"] = dict(ref, status="passed")
                lane_results[lane][suite] = item
        comparison_rows = {}
        for suite, source, relative in (
                ("cli", evidence / "comparison.json", "inputs/comparisons/cli.json"),
                ("vscode", evidence / "vscode" / "comparison.json", "inputs/comparisons/vscode.json")):
            ref = descriptor(staging, source, relative)
            references[relative] = ref
            comparison_value = json.loads((staging / relative).read_text())
            comparison_rows[suite] = dict(ref, status=comparison_value.get("status"))
            if comparisons.get(suite, {}).get("status") != comparison_value.get("status"):
                raise ValueError(f"{suite} comparison status changed while sealing")
        manifest_ref = descriptor(staging, evidence / "release-manifest.json",
                                  "inputs/comparisons/release-manifest.json")
        references[manifest_ref["path"]] = manifest_ref
        release_manifest_value = json.loads((staging / manifest_ref["path"]).read_text())
        comparison_rows["releaseManifest"] = dict(manifest_ref, status=release_manifest_value.get("status"))
        if comparisons.get("releaseManifest", {}).get("status") != release_manifest_value.get("status"):
            raise ValueError("release manifest validation status changed while sealing")

        cleanup_rows = {}
        service_journals = {}
        for lane in LANES:
            if cleanup[lane].get("status") != "restored":
                raise ValueError(f"Cannot seal a qualification with unrestored {lane} state")
            source = evidence / f"{lane}-cleanup.json"
            relative = f"inputs/cleanup/{lane}.json"
            ref = descriptor(staging, source, relative)
            references[relative] = ref
            cleanup_rows[lane] = dict(cleanup[lane], **ref)
            if lane != "docker":
                source = evidence / f"{lane}-service-journal-receipt.json"
                relative = f"inputs/service-journals/{lane}.json"
                ref = descriptor(staging, source, relative)
                references[relative] = ref
                journal = json.loads((staging / relative).read_text())
                service_journals[lane] = dict(ref, ownerSHA256=journal["ownerSHA256"],
                                              records=journal["records"], seal=journal["seal"])
        host_ref = descriptor(staging, evidence / "host-cleanup.json", "inputs/cleanup/host.json")
        references[host_ref["path"]] = host_ref
        cleanup_rows["host"] = {
            "status": host_payload["status"],
            "initialColima": host_payload["initialColima"],
            "finalColima": host_payload["finalColima"],
            "hostGuardCleared": host_payload["hostGuardCleared"],
            **host_ref,
        }

        for tool in ("appleStock", "containerCompose"):
            relative = f"inputs/providers/{tool}.json"
            source = evidence / "providers" / f"{tool}.json"
            ref = descriptor(staging, source, relative)
            references[relative] = ref
            provider_tools[tool]["apiServerEvidence"] = ref
        relative = "inputs/providers/docker.json"
        ref = descriptor(staging, evidence / "providers/docker.json", relative)
        references[relative] = ref
        provider_tools["docker"]["engineEvidence"] = ref

        receipt = {
            "schemaVersion": 1, "scope": "local-native-package-parity-qualification",
            "executedLocally": True, "status": "passed",
            "sourceCommit": args.source_commit, "sourceTree": args._source_tree,
            "sourceDirty": False, "controllerSHA256": sha256(CONTROLLER),
            "parityHarnessSHA256": args._parity_harness_sha256,
            "finalizationProvenanceSHA256": args.provenance_sha256,
            "archiveSHA256": package_proof["archiveSHA256"],
            "trustedStateSHA256": package_proof["trustedStateSHA256"],
            "submissionID": package_proof["submissionID"],
            "fixtureCounts": {"cliPerLane": 27, "vscodePerLane": 1,
                              "laneCount": 3, "totalLaneFixtureResults": 84},
            "restoration": {lane: "restored" for lane in LANES},
            "guardCleared": True, "enginePins": args._manifest["referencePins"]["docker"],
            "providerTools": provider_tools, "laneResults": lane_results,
            "comparisons": comparison_rows, "cleanup": cleanup_rows,
            "serviceJournalReceipts": service_journals,
            "inputFiles": [{"path": path, "sha256": ref["sha256"],
                            "size": (staging / path).stat().st_size}
                           for path, ref in sorted(references.items())],
        }
        if any(row.get("status") != "passed" for value in lane_results.values()
               for row in value.values()):
            raise ValueError("Not all six local parity suites passed")
        if any(row.get("status") != "passed" for row in comparison_rows.values()):
            raise ValueError("Not all local parity comparisons passed")
        require_host_restoration(cleanup, host_payload["initialColima"],
                                 host_payload["finalColima"], True)
        payload = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()
        receipt_path = staging / "qualification.json"
        with receipt_path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(receipt_path, 0o600)
        digest = hashlib.sha256(payload).hexdigest()
        final = parent / digest
        if final.exists():
            raise ValueError(f"Qualification CAS leaf already exists: {final}")
        os.rename(staging, final)
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return final, digest
    except BaseException:
        # Preserve private partial material for diagnosis; never report it as a qualification.
        raise


def validate_provider_result(path: Path, lane: str, expected: dict[str, str]) -> None:
    value = json.loads(path.read_text())
    actual = value.get("providerBinarySHA256", {})
    if lane == "docker":
        required = {}
    elif lane == "apple-stock":
        required = {"DEVCONTAINER_CONTAINER_BIN": expected["stockContainer"]}
    else:
        required = {"DEVCONTAINER_CONTAINER_BIN": expected["composeContainer"],
                    "DEVCONTAINER_COMPOSE_BIN": expected["composeProvider"]}
    if actual != required:
        raise RuntimeError(f"{lane} provider bytes in results.json differ from selected executables")


def native_compose_frontend_environment(args: argparse.Namespace, lane: str) -> dict[str, str]:
    """Select the signed wrapper's separately admitted Compose frontend per native lane."""
    if lane == "apple-stock":
        return {
            "DEVCONTAINER_BACKEND": "stock",
            "DEVCONTAINER_COMPOSE_PROVIDER": "docker",
            "DEVCONTAINER_DOCKER_BIN": str(args.docker_bin),
            "DEVCONTAINER_DOCKER_COMPOSE_BIN": str(args.docker_compose_bin),
        }
    if lane == "container-compose":
        return {
            "DEVCONTAINER_BACKEND": "container-compose",
            "DEVCONTAINER_COMPOSE_PROVIDER": "container-compose",
            "DEVCONTAINER_COMPOSE_BIN": str(args.compose_provider_bin),
            "DEVCONTAINER_COMPOSE_PROVIDER_SHA256": args.compose_provider_sha256,
        }
    raise ValueError(f"unsupported native Compose lane: {lane}")


def host_service_digest() -> tuple[str, int]:
    """Hash only public-safe service metadata, never retained plist payloads."""
    sys.path.insert(0, str(REPOSITORY / "Tools/testing"))
    from runtime_services import Launchd, authorised_roots
    from service_switch import snapshot
    launchd = Launchd()
    entries = snapshot(launchd, authorised_roots(launchd, ACCOUNT_HOME))
    safe = [{key: item[key] for key in ("label", "path", "program", "sha256")}
            for item in entries]
    payload = json.dumps(safe, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest(), len(entries)


def make_provider_tools(args: argparse.Namespace, evidence: Path) -> dict:
    """Build manifest-bound provider rows and sanitized identity evidence."""
    pins = args._manifest["referencePins"]
    docker_pins = pins["docker"]
    vscode_pins = pins["vscode"]
    apple_pins = pins["appleContainer"]
    compose_pins = pins["containerCompose"]
    docker_observed = json.loads((evidence / "docker-runtime-identity.json").read_text())
    if (docker_observed.get("buildxVersion") != docker_pins["buildxVersion"]
            or docker_observed.get("buildxSHA256") != args._provider_hashes["dockerBuildx"]):
        raise ValueError("Docker Buildx identity evidence changed before qualification sealing")
    docker_row = {
        "version": docker_pins["cliVersion"], "sha256": args._provider_hashes["docker"],
        "buildxVersion": docker_observed["buildxVersion"],
        "buildxSHA256": docker_observed["buildxSHA256"],
        "engineVersion": docker_observed["engineVersion"], "engineCommit": docker_observed["engineCommit"],
        "engineApiVersion": docker_observed["engineApiVersion"], "engineSHA256": docker_observed["dockerdSHA256"],
    }
    compose_row = {"version": docker_pins["composeVersion"],
                   "sha256": args._provider_hashes["dockerCompose"],
                   "bottleSHA256": docker_pins["composeBottleSHA256"]}
    colima_version = run([str(args.colima_bin), "--version"], env={"PATH": "/usr/bin:/bin"},
                         capture=True).stdout.strip().splitlines()[0]
    vscode_row = {"version": vscode_pins["version"], "commit": vscode_pins["commit"],
                  "archiveSHA256": vscode_pins["archiveSHA256"],
                  "vsixSHA256": vscode_pins["devContainersExtension"]["vsixSHA256"],
                  "launcherSHA256": args._provider_hashes["vscode"]}
    apple = {"version": apple_pins["stableVersion"], "commit": apple_pins["stableCommit"],
             "containerSHA256": args._provider_hashes["stockContainer"],
             "apiServerSHA256": args._provider_hashes["stockAPIServer"]}
    compose = {"version": compose_pins["stableVersion"], "commit": compose_pins["stableCommit"],
               "containerSHA256": args._provider_hashes["composeContainer"],
               "composeSHA256": args._provider_hashes["composeProvider"],
               "apiServerSHA256": args._provider_hashes["composeAPIServer"]}
    write_json(evidence / "providers" / "appleStock.json", {
        "schemaVersion": 1, "lane": "apple-stock", "providerVersion": apple["version"],
        "providerCommit": apple["commit"], "containerSHA256": apple["containerSHA256"],
        "apiServerSHA256": apple["apiServerSHA256"],
        "preparedProvider": args._provider_helper_evidence["apple-stock"]})
    write_json(evidence / "providers" / "containerCompose.json", {
        "schemaVersion": 1, "lane": "container-compose", "providerVersion": compose["version"],
        "providerCommit": compose["commit"], "containerSHA256": compose["containerSHA256"],
        "composeVersion": compose["version"], "composeSHA256": compose["composeSHA256"],
        "apiServerSHA256": compose["apiServerSHA256"],
        "preparedProvider": args._provider_helper_evidence["container-compose"]})
    write_json(evidence / "providers" / "docker.json", {
        "schemaVersion": 1, "source": "docker-oracle", "clientVersion": docker_observed["clientVersion"],
        "dockerCLISHA256": args._provider_hashes["docker"],
        "engineVersion": docker_observed["engineVersion"], "engineCommit": docker_observed["engineCommit"],
        "engineApiVersion": docker_observed["engineApiVersion"], "engineSHA256": docker_observed["dockerdSHA256"],
        "composeVersion": docker_observed["composeVersion"],
        "composeSHA256": args._provider_hashes["dockerCompose"],
        "buildxVersion": docker_observed["buildxVersion"],
        "buildxSHA256": docker_observed["buildxSHA256"],
        "bottleSHA256": docker_pins["composeBottleSHA256"]})
    return {"docker": docker_row, "dockerCompose": compose_row,
            "appleStock": apple, "containerCompose": compose,
            "colima": {"version": colima_version, "sha256": args._provider_hashes["colima"]},
            "vscode": vscode_row}


def seal_after_host_cleanup(args: argparse.Namespace, evidence: Path, cleanup: dict,
                            comparisons: dict, provider_tools: dict,
                            package_proof: dict) -> tuple[Path, str]:
    """Seal only from the durable host-cleanup receipt written by finalization."""
    host_path = evidence / "host-cleanup.json"
    host_payload = json.loads(host_path.read_text())
    if not isinstance(host_payload, dict):
        raise ValueError("host cleanup receipt is not an object")
    return seal_qualification(args, evidence, cleanup, host_payload,
                              comparisons, provider_tools, package_proof)


def process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
        return True
    except ProcessLookupError:
        return False


def terminate_process_group(process: subprocess.Popen[str], *, grace: float = 30,
                            reap_grace: float = 5) -> None:
    """Run cancellation handlers, kill the whole new session, and verify group disappearance."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=reap_grace)
    deadline = time.monotonic() + grace
    while process_group_exists(process.pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if process_group_exists(process.pid):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + reap_grace
        while process_group_exists(process.pid) and time.monotonic() < deadline:
            time.sleep(0.05)
    if process_group_exists(process.pid):
        raise RuntimeError("timed-out runner process group did not disappear; cleanup is unverified")
    if process.stdout is not None or process.stderr is not None:
        process.communicate()


def default_colima_endpoint(home: Path) -> str:
    """Use the controlled default profile socket even when stopped Colima removes its context."""
    profile = home / ".colima/default"
    if (not home.is_absolute() or home.resolve(strict=True) != home
            or profile.resolve(strict=True) != profile or not profile.is_dir()):
        raise ValueError("Colima default profile requires canonical configured account storage")
    info = profile.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError("Colima default profile is not protected user-owned storage")
    return "unix://" + str(profile / "docker.sock")


def create_docker_cli_config(evidence: Path, args: argparse.Namespace) -> Path:
    """Expose only the admitted Compose and Buildx plugins to the Docker CLI."""
    docker_config = evidence / "docker-config"
    plugin_directory = docker_config / "cli-plugins"
    plugin_directory.mkdir(parents=True, mode=0o700)
    (plugin_directory / "docker-compose").symlink_to(args.docker_compose_bin)
    (plugin_directory / "docker-buildx").symlink_to(args.docker_buildx_bin)
    return docker_config


def colima_state(colima: Path, env: dict[str, str]) -> tuple[str, str]:
    result = subprocess.run([str(colima), "--profile", "default", "status"],
                            capture_output=True, text=True, env=env, timeout=30, check=False)
    detail = (result.stdout + result.stderr).strip()
    if result.returncode == 0 and "running" in detail.lower():
        return "running", detail
    if result.returncode != 0 and any(token in detail.lower() for token in
                                      ("not running", "does not exist", "not found")):
        return "stopped", detail
    raise RuntimeError(f"Cannot classify initial Colima default profile state: {detail[-1000:]}")


def finalized_args(args: argparse.Namespace) -> list[str]:
    return ["--finalized-directory", str(args.finalized_directory),
            "--finalization-provenance-sha256", args.provenance_sha256,
            "--finalization-state", str(args.accepted_state),
            "--expected-source-commit", args.source_commit]


def lane_commands(args: argparse.Namespace, lane: str, evidence: Path) -> tuple[list[str], list[str]]:
    """Plan distinct maintained output roots for CLI and V01 runners."""
    cli = [sys.executable, str(REPOSITORY / "Tools/parity/run_lane.py"), lane,
           str(evidence), *finalized_args(args)]
    vscode = [sys.executable, str(REPOSITORY / "Tools/parity/run_vscode.py"), lane,
              str(evidence / "vscode"), *finalized_args(args)]
    if lane == "docker":
        vscode += ["--colima-bin", str(args.colima_bin), "--colima-sha256", args.colima_sha256]
    return cli, vscode


def controller_capture_directory(evidence: Path, lane: str, suite: str) -> Path:
    """Keep controller-owned process captures outside both runners' replaceable output roots."""
    if lane not in LANES or suite not in {"cli", "vscode"}:
        raise ValueError("unknown parity lane or suite for controller output capture")
    return evidence / "controller-logs" / lane / suite


def capture_component_evidence_identity(evidence: Path, operator_inputs: dict) -> dict:
    """Bind the component recheck to its initialized private evidence root."""
    if (not evidence.is_absolute() or evidence.resolve(strict=True) != evidence
            or evidence.is_symlink() or not evidence.is_dir()):
        raise ValueError("component evidence root is not a canonical initialized directory")
    info = evidence.lstat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("component evidence root must be user-owned mode 0700")
    inputs_path = evidence / "operator-inputs.json"
    inputs_info = inputs_path.lstat()
    if (not stat.S_ISREG(inputs_info.st_mode) or inputs_info.st_nlink != 1
            or inputs_info.st_uid != os.getuid() or stat.S_IMODE(inputs_info.st_mode) != 0o600):
        raise ValueError("component operator inputs must be a private single-link regular file")
    if json.loads(inputs_path.read_text(encoding="utf-8")) != operator_inputs:
        raise ValueError("component operator inputs differ from the initialized values")
    return {
        "path": str(evidence), "device": info.st_dev, "inode": info.st_ino,
        "owner": info.st_uid, "mode": stat.S_IMODE(info.st_mode),
        "operatorInputsSHA256": sha256(inputs_path),
        "sourceCommit": operator_inputs["sourceCommit"],
        "campaign": operator_inputs["campaign"],
    }


def validate_evidence_root(args: argparse.Namespace,
                           initialized_component_identity: dict | None = None) -> None:
    """Require fresh evidence initially, or the exact initialized component root on recheck."""
    evidence = args.evidence
    if not evidence.is_absolute() or evidence.resolve(strict=False) != evidence:
        raise ValueError("evidence directory must be a canonical absolute path")
    if initialized_component_identity is None:
        if evidence.exists() or evidence.is_symlink():
            raise ValueError("evidence directory must be fresh")
        return
    if getattr(args, "component_fixture", None) != COMPONENT_FIXTURE:
        raise ValueError("existing evidence is permitted only for the E13 component recheck")
    try:
        info = evidence.lstat()
    except FileNotFoundError as error:
        raise ValueError("initialized component evidence root disappeared") from error
    expected = initialized_component_identity
    if (evidence.is_symlink() or not stat.S_ISDIR(info.st_mode)
            or evidence.resolve(strict=True) != evidence
            or str(evidence) != expected.get("path")
            or info.st_dev != expected.get("device") or info.st_ino != expected.get("inode")
            or info.st_uid != os.getuid() or info.st_uid != expected.get("owner")
            or stat.S_IMODE(info.st_mode) != 0o700 or stat.S_IMODE(info.st_mode) != expected.get("mode")
            or expected.get("sourceCommit") != args.source_commit
            or expected.get("campaign") != args.campaign):
        raise ValueError("component evidence root no longer matches its initialized identity")
    inputs_path = evidence / "operator-inputs.json"
    inputs_info = inputs_path.lstat()
    if (not stat.S_ISREG(inputs_info.st_mode) or inputs_info.st_nlink != 1
            or inputs_info.st_uid != os.getuid() or stat.S_IMODE(inputs_info.st_mode) != 0o600
            or sha256(inputs_path) != expected.get("operatorInputsSHA256")):
        raise ValueError("component operator inputs changed after initialization")
    payload = json.loads(inputs_path.read_text(encoding="utf-8"))
    if (payload.get("sourceCommit") != args.source_commit
            or payload.get("campaign") != args.campaign
            or payload.get("sourceTree") != getattr(args, "_source_tree", None)):
        raise ValueError("component operator inputs do not bind the current source and campaign")


def validate_inputs(args: argparse.Namespace, *,
                    initialized_component_identity: dict | None = None) -> dict[str, str]:
    manifest_path = args.repository / "Tests/Parity/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    from engine_fixture_routes import validate_engine_fixture_routes

    try:
        validate_engine_fixture_routes(manifest)
    except ValueError as error:
        raise ValueError(f"engine fixture route preflight failed: {error}") from error
    pins = manifest.get("referencePins", {})
    expected = {
        "docker": sha256(args.docker_bin),
        "dockerCompose": sha256(args.docker_compose_bin),
        "dockerBuildx": sha256(args.docker_buildx_bin),
        "stockContainer": sha256(args.stock_container_bin),
        "composeContainer": sha256(args.compose_container_bin),
        "composeProvider": sha256(args.compose_provider_bin),
        "stockAPIServer": sha256(args.stock_container_bin.parent / "container-apiserver"),
        "composeAPIServer": sha256(args.compose_container_bin.parent / "container-apiserver"),
        "colima": sha256(args.colima_bin),
        "vscode": sha256(args.vscode_bin),
    }
    docker_pins = pins.get("docker", {})
    provider_pins = {
        "docker": docker_pins.get("cliSHA256"),
        "dockerCompose": docker_pins.get("composeSHA256"),
        "stockContainer": args.stock_container_sha256,
        "composeContainer": args.compose_container_sha256,
        "composeProvider": args.compose_provider_sha256,
        "stockAPIServer": args.stock_api_sha256,
        "composeAPIServer": args.compose_api_sha256,
        "colima": args.colima_sha256,
        "vscode": pins.get("vscode", {}).get("launcherSHA256"),
    }
    # Provider identities not represented by the public parity manifest are
    # explicit protected inputs; all manifest-pinned bytes are compared here.
    for key, value in provider_pins.items():
        if value and expected[key] != value:
            raise ValueError(f"runtime provider {key} differs from its trusted SHA-256")
    if expected["docker"] != docker_pins.get("cliSHA256"):
        raise ValueError("Docker CLI bytes differ from the checked-in parity pins")
    if expected["dockerCompose"] != docker_pins.get("composeSHA256"):
        raise ValueError("Docker Compose bytes differ from the checked-in parity pins")
    if expected["dockerBuildx"] != docker_pins.get("buildxSHA256"):
        raise ValueError("Docker Buildx bytes differ from the checked-in parity pins")
    for key, path in (("docker", args.docker_bin), ("docker compose", args.docker_compose_bin),
                      ("docker buildx", args.docker_buildx_bin),
                      ("stock container", args.stock_container_bin),
                      ("compose container", args.compose_container_bin),
                      ("compose provider", args.compose_provider_bin),
                      ("stock API server", args.stock_container_bin.parent / "container-apiserver"),
                      ("compose API server", args.compose_container_bin.parent / "container-apiserver"),
                      ("Colima", args.colima_bin), ("VS Code launcher", args.vscode_bin)):
        if (not path.is_absolute() or path.resolve(strict=True) != path
                or not path.is_file() or not os.access(path, os.X_OK)):
            raise ValueError(f"{key} must be an executable at a canonical absolute path")
    for key, path in (("repository", args.repository), ("SSD scratch root", args.ssd_root),
                      ("retained root", args.retained_root), ("qualification directory", args.qualification_directory),
                      ("finalized package", args.finalized_directory),
                      ("accepted state", args.accepted_state)):
        if not path.is_absolute() or path.resolve(strict=True) != path or not path.is_dir():
            raise ValueError(f"{key} must be a canonical absolute directory")
    validate_evidence_root(args, initialized_component_identity)
    if (not args.retained_root.is_relative_to(ACCOUNT_HOME)
            or args.qualification_directory != args.retained_root / "qualifications"):
        raise ValueError("qualification output must remain beneath the configured internal retained root")
    if (args.qualification_directory.stat().st_dev != ACCOUNT_HOME.stat().st_dev
            or args.qualification_directory.stat().st_uid != os.getuid()
            or args.qualification_directory.stat().st_mode & 0o777 != 0o700):
        raise ValueError("qualification retained root must be user-owned internal storage with mode 0700")
    args._guest_retained_root = validate_guest_asset_retained_root(DEFAULT_WORKFLOW_RETAINED)
    if not args.evidence.is_relative_to(args.ssd_root):
        raise ValueError("raw runtime evidence must remain beneath SSD scratch")
    if len(args.source_commit) != 40 or any(c not in "0123456789abcdef" for c in args.source_commit):
        raise ValueError("source commit must be a full lowercase Git SHA")
    if len(args.provenance_sha256) != 64 or any(c not in "0123456789abcdef" for c in args.provenance_sha256):
        raise ValueError("provenance SHA must be a full lowercase SHA-256")
    if len(args.state_sha256) != 64 or any(c not in "0123456789abcdef" for c in args.state_sha256):
        raise ValueError("accepted-state SHA must be a full lowercase SHA-256")
    if sha256(args.accepted_state / "state.json") != args.state_sha256:
        raise ValueError("accepted notary state differs from its independently trusted SHA-256")
    if expected["dockerCompose"] != "6c4a20e62f3a776dc7ee603dc296ec63c7194b46067c6461be9208d191c922b3":
        raise ValueError("docker-compose reference binary is not the admitted 5.3.1 bottle")
    compose_version = run([str(args.docker_compose_bin), "version", "--short"],
                          env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}, timeout=20, capture=True)
    if compose_version.stdout.strip() != docker_pins.get("composeVersion"):
        raise ValueError("Docker Compose version differs from the checked-in parity pins")
    admit_docker_buildx(args.docker_buildx_bin, docker_pins)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=args.repository, text=True).strip()
    status = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=args.repository, text=True)
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=args.repository, text=True).strip()
    if head != args.source_commit or status:
        raise ValueError("repository must be clean at the exact finalized source commit")
    evidence = manifest_path
    fixture_count = len(manifest["fixtures"])
    vscode_count = sum(1 for item in manifest["fixtures"] if item.get("runner") == "vscode")
    if (fixture_count != 28 or vscode_count != 1 or
            any(item.get("status") != "implemented" for item in manifest["fixtures"])):
        raise ValueError("the maintained release manifest no longer defines 27 CLI plus one VS Code fixture")
    if args.ssd_root.stat().st_dev == ACCOUNT_HOME.stat().st_dev:
        raise ValueError("runtime evidence scratch must be on an enrolled SSD separate from internal storage")
    verify_enrolled_ssd(args)
    args._source_tree = tree
    args._manifest = manifest
    args._provider_hashes = expected
    return expected


def admit_package_before_runtime(args: argparse.Namespace) -> dict[str, dict]:
    """Use the maintained admission API before changing any runtime state."""
    from owned_guest_fixture import preflight_guest_inputs

    guest_retained = getattr(args, "_guest_retained_root", DEFAULT_WORKFLOW_RETAINED)
    args._guest_input_admissions = {
        lane: preflight_guest_inputs(REPOSITORY, lane, guest_retained)
        for lane in ("docker", "apple-stock", "container-compose")
    }
    admit = load_run_lane(REPOSITORY).load_finalized_admitter(REPOSITORY)
    retained = RETAINED / "finalized-admissions"
    scratch = SSD / "finalized-admission"
    evidence = retained / "signature-evidence"
    results = {}
    helper_programs = {}
    for lane in ("apple-stock", "container-compose"):
        admission = admit(
            repository=REPOSITORY.resolve(),
            finalized=args.finalized_directory,
            trusted_provenance_sha256=args.provenance_sha256,
            expected_source_commit=args.source_commit,
            provider_lane=lane,
            state=args.accepted_state,
            scratch_root=scratch,
            retained_root=retained,
            evidence_root=evidence,
        )
        if (admission.get("scope") != "finalized-native-package-runtime-input"
                or admission.get("kind") != "signed-notarized-native-package"
                or admission.get("distributionReady") is not False
                or admission.get("sourceCommit") != args.source_commit
                or admission.get("runtimeProfile") != "stock"
                or admission.get("finalizationProvenanceSHA256") != args.provenance_sha256
                or admission.get("providerLane") != lane):
            raise ValueError(f"maintained admission returned an unsupported {lane} package identity")
        compose_path = Path(admission["executables"]["devcontainer-compose"])
        if (not compose_path.is_absolute() or compose_path.resolve(strict=True) != compose_path
                or sha256(compose_path) != admission["productionBinarySHA256"].get("bin/devcontainer-compose")):
            raise ValueError(f"{lane} signed Compose frontend differs from its package inventory")
        helper_programs[lane] = admit_provider_helper_programs(lane, args)
        results[lane] = {
            "scope": admission["scope"],
            "kind": admission["kind"],
            "distributionReady": admission["distributionReady"],
            "sourceCommit": admission["sourceCommit"],
            "runtimeProfile": admission["runtimeProfile"],
            "providerLane": admission["providerLane"],
            "archiveSHA256": admission["archiveSHA256"],
            "finalizationProvenanceSHA256": admission["finalizationProvenanceSHA256"],
            "signatureInventorySHA256": admission["signatureInventorySHA256"],
        }
    if results["apple-stock"]["archiveSHA256"] != results["container-compose"]["archiveSHA256"]:
        raise ValueError("provider lanes did not admit the same finalized archive")
    args._provider_helper_programs = helper_programs
    return results


def provider_quiescence(runtime, container: Path, environment: dict[str, str], expected_programs: dict) -> dict:
    """Prove the selected provider has no workload before changing helper HOME."""

    from runtime_services import PROVIDER_HELPER_LAYOUT, process_inventory
    from service_switch import API

    status_result = run([str(container), "system", "status", "--format", "json"],
                        env=environment, timeout=30, capture=True)
    status = json.loads(status_result.stdout)
    if not isinstance(status, dict) or status.get("status") != "running":
        raise RuntimeError("selected provider is not running during helper quiescence check")

    def records(arguments: list[str], name: str) -> list[dict]:
        result = run([str(container), *arguments], env=environment, timeout=30, capture=True)
        rows = json.loads(result.stdout)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise RuntimeError(f"provider {name} inventory has an unsupported shape")
        return rows

    containers = records(["list", "--all", "--format", "json"], "container")
    volumes = records(["volume", "list", "--format", "json"], "volume")
    networks = records(["network", "list", "--format", "json"], "network")
    custom_networks = []
    for network in networks:
        configuration = network.get("configuration", {})
        if configuration is None:
            configuration = {}
        if not isinstance(configuration, dict):
            raise RuntimeError("provider network inventory has an unsupported configuration")
        name = configuration.get("name", network.get("name"))
        identifier = network.get("id")
        identities = [value for value in (name, identifier) if value is not None]
        if (not identities or any(not isinstance(value, str) or not value for value in identities)):
            raise RuntimeError("provider network inventory omits network identity")
        if any(value != "default" for value in identities):
            custom_networks.append(network)

    allowed_pids = {runtime.service.get("pid")}
    if runtime.launchd.inspect(API) != {
            "label": API, "path": str(runtime.root / "selected-apiserver.plist"),
            "program": str(runtime.executable)}:
        raise RuntimeError("selected API service identity changed before helper restart")
    for label in PROVIDER_HELPER_LAYOUT:
        expected = expected_programs[label]
        job = runtime.launchd.inspect(label)
        if (not isinstance(job, dict) or job.get("label") != label
                or job.get("program") != str(expected["program"])):
            raise RuntimeError("provider helper identity differs from the locked package")
        pid = runtime.launchd.process_id(label)
        if not isinstance(pid, int) or pid <= 0:
            raise RuntimeError("provider helper is not running before private HOME propagation")
        allowed_pids.add(pid)

    clients = []
    guests = []
    busy_names = {"Runner.Worker", "container", "compose", "docker", "docker-compose",
                  "colima", "container-compose", "devcontainer", "devcontainer-compose",
                  "devcontainer-engine"}
    guest_names = {"container-runtime-linux", "com.apple.Virtualization.VirtualMachine"}
    for pid, process in process_inventory().items():
        name = Path(process["program"]).name
        if name in guest_names:
            guests.append(pid)
        if name in busy_names and pid not in allowed_pids:
            clients.append(pid)
    resource_count = len(volumes) + len(custom_networks)
    return {
        "status": "running",
        "containerCount": len(containers),
        "resourceCount": resource_count,
        "guestCount": len(guests),
        "clientCount": len(clients),
    }


def _finalize_host_cleanup(evidence: Path, args: argparse.Namespace, base_env: dict[str, str],
                           cleanup: dict[str, dict], initial_colima: str, initial_colima_detail: str,
                           initial_services: str, initial_service_count: int, guard, transaction_owner: dict,
                           errors: list[str], *, interrupted: bool) -> bool:
    """Record independent final observations; retain the guard on any unknown state."""

    observation_errors = []
    final_colima = None
    final_colima_detail = None
    try:
        final_colima, final_colima_detail = colima_state(args.colima_bin, base_env)
    except BaseException as error:
        observation_errors.append(f"Colima final observation failed: {type(error).__name__}: {error}")
    final_services = None
    final_service_count = None
    try:
        final_services, final_service_count = host_service_digest()
    except BaseException as error:
        observation_errors.append(f"host service final observation failed: {type(error).__name__}: {error}")
    errors.extend(item for item in observation_errors if item not in errors)
    host_restorable = (not observation_errors and final_colima is not None and final_services is not None
                       and host_can_clear_guard(cleanup, initial_colima, final_colima,
                                                initial_services, final_services))
    guard_cleared = False
    if host_restorable:
        try:
            guard.clear(transaction_owner)
            guard_cleared = True
        except BaseException as error:
            errors.append(f"runtime guard clear failed: {type(error).__name__}: {error}")
    if not guard_cleared:
        errors.append("host runtime state is uncertain; durable runtime guard retained")

    for lane in LANES:
        row = cleanup[lane]
        payload = {"schemaVersion": 1, "lane": lane,
                   "status": row.get("status", "uncertain"),
                   "cliCleanupComplete": row.get("cliCleanupComplete") is True,
                   "vscodeCleanupComplete": row.get("vscodeCleanupComplete") is True}
        if getattr(args, "component_fixture", None):
            payload["vscodeStatus"] = "skipped"
        if lane == "docker":
            payload["colima"] = row.get("colima", {"initial": initial_colima,
                                                   "final": final_colima,
                                                   "restored": final_colima == initial_colima})
        else:
            payload.update({"initialColima": initial_colima, "finalColima": final_colima,
                            "providerStopped": row.get("providerStopped") is True,
                            "serviceRestored": row.get("serviceRestored") is True,
                            "serviceJournalReceiptSHA256": row.get("serviceJournalReceiptSHA256")})
        write_json(evidence / f"{lane}-cleanup.json", payload)
    host_payload = {"schemaVersion": 1, "status": "restored" if guard_cleared else "uncertain",
                    "initialColima": initial_colima, "finalColima": final_colima,
                    "initialServiceSetSHA256": initial_services,
                    "finalServiceSetSHA256": final_services,
                    "initialServiceCount": initial_service_count,
                    "finalServiceCount": final_service_count,
                    "hostGuardCleared": guard_cleared,
                    "restoration": {lane: cleanup[lane].get("status") for lane in LANES}}
    write_json(evidence / "host-cleanup.json", host_payload)
    write_json(evidence / "cleanup-state.json", {
        "initialColima": initial_colima, "initialColimaDetail": initial_colima_detail,
        "finalColima": final_colima, "finalColimaDetail": final_colima_detail,
        "initialServiceSetSHA256": initial_services, "finalServiceSetSHA256": final_services,
        "initialServiceCount": initial_service_count, "finalServiceCount": final_service_count,
        "guardCleared": guard_cleared, "lanes": cleanup, "errors": errors,
    })
    if errors or interrupted or not guard_cleared:
        write_json(evidence / "controller-failure.json", {"status": "failed", "errors": errors})
    return guard_cleared


def admit_provider_helper_programs(lane: str, args: argparse.Namespace) -> dict:
    """Bind launchd helper hashes to the complete retained provider package."""

    if lane not in {"apple-stock", "container-compose"}:
        raise ValueError("unknown native provider lane for helper admission")
    retained = getattr(args, "_guest_retained_root", DEFAULT_WORKFLOW_RETAINED)
    sys.path.insert(0, str(REPOSITORY / "Tools/testing"))
    sys.path.insert(0, str(REPOSITORY / "Tools/bazel"))
    prepare_releases = importlib.import_module("prepare_releases")
    released_engine = importlib.import_module("released_engine")
    if (Path(prepare_releases.__file__).resolve() !=
            (REPOSITORY / "Tools/bazel/prepare_releases.py").resolve(strict=True)
            or Path(released_engine.__file__).resolve() !=
            (REPOSITORY / "Tools/testing/released_engine.py").resolve(strict=True)):
        raise ValueError("provider release helpers resolved outside the selected source checkout")

    lock = json.loads((REPOSITORY / "Tools/bazel/releases.lock.json").read_bytes())
    asset = released_engine.provider_runtime_selection(lock, lane)
    prepared = prepare_releases.require_retained(
        asset, retained / "release-objects" / asset["sha256"],
        retained / "prepared-releases", retained / "prepared-receipts")
    package_root = Path(prepared["root"])
    specification = {"schemaVersion": 1, "assetSHA256": asset["sha256"],
                     "layout": prepare_releases.layout(asset)}
    receipt_path = retained / "prepared-receipts" / (prepared["preparationSHA256"] + ".json")
    receipt = json.loads(receipt_path.read_bytes())
    verified = prepare_releases.validate_prepared(package_root, specification, receipt)
    provider_root = provider_install_root(lane, args)
    prefix = package_root / ("Payload" if specification["layout"]["format"] == "pkg" else "")
    if (provider_root != prefix or provider_root.is_symlink()
            or provider_root.resolve(strict=True) != provider_root):
        raise ValueError(f"{lane} selected provider root differs from the retained locked package")

    helpers = {
        "com.apple.container.container-core-images":
            "libexec/container/plugins/container-core-images/bin/container-core-images",
        "com.apple.container.machine-apiserver":
            "libexec/container/plugins/machine-apiserver/bin/machine-apiserver",
    }
    admitted = {}
    helper_evidence = {}
    for label, relative in helpers.items():
        inventory_relative = (Path("Payload") / relative if specification["layout"]["format"] == "pkg"
                              else Path(relative)).as_posix()
        item = verified["inventory"].get(inventory_relative, {})
        program = provider_root / relative
        if (item.get("kind") != "file" or not item.get("mode", 0) & 0o111
                or not program.is_file() or program.is_symlink()
                or sha256(program) != item.get("sha256")):
            raise ValueError(f"{lane} helper bytes differ from the locked prepared package")
        admitted[label] = {"program": program, "sha256": item["sha256"]}
        helper_evidence[label.rsplit(".", 1)[-1]] = {
            "path": relative, "sha256": item["sha256"]}
    args._provider_helper_evidence = getattr(args, "_provider_helper_evidence", {})
    args._provider_helper_evidence[lane] = {
        "assetSHA256": asset["sha256"],
        "preparationSHA256": prepared["preparationSHA256"],
        "preparedReceiptSHA256": sha256(receipt_path),
        "inventorySHA256": prepared["inventorySHA256"],
        "helperExecutables": helper_evidence,
    }
    return admitted


def apple_lane(args: argparse.Namespace, lane: str, evidence: Path, api: Path,
               base_env: dict[str, str], cleanup: dict[str, dict]) -> None:
    sys.path.insert(0, str(REPOSITORY / "Tools/testing"))
    from host_runtime import HostGuard, runtime_lease
    from private_keychain import run_keychain
    from runtime_services import ControlledRuntime

    identity = {"campaign": args.campaign, "lane": lane, "sourceCommit": args.source_commit}
    root = Path(tempfile.mkdtemp(prefix=f"native-parity-{lane}-", dir=SSD))
    root.chmod(0o700)
    owner = {"identity": identity, "root": str(root)}
    (root / "owner.json").write_text(json.dumps(owner, sort_keys=True) + "\n")
    os.chmod(root / "owner.json", 0o600)
    guard = HostGuard(GUARD_PATH)
    runtime = ControlledRuntime(root, owner, api, JOURNAL_PARENT, home=ACCOUNT_HOME)
    cleanup[lane] = {"status": "active"}
    restored = keychain_created = provider_started = provider_stopped = False
    api_definition_capture = None
    lane_env = dict(base_env)
    # API service and clients share the transaction-owned HOME. Admission
    # anchors retained authority to the real account through pwd.
    lane_env.update({"HOME": str(root), "TMPDIR": str(SSD),
                     "TEMP": str(SSD), "TMP": str(SSD)})
    lane_env["DEVCONTAINER_CONTAINER_BIN"] = str(
        args.stock_container_bin if lane == "apple-stock" else args.compose_container_bin)
    lane_env.update(native_compose_frontend_environment(args, lane))
    provider_root = str(provider_install_root(lane, args))
    lane_env.update({"CONTAINER_APP_ROOT": str(root / "container"),
                     "CONTAINER_INSTALL_ROOT": provider_root,
                     "CONTAINER_INSTALLATION_ROOT": provider_root,
                     "CONTAINER_LOG_ROOT": str(root / "container-logs"),
                     "DEVCONTAINER_PARITY_GUARD": str(GUARD_PATH)})

    def prepare_private_home(journal):
        nonlocal keychain_created
        run_keychain(root, "create", journal)
        keychain_created = True

    primary_error = None
    suites_started = False
    provider_root_path = Path(provider_root)
    try:
        runtime.start(prepare_home=prepare_private_home)
        system_start_started = time.time()
        try:
            runtime_started = run([lane_env["DEVCONTAINER_CONTAINER_BIN"], "system", "start",
                                   "--enable-kernel-install", "--timeout", "120"],
                                  env=lane_env, timeout=150, capture=True, check=False)
        finally:
            api_definition_capture = runtime.capture_system_start_api_definition(
                provider_root_path, system_start_started, time.time(), provider_lane=lane)
        if runtime_started.returncode != 0:
            raise RuntimeError(f"{lane} provider SystemStart exited {runtime_started.returncode}")
        runtime_status = json.loads(run([lane_env["DEVCONTAINER_CONTAINER_BIN"], "system", "status",
                                         "--format", "json"], env=lane_env, timeout=30,
                                        capture=True).stdout)
        provider_started = runtime_status.get("status") == "running"
        expected_helpers = args._provider_helper_programs[lane]
        helper_home = None
        if provider_started:
            helper_home = runtime.propagate_provider_helper_home(
                provider_root_path, expected_helpers,
                lambda: provider_quiescence(runtime, Path(lane_env["DEVCONTAINER_CONTAINER_BIN"]),
                                            lane_env, expected_helpers))
        (evidence / f"{lane}-runtime-initialization.json").write_text(json.dumps(
            {"commandExitCode": runtime_started.returncode,
             "commandStdout": runtime_started.stdout[-4000:],
             "commandStderr": runtime_started.stderr[-4000:], "status": runtime_status,
             "containerBinarySHA256": sha256(Path(lane_env["DEVCONTAINER_CONTAINER_BIN"])),
             "apiServerSHA256": sha256(api),
             "providerHelperSHA256": {label: value["sha256"] for label, value in expected_helpers.items()},
             "apiDefinitionCapture": api_definition_capture,
             "providerHelperHome": helper_home,
             "kernelInstall": "maintained system start under private HOME"}, sort_keys=True, indent=2) + "\n")
        if not provider_started:
            raise RuntimeError(f"{lane} provider did not reach running status")
        cli, vscode = lane_commands(args, lane, evidence)
        suites_started = True
        cli_result, vscode_result, suites_passed = run_suite_pair(
            lambda: command_outcome(cli, env=selected_fixture_environment(
                                        lane_env, getattr(args, "component_fixture", None)),
                                    timeout=3 * 60 * 60,
                                    capture_directory=controller_capture_directory(evidence, lane, "cli")),
            lambda: command_outcome(vscode, env={**lane_env, "DEVCONTAINER_VSCODE_BIN": str(args.vscode_bin),
                                                 "DEVCONTAINER_VSCODE_APP": str(args.vscode_app)}, timeout=90 * 60,
                                    capture_directory=controller_capture_directory(evidence, lane, "vscode")),
            lambda: cli_cleanup_is_complete(evidence, lane, getattr(args, "component_fixture", None)),
            lambda: vscode_cleanup_is_complete(evidence, lane),
            component_fixture=getattr(args, "component_fixture", None))
        runtime.verify()
        if not suites_passed:
            primary_error = RuntimeError(f"{lane} parity command returned a nonzero status")
        if getattr(args, "component_fixture", None):
            result_path = evidence / lane / "results.json"
            if cli_result.returncode or json.loads(result_path.read_text()).get("status") != "passed":
                primary_error = RuntimeError(f"{lane} component fixture did not pass")
        else:
            for suite, command_result, result_path in (
                    ("CLI", cli_result, evidence / lane / "results.json"),
                    ("V01", vscode_result, evidence / "vscode" / lane / "results.json")):
                if command_result.returncode or json.loads(result_path.read_text()).get("status") != "passed":
                    primary_error = RuntimeError(f"{lane} {suite} parity result did not pass")
    except BaseException as error:
        primary_error = error

    cleanup_error = None
    component_fixture = getattr(args, "component_fixture", None)
    complete = lane_cleanup_is_complete(evidence, lane, component_fixture)
    if provider_started and suites_started and not complete:
        cleanup_error = RuntimeError(f"{lane} fixture cleanup is incomplete; preserving active provider and quarantine")
    if runtime.switch is not None and api_definition_capture is not None:
        try:
            runtime.restore_system_start_api_definition()
        except BaseException as error:
            cleanup_error = error
    if provider_started and (complete or not suites_started) and cleanup_error is None:
        try:
            stopped = run([lane_env["DEVCONTAINER_CONTAINER_BIN"], "system", "stop"],
                          env=lane_env, timeout=120, capture=True)
            (evidence / f"{lane}-runtime-stop.json").write_text(json.dumps(
                {"exitCode": stopped.returncode, "stdout": stopped.stdout[-4000:],
                 "stderr": stopped.stderr[-4000:]}, sort_keys=True, indent=2) + "\n")
            provider_stopped = True
        except BaseException as error:
            cleanup_error = error
    if runtime.switch is not None and (not provider_started or provider_stopped) and cleanup_error is None:
        try:
            runtime.restore_provider_helper_definitions()
            runtime.restore()
            restored = True
        except BaseException as error:
            cleanup_error = error
    if restored and (not provider_started or provider_stopped) and cleanup_error is None:
        try:
            if keychain_created:
                run_keychain(root, "delete", runtime.journal)
            runtime.preserve_logs()
            private_receipt = runtime.receipt()
            journal_receipt = {"schemaVersion": 1, "lane": lane, "status": "restored",
                               "ownerSHA256": private_receipt.get("ownerSHA256"),
                               "records": private_receipt.get("records"), "seal": private_receipt.get("seal")}
            (evidence / f"{lane}-service-journal-receipt.json").write_text(
                json.dumps(journal_receipt, sort_keys=True, indent=2) + "\n")
            if json.loads((root / "owner.json").read_text()) != owner:
                raise RuntimeError("Apple lane ownership marker changed; preserving scratch")
            shutil.rmtree(root)
        except BaseException as error:
            cleanup_error = error
        else:
            cleanup[lane] = {"status": "restored",
                             "cliCleanupComplete": cli_cleanup_is_complete(evidence, lane, component_fixture),
                             "vscodeCleanupComplete": False if component_fixture else vscode_cleanup_is_complete(evidence, lane),
                             "providerStopped": provider_stopped, "serviceRestored": True,
                             "serviceJournalReceiptSHA256": sha256(evidence / f"{lane}-service-journal-receipt.json")}
            if component_fixture:
                cleanup[lane]["vscodeStatus"] = "skipped"
    else:
        cleanup[lane] = {"status": "uncertain", "providerStopped": provider_stopped,
                         "serviceRestored": restored, "scratchRoot": str(root)}
        if component_fixture:
            cleanup[lane]["cliCleanupComplete"] = cli_cleanup_is_complete(evidence, lane, component_fixture)
            cleanup[lane]["vscodeCleanupComplete"] = False
            cleanup[lane]["vscodeStatus"] = "skipped"
        cleanup_error = cleanup_error or RuntimeError(f"{lane} restoration is uncertain; guard and scratch retained")
    if cleanup_error is not None:
        raise RuntimeError(f"{lane} cleanup failed: {cleanup_error}") from primary_error
    if primary_error is not None:
        raise primary_error


def provider_install_root(lane: str, args: argparse.Namespace) -> Path:
    executable = args.stock_container_bin if lane == "apple-stock" else args.compose_container_bin
    return executable.parent.parent


def cli_cleanup_is_complete(evidence: Path, lane: str,
                            component_fixture: str | None = None) -> bool:
    """Check maintained CLI cleanup independently so V01 can still run after a clean failure."""
    lane_root = evidence / lane
    try:
        result = json.loads((lane_root / "results.json").read_text())
        fixtures = result.get("fixtures", [])
        manifest = json.loads((REPOSITORY / "Tests/Parity/manifest.json").read_text())
        expected = {item["id"]: item.get("runner") for item in manifest["fixtures"]
                    if item.get("runner") != "vscode" and lane in item.get("backends", [])}
        if component_fixture is not None:
            if component_fixture != COMPONENT_FIXTURE or component_fixture not in expected:
                return False
            expected = {component_fixture: expected[component_fixture]}
        if (len(fixtures) != len(expected) or {item.get("id") for item in fixtures} != set(expected)
                or result.get("cleanupDifferences") != []):
            return False
        # Engine-runner fixtures don't emit per-fixture cleanup.log files; the
        # maintained LaneRunner verifies the shared state DB has zero projects
        # and runtime_containers and reports any survivor in cleanupDifferences.
        for fixture, runner in expected.items():
            if runner == "engine":
                continue
            cleanup = lane_root / "raw" / fixture / "cleanup.log"
            if not cleanup.is_file() or cleanup.read_text(errors="replace").startswith("ERROR:"):
                return False
        return True
    except (OSError, ValueError, TypeError):
        return False


def vscode_cleanup_is_complete(evidence: Path, lane: str) -> bool:
    """Read the maintained V01 result and security scan cleanup contract."""
    try:
        manifest = json.loads((REPOSITORY / "Tests/Parity/manifest.json").read_text())
        vscode = json.loads((evidence / "vscode" / lane / "results.json").read_text())
        rows = vscode.get("fixtures")
        vscode_ids = [item["id"] for item in manifest["fixtures"]
                      if item.get("runner") == "vscode" and lane in item.get("backends", [])]
        if (not isinstance(rows, list) or len(rows) != 1 or len(vscode_ids) != 1 or
                rows[0].get("id") != vscode_ids[0] or
                rows[0].get("observations", {}).get("cleanup") != "true"):
            return False
        security = json.loads((evidence / "vscode" / lane / "security-scan.json").read_text())
        return isinstance(security, dict)
    except (OSError, ValueError, TypeError):
        return False


def lane_cleanup_is_complete(evidence: Path, lane: str,
                             component_fixture: str | None = None) -> bool:
    if component_fixture is not None:
        return cli_cleanup_is_complete(evidence, lane, component_fixture)
    return cli_cleanup_is_complete(evidence, lane) and vscode_cleanup_is_complete(evidence, lane)


def record_docker_cleanup(evidence: Path, cleanup: dict[str, dict], initial: str,
                          final: str, started_here: bool, suites_started: bool,
                          component_fixture: str | None = None) -> None:
    """Keep the global runtime guard unless both Docker cleanup proofs and Colima restore pass."""
    suite_clean = lane_cleanup_is_complete(evidence, "docker", component_fixture)
    complete = docker_restore_is_safe(started_here=started_here, suites_started=suites_started,
                                      suite_cleanup_complete=suite_clean, initial=initial, final=final)
    cleanup["docker"] = {
        "status": "restored" if complete else "uncertain",
        "cliCleanupComplete": cli_cleanup_is_complete(evidence, "docker", component_fixture),
        "vscodeCleanupComplete": False if component_fixture else vscode_cleanup_is_complete(evidence, "docker"),
        "colima": {"initial": initial, "final": final,
                   "restored": complete, "startedByController": started_here},
    }
    if component_fixture:
        cleanup["docker"]["vscodeStatus"] = "skipped"


def docker_lane(args: argparse.Namespace, evidence: Path, endpoint: str,
                base_env: dict[str, str], initial: str, cleanup: dict[str, dict]) -> None:
    started_here = initial == "stopped"
    primary_error = None
    suites_started = False
    cleanup["docker"] = {"status": "active"}
    lane_env = dict(base_env)
    lane_env["DEVCONTAINER_DOCKER_ORACLE_HOST"] = endpoint
    try:
        if started_here:
            run([str(args.colima_bin), "--profile", "default", "start", "--runtime", "docker"],
                env=base_env, timeout=15 * 60)
            if colima_state(args.colima_bin, base_env)[0] != "running":
                raise RuntimeError("Colima default profile failed to reach running state")
        oracle_env = {**base_env, "DOCKER_HOST": endpoint}
        manifest = json.loads((REPOSITORY / "Tests/Parity/manifest.json").read_text())
        pins = manifest["referencePins"]["docker"]
        compose_pins = manifest["referencePins"]["docker"]
        client = run([str(args.docker_bin), "version", "--format", "{{.Client.Version}}"],
                     env=oracle_env, capture=True).stdout.strip()
        server = run([str(args.docker_bin), "version", "--format", "{{.Server.Version}}"],
                     env=oracle_env, capture=True).stdout.strip()
        api = run([str(args.docker_bin), "version", "--format", "{{.Server.APIVersion}}"],
                  env=oracle_env, capture=True).stdout.strip()
        commit = run([str(args.docker_bin), "version", "--format", "{{.Server.GitCommit}}"],
                    env=oracle_env, capture=True).stdout.strip()
        if (client != pins["cliVersion"] or server != pins["engineVersion"] or
                api != pins["engineApiVersion"] or commit != pins["engineCommit"]):
            raise RuntimeError("Docker oracle client or daemon identity differs from the parity manifest")
        buildx_version = docker_buildx_version(args.docker_buildx_bin)
        buildx_sha256 = sha256(args.docker_buildx_bin)
        if (buildx_version != pins["buildxVersion"]
                or buildx_sha256 != pins["buildxSHA256"]):
            raise RuntimeError("Docker Buildx executable differs from the parity manifest")
        dockerd_hash = run([str(args.colima_bin), "--profile", "default", "ssh", "--",
                            "sha256sum", "/usr/bin/dockerd"],
                           env=base_env, capture=True).stdout.split()[0]
        if dockerd_hash != pins["engineSHA256"]:
            raise RuntimeError("Colima dockerd executable differs from the parity manifest")
        compose_version = run([str(args.docker_compose_bin), "version", "--short"],
                              env=oracle_env, capture=True).stdout.strip()
        if (compose_version != compose_pins["composeVersion"] or
                sha256(args.docker_compose_bin) != compose_pins["composeSHA256"]):
            raise RuntimeError("Docker Compose reference version differs from the parity manifest")
        (evidence / "docker-runtime-identity.json").write_text(json.dumps(
            {"endpoint": endpoint, "clientVersion": client, "engineVersion": server,
             "engineApiVersion": api, "engineCommit": commit,
             "dockerdSHA256": dockerd_hash, "composeVersion": compose_version,
             "buildxVersion": buildx_version, "buildxSHA256": buildx_sha256,
             "dockerBinarySHA256": sha256(args.docker_bin),
             "dockerComposeSHA256": sha256(args.docker_compose_bin)},
            sort_keys=True, indent=2) + "\n")
        cli, vscode = lane_commands(args, "docker", evidence)
        suites_started = True
        cli_result, vscode_result, suites_passed = run_suite_pair(
            lambda: command_outcome(cli, env=selected_fixture_environment(
                                        {**lane_env, "DEVCONTAINER_DOCKER_ORACLE_HOST": endpoint},
                                        getattr(args, "component_fixture", None)),
                                    timeout=3 * 60 * 60,
                                    capture_directory=controller_capture_directory(evidence, "docker", "cli")),
            lambda: command_outcome(vscode, env={**lane_env, "DEVCONTAINER_DOCKER_ORACLE_HOST": endpoint,
                                                 "DEVCONTAINER_VSCODE_BIN": str(args.vscode_bin),
                                                 "DEVCONTAINER_VSCODE_APP": str(args.vscode_app)}, timeout=90 * 60,
                                    capture_directory=controller_capture_directory(evidence, "docker", "vscode")),
            lambda: cli_cleanup_is_complete(evidence, "docker", getattr(args, "component_fixture", None)),
            lambda: vscode_cleanup_is_complete(evidence, "docker"),
            component_fixture=getattr(args, "component_fixture", None))
        if getattr(args, "component_fixture", None):
            if cli_result.returncode or json.loads((evidence / "docker" / "results.json").read_text()).get("status") != "passed":
                raise RuntimeError("Docker component fixture did not pass")
        else:
            for suite, command_result, result_path in (
                    ("CLI", cli_result, evidence / "docker" / "results.json"),
                    ("V01", vscode_result, evidence / "vscode" / "docker" / "results.json")):
                if command_result.returncode or json.loads(result_path.read_text()).get("status") != "passed":
                    raise RuntimeError(f"Docker {suite} parity result did not pass")
    except BaseException as error:
        primary_error = error
    finally:
        component_fixture = getattr(args, "component_fixture", None)
        suite_clean = lane_cleanup_is_complete(evidence, "docker", component_fixture)
        safe_to_stop = docker_stop_allowed(started_here=started_here, suites_started=suites_started,
                                           suite_cleanup_complete=suite_clean)
        cleanup_error = None
        if started_here and safe_to_stop:
            current = colima_state(args.colima_bin, base_env)[0]
            if current == "running":
                try:
                    run([str(args.colima_bin), "--profile", "default", "stop"], env=base_env, timeout=15 * 60)
                except BaseException as error:
                    cleanup_error = error
                current = colima_state(args.colima_bin, base_env)[0]
            if current != "stopped":
                cleanup_error = cleanup_error or RuntimeError("Colima default profile did not return to its initial stopped state")
        elif colima_state(args.colima_bin, base_env)[0] != "running":
            cleanup_error = cleanup_error or RuntimeError("Initially running Colima default profile stopped during parity")
        final, _ = colima_state(args.colima_bin, base_env)
        record_docker_cleanup(evidence, cleanup, initial, final, started_here, suites_started,
                              component_fixture)
        if cleanup_error is not None:
            raise RuntimeError(f"Docker restoration failed: {cleanup_error}") from primary_error
    if primary_error is not None:
        raise primary_error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="perform the live 84-observation campaign")
    parser.add_argument("--component-fixture", choices=(COMPONENT_FIXTURE,),
                        help="run only the named CLI fixture as a non-qualifying component check")
    parser.add_argument("--repository", type=Path, default=REPOSITORY)
    parser.add_argument("--ssd-root", type=Path, required=True,
                        help="fresh runtime evidence parent on the enrolled SSD")
    parser.add_argument("--retained-root", type=Path, required=True,
                        help="internal retained root for the sealed qualification")
    parser.add_argument("--qualification-directory", type=Path, required=True)
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--finalized-directory", required=True, type=Path)
    parser.add_argument("--provenance-sha256", required=True)
    parser.add_argument("--state-sha256", required=True)
    parser.add_argument("--accepted-state", required=True, type=Path)
    parser.add_argument("--docker-bin", required=True, type=Path)
    parser.add_argument("--docker-compose-bin", required=True, type=Path)
    parser.add_argument("--docker-buildx-bin", required=True, type=Path,
                        help="canonical manifest-pinned docker-buildx executable")
    parser.add_argument("--stock-container-bin", required=True, type=Path)
    parser.add_argument("--compose-container-bin", required=True, type=Path)
    parser.add_argument("--compose-provider-bin", required=True, type=Path)
    parser.add_argument("--colima-bin", required=True, type=Path)
    parser.add_argument("--vscode-bin", required=True, type=Path)
    parser.add_argument("--vscode-app", required=True, type=Path)
    parser.add_argument("--vscode-vsix", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--stock-container-sha256", required=True)
    parser.add_argument("--stock-api-sha256", required=True)
    parser.add_argument("--compose-container-sha256", required=True)
    parser.add_argument("--compose-api-sha256", required=True)
    parser.add_argument("--compose-provider-sha256", required=True)
    parser.add_argument("--colima-sha256", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    global REPOSITORY, SSD, RETAINED, JOURNAL_PARENT, GUARD_PATH
    REPOSITORY = args.repository
    SSD = args.ssd_root
    RETAINED = args.retained_root
    JOURNAL_PARENT = RETAINED / "runtime-journals"
    GUARD_PATH = DEFAULT_WORKFLOW_RETAINED / "runtime-admission.json"
    component_fixture = getattr(args, "component_fixture", None)
    binaries = validate_inputs(args)
    if not component_fixture and (not args.vscode_app.is_absolute() or args.vscode_app.resolve(strict=True) != args.vscode_app
                                  or not (args.vscode_app / "Contents/MacOS/Code").is_file()):
        raise ValueError("VS Code app must be the exact staged application bundle")
    manifest = args._manifest
    if not component_fixture:
        expected_vsix = manifest["referencePins"]["vscode"]["devContainersExtension"]["vsixSHA256"]
        if (not args.vscode_vsix.is_absolute() or args.vscode_vsix.resolve(strict=True) != args.vscode_vsix
                or sha256(args.vscode_vsix) != expected_vsix):
            raise ValueError("VSIX must be the exact manifest-pinned retained extension archive")
    if not args.execute:
        preflight = {"scope": "component-only-inert-preflight", "componentFixture": component_fixture,
                     "releaseAuthority": False, "sourceCommit": args.source_commit,
                     "providerSHA256": binaries, "lanes": LANES,
                     "evidence": str(args.evidence)} if component_fixture else {
                         "scope": "inert-preflight", "sourceCommit": args.source_commit,
                         "providerSHA256": binaries, "lanes": LANES, "observations": 84,
                         "evidence": str(args.evidence),
                         "qualificationRoot": str(args.qualification_directory)}
        print(json.dumps(preflight, sort_keys=True, indent=2))
        return 0

    if component_fixture and (args.evidence.exists() or args.evidence.is_symlink()):
        raise ValueError("Component evidence root must be fresh")

    # Exact signed-package admissions precede lease acquisition and any runtime,
    # Colima, launchd, provider or keychain mutation.
    package_admissions = admit_package_before_runtime(args)
    prepare_finalized_package = load_finalized_package_helper(REPOSITORY)
    package_proof, _ = prepare_finalized_package.read_provenance(
        args.finalized_directory, args.provenance_sha256)
    if (package_proof.get("sourceCommit") != args.source_commit
            or package_proof.get("trustedStateSHA256") != args.state_sha256
            or package_proof.get("archiveSHA256") != package_admissions["apple-stock"]["archiveSHA256"]):
        raise ValueError("finalized package proof differs from the exact accepted archive and source")
    if component_fixture:
        args._component_package_proof = package_proof
    args._parity_harness_sha256 = load_run_lane(REPOSITORY).parity_harness_sha256(REPOSITORY)
    args._package_admissions = package_admissions

    os.umask(0o077)
    args.evidence.mkdir(parents=True, mode=0o700)
    JOURNAL_PARENT.mkdir(parents=True, mode=0o700, exist_ok=True)
    if stat_mode(JOURNAL_PARENT) != 0o700 or stat_mode(GUARD_PATH.parent) != 0o700:
        raise ValueError("private retained journal and guard directories must be mode 0700")
    original_environment = dict(os.environ)
    endpoint = default_colima_endpoint(ACCOUNT_HOME)
    base_env = {key: value for key, value in original_environment.items()
                if key in {"LANG", "LC_ALL", "LC_CTYPE", "DEVELOPER_DIR", "SDKROOT", "TOOLCHAINS"}}
    base_env.update({"HOME": str(ACCOUNT_HOME),
                     "PATH": f"{args.docker_compose_bin.parent}:{args.docker_bin.parent}:{args.stock_container_bin.parent}:{args.compose_container_bin.parent}:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
                     "TMPDIR": str(SSD), "TEMP": str(SSD), "TMP": str(SSD),
                     "DEVCONTAINER_DOCKER_BIN": str(args.docker_bin),
                     "DEVCONTAINER_DOCKER_COMPOSE_BIN": str(args.docker_compose_bin),
                     "DEVCONTAINER_PARITY_RETAINED_ROOT": str(args._guest_retained_root),
                     "DEVCONTAINER_VSCODE_BIN": str(args.vscode_bin),
                     "DEVCONTAINER_VSCODE_APP": str(args.vscode_app),
                     "DEVCONTAINER_VSCODE_LIVE": "1"})
    docker_config = create_docker_cli_config(args.evidence, args)
    base_env["DOCKER_CONFIG"] = str(docker_config)
    if not component_fixture:
        reference = args.evidence / "vscode" / "reference"
        reference.mkdir(parents=True, mode=0o700)
        extension_version = manifest["referencePins"]["vscode"]["devContainersExtension"]["version"]
        shutil.copyfile(args.vscode_vsix, reference / f"remote-containers-{extension_version}.vsix")
    initial_colima, initial_colima_detail = colima_state(args.colima_bin, base_env)
    initial_services, initial_service_count = host_service_digest()
    operator_inputs = {
        "sourceCommit": args.source_commit, "sourceTree": args._source_tree,
        "campaign": args.campaign,
        "finalizationProvenanceSHA256": args.provenance_sha256,
        "trustedStateSHA256": args.state_sha256,
        "archiveSHA256": package_proof["archiveSHA256"],
        "packageAdmissions": package_admissions, "providerSHA256": binaries,
        "guestInputAdmissions": args._guest_input_admissions,
        "initialColima": initial_colima, "initialColimaDetail": initial_colima_detail,
        "initialServiceSetSHA256": initial_services, "initialServiceCount": initial_service_count,
    }
    write_json(args.evidence / "operator-inputs.json", operator_inputs)
    if component_fixture:
        args._component_evidence_identity = capture_component_evidence_identity(
            args.evidence, operator_inputs)
    sys.path.insert(0, str(REPOSITORY / "Tools/testing"))
    from host_runtime import HostGuard, cancellation, runtime_lease
    guard = HostGuard(GUARD_PATH)
    transaction_owner = {"identity": {"campaign": args.campaign, "sourceCommit": args.source_commit,
                                      "scope": "finalized-native-parity-component" if component_fixture
                                      else "finalized-native-parity"}, "root": str(args.evidence)}
    cleanup = {lane: {"status": "not-started"} for lane in LANES}
    guard_cleared = False
    errors = []
    with runtime_lease(LEASE_PATH, guard), cancellation():
        guard.begin(transaction_owner)
        try:
            try:
                docker_lane(args, args.evidence, endpoint, base_env, initial_colima, cleanup)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                errors.append(f"docker: {error}")
            if cleanup["docker"].get("status") == "restored":
                for lane, api in (
                    ("apple-stock", args.stock_container_bin.parent / "container-apiserver"),
                    ("container-compose", args.compose_container_bin.parent / "container-apiserver"),
                ):
                    if any(cleanup[prior].get("status") != "restored" for prior in LANES[:LANES.index(lane)]):
                        break
                    try:
                        apple_lane(args, lane, args.evidence, api.resolve(strict=True), base_env, cleanup)
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                        errors.append(f"{lane}: {error}")
                        if cleanup[lane].get("status") != "restored":
                            break
            comparison_status = {}
            component_comparison = {"status": "failed"} if component_fixture else None
            if component_fixture:
                try:
                    component_comparison = compare_component_results(args.evidence, component_fixture)
                    comparison_status["component"] = {"status": component_comparison.get("status")}
                    if component_comparison.get("status") != "passed":
                        errors.append("E13 component comparison did not pass")
                except (OSError, ValueError, RuntimeError, KeyError, TypeError, ImportError) as error:
                    component_comparison = {"status": "failed"}
                    comparison_status["component"] = {"status": "failed"}
                    errors.append(f"E13 component comparison failed: {error}")
            else:
                for suite, root in (("cli", args.evidence), ("vscode", args.evidence / "vscode")):
                    comparison = run([sys.executable, str(REPOSITORY / "Tools/parity/compare_results.py"),
                                      str(root), "--manifest", str(REPOSITORY / "Tests/Parity/manifest.json"),
                                      "--suite", suite], env=base_env, timeout=5 * 60, capture=True, check=False)
                    comparison_path = root / "comparison.json"
                    comparison_payload = json.loads(comparison_path.read_text()) if comparison_path.is_file() else {}
                    comparison_status[suite] = {"status": comparison_payload.get("status")}
                    if comparison.returncode or comparison_status[suite]["status"] != "passed":
                        errors.append(f"{suite} comparison exited {comparison.returncode}")
                release_validation = run([sys.executable, str(REPOSITORY / "Tools/parity/validate_manifest.py"),
                                          "--release"], env=base_env, timeout=5 * 60, capture=True, check=False)
                if release_validation.returncode == 0:
                    write_json(args.evidence / "release-manifest.json", {
                        "schemaVersion": 1, "status": "passed", "sourceCommit": args.source_commit,
                        "manifestSHA256": sha256(REPOSITORY / "Tests/Parity/manifest.json"),
                        "validator": "Tools/parity/validate_manifest.py --release", "exitCode": 0})
                    comparison_status["releaseManifest"] = {"status": "passed"}
                else:
                    comparison_status["releaseManifest"] = {"status": "failed"}
                    errors.append(f"release manifest validation exited {release_validation.returncode}")
        finally:
            interrupted = isinstance(sys.exc_info()[1], KeyboardInterrupt)
            if interrupted:
                errors.append("qualification interrupted; child groups were terminated and cleanup was attempted")
            active_error = sys.exc_info()[1]
            if active_error is not None and not interrupted:
                errors.append(f"qualification aborted: {type(active_error).__name__}")
            guard_cleared = _finalize_host_cleanup(
                args.evidence, args, base_env, cleanup, initial_colima, initial_colima_detail,
                initial_services, initial_service_count, guard, transaction_owner, errors,
                interrupted=interrupted)

    if (subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPOSITORY, text=True).strip() != args.source_commit
            or subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"],
                                       cwd=REPOSITORY, text=True)):
        errors.append("repository source changed during parity execution")
    if (sha256(args.finalized_directory / "native-finalization-provenance.json") != args.provenance_sha256
            or sha256(args.accepted_state / "state.json") != args.state_sha256):
        errors.append("finalized package or accepted state changed during parity execution")
    if not guard_cleared:
        errors.append("runtime guard was not cleared")
    if not all(cleanup[lane].get("status") == "restored" for lane in LANES):
        errors.append("one or more runtime lanes were not fully restored")
    if component_fixture:
        component_comparison = component_comparison or {"status": "failed"}
        component_provider_inputs = {}
        try:
            final_binaries = validate_inputs(
                args, initialized_component_identity=args._component_evidence_identity)
            if final_binaries != binaries or args._source_tree != json.loads(
                    (args.evidence / "operator-inputs.json").read_text())["sourceTree"]:
                raise ValueError("source or provider inputs changed during component run")
            final_package_admissions = admit_package_before_runtime(args)
            if final_package_admissions != package_admissions:
                raise ValueError("finalized package admissions changed during component run")
            final_proof, _ = prepare_finalized_package.read_provenance(
                args.finalized_directory, args.provenance_sha256)
            if (final_proof != package_proof
                    or sha256(args.finalized_directory / "native-finalization-provenance.json") != args.provenance_sha256
                    or sha256(args.accepted_state / "state.json") != args.state_sha256):
                raise ValueError("finalization provenance or accepted state changed during component run")
            component_provider_inputs = {
                "providerSHA256": final_binaries,
                "admittedPackageLanes": final_package_admissions,
                "providerHelperEvidence": args._provider_helper_evidence,
                "laneEvidenceSHA256": {},
            }
            for lane in LANES:
                lane_root = args.evidence / lane
                validate_provider_result(lane_root / "results.json", lane, final_binaries)
                validate_provider_fingerprint(lane_root / "fingerprint.json", lane,
                                              final_binaries, args._manifest)
                component_provider_inputs["laneEvidenceSHA256"][lane] = {
                    "results": sha256(lane_root / "results.json"),
                    "fingerprint": sha256(lane_root / "fingerprint.json"),
                }
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
            errors.append(f"component input recheck failed: {error}")
        host_payload = json.loads((args.evidence / "host-cleanup.json").read_text())
        payload = finalize_component_result(args, cleanup, component_comparison, host_payload,
                                            binaries, component_provider_inputs, errors)
        errors = payload["failures"]
        passed = payload["status"] == "passed"
        if not passed:
            write_json(args.evidence / "controller-failure.json", {"status": "failed", "errors": errors})
            print(json.dumps({"status": "failed", "scope": "component-only",
                              "evidence": str(args.evidence), "errors": errors}, indent=2),
                  file=sys.stderr)
            return 1
        print(json.dumps({"status": "passed", "scope": "component-only",
                          "releaseAuthority": False, "componentResult": str(args.evidence / "component-result.json"),
                          "sourceCommit": args.source_commit, "fixture": COMPONENT_FIXTURE,
                          "fixtureResults": 3}, sort_keys=True, indent=2))
        return 0

    if errors:
        write_json(args.evidence / "controller-failure.json", {"status": "failed", "errors": errors})
        print(json.dumps({"status": "failed", "evidence": str(args.evidence), "errors": errors}, indent=2),
              file=sys.stderr)
        return 1

    try:
        provider_tools = make_provider_tools(args, args.evidence)
        # The API/front-end bytes are checked again after all lane switching.
        if (sha256(args.stock_container_bin) != binaries["stockContainer"]
                or sha256(args.compose_container_bin) != binaries["composeContainer"]
                or sha256(args.docker_buildx_bin) != binaries["dockerBuildx"]
                or sha256(args.compose_provider_bin) != binaries["composeProvider"]
                or sha256(args.stock_container_bin.parent / "container-apiserver") != binaries["stockAPIServer"]
                or sha256(args.compose_container_bin.parent / "container-apiserver") != binaries["composeAPIServer"]):
            raise ValueError("provider or API bytes changed during the qualification")
        final, receipt_sha = seal_after_host_cleanup(args, args.evidence, cleanup,
                                                    comparison_status, provider_tools,
                                                    package_proof)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        write_json(args.evidence / "controller-failure.json", {"status": "failed", "errors": [str(error)]})
        print(f"qualification evidence was not sealed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "passed", "qualificationDirectory": str(final),
                      "qualificationSHA256": receipt_sha, "sourceCommit": args.source_commit,
                      "fixtureResults": 84}, sort_keys=True, indent=2))
    return 0


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("error: qualification interrupted after owned-process cleanup", file=sys.stderr)
        raise SystemExit(130)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
