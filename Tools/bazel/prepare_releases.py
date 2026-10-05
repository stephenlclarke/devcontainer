"""Extract on SSD and retain verified release payloads as durable local assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile

from release_inputs import acquire, canonical, sha256, validate_lock, verify_object


RECEIPT = ".prepared-release.json"
MAX_FILES = 10000
MAX_BYTES = 2 * 1024**3
KERNEL_MEMBER = "./opt/kata/share/kata-containers/vmlinux-6.18.35-197-debug"
KERNEL_BYTES = 30423552


def layout(asset: dict) -> dict:
    """Only reviewed release layouts are admitted; never guess a missing binary."""
    repository, name = asset["repository"], asset["name"]
    if (repository, asset["tag"], name) == (
            "nodejs.org/dist", "24.21.0", "node-v24.21.0-darwin-arm64.tar.gz"):
        return {"format": "selected-tar", "executables": {"node": "node-v24.21.0-darwin-arm64/bin/node"},
                "files": {"license": "node-v24.21.0-darwin-arm64/LICENSE"}}
    if (repository, asset["tag"], name) == ("registry.npmjs.org/@devcontainers/cli", "0.88.0", "cli-0.88.0.tgz"):
        return {"format": "tar", "executables": {"devcontainer-cli": "package/devcontainer.js"},
                "files": {"package": "package/package.json"}}
    if (repository, asset["tag"], name) == (
            "ghcr.io/homebrew/core/docker", "29.6.2", "docker--29.6.2.arm64_tahoe.bottle.tar.gz"):
        return {"format": "tar", "executables": {"docker": "docker/29.6.2/bin/docker"}}
    if repository == "local/devcontainer-candidate" and name == "candidate_archive.tar.gz":
        return {"format": "tar", "executables": {
            key: f"devcontainer-{asset['tag']}/bin/{key}"
            for key in ("devcontainer", "devcontainer-engine", "devcontainer-compose")}}
    if repository == "local/container-compose-candidate" and name == "candidate_archive.tar.gz":
        return {"format": "tar", "executables": {"compose": "compose/bin/compose",
                "compose-normalizer": "compose/resources/compose-normalizer", **{
                    "compose-volume-initializer-linux-" + arch:
                        "compose/resources/volume-initializer/compose-volume-initializer-linux-" + arch
                    for arch in ("arm64", "amd64")}}}
    if repository == "local/devcontainer-candidate" and name == "candidate_archive_v2.tar.gz":
        prefix = f"devcontainer-{asset['tag']}/"
        reference = prefix + "libexec/devcontainer/reference/"
        return {"format": "tar", "executables": {
            **{key: prefix + "bin/" + key for key in
               ("devcontainer", "devcontainer-engine", "devcontainer-compose", "devcontainer-docker")},
            "reference-node": reference + "node",
            "terminal-launcher-arm64": prefix + "libexec/devcontainer/terminal-launcher/devcontainer-terminal-linux-arm64",
            "terminal-launcher-amd64": prefix + "libexec/devcontainer/terminal-launcher/devcontainer-terminal-linux-amd64"}, "files": {
                name: reference + name for name in (
                    "NODE-LICENSE.txt", "runtime-lock.json", "cli/devcontainer.js",
                    "cli/dist/spec-node/devContainersSpecCLI.js", "cli/scripts/updateUID.Dockerfile",
                    "cli/package.json", "cli/LICENSE.txt", "cli/ThirdPartyNotices.txt")}
                | {"terminal-launcher-go-license": prefix + "libexec/devcontainer/terminal-launcher/GO-LICENSE.txt"}}
    if (repository, asset["tag"], name) == ("kata-containers/kata-containers", "3.32.0", "kata-static-3.32.0-arm64.tar.zst"):
        return {"format": "kernel-zstd", "executables": {}, "files": {"kernel": "kernel/vmlinux"}}
    if repository == "stephenlclarke/devcontainer" and name == "devcontainer-release-arm64.tar.gz":
        return {"format": "tar", "executables": {
            key: f"devcontainer-{asset['tag']}/bin/{key}"
            for key in ("devcontainer", "devcontainer-engine", "devcontainer-compose")}}
    if repository == "stephenlclarke/container-compose":
        if name == "container-compose-plugin-release-arm64.tar.gz":
            return {"format": "tar", "executables": {"compose": "compose/bin/compose",
                    "compose-normalizer": "compose/resources/compose-normalizer"}}
        if name == "container-release-arm64.tar.gz":
            return {"format": "tar", "executables": {key: "bin/" + key for key in
                    ("container", "container-engine", "container-apiserver")}}
        if name == "container-compose-signed-arm64.zip":
            return {"format": "zip", "executables": {
                "compose": "compose/bin/compose",
                "compose-normalizer": "compose/resources/compose-normalizer",
                "compose-volume-initializer-linux-amd64":
                    "compose/resources/volume-initializer/compose-volume-initializer-linux-amd64",
                "compose-volume-initializer-linux-arm64":
                    "compose/resources/volume-initializer/compose-volume-initializer-linux-arm64"}}
        if name == "qualified-compose-release.json":
            return {"format": "json", "executables": {},
                    "files": {"provenance": "qualified-compose-release.json"}}
    if repository == "stephenlclarke/container" and name == "container-homebrew-arm64.tar.gz":
        return {"format": "tar", "executables": {key: "bin/" + key for key in
                ("container", "container-engine", "container-apiserver")}}
    if repository == "stephenlclarke/container" and name == "qualified-container-assets.json":
        return {"format": "json", "executables": {},
                "files": {"provenance": "qualified-container-assets.json"}}
    if ((repository, name) in {
            ("stephenlclarke/containerization", "guest.oci.tar"),
            ("stephenlclarke/container-builder-shim", "builder.oci.tar")}):
        # Q publishes these OCI archives as independent lower-layer assets.
        # Preserve their bytes as data; image admission is delegated to the
        # existing OCI archive verifier, never inferred from this layout.
        return {"format": "raw-data", "executables": {}, "files": {"archive": name}}
    if repository == "apple/container" and name == f"container-{asset['tag']}-installer-signed.pkg":
        return {"format": "pkg", "executables": {"container": "Payload/bin/container",
                "container-apiserver": "Payload/bin/container-apiserver"}}
    if repository == "docker/compose" and name == "docker-compose-darwin-aarch64":
        return {"format": "raw", "executables": {"docker-compose": "bin/docker-compose"}}
    if (repository, asset["tag"], name) == ("abiosoft/colima", "v0.10.3", "colima-Darwin-arm64"):
        return {"format": "raw", "executables": {"colima": "bin/colima"}}
    if (repository, asset["tag"], name) == ("lima-vm/lima", "v2.2.0", "lima-2.2.0-Darwin-arm64.tar.gz"):
        return {"format": "tar", "executables": {"limactl": "bin/limactl", "lima": "bin/lima"},
                "files": {"guest-agent": "share/lima/lima-guestagent.Linux-aarch64.gz"},
                # Documentation alias only. Never materialize archive links or
                # relax the regular-file rule for any runtime payload.
                "omittedLinks": {"share/doc/lima/templates": "../../lima/templates"}}
    if (repository, asset["tag"], name) == (
            "abiosoft/colima-core", "v0.10.4", "ubuntu-24.04-minimal-cloudimg-arm64-docker.raw.gz"):
        return {"format": "raw-data", "executables": {}, "files": {"disk-image": "images/" + name}}
    raise ValueError("No reviewed layout for this published asset")


def member_path(name: str) -> Path:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or "\x00" in name:
        raise ValueError("Unsafe release archive member")
    if path.parts and path.parts[0] == RECEIPT:
        raise ValueError("Archive collides with preparation receipt")
    return Path(*path.parts)


def unpack_tar(source: Path, destination: Path, omitted_links: dict | None = None) -> None:
    """No links, device nodes, owner restoration or implicit tar extraction."""
    seen = set()
    omitted_links = omitted_links or {}
    omitted = set()
    size = 0
    with tarfile.open(source, "r:gz") as archive:
        for entry in archive:
            path = member_path(entry.name)
            if path == Path(".") and entry.isdir():
                continue
            if path == Path(".") or path in seen or len(seen) >= MAX_FILES:
                raise ValueError("Duplicate or excessive release archive members")
            seen.add(path)
            if path.as_posix() in omitted_links:
                if not entry.issym() or entry.linkname != omitted_links[path.as_posix()] or entry.size != 0:
                    raise ValueError("Reviewed documentation link changed")
                omitted.add(path.as_posix())
                continue
            if not (entry.isdir() or entry.isfile()) or entry.size < 0:
                raise ValueError("Release archive must contain only files and directories")
            size += entry.size
            if size > MAX_BYTES:
                raise ValueError("Expanded release exceeds size limit")
            target = destination / path
            if entry.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(entry) as incoming, target.open("xb") as output:
                    shutil.copyfileobj(incoming, output)
                # Retain executable semantics, never setuid/setgid or world write.
                target.chmod(0o755 if entry.mode & 0o111 else 0o644)
    if omitted != set(omitted_links):
        raise ValueError("Reviewed documentation link is missing")


def copy_raw(source: Path, destination: Path, specification: dict) -> None:
    """Copy one reviewed raw asset; VM archives remain non-executable data."""
    executable = specification["format"] == "raw"
    paths = specification["executables" if executable else "files"]
    if len(paths) != 1:
        raise ValueError("Raw release layout requires exactly one destination")
    target = destination / member_path(next(iter(paths.values())))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(0o755 if executable else 0o600)


def unpack_selected_tar(source: Path, destination: Path, specification: dict) -> None:
    """Retain only reviewed Node binary/license; never materialize npm or links."""
    executables = set(specification["executables"].values())
    selected = executables | set(specification["files"].values())
    seen, found, size = set(), set(), 0
    with tarfile.open(source, "r:gz") as archive:
        for entry in archive:
            path = member_path(entry.name)
            if path == Path(".") and entry.isdir():
                continue
            if path == Path(".") or path in seen or len(seen) >= MAX_FILES or entry.size < 0:
                raise ValueError("Duplicate or excessive selected archive members")
            seen.add(path)
            size += entry.size
            if size > MAX_BYTES:
                raise ValueError("Expanded selected archive exceeds size limit")
            if path.as_posix() not in selected:
                continue
            if not entry.isfile():
                raise ValueError("Selected archive member is not a regular file")
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(entry) as incoming, target.open("xb") as output:
                shutil.copyfileobj(incoming, output)
            target.chmod(0o755 if path.as_posix() in executables else 0o600)
            found.add(path.as_posix())
    if found != selected:
        raise ValueError("Selected release payload is incomplete")


def unpack_zip(source: Path, destination: Path) -> None:
    """Extract the reviewed Compose ZIP without honoring archive paths or links."""
    seen = set()
    total_size = 0
    with zipfile.ZipFile(source) as archive:
        for entry in archive.infolist():
            member = entry.filename.rstrip("/") if entry.is_dir() else entry.filename
            path = member_path(member)
            if path == Path(".") or path in seen or len(seen) >= MAX_FILES:
                raise ValueError("Duplicate or excessive signed ZIP members")
            if path.parts[0] != "compose":
                raise ValueError("Signed Compose ZIP contains a path outside compose/")
            seen.add(path)
            mode = (entry.external_attr >> 16) & 0xFFFF
            kind = stat.S_IFMT(mode)
            expected_kind = stat.S_IFDIR if entry.is_dir() else stat.S_IFREG
            if kind not in (0, expected_kind) or mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                raise ValueError("Signed Compose ZIP contains a link or special file")
            if entry.file_size < 0:
                raise ValueError("Signed Compose ZIP member has an invalid size")
            total_size += entry.file_size
            if total_size > MAX_BYTES:
                raise ValueError("Expanded signed Compose ZIP exceeds size limit")
            target = destination / path
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(0o755)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry, "r") as incoming, target.open("xb") as output:
                copied = 0
                while block := incoming.read(1024 * 1024):
                    copied += len(block)
                    if copied > entry.file_size:
                        raise ValueError("Signed Compose ZIP member exceeded its declared size")
                    output.write(block)
            if copied != entry.file_size:
                raise ValueError("Signed Compose ZIP member was truncated")
            target.chmod(0o755 if mode & 0o111 else 0o644)
    if not {Path("compose"), Path("compose/bin"), Path("compose/resources")} <= seen:
        raise ValueError("Signed Compose ZIP is missing its reviewed root directories")


def expand_package(source: Path, destination: Path) -> None:
    # pkgutil expands payloads only. It does not run installer scripts, write to
    # /usr/local, register packages, start services or ask for administrator auth.
    subprocess.run(["/usr/sbin/pkgutil", "--expand-full", str(source), str(destination)],
                   check=True, capture_output=True, timeout=120,
                   env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "TMPDIR": str(destination.parent)})


def unpack_kernel(source: Path, destination: Path) -> None:
    """Read one reviewed release member; never extract the full Kata root tree."""
    result = subprocess.run(["/usr/bin/tar", "-xOf", str(source), KERNEL_MEMBER],
                            check=True, capture_output=True, timeout=60,
                            env={"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "TMPDIR": str(destination.parent)})
    # The input archive digest is checked before and after this extraction.
    # Pin size as well: duplicate matches cannot silently concatenate kernels.
    if result.stderr or len(result.stdout) != KERNEL_BYTES or result.stdout[56:60] != b"ARMd":
        raise ValueError("Recommended kernel has unexpected size, header or extraction warning")
    kernel = destination / "kernel/vmlinux"
    kernel.parent.mkdir()
    with kernel.open("xb") as output:
        output.write(result.stdout)
    kernel.chmod(0o600)


def inventory(root: Path) -> dict:
    """Authenticate the complete payload, not just the command used first."""
    result = {}
    size = 0
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in sorted(directories + files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if relative == RECEIPT:
                continue
            info = path.lstat()
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError("Prepared release contains a link or special file")
            size += info.st_size if stat.S_ISREG(info.st_mode) else 0
            if len(result) >= MAX_FILES or size > MAX_BYTES:
                raise ValueError("Prepared release exceeds inventory limits")
            result[relative] = {"mode": stat.S_IMODE(info.st_mode), "kind": "directory" if path.is_dir() else "file"}
            if path.is_file():
                result[relative].update(size=info.st_size, sha256=sha256(path))
    return result


def validate_prepared(root: Path, specification: dict, retained_receipt: dict) -> dict:
    receipt_path = root / RECEIPT
    if root.is_symlink() or not root.is_dir() or receipt_path.is_symlink():
        raise ValueError("Invalid prepared release root or receipt")
    receipt = json.loads(receipt_path.read_text())
    if receipt != retained_receipt:
        raise ValueError("SSD receipt differs from internally retained inventory")
    if set(receipt) != {"specification", "inventory"} or receipt["specification"] != specification:
        raise ValueError("Prepared release identity differs")
    if receipt["inventory"] != inventory(root):
        raise ValueError("Prepared release bytes or modes changed")
    for path in specification["layout"]["executables"].values():
        item = receipt["inventory"].get(path, {})
        if item.get("kind") != "file" or not item["mode"] & 0o111:
            raise ValueError("Required release executable is missing or not executable")
    for path in specification["layout"].get("files", {}).values():
        item = receipt["inventory"].get(path, {})
        if item.get("kind") != "file" or item.get("size", 0) <= 0 or item["mode"] & 0o111:
            raise ValueError("Required release data file is missing, empty or executable")
    return receipt


def validate_compose_signed_tree(provenance: dict, compose_root: Path) -> dict:
    """Require the extracted ZIP tree to match the signed tree in its provenance."""
    signed_tree = provenance.get("signedTree")
    if (not isinstance(signed_tree, dict) or not signed_tree
            or any(not isinstance(path, str) or not isinstance(digest, str)
                   or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                   for path, digest in signed_tree.items())):
        raise ValueError("Compose provenance has no valid signed tree inventory")
    expected = {"compose/" + member_path(path).as_posix(): digest for path, digest in signed_tree.items()}
    actual = {}
    for directory, _, files in os.walk(compose_root, followlinks=False):
        for name in files:
            path = Path(directory) / name
            relative = path.relative_to(compose_root).as_posix()
            if relative == RECEIPT or name.startswith("._"):
                continue
            actual[relative] = sha256(path)
    if actual != expected:
        raise ValueError("Extracted Compose ZIP differs from its provenance signed tree")
    return actual


def validate_q_runtime_provenance(bundle: dict, runtime_asset: dict,
                                  guest_asset: dict, builder_asset: dict,
                                  prepared_runtime_root: Path) -> dict:
    """Bind the selected Q sidecar and its declared runtime files to the prepared archive."""
    if (bundle.get("schema") != 1 or bundle.get("kind") != "container-qualified-runtime-assets"
            or bundle.get("qualified_container_source") != runtime_asset.get("commit")
            or bundle.get("qualification") != {"target": "bazel-qualify", "passed": True}):
        raise ValueError("Q runtime provenance has the wrong source or qualification state")
    assets = bundle.get("assets")
    if not isinstance(assets, dict) or set(assets) != {"runtime", "guest", "builder"}:
        raise ValueError("Q runtime provenance asset inventory is incomplete")
    runtime_record = assets["runtime"]
    if (not isinstance(runtime_record, dict)
            or runtime_record.get("name") != runtime_asset.get("name")
            or runtime_record.get("sha256") != runtime_asset.get("sha256")
            or runtime_record.get("source") != runtime_asset.get("commit")):
        raise ValueError("Q runtime provenance does not bind the selected runtime archive")
    selected_images = {"guest": guest_asset, "builder": builder_asset}
    for name, selected in selected_images.items():
        item = assets[name]
        if (not isinstance(item, dict) or not isinstance(item.get("name"), str)
                or not isinstance(item.get("source"), str)
                or re.fullmatch(r"[0-9a-f]{40}", item["source"]) is None
                or re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", "")) is None
                or item.get("name") != selected.get("name")
                or item.get("sha256") != selected.get("sha256")
                or item.get("source") != selected.get("commit")
                or selected.get("repository") != {
                    "guest": "stephenlclarke/containerization",
                    "builder": "stephenlclarke/container-builder-shim"}[name]):
            raise ValueError(f"Q runtime provenance {name} asset identity is malformed")
    runtime = bundle.get("runtime")
    payload = runtime.get("payload") if isinstance(runtime, dict) else None
    guest, builder = bundle.get("guest"), bundle.get("builder")
    if (not isinstance(runtime, dict) or not isinstance(guest, dict) or not isinstance(builder, dict)
            or guest.get("source") != assets["guest"].get("source")
            or builder.get("source") != assets["builder"].get("source")
            or guest.get("reference") != assets["guest"].get("reference")
            or builder.get("reference") != assets["builder"].get("reference")
            or runtime.get("init_archive_sha256") != assets["guest"]["sha256"]
            or runtime.get("builder_archive_sha256") != assets["builder"]["sha256"]
            or runtime.get("init_image") != assets["guest"].get("reference")
            or runtime.get("builder_image") != assets["builder"].get("reference")
            or not isinstance(runtime.get("workload_image"), str)
            or not runtime["workload_image"].startswith("docker.io/library/alpine@sha256:")
            or not isinstance(runtime.get("notary"), dict)
            or runtime["notary"].get("status") != "Accepted"
            or not runtime["notary"].get("id")):
        raise ValueError("Q runtime provenance guest, builder or notarization identity differs")
    if not isinstance(payload, dict) or len(payload) != 25:
        raise ValueError("Q runtime provenance has no runtime binary inventory")
    for relative, digest in payload.items():
        if not isinstance(relative, str) or not isinstance(digest, str):
            raise ValueError("Q runtime binary inventory contains an invalid path or digest")
        path = member_path(relative)
        if (path.as_posix() != relative or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                or path.is_absolute()):
            raise ValueError("Q runtime binary inventory contains an invalid path or digest")
        actual = prepared_runtime_root / path
        if actual.is_symlink() or not actual.is_file() or sha256(actual) != digest:
            raise ValueError("Q runtime binary inventory differs from the selected archive")
    chain = bundle.get("native_compiled_chain")
    if not isinstance(chain, dict):
        raise ValueError("Q runtime provenance omits its native compiled source chain")
    chain_bytes = (json.dumps(chain, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    chain_sha = hashlib.sha256(chain_bytes).hexdigest()
    if (chain.get("schema") != 1 or chain.get("source") != runtime_asset.get("commit")
            or bundle.get("native_compiled_chain_sha256") != chain_sha):
        raise ValueError("Q runtime provenance has an invalid native compiled source chain")
    return {"sha256": runtime_asset.get("sha256"), "commit": runtime_asset.get("commit"),
            "runtimePayloadSHA256": hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "nativeCompiledChainSHA256": chain_sha}


def validate_compose_runtime_association(selection: dict, provenance: dict,
                                         runtime_provenance: dict,
                                         prepared_runtime_root: Path,
                                         compose_root: Path | None = None) -> dict:
    """Authenticate the selected Compose profile and enhanced runtime association."""
    compose_asset = selection["composeArchive"]
    provenance_asset = selection["composeProvenance"]
    if provenance.get("kind") == "signed-compose-stock-gateway-provenance":
        if compose_root is None:
            raise ValueError("Stock Compose admission requires the extracted signed archive tree")
        validate_compose_signed_tree(provenance, compose_root)
        return validate_stock_compose_gateway(selection, provenance, compose_root)
    if provenance.get("runtimeProfile") == "stock":
        raise ValueError("Stock Compose provenance has an unsupported scope or kind")
    runtime_asset = selection["containerRuntime"]
    runtime_provenance_asset = selection["containerRuntimeProvenance"]
    guest_asset = selection["guestImage"]
    builder_asset = selection["builderImage"]
    if (compose_asset.get("repository") != "stephenlclarke/container-compose"
            or compose_asset.get("name") != "container-compose-signed-arm64.zip"
            or provenance_asset.get("repository") != compose_asset.get("repository")
            or provenance_asset.get("tag") != compose_asset.get("tag")
            or provenance_asset.get("commit") != compose_asset.get("commit")
            or provenance_asset.get("name") != "qualified-compose-release.json"):
        raise ValueError("Compose archive and provenance release identities differ")
    compose_commit = compose_asset.get("commit")
    runtime_commit = runtime_asset.get("commit")
    if (not isinstance(compose_commit, str) or re.fullmatch(r"[0-9a-f]{40}", compose_commit) is None
            or not isinstance(runtime_commit, str) or re.fullmatch(r"[0-9a-f]{40}", runtime_commit) is None):
        raise ValueError("Compose and runtime source commits must be full Git identities")
    notary = provenance.get("notary")
    if (provenance.get("source") != compose_commit
            or provenance.get("signedArchiveSHA256") != compose_asset.get("sha256")
            or provenance.get("signedAndNotarized") is not True
            or not isinstance(notary, dict) or notary.get("status") != "Accepted"):
        raise ValueError("Compose provenance does not authenticate the signed release archive")
    if type(provenance.get("signedDistributionReady")) is not bool:
        raise ValueError("Compose provenance omits its distribution-readiness status")
    chain = provenance.get("compiledSdkChain")
    lower_assets = provenance.get("lowerReleasedAssets")
    if not isinstance(chain, dict) or not isinstance(lower_assets, dict):
        raise ValueError("Compose provenance omits its SDK or lower-runtime chain")
    locks = chain.get("locks")
    if set(lower_assets) != {"runtime", "provenance", "guest", "builder"}:
        raise ValueError("Compose provenance must name the complete Q runtime/OCI release closure")
    lower_runtime = lower_assets.get("runtime")
    lower_runtime_provenance = lower_assets.get("provenance")
    lower_guest = lower_assets.get("guest")
    lower_builder = lower_assets.get("builder")
    if (not isinstance(locks, dict) or any(not isinstance(item, dict) for item in
            (lower_runtime, lower_runtime_provenance, lower_guest, lower_builder))):
        raise ValueError("Compose provenance has an incomplete runtime dependency chain")
    sdk = locks.get("container-sdk")
    runtime_release = lower_runtime.get("release")
    runtime_provenance_release = lower_runtime_provenance.get("release")
    if (not isinstance(sdk, dict) or not isinstance(runtime_release, dict)
            or not isinstance(runtime_provenance_release, dict)
            or re.fullmatch(r"[0-9a-f]{64}", lower_runtime_provenance.get("sha256", "")) is None):
        raise ValueError("Compose provenance has an invalid runtime dependency identity")
    if (provenance.get("qualifiedContainer") != runtime_commit
            or chain.get("schema") != 1 or chain.get("profile") != "enhanced"
            or chain.get("selected_config") != "prebuilt-container-sdk"
            or chain.get("source") != compose_commit
            or sdk.get("repository") != "stephenlclarke/container"
            or sdk.get("target_commit") != runtime_commit
            or lower_runtime.get("sha256") != runtime_asset.get("sha256")
            or runtime_release.get("repository") != runtime_asset.get("repository")
            or runtime_release.get("tag") != runtime_asset.get("tag")
            or runtime_release.get("target_commit") != runtime_commit
            or runtime_provenance_release.get("repository") != runtime_asset.get("repository")
            or runtime_provenance_release.get("tag") != runtime_asset.get("tag")
            or runtime_provenance_release.get("target_commit") != runtime_commit
            or lower_runtime_provenance.get("sha256") != runtime_provenance_asset.get("sha256")
            or runtime_provenance_asset.get("repository") != runtime_asset.get("repository")
            or runtime_provenance_asset.get("name") != "qualified-container-assets.json"
            or runtime_provenance_asset.get("tag") != runtime_asset.get("tag")
            or runtime_provenance_asset.get("commit") != runtime_commit
            or runtime_provenance_release.get("repository") != runtime_provenance_asset.get("repository")
            or runtime_provenance_release.get("tag") != runtime_provenance_asset.get("tag")
            or runtime_provenance_release.get("target_commit") != runtime_provenance_asset.get("commit")
            or runtime_asset.get("repository") != "stephenlclarke/container"
            or runtime_asset.get("name") != "container-homebrew-arm64.tar.gz"):
        raise ValueError("Compose provenance is not associated with the selected Q runtime release")
    for name, lower, selected in (("guest", lower_guest, guest_asset),
                                  ("builder", lower_builder, builder_asset)):
        release = lower.get("release")
        if (not isinstance(release, dict) or lower.get("sha256") != selected.get("sha256")
                or (release.get("repository"), release.get("tag"), release.get("target_commit")) !=
                   (selected.get("repository"), selected.get("tag"), selected.get("commit"))):
            raise ValueError(f"Compose provenance {name} identity differs from the locked Q OCI asset")
    q_identity = validate_q_runtime_provenance(runtime_provenance, runtime_asset,
                                               guest_asset, builder_asset,
                                               prepared_runtime_root)
    return {
        "containerCompose": {"repository": compose_asset["repository"],
                             "version": None, "commit": compose_commit,
                             "archiveSHA256": compose_asset["sha256"]},
        "containerRuntime": {"repository": runtime_asset["repository"],
                             "commit": runtime_commit,
                             "archiveSHA256": runtime_asset["sha256"]},
        "signedAndNotarized": True,
        "distributionReady": provenance["signedDistributionReady"],
        "qRuntimeProvenance": q_identity,
    }


def validate_stock_compose_gateway(selection: dict, provenance: dict,
                                   compose_root: Path) -> dict:
    """Admit signed stock Engine-gateway bytes without claiming live runtime qualification."""
    compose_asset = selection["composeArchive"]
    provenance_asset = selection["composeProvenance"]
    source = compose_asset.get("commit")
    archive_sha = compose_asset.get("sha256")
    if (compose_asset.get("repository") != "stephenlclarke/container-compose"
            or compose_asset.get("name") != "container-compose-signed-arm64.zip"
            or provenance_asset.get("repository") != compose_asset.get("repository")
            or provenance_asset.get("tag") != compose_asset.get("tag")
            or provenance_asset.get("commit") != source
            or provenance_asset.get("name") != "qualified-compose-release.json"
            or not isinstance(source, str) or re.fullmatch(r"[0-9a-f]{40}", source) is None
            or not isinstance(archive_sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", archive_sha) is None):
        raise ValueError("Stock Compose archive and provenance release identities differ")
    notary = provenance.get("notary")
    if (provenance.get("scope") != "signed-stock-gateway-compiled-layer"
            or provenance.get("runtimeProfile") != "stock"
            or provenance.get("gatewayBackend") != "engine"
            or provenance.get("runtimeQualification") != "pending"
            or provenance.get("architecture") != "arm64"
            or provenance.get("source") != source
            or provenance.get("signedArchiveSHA256") != archive_sha
            or provenance.get("signedAndNotarized") is not True
            or provenance.get("signedDistributionReady") is not False
            or not isinstance(notary, dict) or notary.get("status") != "Accepted"
            or not isinstance(notary.get("id"), str) or not notary["id"]):
        raise ValueError("Stock Compose provenance does not authenticate the pending signed gateway layer")

    chain = provenance.get("compiledSdkChain")
    pins = provenance.get("compiledSourcePins")
    lock_repositories = {
        "argument-parser": "stephenlclarke/container",
        "foundation": "stephenlclarke/container-compose",
        "containerization": "stephenlclarke/containerization",
        "engine-api": "stephenlclarke/container-engine-api",
        "container-sdk": "stephenlclarke/container",
    }
    if (not isinstance(chain, dict) or chain.get("schema") != 1
            or chain.get("source") != source or chain.get("profile") != "stock"
            or chain.get("selected_config") != "prebuilt-container-sdk"
            or not isinstance(chain.get("package_invocation"), str)
            or re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                            chain["package_invocation"]) is None
            or not isinstance(pins, dict)
            or set(pins) != {"container", "containerization", "engine-api"}):
        raise ValueError("Stock Compose compiled source chain is incomplete or mismatched")
    records = chain.get("locks")
    if not isinstance(records, dict) or set(records) != set(lock_repositories):
        raise ValueError("Stock Compose compiled dependency lock set is incomplete")
    for name, repository in lock_repositories.items():
        record = records[name]
        if (not isinstance(record, dict)
                or record.get("repository") != repository
                or not isinstance(record.get("tag"), str)
                or (not record["tag"].startswith("layer-argument-parser-")
                    if name == "argument-parser"
                    else not record["tag"].startswith(f"layer-{name}-stock-"))
                or not isinstance(record.get("target_commit"), str)
                or re.fullmatch(r"[0-9a-f]{40}", record["target_commit"]) is None
                or any(not isinstance(record.get(key), str)
                       or re.fullmatch(r"[0-9a-f]{64}", record[key]) is None
                       for key in ("lock_sha256", "archive_sha256"))):
            raise ValueError("Stock Compose compiled dependency record is malformed: " + name)
    pin_repositories = {
        "container": "apple/container",
        "containerization": "apple/containerization",
        "engine-api": "stephenlclarke/container-engine-api",
    }
    for name, repository in pin_repositories.items():
        pin = pins[name]
        if (not isinstance(pin, dict) or pin.get("repository") != repository
                or not isinstance(pin.get("commit"), str)
                or re.fullmatch(r"[0-9a-f]{40}", pin["commit"]) is None):
            raise ValueError("Stock Compose compiled source pin is malformed: " + name)
    if any(records[lock_name]["target_commit"] != pins[pin_name]["commit"]
           for lock_name, pin_name in (("container-sdk", "container"),
                                       ("containerization", "containerization"),
                                       ("engine-api", "engine-api"))):
        raise ValueError("Stock Compose compiled source pins differ from published layer locks")

    signed_payload = provenance.get("signedPayload")
    expected_payload = {"bin/compose", "resources/compose-normalizer"}
    if not isinstance(signed_payload, dict) or set(signed_payload) != expected_payload:
        raise ValueError("Stock Compose provenance has an incomplete signed payload inventory")
    signed_tree = provenance.get("signedTree")
    for relative, digest in signed_payload.items():
        if (not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                or signed_tree.get(relative) != digest):
            raise ValueError("Stock Compose signed payload differs from its signed tree")

    candidate_path = compose_root / "compose/resources/candidate.json"
    build_info_path = compose_root / "compose/resources/build-info.json"
    for path in (candidate_path, build_info_path):
        if path.is_symlink() or not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
            raise ValueError("Stock Compose archive is missing regular build identity metadata")
    if signed_tree.get("resources/candidate.json") != sha256(candidate_path):
        raise ValueError("Stock Compose candidate identity is not bound by the signed tree")
    try:
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        build_info = json.loads(build_info_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Stock Compose build identity metadata is unreadable") from error
    dependency_lock_sha = provenance.get("dependencyLockSHA256")
    if (not isinstance(candidate, dict) or not isinstance(build_info, dict)
            or not isinstance(dependency_lock_sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", dependency_lock_sha) is None
            or candidate.get("dependencyLockSHA256") != dependency_lock_sha
            or candidate.get("kind") != "unsigned-native-candidate"
            or candidate.get("runtimeProfile") != "stock"
            or candidate.get("commit") != source
            or candidate.get("architecture") != "arm64"
            or candidate.get("compilationMode") != "opt"
            or candidate.get("distributionReady") is not False
            or build_info.get("source") != compose_asset["repository"]
            or build_info.get("commit") != source
            or build_info.get("containerSource") != pins["container"]["repository"]
            or build_info.get("containerRef") != pins["container"]["commit"]
            or build_info.get("containerizationSource") != pins["containerization"]["repository"]
            or build_info.get("containerizationRef") != pins["containerization"]["commit"]):
        raise ValueError("Stock Compose candidate or build metadata differs from its compiled profile")

    runtime_asset = selection["containerRuntime"]
    runtime_commit = runtime_asset.get("commit")
    if (runtime_asset.get("repository") != "stephenlclarke/container"
            or runtime_asset.get("name") != "container-homebrew-arm64.tar.gz"
            or not isinstance(runtime_commit, str)
            or re.fullmatch(r"[0-9a-f]{40}", runtime_commit) is None
            or not isinstance(runtime_asset.get("sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", runtime_asset["sha256"]) is None):
        raise ValueError("Stock Compose selected Q runtime identity is malformed")
    return {
        "containerCompose": {"repository": compose_asset["repository"], "version": None,
                             "commit": source, "archiveSHA256": archive_sha},
        "containerRuntime": {"repository": runtime_asset["repository"],
                             "commit": runtime_commit, "archiveSHA256": runtime_asset["sha256"]},
        "runtimeProfile": "stock", "gatewayBackend": "engine",
        "runtimeQualification": "pending", "compiledSourcePins": pins,
        "signedAndNotarized": True, "distributionReady": False,
    }


def admit_compose_runtime_inputs(selection: dict, retained_objects: Path,
                                 prepared: dict) -> dict:
    """Authenticate archive, provenance, signed tree and distinct source identities."""
    selected = (("composeArchive", "composeArchive"), ("composeProvenance", "composeProvenance"),
                ("containerRuntime", "containerRuntime"),
                ("containerRuntimeProvenance", "containerRuntimeProvenance"),
                ("guestImage", "guestImage"), ("builderImage", "builderImage"))
    for asset_key, prepared_key in selected:
        asset = selection[asset_key]
        verify_object(retained_objects / asset["sha256"], asset)
        if prepared_key not in prepared:
            raise ValueError("Selected Compose/Q release is missing retained preparation")
    provenance_asset = selection["composeProvenance"]
    runtime_provenance_asset = selection["containerRuntimeProvenance"]
    provenance_path = Path(prepared["composeProvenance"]["files"]["provenance"])
    runtime_provenance_path = Path(prepared["containerRuntimeProvenance"]["files"]["provenance"])
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    runtime_provenance = json.loads(runtime_provenance_path.read_text(encoding="utf-8"))
    compose_root = Path(prepared["composeArchive"]["root"])
    identity = validate_compose_runtime_association(
        selection, provenance, runtime_provenance,
        Path(prepared["containerRuntime"]["root"]), compose_root)
    if identity.get("runtimeProfile") == "stock":
        identity["qRuntimeProvenance"] = validate_q_runtime_provenance(
            runtime_provenance, selection["containerRuntime"], selection["guestImage"],
            selection["builderImage"], Path(prepared["containerRuntime"]["root"]))
    else:
        validate_compose_signed_tree(provenance, compose_root)
    build_info_path = Path(prepared["composeArchive"]["root"]) / "compose/resources/build-info.json"
    build_info = json.loads(build_info_path.read_text(encoding="utf-8"))
    if (build_info.get("source") != identity["containerCompose"]["repository"]
            or build_info.get("commit") != identity["containerCompose"]["commit"]
            or not isinstance(build_info.get("version"), str) or not build_info["version"]):
        raise ValueError("Compose build metadata conflates or changes its runtime identity")
    if identity.get("runtimeProfile") != "stock" and (
            build_info.get("containerSource") != identity["containerRuntime"]["repository"]
            or build_info.get("containerRef") != identity["containerRuntime"]["commit"]):
        raise ValueError("Compose build metadata conflates or changes its runtime identity")
    identity["containerCompose"]["version"] = build_info["version"]
    return identity


def select_compose_runtime_assets(lock: dict) -> dict:
    """Select the new signed pair and Q runtime, or the exact currently locked legacy pair."""
    assets = validate_lock(lock)

    def matches(repository: str, name: str) -> list[dict]:
        return [asset for asset in assets
                if asset["repository"] == repository and asset["name"] == name]

    signed = matches("stephenlclarke/container-compose", "container-compose-signed-arm64.zip")
    provenance = matches("stephenlclarke/container-compose", "qualified-compose-release.json")
    if signed or provenance:
        runtime = matches("stephenlclarke/container", "container-homebrew-arm64.tar.gz")
        runtime_provenance = matches("stephenlclarke/container", "qualified-container-assets.json")
        guest = matches("stephenlclarke/containerization", "guest.oci.tar")
        builder = matches("stephenlclarke/container-builder-shim", "builder.oci.tar")
        if (len(signed) != 1 or len(provenance) != 1 or len(runtime) != 1
                or len(runtime_provenance) != 1 or len(guest) != 1 or len(builder) != 1):
            raise ValueError("signed Compose release requires locked Q runtime, provenance, guest and builder assets")
        archive, proof = signed[0], provenance[0]
        if any(archive[key] != proof[key] for key in ("tag", "tagObject", "releaseID", "commit", "publisher")):
            raise ValueError("signed Compose ZIP and provenance are not from one locked release")
        if any(runtime[0][key] != runtime_provenance[0][key]
               for key in ("repository", "tag", "tagObject", "releaseID", "commit", "publisher")):
            raise ValueError("Q runtime archive and provenance are not from one locked release")
        return {"format": "signed-compose-q-runtime", "composeArchive": archive,
                "composeProvenance": proof, "containerRuntime": runtime[0],
                "containerRuntimeProvenance": runtime_provenance[0],
                "guestImage": guest[0], "builderImage": builder[0]}

    legacy_compose = matches("stephenlclarke/container-compose", "container-compose-plugin-release-arm64.tar.gz")
    legacy_runtime = matches("stephenlclarke/container-compose", "container-release-arm64.tar.gz")
    if len(legacy_compose) != 1 or len(legacy_runtime) != 1:
        raise ValueError("locked Compose/runtime release inputs are missing or ambiguous")
    compose, runtime = legacy_compose[0], legacy_runtime[0]
    if any(compose[key] != runtime[key] for key in ("tag", "tagObject", "releaseID", "commit", "publisher")):
        raise ValueError("legacy Compose and runtime assets are not from one locked release")
    return {"format": "legacy-compose-bundle", "composeArchive": compose,
            "containerRuntime": runtime}


def admit_locked_compose_runtime(lock: dict, retained_objects: Path,
                                prepared_root: Path, prepared_receipts: Path) -> dict:
    """Read-only admit the lock-selected provider archives and exact executable paths."""
    selection = select_compose_runtime_assets(lock)

    def require_asset(asset: dict) -> dict:
        source = retained_objects / asset["sha256"]
        return require_retained(asset, source, prepared_root, prepared_receipts)

    compose_prepared = require_asset(selection["composeArchive"])
    runtime_prepared = require_asset(selection["containerRuntime"])
    if selection["format"] == "signed-compose-q-runtime":
        provenance_prepared = require_asset(selection["composeProvenance"])
        runtime_provenance_prepared = require_asset(selection["containerRuntimeProvenance"])
        guest_prepared = require_asset(selection["guestImage"])
        builder_prepared = require_asset(selection["builderImage"])
        identity = admit_compose_runtime_inputs(selection, retained_objects, {
            "composeArchive": compose_prepared,
            "composeProvenance": provenance_prepared,
            "containerRuntime": runtime_prepared,
            "containerRuntimeProvenance": runtime_provenance_prepared,
            "guestImage": guest_prepared,
            "builderImage": builder_prepared,
        })
        identity.update({
            "format": selection["format"],
            "containerCompose": {**identity["containerCompose"],
                                 "preparationSHA256": compose_prepared["preparationSHA256"],
                                 "inventorySHA256": compose_prepared["inventorySHA256"]},
            "containerRuntime": {**identity["containerRuntime"],
                                 "releaseTag": selection["containerRuntime"]["tag"],
                                 "containerSHA256": sha256(Path(runtime_prepared["executables"]["container"])),
                                 "apiServerSHA256": sha256(Path(runtime_prepared["executables"]["container-apiserver"])),
                                 "preparationSHA256": runtime_prepared["preparationSHA256"],
                                 "inventorySHA256": runtime_prepared["inventorySHA256"]},
            "composeProviderSHA256": sha256(Path(compose_prepared["executables"]["compose"])),
            "composeProvenanceSHA256": selection["composeProvenance"]["sha256"],
            "composeProvenancePreparationSHA256": provenance_prepared["preparationSHA256"],
            "composeProvenance": json.loads(
                Path(provenance_prepared["files"]["provenance"]).read_text(encoding="utf-8")),
            "containerRuntimeProvenanceSHA256": selection["containerRuntimeProvenance"]["sha256"],
            "containerRuntimeProvenancePreparationSHA256":
                runtime_provenance_prepared["preparationSHA256"],
            "containerRuntimeProvenance": json.loads(
                Path(runtime_provenance_prepared["files"]["provenance"]).read_text(encoding="utf-8")),
        })
        q_proof = identity["containerRuntimeProvenance"]
        identity["qOciInputs"] = {
            role: _q_oci_input(selection[asset_key], prepared,
                               q_proof["assets"][role])
            for role, asset_key, prepared in (
                ("guest", "guestImage", guest_prepared),
                ("builder", "builderImage", builder_prepared))}
        identity["guestArchive"] = identity["qOciInputs"]["guest"]["path"]
        identity["builderArchive"] = identity["qOciInputs"]["builder"]["path"]
    else:
        identity = {
            "format": selection["format"],
            "containerCompose": {"repository": selection["composeArchive"]["repository"],
                                 "commit": selection["composeArchive"]["commit"],
                                 "releaseTag": selection["composeArchive"]["tag"],
                                 "archiveSHA256": selection["composeArchive"]["sha256"],
                                 "preparationSHA256": compose_prepared["preparationSHA256"],
                                 "inventorySHA256": compose_prepared["inventorySHA256"]},
            "containerRuntime": {"repository": selection["containerRuntime"]["repository"],
                                 "commit": selection["containerRuntime"]["commit"],
                                 "releaseTag": selection["containerRuntime"]["tag"],
                                 "archiveSHA256": selection["containerRuntime"]["sha256"],
                                 "containerSHA256": sha256(Path(runtime_prepared["executables"]["container"])),
                                 "apiServerSHA256": sha256(Path(runtime_prepared["executables"]["container-apiserver"])),
                                 "preparationSHA256": runtime_prepared["preparationSHA256"],
                                 "inventorySHA256": runtime_prepared["inventorySHA256"]},
            "composeProviderSHA256": sha256(Path(compose_prepared["executables"]["compose"])),
        }
    identity["executables"] = {
        "container": runtime_prepared["executables"]["container"],
        "container-apiserver": runtime_prepared["executables"]["container-apiserver"],
        "compose": compose_prepared["executables"]["compose"],
    }
    return identity


def _q_oci_input(asset: dict, prepared: dict, provenance_row: dict) -> dict:
    """Project the authenticated Q OCI archive identity and its retained path."""
    reference = provenance_row.get("reference")
    if (not isinstance(reference, str) or not reference.startswith("ghcr.io/")
            or provenance_row.get("name") != asset.get("name")
            or provenance_row.get("sha256") != asset.get("sha256")
            or provenance_row.get("source") != asset.get("commit")):
        raise ValueError("Q OCI provenance differs from the selected retained archive")
    return {"repository": asset["repository"], "tag": asset["tag"], "source": asset["commit"],
            "name": asset["name"], "archiveSHA256": asset["sha256"], "reference": reference,
            "path": prepared["files"]["archive"],
            "preparationSHA256": prepared["preparationSHA256"],
            "inventorySHA256": prepared["inventorySHA256"]}


def prepare_q_guest_builder_images(identity: dict, guest_lock: dict, builder_lock: dict,
                                   scratch: Path, retained_images: Path) -> dict:
    """Import lock-selected Q OCI bytes through the maintained guest-image verifier.

    Caller holds the reference-store lease and has already authenticated the
    release lock/provenance with ``admit_locked_compose_runtime``. This helper
    only connects those retained asset paths to the existing exact OCI import
    and image-cache receipt APIs.
    """
    if identity.get("format") != "signed-compose-q-runtime":
        return {}
    q_inputs = identity.get("qOciInputs")
    if not isinstance(q_inputs, dict) or set(q_inputs) != {"guest", "builder"}:
        raise ValueError("Q runtime admission did not retain its guest/builder OCI pair")
    from prepare_guest_images import prepare as prepare_image, validate_image

    specifications = (("guest", guest_lock, "enhanced-vminit"),
                      ("builder", builder_lock, "enhanced-builder"))
    imports = []
    for role, image_lock, selected_name in specifications:
        rows = image_lock.get("images") if isinstance(image_lock, dict) else None
        matches = [item for item in rows if isinstance(item, dict) and item.get("name") == selected_name] \
            if isinstance(rows, list) else []
        selected = q_inputs[role]
        if len(matches) != 1:
            raise ValueError(f"Q {role} import requires one checked-in selected image row")
        image = matches[0]
        validate_image(image)
        if (image.get("reference") != selected["reference"]
                or image.get("archiveSHA256") != selected["archiveSHA256"]):
            raise ValueError(f"checked-in Q {role} image lock differs from retained provenance")
        imports.append((role, image, Path(selected["path"])))
    prepared = {}
    for role, image, source in imports:
        prepared[role] = prepare_image(image, scratch, retained_images,
                                      source_archive=source)
    return prepared


def prepare_locked_q_guest_builder_images(lock: dict, retained: Path, scratch: Path,
                                          prepared_root: Path, receipts: Path) -> dict:
    """Import Q OCI pairs only when the selected release lock contains all six assets."""
    assets = validate_lock(lock)
    compose_names = {"container-compose-signed-arm64.zip", "qualified-compose-release.json",
                     "container-compose-plugin-release-arm64.tar.gz", "container-release-arm64.tar.gz"}
    if not any(asset["repository"] == "stephenlclarke/container-compose"
               and asset["name"] in compose_names for asset in assets):
        return {}
    selection = select_compose_runtime_assets(lock)
    if selection["format"] != "signed-compose-q-runtime":
        return {}
    identity = admit_locked_compose_runtime(
        lock, retained / "release-objects", prepared_root, receipts)
    bazel = Path(__file__).resolve().parent
    guest_lock = json.loads((bazel / "guest-images.lock.json").read_text(encoding="utf-8"))
    builder_lock = json.loads((bazel / "builder-images.lock.json").read_text(encoding="utf-8"))
    scratch_images = scratch / "tmp"
    scratch_images.mkdir(mode=0o700, parents=True, exist_ok=True)
    retained_images = retained / "guest-images"
    retained_images.mkdir(mode=0o700, exist_ok=True)
    if (scratch_images.resolve() != scratch_images or retained_images.resolve() != retained_images
            or stat.S_IMODE(retained_images.stat().st_mode) != 0o700):
        raise ValueError("Q guest-image storage is not canonical and private")
    return prepare_q_guest_builder_images(identity, guest_lock, builder_lock,
                                          scratch_images, retained_images)


def retain_receipt(path: Path, receipt: dict) -> None:
    """Publish the inventory internally before the disposable SSD tree appears."""
    if path.is_symlink():
        raise ValueError("Symlinked retained preparation receipt")
    if path.exists():
        if json.loads(path.read_text()) != receipt:
            raise ValueError("Cannot replace a different retained preparation receipt")
    else:
        with tempfile.TemporaryDirectory(dir=path.parent, prefix="prepare-receipt-") as temporary:
            staged = Path(temporary) / "receipt"
            with os.fdopen(os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
                output.write(canonical(receipt))
                output.flush()
                os.fsync(output.fileno())
            staged.rename(path)
    # Recovery can encounter a receipt renamed just before an earlier process
    # died. Re-establish file and directory durability even when bytes match.
    with path.open("rb") as durable:
        os.fsync(durable.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def prepare(asset: dict, source: Path, root: Path, receipts: Path, *, expand=expand_package) -> dict:
    """Caller holds the reference-store lease; interruption never seals half a tree."""
    for directory in (root, receipts):
        if directory.resolve() != directory or not directory.is_dir():
            raise ValueError("Preparation roots must be existing non-symlinked directories")
    verify_object(source, asset)
    specification = {"schemaVersion": 1, "assetSHA256": asset["sha256"], "layout": layout(asset)}
    key = hashlib.sha256(canonical(specification).encode()).hexdigest()
    destination = root / key
    retained = receipts / (key + ".json")
    if retained.is_symlink():
        raise ValueError("Symlinked retained preparation receipt")
    if destination.exists() or destination.is_symlink():
        receipt = validate_prepared(destination, specification, json.loads(retained.read_text()))
    else:
        with tempfile.TemporaryDirectory(dir=root, prefix="prepare-") as temporary:
            staged = Path(temporary) / "payload"
            kind = specification["layout"]["format"]
            if kind == "pkg":
                expand(source, staged)
            else:
                staged.mkdir()
                if kind == "tar":
                    unpack_tar(source, staged, specification["layout"].get("omittedLinks"))
                elif kind == "zip":
                    unpack_zip(source, staged)
                elif kind == "selected-tar":
                    unpack_selected_tar(source, staged, specification["layout"])
                elif kind == "kernel-zstd":
                    unpack_kernel(source, staged)
                elif kind in ("raw", "raw-data", "json"):
                    copy_raw(source, staged, specification["layout"])
                else:
                    raise ValueError("Unsupported release preparation format")
            receipt = {"specification": specification, "inventory": inventory(staged)}
            with (staged / RECEIPT).open("x") as output:
                output.write(canonical(receipt))
            validate_prepared(staged, specification, receipt)
            # Detect changed retained bytes before publishing a preparation.
            verify_object(source, asset)
            retain_receipt(retained, receipt)
            staged.rename(destination)
    return {"assetSHA256": asset["sha256"], "preparationSHA256": key, "root": str(destination),
            "inventorySHA256": hashlib.sha256(canonical(receipt["inventory"]).encode()).hexdigest(),
            "executables": {name: str(destination / path) for name, path in specification["layout"]["executables"].items()},
            **({"files": {name: str(destination / path) for name, path in specification["layout"]["files"].items()}}
               if "files" in specification["layout"] else {})}


def require_prepared(asset: dict, source: Path, root: Path, receipts: Path) -> dict:
    """Runtime admission is read-only: never download, expand or repair a payload."""
    for directory in (root, receipts):
        if directory.resolve() != directory or not directory.is_dir():
            raise ValueError("Preparation roots must be existing non-symlinked directories")
    verify_object(source, asset)
    specification = {"schemaVersion": 1, "assetSHA256": asset["sha256"], "layout": layout(asset)}
    key = hashlib.sha256(canonical(specification).encode()).hexdigest()
    retained = receipts / (key + ".json")
    if retained.is_symlink():
        raise ValueError("Symlinked retained preparation receipt")
    destination = root / key
    receipt = validate_prepared(destination, specification, json.loads(retained.read_text()))
    return {"assetSHA256": asset["sha256"], "preparationSHA256": key, "root": str(destination),
            "inventorySHA256": hashlib.sha256(canonical(receipt["inventory"]).encode()).hexdigest(),
            "executables": {name: str(destination / path) for name, path in specification["layout"]["executables"].items()},
            **({"files": {name: str(destination / path) for name, path in specification["layout"]["files"].items()}}
               if "files" in specification["layout"] else {})}


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_file(path: Path, source: Path | None, data: bytes | None = None, mode: int = 0o600) -> None:
    """Write only registered pending asset files, never linked or foreign files."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        info = os.fstat(output.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError("Pending release file is not privately owned")
        os.ftruncate(output.fileno(), 0)
        if source is not None:
            with source.open("rb") as incoming:
                shutil.copyfileobj(incoming, output)
        else:
            output.write(data)
        output.flush()
        os.fchmod(output.fileno(), mode)
        os.fsync(output.fileno())


def require_retained(asset: dict, source: Path, root: Path, receipts: Path) -> dict:
    specification = {"schemaVersion": 1, "assetSHA256": asset["sha256"], "layout": layout(asset)}
    key = hashlib.sha256(canonical(specification).encode()).hexdigest()
    pending = root / (key + ".pending.json")
    if pending.exists() or pending.is_symlink():
        raise ValueError("Retained executable publication is unfinished")
    return require_prepared(asset, source, root, receipts)


def retain_prepared(asset: dict, source: Path, scratch: Path, root: Path, receipts: Path) -> dict:
    """Publish into permanent asset slots; scratch/extraction stays on SSD.

    The caller holds the reference-store lease. Pending intent precedes writes;
    runtime admission rejects that intent until every byte and mode is durable.
    Completed assets are immutable and never silently repaired or overwritten.
    """
    if root.resolve() != root or not root.is_dir():
        raise ValueError("Retained executable root must be a canonical directory")
    info = root.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("Retained executable root must be private")
    prepared = require_prepared(asset, source, scratch, receipts)
    key = prepared["preparationSHA256"]
    original, destination = Path(prepared["root"]), root / key
    receipt = json.loads((receipts / (key + ".json")).read_text())
    intent = canonical({"preparationSHA256": key, "inventorySHA256": prepared["inventorySHA256"]}).encode()
    pending = root / (key + ".pending.json")
    if pending.is_symlink() or destination.is_symlink():
        raise ValueError("Retained release paths must not be aliases")
    if pending.exists():
        info = pending.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or
                stat.S_IMODE(info.st_mode) != 0o600 or pending.read_bytes() != intent):
            raise ValueError("Pending release ownership changed")
    elif destination.exists():
        return require_retained(asset, source, root, receipts)
    else:
        with os.fdopen(os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
            output.write(intent)
            output.flush()
            os.fsync(output.fileno())
        sync_directory(root)
    destination.mkdir(mode=0o700, exist_ok=True)
    if destination.stat().st_uid != os.getuid() or stat.S_IMODE(destination.stat().st_mode) != 0o700:
        raise ValueError("Pending release directory ownership changed")
    # Reject foreign residue before resuming owned, partially copied files.
    existing = inventory(destination)
    expected = receipt["inventory"]
    if not existing.keys() <= expected.keys() or any(existing[name]["kind"] != expected[name]["kind"] for name in existing):
        raise ValueError("Unregistered files in pending release")
    for name, item in sorted(expected.items(), key=lambda pair: (pair[0].count("/"), pair[0])):
        target = destination / name
        if item["kind"] == "directory":
            target.mkdir(exist_ok=True)
            if target.stat().st_uid != os.getuid():
                raise ValueError("Pending release directory is foreign")
            target.chmod(item["mode"])
        else:
            if existing.get(name) == item:
                with os.fdopen(os.open(target, os.O_RDONLY | os.O_NOFOLLOW), "rb") as durable:
                    info = os.fstat(durable.fileno())
                    if info.st_uid != os.getuid() or info.st_nlink != 1:
                        raise ValueError("Pending release file ownership changed")
                    os.fsync(durable.fileno())
            else:
                durable_file(target, original / name, mode=item["mode"])
    durable_file(destination / RECEIPT, None, canonical(receipt).encode())
    validate_prepared(destination, receipt["specification"], receipt)
    # Detect source changes during copying, before publishing any executable.
    require_prepared(asset, source, scratch, receipts)
    for directory, _, _ in os.walk(destination, topdown=False):
        sync_directory(Path(directory))
    sync_directory(root)
    pending.unlink()
    sync_directory(root)
    return require_retained(asset, source, root, receipts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lock", type=Path)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    retained = Path.home() / "Library/Application Support/ContainerFamily/retained/workflow"
    if retained.stat().st_dev != Path.home().stat().st_dev:
        raise ValueError("Release archives require internal retained storage")
    scratch = Path("/Volumes/SSD/cf/bazel")
    executables = retained / "prepared-releases"
    executables.mkdir(mode=0o700, exist_ok=True)
    source_lock = json.loads(args.lock.read_text())
    acquired = acquire(source_lock, retained, scratch / "tmp", offline=args.offline)
    result = {"schemaVersion": 1, "scope": "prepared-release-assets-only", "runtimeReady": False,
              "lockSHA256": acquired["lockSHA256"], "assets": []}
    for asset in acquired["assets"]:
        source, receipts = Path(asset["path"]), retained / "prepared-receipts"
        specification = {"schemaVersion": 1, "assetSHA256": asset["sha256"], "layout": layout(asset)}
        key = hashlib.sha256(canonical(specification).encode()).hexdigest()
        if (executables / key).exists() and not (executables / (key + ".pending.json")).exists():
            result["assets"].append(require_retained(asset, source, executables, receipts))
        else:
            prepare(asset, source, scratch / "prepared-releases", receipts)
            result["assets"].append(retain_prepared(asset, source, scratch / "prepared-releases", executables, receipts))
    q_images = prepare_locked_q_guest_builder_images(
        source_lock, retained, scratch, executables, receipts)
    if q_images:
        result["qGuestBuilderImages"] = q_images
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
