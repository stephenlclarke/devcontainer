"""Create an unsigned native candidate; not a signed distributable release."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import struct
import sys
import tempfile


PRODUCTS = {"devcontainer", "devcontainer-compose", "devcontainer-engine"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package(manifest: dict, archive: Path, receipt: Path, archive_tool: Path) -> None:
    """Only copy declared inputs; never discover a build or run a compiler."""
    if set(manifest["binaries"]) != PRODUCTS:
        raise ValueError("Candidate must contain exactly the three native products")
    version_match = re.search(r"^DEVCONTAINER_VERSION\s*\?=\s*([0-9]+\.[0-9]+\.[0-9]+)\s*$", Path(manifest["makefile"]).read_text(), re.M)
    if not version_match:
        raise ValueError("Missing semantic version in Makefile")
    version = version_match[1]
    commit = manifest["commit"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Invalid candidate source commit")
    epoch = int(manifest["epoch"])
    if epoch < 0 or manifest["profile"] not in {"stock", "enhanced"}:
        raise ValueError("Invalid candidate profile or epoch")
    spec = importlib.util.spec_from_file_location("reproducible_archive", archive_tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    archive.parent.mkdir(parents=True, exist_ok=True)
    # Stage beside the declared Bazel output, not Foundation or Python's default
    # temporary location. Bazel removes this sandbox after retaining its outputs.
    with tempfile.TemporaryDirectory(dir=archive.parent, prefix="candidate-stage-") as temporary:
        stage = Path(temporary) / f"devcontainer-{version}"
        binary_root = stage / "bin"
        binary_root.mkdir(parents=True)
        shared = stage / "share/devcontainer"
        shared.mkdir(parents=True)
        for name, source in manifest["binaries"].items():
            with Path(source).open("rb") as binary:
                if binary.read(8) != struct.pack("<II", 0xFEEDFACF, 0x0100000C):
                    raise ValueError("Candidate product is not a thin arm64 Mach-O binary")
            shutil.copyfile(source, binary_root / name)
            (binary_root / name).chmod(0o755)
        for name, source in manifest["files"].items():
            if name != Path(name).name or name in {".", ".."}:
                raise ValueError("Unsafe candidate resource name")
            shutil.copyfile(source, shared / name)
            (shared / name).chmod(0o644)
        plugin = stage / "libexec/container/plugins/devcontainer"
        (plugin / "bin").mkdir(parents=True)
        shutil.copyfile(binary_root / "devcontainer", plugin / "bin/devcontainer")
        (plugin / "bin/devcontainer").chmod(0o755)
        shutil.move(shared / "devcontainer-plugin-config.toml", plugin / "config.toml")
        entries = json.loads(Path(manifest["licenses"]).read_text())
        texts = sorted({entry["license_text"] for group in entries for entry in group["licenses"] if entry.get("license_text")})
        if not texts:
            raise ValueError("Candidate has no transitive dependency license evidence")
        (shared / "THIRD-PARTY-NOTICES.txt").write_text("\n\n".join(path + "\n\n" + Path(path).read_text() for path in texts))
        shutil.copyfile(manifest["resolved"], shared / "Package.resolved")
        identity = {
            "schemaVersion": 1, "kind": "unsigned-native-candidate", "version": version,
            "commit": commit, "runtimeProfile": manifest["profile"], "architecture": "arm64",
            "compilationMode": "opt", "distributionReady": False,
            "dependencyLockSHA256": sha256(Path(manifest["resolved"])),
            "products": {name: sha256(binary_root / name) for name in sorted(PRODUCTS)},
        }
        (shared / "candidate.json").write_text(json.dumps(identity, sort_keys=True, indent=2) + "\n")
        # Normalize permissions independently of the operator's umask.
        for path in [stage, *stage.rglob("*")]:
            if path.is_dir():
                path.chmod(0o755)
            elif path.parent.name != "bin":
                path.chmod(0o644)
        module.create_archive(stage, archive, epoch)
    identity["archiveSHA256"] = sha256(archive)
    identity["archiveSize"] = archive.stat().st_size
    receipt.write_text(json.dumps(identity, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    package(json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
