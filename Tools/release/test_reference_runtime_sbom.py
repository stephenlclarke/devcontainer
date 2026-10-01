# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

"""Portable native SPDX closure tests; no executable or signing tools run."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from reference_runtime_metadata import packages_from_root
import test_verify_package as package_tests

COMMIT = package_tests.COMMIT
VERSION = package_tests.VERSION


TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[1]

def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def encoded(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


class RuntimeSBOMTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage = self.root / f"devcontainer-{VERSION}"
        self.runtime = self.stage / "libexec/devcontainer/reference"
        self.shared = self.stage / "share/devcontainer"
        self.shared.mkdir(parents=True)
        self.lock = {
            "schemaVersion": 1,
            "node": {"version": "24.21.0", "url": "https://nodejs.org/dist/v24.21.0/node-v24.21.0-darwin-arm64.tar.gz",
                     "integrity": "sha256-" + base64.b64encode(b"n" * 32).decode(), "maxBytes": 67108864},
            "cli": {"version": "0.88.0", "url": "https://registry.npmjs.org/@devcontainers/cli/-/cli-0.88.0.tgz",
                    "integrity": "sha512-" + base64.b64encode(b"c" * 64).decode(), "maxBytes": 4194304},
        }
        mit = b'Permission is hereby granted, free of charge\ncopyright notice\nTHE SOFTWARE IS PROVIDED "AS IS"\n'
        self.files = {
            "node": b"unsigned-node-fixture", "NODE-LICENSE.txt": b"Node.js is licensed for use as follows:\n" + mit,
            "runtime-lock.json": encoded(self.lock), "cli/package.json": encoded({"name": "@devcontainers/cli", "version": "0.88.0", "license": "MIT"}),
            "cli/LICENSE.txt": b"MIT License\n" + mit, "cli/ThirdPartyNotices.txt": b"retained upstream dependency notices\n",
            "cli/devcontainer.js": b"entrypoint", "cli/dist/spec-node/devContainersSpecCLI.js": b"cli-code",
            "cli/scripts/updateUID.Dockerfile": b"FROM fixture",
        }
        self.resolved = (ROOT / "Package.resolved").read_bytes()
        self.candidate = {
            "schemaVersion": 2, "kind": "unsigned-native-candidate", "version": VERSION, "commit": COMMIT,
            "runtimeProfile": "stock", "architecture": "arm64", "compilationMode": "opt", "distributionReady": False,
            "dependencyLockSHA256": digest(self.resolved),
            "referenceRuntime": {"nodeVersion": "24.21.0", "cliVersion": "0.88.0", "lockSHA256": digest(self.files["runtime-lock.json"]),
                                 "files": {name: digest(content) for name, content in self.files.items()}},
        }
        self.write_stage()

    def write_stage(self) -> None:
        for name, content in self.files.items():
            path = self.runtime / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            path.chmod(0o755 if name == "node" else 0o644)
        (self.shared / "candidate.json").write_bytes(encoded(self.candidate))

    def rebind(self) -> None:
        self.files["runtime-lock.json"] = encoded(self.lock)
        self.candidate["referenceRuntime"]["lockSHA256"] = digest(self.files["runtime-lock.json"])
        self.candidate["referenceRuntime"]["files"] = {name: digest(content) for name, content in self.files.items()}
        self.write_stage()

    def project(self) -> list[dict]:
        return packages_from_root(self.runtime, version=VERSION, commit=COMMIT, resolved=ROOT / "Package.resolved")

    def sbom(self) -> dict:
        output = self.root / "sbom.json"
        subprocess.run([sys.executable, str(TOOLS / "write-sbom.py"), "--version", VERSION, "--commit", COMMIT,
                        "--source-date-epoch", "0", "--output", str(output), "--resolved", str(ROOT / "Package.resolved"),
                        "--license-manifest", str(TOOLS / "dependency-licenses.json"), "--reference-runtime-root", str(self.runtime)],
                       check=True, capture_output=True)
        return json.loads(output.read_text())

    def archive(self, mutate=None) -> tuple[Path, Path]:
        fixture = package_tests.PackageVerificationTests()
        archive, checksum = fixture.write_fixture(self.root)
        with tarfile.open(archive) as source:
            payload = {m.name: (source.extractfile(m).read(), m.mode) for m in source.getmembers()}
        prefix = f"devcontainer-{VERSION}"
        payload[f"{prefix}/share/devcontainer/devcontainer.spdx.json"] = (encoded(self.sbom()), 0o644)
        payload[f"{prefix}/share/devcontainer/candidate.json"] = (encoded(self.candidate), 0o644)
        payload[f"{prefix}/share/devcontainer/Package.resolved"] = (self.resolved, 0o644)
        payload[f"{prefix}/bin/devcontainer-docker"] = (b"binary", 0o755)
        for name, content in self.files.items():
            # A signed Node differs from the unsigned candidate; this test does not claim signature admission.
            payload[f"{prefix}/libexec/devcontainer/reference/{name}"] = (b"signed-node-fixture" if name == "node" else content, 0o755 if name == "node" else 0o644)
        if mutate:
            mutate(payload)
        with tarfile.open(archive, "w:gz") as target:
            for name, (content, mode) in payload.items():
                fixture.add_bytes(target, name, content, mode)
        checksum.write_text(f"{digest(archive.read_bytes())}  {archive.name}\n")
        return archive, checksum

    def verify(self, mutate=None) -> subprocess.CompletedProcess:
        return package_tests.PackageVerificationTests().run_verifier(*self.archive(mutate))

    def test_writer_and_archive_admit_exact_two_runtime_entries(self) -> None:
        packages = self.project()
        self.assertEqual([p["name"] for p in packages], ["node", "@devcontainers/cli"])
        self.assertEqual(packages[0]["checksums"], [{"algorithm": "SHA256", "checksumValue": (b"n" * 32).hex()}])
        self.assertEqual(packages[1]["checksums"][0]["algorithm"], "SHA512")
        self.assertEqual({p["licenseConcluded"] for p in packages}, {"NOASSERTION"})
        first = self.sbom()
        self.assertEqual(first, self.sbom())
        self.assertEqual(len(first["packages"]), len(json.loads((ROOT / "Package.resolved").read_bytes())["pins"]) + 3)
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_changed_runtime_version_or_archive_origin(self) -> None:
        for field, value in (("version", "99.0.0"), ("url", "https://untrusted.invalid/node.tar.gz")):
            with self.subTest(field=field):
                original = self.lock["node"][field]
                self.lock["node"][field] = value
                self.rebind()
                with self.assertRaisesRegex(ValueError, "version or archive"):
                    self.project()
                self.lock["node"][field] = original
        self.rebind()

    def test_rejects_malformed_or_wrong_algorithm_integrity(self) -> None:
        for integrity in ("sha256-not-base64!", "sha512-" + base64.b64encode(b"n" * 64).decode(), "sha256-" + base64.b64encode(b"x").decode()):
            with self.subTest(integrity=integrity):
                self.lock["node"]["integrity"] = integrity
                self.rebind()
                with self.assertRaisesRegex(ValueError, "integrity"):
                    self.project()

    def test_rejects_changed_lock_without_candidate_rebinding(self) -> None:
        (self.runtime / "runtime-lock.json").write_bytes(encoded({**self.lock, "schemaVersion": 2}))
        with self.assertRaisesRegex(ValueError, "differs from candidate"):
            self.project()

    def test_rejects_missing_legal_file_or_changed_authenticated_legal_text(self) -> None:
        for name in ("NODE-LICENSE.txt", "cli/LICENSE.txt", "cli/ThirdPartyNotices.txt"):
            with self.subTest(name=name):
                path = self.runtime / name
                path.unlink()
                with self.assertRaisesRegex(ValueError, "missing or linked"):
                    self.project()
                self.write_stage()
        original_files = self.files.copy()
        self.files["cli/LICENSE.txt"] = b"not an MIT declaration"
        self.rebind()
        with self.assertRaisesRegex(ValueError, "license text"):
            self.project()
        for name, replacement, message in (
            ("NODE-LICENSE.txt", original_files["NODE-LICENSE.txt"].split(b"\n", 1)[1], "Node license declaration"),
            ("cli/LICENSE.txt", original_files["cli/LICENSE.txt"].split(b"\n", 1)[1], "CLI license declaration"),
            ("cli/ThirdPartyNotices.txt", b" \n ", "third-party notices are empty"),
        ):
            with self.subTest(name=name, authenticated_legal_content="invalid"):
                self.files = original_files.copy()
                self.files[name] = replacement
                self.rebind()
                with self.assertRaisesRegex(ValueError, message):
                    self.project()

    def test_rejects_cli_version_name_license_and_runtime_inventory_drift(self) -> None:
        for key, value in (("version", "0.1.0"), ("name", "other-cli"), ("license", "Apache-2.0")):
            with self.subTest(key=key):
                self.files["cli/package.json"] = encoded({"name": "@devcontainers/cli", "version": "0.88.0", "license": "MIT", key: value})
                self.rebind()
                with self.assertRaisesRegex(ValueError, "CLI package metadata"):
                    self.project()
        self.files["cli/package.json"] = encoded({"name": "@devcontainers/cli", "version": "0.88.0", "license": "MIT"})
        self.rebind()
        (self.runtime / "extra.txt").write_text("extra")
        with self.assertRaisesRegex(ValueError, "unexpected files"):
            self.project()

    def test_rejects_symlinked_stage_legal_file(self) -> None:
        path = self.runtime / "cli/LICENSE.txt"
        copy_path = self.root / "license"
        copy_path.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(copy_path)
        with self.assertRaisesRegex(ValueError, "missing or linked"):
            self.project()

    def test_rejects_wrong_candidate_source_or_selected_lock(self) -> None:
        for key, value in (("commit", "f" * 40), ("version", "1.2.4"), ("dependencyLockSHA256", "f" * 64)):
            with self.subTest(key=key):
                original = self.candidate[key]
                self.candidate[key] = value
                self.write_stage()
                with self.assertRaisesRegex(ValueError, "selected package source"):
                    self.project()
                self.candidate[key] = original

    def test_archive_rejects_missing_runtime_candidate_and_fourth_executable(self) -> None:
        prefix = f"devcontainer-{VERSION}"
        for name in (f"{prefix}/share/devcontainer/candidate.json", f"{prefix}/bin/devcontainer-docker",
                     f"{prefix}/libexec/devcontainer/reference/node", f"{prefix}/libexec/devcontainer/reference/cli/LICENSE.txt"):
            with self.subTest(name=name):
                result = self.verify(lambda payload: payload.pop(name))
                self.assertNotEqual(result.returncode, 0)

    def test_archive_rejects_runtime_spdx_absence_tampering_and_relationship_drift(self) -> None:
        key = f"devcontainer-{VERSION}/share/devcontainer/devcontainer.spdx.json"
        def change(payload, mutation):
            sbom = json.loads(payload[key][0])
            mutation(sbom)
            payload[key] = (encoded(sbom), 0o644)
        for field, value in (("versionInfo", "24.0.0"), ("downloadLocation", "https://elsewhere.invalid/archive"),
                             ("checksums", []), ("licenseConcluded", "MIT")):
            with self.subTest(field=field):
                result = self.verify(lambda payload: change(payload, lambda sbom: sbom["packages"][-2].update({field: value})))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("runtime metadata", result.stderr)
        for mutation in (lambda sbom: sbom["packages"].pop(), lambda sbom: sbom["relationships"].pop()):
            result = self.verify(lambda payload: change(payload, mutation))
            self.assertNotEqual(result.returncode, 0)

    def test_legacy_archive_cannot_add_runtime_sbom_entries_without_native_closure(self) -> None:
        fixture = package_tests.PackageVerificationTests()
        archive, checksum = fixture.write_fixture(self.root)
        with tarfile.open(archive) as source:
            payload = {m.name: (source.extractfile(m).read(), m.mode) for m in source.getmembers()}
        key = f"devcontainer-{VERSION}/share/devcontainer/devcontainer.spdx.json"
        sbom = json.loads(payload[key][0])
        sbom["packages"].extend(self.project())
        payload[key] = (encoded(sbom), 0o644)
        with tarfile.open(archive, "w:gz") as target:
            for name, (content, mode) in payload.items():
                fixture.add_bytes(target, name, content, mode)
        checksum.write_text(f"{digest(archive.read_bytes())}  {archive.name}\n")
        result = fixture.run_verifier(archive, checksum)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dependency set", result.stderr)

    def test_archive_rejects_changed_packaged_lock_and_runtime_cli_bytes(self) -> None:
        for name in ("share/devcontainer/Package.resolved", "libexec/devcontainer/reference/cli/devcontainer.js"):
            result = self.verify(lambda payload: payload.update({f"devcontainer-{VERSION}/" + name: (b"changed", 0o644)}))
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
