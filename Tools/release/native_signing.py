#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# https://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Sign the six native package executables and retain resumable notary evidence."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
import pwd
from pathlib import Path
import plistlib
import re
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
import zipfile

def account_home() -> Path:
    """Keep durable authority under the real account, independent of fixture HOME."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS.parent / "bazel/package_checks"))
from cli_process import session_members, terminate_session  # noqa: E402

PRODUCTS = ("devcontainer", "devcontainer-compose", "devcontainer-engine", "devcontainer-docker")
REFERENCE = "libexec/devcontainer/reference/"
PLUGIN = "libexec/container/plugins/devcontainer/bin/devcontainer"
NODE = REFERENCE + "node"
BINARIES = tuple("bin/" + name for name in PRODUCTS) + (PLUGIN, NODE)
REFERENCE_FILES = {"node", "NODE-LICENSE.txt", "runtime-lock.json", "cli/devcontainer.js",
                   "cli/dist/spec-node/devContainersSpecCLI.js", "cli/scripts/updateUID.Dockerfile",
                   "cli/package.json", "cli/LICENSE.txt", "cli/ThirdPartyNotices.txt"}
JIT = {"com.apple.security.cs.allow-jit": True}
UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
SHA = re.compile(r"[0-9a-f]{64}")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write(path: Path, value: dict) -> None:
    """Atomically retain state before any subsequent external operation."""
    temporary = path.with_name(path.name + ".new")
    with temporary.open("xb") as stream:
        stream.write((json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode())
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def physical(path: Path) -> Path:
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("Path must be absolute and have no aliases or symbolic links")
    return path


def validate_storage(stage: Path, state: Path, scratch: Path, evidence: Path) -> None:
    """Production policy: durable evidence internal, disposable work on the SSD."""
    internal = account_home() / "Library/Application Support/ContainerFamily/retained/devcontainer/notary"
    ssd = Path("/Volumes/SSD")
    for path in (stage, state, scratch, evidence, internal, ssd):
        physical(path)
    if not state.is_relative_to(internal) or state == internal:
        raise ValueError("Notary state must be beneath internal retained devcontainer/notary")
    if not scratch.is_relative_to(ssd) or scratch == ssd:
        raise ValueError("Notary scratch must be beneath /Volumes/SSD")
    if not ssd.is_mount() or ssd.stat().st_dev == account_home().stat().st_dev:
        raise ValueError("SSD scratch is not a separate mounted filesystem")
    if evidence.is_relative_to(stage) or evidence.is_relative_to(scratch):
        raise ValueError("Acceptance evidence must be outside the disposable stage and scratch")
    if stage.is_relative_to(state) or state.is_relative_to(stage):
        raise ValueError("Stage and durable notary state must be disjoint")
    state.parent.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    for path in (state.parent, scratch):
        physical(path)
    if state.parent.stat().st_dev != account_home().stat().st_dev:
        raise ValueError("Notary evidence is not on internal storage")
    existing = evidence.parent
    while not existing.exists():
        existing = existing.parent
    if existing.stat().st_dev != account_home().stat().st_dev:
        raise ValueError("Acceptance evidence must be on internal storage")
    if scratch.stat().st_dev != ssd.stat().st_dev:
        raise ValueError("Scratch is not on the selected SSD")


def inventory(stage: Path, evidence: Path) -> dict:
    result = {}
    for path in sorted(stage.rglob("*")):
        if path == evidence:
            if path.is_symlink():
                raise ValueError("Acceptance evidence must not be a symbolic link")
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise ValueError("Package contains a link or special file")
        if path.is_file():
            if info.st_nlink != 1:
                raise ValueError("Package files must not be hard-linked")
            result[path.relative_to(stage).as_posix()] = {
                "sha256": digest(path), "size": info.st_size, "mode": stat.S_IMODE(info.st_mode)}
    return result


def authenticate(stage: Path, receipt_path: Path, expected: str, evidence: Path) -> tuple[dict, dict]:
    """The caller supplies the previously admitted candidate receipt's checksum."""
    physical(stage)
    physical(receipt_path)
    if not SHA.fullmatch(expected) or digest(receipt_path) != expected:
        raise ValueError("Candidate receipt checksum differs")
    receipt = json.loads(receipt_path.read_bytes())
    if (receipt.get("schemaVersion") != 2 or receipt.get("kind") != "unsigned-native-candidate"
            or receipt.get("distributionReady") is not False or receipt.get("architecture") != "arm64"
            or receipt.get("compilationMode") != "opt" or receipt.get("runtimeProfile") not in {"stock", "enhanced"}
            or set(receipt.get("products", {})) != set(PRODUCTS)
            or not re.fullmatch(r"[0-9a-f]{40}", receipt.get("commit", ""))):
        raise ValueError("Expected an admitted four-product native candidate")
    tree = inventory(stage, evidence)
    embedded = {key: value for key, value in receipt.items() if key not in {"archiveSHA256", "archiveSize"}}
    if json.loads((stage / "share/devcontainer/candidate.json").read_bytes()) != embedded:
        raise ValueError("Embedded candidate identity differs")
    if digest(stage / "share/devcontainer/Package.resolved") != receipt["dependencyLockSHA256"]:
        raise ValueError("Packaged dependency lock differs")
    reference = receipt["referenceRuntime"]
    if set(reference["files"]) != REFERENCE_FILES:
        raise ValueError("Private reference runtime inventory differs")
    for relative, checksum in reference["files"].items():
        if tree.get(REFERENCE + relative, {}).get("sha256") != checksum:
            raise ValueError("Private reference runtime bytes differ")
    lock = json.loads((stage / REFERENCE / "runtime-lock.json").read_bytes())
    if (reference["lockSHA256"] != reference["files"]["runtime-lock.json"]
            or reference["nodeVersion"] != lock["node"]["version"]
            or reference["cliVersion"] != lock["cli"]["version"]
            or json.loads((stage / REFERENCE / "cli/package.json").read_bytes())["version"] != reference["cliVersion"]):
        raise ValueError("Private runtime versions differ from its authenticated lock")
    expected_binaries = {"bin/" + key: value for key, value in receipt["products"].items()}
    expected_binaries[PLUGIN] = receipt["products"]["devcontainer"]
    expected_binaries[NODE] = reference["files"]["node"]
    macho = set()
    for relative in tree:
        path = stage / relative
        with path.open("rb") as stream:
            header = stream.read(16)
        if header[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"):
            macho.add(relative)
        if relative in expected_binaries:
            if len(header) != 16 or struct.unpack("<IIII", header)[0:2] != (0xFEEDFACF, 0x0100000C) or struct.unpack("<IIII", header)[3] != 2:
                raise ValueError("Expected thin arm64 Mach-O executable: " + relative)
            if tree[relative]["sha256"] != expected_binaries[relative] or tree[relative]["mode"] != 0o755:
                raise ValueError("Unsigned executable checksum or mode differs: " + relative)
    if macho != set(BINARIES):
        raise ValueError("Package Mach-O closure is not exactly the six declared executables")
    return receipt, tree



def authenticate_provenance(path: Path, expected: str, receipt: dict, tree: dict) -> dict:
    """Bind the stager's caller-authenticated legal and metadata closure."""
    physical(path)
    if not path.is_file() or not SHA.fullmatch(expected) or digest(path) != expected:
        raise ValueError("Stage provenance checksum differs")
    value = json.loads(path.read_bytes())
    if (not isinstance(value, dict) or value.get("schema") != 1
            or value.get("scope") != "unsigned-native-package-stage"
            or value.get("sourceCommit") != receipt["commit"]
            or value.get("profile") != receipt["runtimeProfile"]
            or not isinstance(receipt.get("archiveSHA256"), str)
            or not SHA.fullmatch(receipt["archiveSHA256"])
            or value.get("candidateAssetSHA256") != receipt["archiveSHA256"]
            or value.get("candidateProducts") != receipt["products"]
            or value.get("selectedLockSHA256") != receipt["dependencyLockSHA256"]
            or value.get("privateRuntime") != receipt["referenceRuntime"]
            or value.get("legalCompleteness") is not True
            or any(value.get(key) is not False for key in
                   ("distributionReady", "signingComplete", "notarizationComplete"))):
        raise ValueError("Stage provenance source, candidate or legal authority differs")
    payload = value.get("unsignedPayloadInventory")
    if not isinstance(payload, dict) or not payload or payload != tree:
        raise ValueError("Unsigned package metadata or payload differs from stage provenance")
    for name, row in payload.items():
        if (not isinstance(name, str) or name.startswith("/")
                or any(part in {"", ".", ".."} for part in name.split("/"))
                or not isinstance(row, dict) or set(row) != {"sha256", "size", "mode"}
                or not isinstance(row["sha256"], str) or not SHA.fullmatch(row["sha256"])
                or type(row["size"]) is not int or row["size"] < 0
                or type(row["mode"]) is not int or not 0 <= row["mode"] <= 0o777):
            raise ValueError("Stage provenance payload inventory is invalid")
    return value


def require_signed_payload(unsigned: dict, signed: dict) -> None:
    """Only the six authenticated Mach-O files may change during signing."""
    if set(signed) != set(unsigned) or any(
            signed[name] != value for name, value in unsigned.items() if name not in BINARIES):
        raise ValueError("Signing modified non-executable package content")

def run_owned(command: list[str], *, cwd: Path, env: dict, stdout, stderr, timeout: int) -> int:
    """Protect the spawn/handle boundary, then reuse maintained exact-session cleanup."""
    process = None
    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    try:
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, signals)
        try:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=stderr, start_new_session=True)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        status = process.wait(timeout=timeout)
        if session_members(process.pid):
            raise RuntimeError("Signing tool exited with outstanding owned helpers")
        return status
    except BaseException:
        previous = {number: signal.signal(number, signal.SIG_IGN) for number in signals}
        try:
            if process is not None:
                terminate_session(process)
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
        raise


def tool(command: list[str], state: Path, label: str, timeout: int, *, env: dict | None = None) -> tuple[int, bytes, bytes]:
    """Keep raw tool output private; never print credentials or command arguments."""
    with (state / (label + ".stdout")).open("xb") as out, (state / (label + ".stderr")).open("xb") as err:
        try:
            status = run_owned(command, cwd=state, env=env or os.environ.copy(), stdout=out, stderr=err, timeout=timeout)
        finally:
            for stream in (out, err):
                stream.flush()
                os.fsync(stream.fileno())
    stdout, stderr = ((state / (label + suffix)).read_bytes() for suffix in (".stdout", ".stderr"))
    write(state / (label + ".result.json"), {"status": status, "stdoutSHA256": hashlib.sha256(stdout).hexdigest(),
                                           "stderrSHA256": hashlib.sha256(stderr).hexdigest()})
    return status, stdout, stderr


def checked(command: list[str], state: Path, label: str, timeout: int, *, env: dict | None = None) -> tuple[bytes, bytes]:
    status, stdout, stderr = tool(command, state, label, timeout, env=env)
    if status:
        raise ValueError("Tool failed; retained private result: " + label)
    return stdout, stderr


def verify_signatures(stage: Path, state: Path, team: str, prefix: str) -> dict:
    signatures = {}
    for index, relative in enumerate(BINARIES):
        path = str(stage / relative)
        checked(["/usr/bin/codesign", "--verify", "--strict", "--all-architectures", path], state, f"{prefix}-verify-{index}", 30)
        out, err = checked(["/usr/bin/codesign", "--display", "--verbose=4", path], state, f"{prefix}-display-{index}", 30)
        text = (out + err).decode("utf-8")
        authorities = re.findall(r"^Authority=(.*)$", text, re.M)
        if (len(authorities) != 3 or not authorities[0].startswith("Developer ID Application: ")
                or authorities[1:] != ["Developer ID Certification Authority", "Apple Root CA"]
                or re.findall(r"^TeamIdentifier=(.*)$", text, re.M) != [team]
                or not re.search(r"^Timestamp=.+$", text, re.M)
                or not re.search(r"flags=\S*\bruntime\b", text)):
            raise ValueError("Developer ID, team, timestamp or hardened runtime readback differs")
        out, _ = checked(["/usr/bin/codesign", "--display", "--entitlements", ":-", path], state, f"{prefix}-entitlements-{index}", 30)
        entitlements = plistlib.loads(out) if out.strip() else {}
        if entitlements != (JIT if relative == NODE else {}) or any(type(value) is not bool for value in entitlements.values()):
            raise ValueError("Unexpected entitlements: " + relative)
        signatures[relative] = {"sha256": digest(stage / relative), "teamIdentifier": team,
                                "authority": authorities[0], "entitlements": entitlements}
    return signatures


def smoke(stage: Path, state: Path, scratch: Path, receipt: dict, prefix: str) -> None:
    with tempfile.TemporaryDirectory(prefix="smoke-", dir=scratch) as directory:
        env = {"PATH": "/usr/bin:/bin", "HOME": directory, "TMPDIR": directory, "LANG": "en_US.UTF-8"}
        node = str(stage / NODE)
        out, _ = checked([node, "--version"], state, prefix + "-node", 30, env=env)
        if out.decode().strip() != "v" + receipt["referenceRuntime"]["nodeVersion"]:
            raise ValueError("Signed private Node version differs")
        out, _ = checked([node, str(stage / REFERENCE / "cli/devcontainer.js"), "--version"], state, prefix + "-cli", 30, env=env)
        if out.decode().strip() != receipt["referenceRuntime"]["cliVersion"]:
            raise ValueError("Signed private official CLI version differs")
        out, _ = checked([str(stage / "bin/devcontainer"), "version", "--format", "json"], state,
                         prefix + "-product", 30, env=env)
        version = json.loads(out)
        if any(version.get(key) != value for key, value in {
                "version": receipt["version"], "commit": receipt["commit"], "architecture": "arm64",
                "lane": "candidate", "buildType": "release"}.items()):
            raise ValueError("Signed product CLI source identity differs")


def verify_zip(archive: Path, stage: Path, tree: dict) -> None:
    actual = {}
    with zipfile.ZipFile(archive) as source:
        seen = set()
        for entry in source.infolist():
            parts = entry.filename.rstrip("/").split("/")
            if entry.filename in seen or any(part in {"", ".", ".."} for part in parts):
                raise ValueError("Duplicate or unsafe notary ZIP member")
            seen.add(entry.filename)
            if parts[0] == "__MACOSX":
                if not entry.is_dir() and not parts[-1].startswith("._"):
                    raise ValueError("Unexpected ZIP resource-fork member")
                continue
            if parts[0] != stage.name or stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("Notary ZIP root or member kind differs")
            if not entry.is_dir():
                relative = "/".join(parts[1:])
                actual[relative] = {"sha256": hashlib.sha256(source.read(entry)).hexdigest(), "size": entry.file_size}
    expected = {name: {k: value[k] for k in ("sha256", "size")} for name, value in tree.items()}
    if actual != expected:
        raise ValueError("Submitted ZIP does not contain the exact signed tree")


def restore_signed_tree(archive: Path, destination: Path, tree: dict) -> None:
    """Rehydrate only the already verified regular payload, never ZIP metadata links."""
    destination.mkdir()
    with zipfile.ZipFile(archive) as source:
        for relative, details in tree.items():
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as output:
                output.write(source.read(destination.name + "/" + relative))
            path.chmod(details["mode"])


def submission_id(state: Path, record: dict) -> str:
    if record.get("submissionID"):
        if not isinstance(record["submissionID"], str) or not UUID.fullmatch(record["submissionID"]):
            raise ValueError("Retained submission ID is invalid")
        return record["submissionID"]
    # A completed stdout can survive interruption before the state update.
    try:
        value = json.loads((state / "submit.stdout").read_bytes()).get("id")
    except (OSError, ValueError, AttributeError):
        value = None
    if not isinstance(value, str) or not UUID.fullmatch(value):
        raise ValueError("Submission outcome is unknown; no valid ID retained. Never resubmit this state")
    record["submissionID"] = value.lower()
    record["phase"] = "submitted"
    write(state / "state.json", record)
    return value.lower()


def accept(state: Path, evidence: Path, record: dict, document: dict) -> None:
    spec = importlib.util.spec_from_file_location("notary_evidence", TOOLS / "write-notarization-evidence.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    value = module.sanitized_evidence(document, record["archiveSHA256"])
    if evidence.exists() and json.loads(evidence.read_bytes()) != value:
        raise ValueError("Existing acceptance evidence differs")
    if not evidence.exists():
        write(evidence, value)
    record["phase"] = "accepted"
    record["acceptanceSHA256"] = digest(evidence)
    write(state / "state.json", record)


def perform(args: argparse.Namespace) -> dict:
    stage, evidence = physical(args.stage), physical(args.evidence)
    identity = os.environ.get("DEVCONTAINER_SIGNING_IDENTITY", "")
    profile = os.environ.get("DEVCONTAINER_NOTARY_PROFILE", "")
    team = os.environ.get("DEVCONTAINER_SIGNING_TEAM_ID", "")
    if not identity or not profile or not re.fullmatch(r"[A-Z0-9]{10}", team):
        raise ValueError("Signing identity, notary keychain profile and ten-character signing team are required")
    scratch = args.scratch_directory
    if not SHA.fullmatch(args.candidate_sha256):
        raise ValueError("Candidate receipt checksum must be lowercase SHA-256")
    if not SHA.fullmatch(args.stage_provenance_sha256):
        raise ValueError("Stage provenance checksum must be lowercase SHA-256")
    state_key = args.candidate_sha256 + "-" + args.stage_provenance_sha256
    state = args.state_directory or account_home() / "Library/Application Support/ContainerFamily/retained/devcontainer/notary" / state_key
    validate_storage(stage, state, scratch, evidence)
    if not args.resume and not stage.is_dir():
        raise ValueError("Stage must already exist before signing")
    if args.resume:
        if not state.is_dir() or state.is_symlink():
            raise ValueError("Missing durable notary state")
    else:
        state.mkdir(mode=0o700)
    if state.stat().st_uid != os.getuid() or stat.S_IMODE(state.stat().st_mode) != 0o700:
        raise ValueError("Notary state is not private and owned")
    if not evidence.parent.is_dir():
        raise ValueError("Durable acceptance parent must already exist")
    with (state / "owner.lock").open("a+b") as lock, contextlib.ExitStack() as cleanup:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        record = {}
        active_stage = stage
        try:
            if args.resume:
                for retained in state.iterdir():
                    physical(retained)
                    if retained.is_file() and retained.stat().st_nlink != 1:
                        raise ValueError("Retained evidence must not be hard-linked")
                retained_receipt = state / "candidate-receipt.json"
                retained_provenance = state / "stage-provenance.json"
                record = json.loads((state / "state.json").read_bytes())
                if (record.get("stage") != str(stage) or record.get("evidence") != str(evidence)
                        or record.get("candidateSHA256") != args.candidate_sha256 or record.get("teamIdentifier") != team
                        or record.get("stageProvenanceSHA256") != args.stage_provenance_sha256
                        or digest(retained_receipt) != args.candidate_sha256
                        or record.get("phase") not in {"submit-intent", "submitted", "accepted"}):
                    raise ValueError("Resume identity or phase differs; preserve prior state")
                receipt = json.loads(retained_receipt.read_bytes())
                if record.get("candidateCommit") != receipt["commit"] or record.get("runtimeProfile") != receipt["runtimeProfile"]:
                    raise ValueError("Resume source or profile differs from retained candidate")
                authenticate_provenance(retained_provenance, args.stage_provenance_sha256, receipt, record["unsignedTree"])
                require_signed_payload(record["unsignedTree"], record["signedTree"])
                archive = state / "submitted.zip"
                if digest(archive) != record["archiveSHA256"] or archive.stat().st_size != record["archiveSize"]:
                    raise ValueError("Retained submitted ZIP changed")
                verify_zip(archive, stage, record["signedTree"])
                if not stage.exists():
                    replay = Path(tempfile.mkdtemp(prefix="notary-replay-", dir=scratch))
                    cleanup.callback(shutil.rmtree, replay)
                    active_stage = replay / stage.name
                    restore_signed_tree(archive, active_stage, record["signedTree"])
                if inventory(active_stage, evidence) != record["signedTree"]:
                    raise ValueError("Signed stage changed since submission")
            else:
                if evidence.exists():
                    raise ValueError("Acceptance evidence already exists")
                if args.candidate_receipt is None or args.stage_provenance is None:
                    raise ValueError("Initial signing requires --candidate-receipt and --stage-provenance")
                receipt, tree = authenticate(stage, args.candidate_receipt, args.candidate_sha256, evidence)
                authenticate_provenance(args.stage_provenance, args.stage_provenance_sha256, receipt, tree)
                for source, filename, expected in (
                    (args.candidate_receipt, "candidate-receipt.json", args.candidate_sha256),
                    (args.stage_provenance, "stage-provenance.json", args.stage_provenance_sha256),
                ):
                    with (state / filename).open("xb") as retained:
                        retained.write(source.read_bytes())
                        retained.flush()
                        os.fsync(retained.fileno())
                    if digest(state / filename) != expected:
                        raise ValueError("Signing authority changed during retention: " + filename)
                if inventory(stage, evidence) != tree:
                    raise ValueError("Unsigned package changed during authority retention")
                record = {"schema": 1, "phase": "signing", "stage": str(stage), "evidence": str(evidence),
                          "candidateSHA256": args.candidate_sha256, "candidateCommit": receipt["commit"],
                          "stageProvenanceSHA256": args.stage_provenance_sha256,
                          "runtimeProfile": receipt["runtimeProfile"], "teamIdentifier": team, "unsignedTree": tree}
                write(state / "state.json", record)
                entitlement = state / "node-entitlements.plist"
                entitlement.write_bytes(plistlib.dumps(JIT))
                for index, relative in enumerate(BINARIES):
                    command = ["/usr/bin/codesign", "--force", "--options", "runtime", "--timestamp", "--sign", identity]
                    if relative == NODE:
                        command.extend(["--entitlements", str(entitlement)])
                    checked(command + [str(stage / relative)], state, f"sign-{index}", 180)
            prefix = "check-" + str(time.time_ns())
            signatures = verify_signatures(active_stage, state, team, prefix)
            smoke(active_stage, state, scratch, receipt, prefix)
            if not args.resume:
                signed_tree = inventory(stage, evidence)
                require_signed_payload(record["unsignedTree"], signed_tree)
                record.update({"phase": "signed", "signedTree": signed_tree, "signatures": signatures})
                write(state / "state.json", record)
                with tempfile.TemporaryDirectory(prefix="devcontainer-notary-", dir=scratch) as directory:
                    temporary = Path(directory) / "submitted.zip"
                    checked(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(stage), str(temporary)], state, "archive", 120)
                    verify_zip(temporary, stage, signed_tree)
                    pending = state / "submitted.zip.pending"
                    with temporary.open("rb") as source, pending.open("xb") as destination:
                        shutil.copyfileobj(source, destination)
                        destination.flush()
                        os.fsync(destination.fileno())
                    if digest(pending) != digest(temporary):
                        raise ValueError("Internal ZIP promotion checksum differs")
                    os.replace(pending, state / "submitted.zip")
                record.update({"phase": "submit-intent", "archiveSHA256": digest(state / "submitted.zip"),
                               "archiveSize": (state / "submitted.zip").stat().st_size})
                write(state / "state.json", record)
                status, _, _ = tool(["/usr/bin/xcrun", "notarytool", "submit", str(state / "submitted.zip"),
                                     "--keychain-profile", profile, "--output-format", "json"], state, "submit", 180)
                submission_id(state, record)
                if status:
                    raise ValueError("Submit returned an error; resume only by retained ID")
            archive = state / "submitted.zip"
            if digest(archive) != record["archiveSHA256"] or archive.stat().st_size != record["archiveSize"]:
                raise ValueError("Retained submitted ZIP changed")
            verify_zip(archive, stage, record["signedTree"])
            identifier = submission_id(state, record)
            deadline = time.monotonic() + args.timeout_seconds
            while True:
                label = "info-" + str(time.time_ns())
                out, _ = checked(["/usr/bin/xcrun", "notarytool", "info", identifier, "--keychain-profile", profile,
                                  "--output-format", "json"], state, label, 60)
                document = json.loads(out)
                if document.get("id", "").lower() != identifier or document.get("status") not in {"Accepted", "In Progress", "Invalid", "Rejected"}:
                    raise ValueError("Notary info has unexpected submission identity or status")
                record["notary"] = {"id": identifier, "status": document["status"]}
                write(state / "state.json", record)
                if inventory(active_stage, evidence) != record["signedTree"]:
                    raise ValueError("Signed stage changed while notarization was pending")
                if document["status"] == "Accepted":
                    if digest(archive) != record["archiveSHA256"]:
                        raise ValueError("Submitted ZIP changed while notarization was pending")
                    accept(state, evidence, record, document)
                    return record
                if document["status"] != "In Progress":
                    record["phase"] = "rejected"
                    write(state / "state.json", record)
                    raise ValueError("Notarization rejected; evidence retained")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("Notarization is still pending; use --resume with the same state")
                time.sleep(min(15, remaining))
        except BaseException as error:
            # Retain prior immutable payload/state even on signal, timeout or rejection.
            write(state / ("failure-" + str(time.time_ns()) + ".json"), {"type": type(error).__name__,
                                                                      "phase": record.get("phase", "admission")})
            raise


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", type=Path)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--candidate-receipt", type=Path, help="required for initial signing; resume uses the internal retained copy")
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--stage-provenance", type=Path, help="required initially; resume uses the internal retained provenance")
    parser.add_argument("--stage-provenance-sha256", required=True)
    parser.add_argument("--state-directory", type=Path)
    parser.add_argument("--scratch-directory", type=Path, default=Path("/Volumes/SSD/cf/notary-scratch"))
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(arguments)
    if args.timeout_seconds < 1 or args.timeout_seconds > 3600:
        parser.error("notary timeout must be between 1 and 3600 seconds")
    os.umask(0o077)
    previous = {}
    def cancelled(number, _frame):
        for item in previous:
            signal.signal(item, signal.SIG_IGN)
        raise InterruptedError("Signing interrupted")
    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous[number] = signal.signal(number, cancelled)
    try:
        perform(args)
    except (Exception, KeyboardInterrupt) as error:
        print("Native signing/notarization did not complete: " + type(error).__name__ + "; inspect retained private state.", file=sys.stderr)
        return 1
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
