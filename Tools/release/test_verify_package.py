"""Tests for release archive structure and provenance verification."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from dependency_metadata import load_dependencies


TOOLS = Path(__file__).resolve().parent
REPOSITORY_ROOT = TOOLS.parents[1]
VERIFIER = TOOLS / "verify-package.py"
VERSION = "1.2.3"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
NATIVE_COMPOSE = json.loads(
    (TOOLS / "native-compose.json").read_text(encoding="utf-8")
)


class PackageVerificationTests(unittest.TestCase):
    def add_bytes(
        self,
        archive: tarfile.TarFile,
        name: str,
        value: bytes,
        mode: int = 0o644,
    ) -> None:
        member = tarfile.TarInfo(name)
        member.size = len(value)
        member.mode = mode
        member.uid = 0
        member.gid = 0
        member.uname = "root"
        member.gname = "wheel"
        member.mtime = 0
        archive.addfile(member, io.BytesIO(value))

    def write_fixture(
        self,
        root: Path,
        *,
        commit: str = COMMIT,
        unsafe: bool = False,
        notarized: bool = True,
        legal_files: bool = True,
        valid_notice_metadata: bool = True,
        valid_compose_checksum: bool = True,
        native_config: bool = True,
        reference_support: bool = True,
        forbidden_runtime: str | None = None,
        unexpected_executable: bool = False,
        readme: bytes = b"README\n",
    ) -> tuple[Path, Path]:
        archive_path = root / "devcontainer-release-arm64.tar.gz"
        package_root = f"devcontainer-{VERSION}"
        metadata_root = f"{package_root}/share/devcontainer"
        build_info = {
            "architecture": "arm64",
            "buildType": "release",
            "commit": commit,
            "containerDistribution": "apple",
            "lane": "stable",
            "provider": "none",
            "source": "stephenlclarke/devcontainer",
            "version": VERSION,
        }
        sbom = {
            "spdxVersion": "SPDX-2.3",
            "name": f"devcontainer-{VERSION}",
            "documentNamespace": (
                "https://github.com/stephenlclarke/devcontainer/sbom/"
                + hashlib.sha256(
                    f"devcontainer:{VERSION}:{commit}".encode()
                ).hexdigest()
            ),
            "creationInfo": {
                "created": datetime.fromtimestamp(
                    0,
                    timezone.utc,
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "creators": ["Tool: devcontainer-write-sbom"],
            },
            "packages": [
                {
                    "name": "devcontainer",
                    "versionInfo": VERSION,
                    "downloadLocation": (
                        "https://github.com/stephenlclarke/devcontainer"
                    ),
                    "licenseDeclared": "Apache-2.0",
                    "licenseConcluded": "Apache-2.0",
                    "sourceInfo": f"Exact Git commit {commit}",
                }
            ],
            "relationships": [],
        }
        native_dependency_names = [
            "container-compose-swift:fixture",
            "container-compose-go:example.com/fixture",
            "container-compose-go:standard-library",
        ]

        def native_spdx_id(name: str) -> str:
            readable = "".join(
                character if character.isalnum() else "-" for character in name
            )
            digest = hashlib.sha256(name.encode()).hexdigest()[:12]
            return f"SPDXRef-{readable}-{digest}"

        native_root_identifier = native_spdx_id("container-compose")
        native_sbom = {
            "spdxVersion": "SPDX-2.3",
            "packages": [
                {
                    "SPDXID": native_root_identifier,
                    "name": "container-compose",
                    "versionInfo": NATIVE_COMPOSE["version"],
                    "licenseDeclared": "Apache-2.0",
                    "licenseConcluded": "Apache-2.0",
                    "sourceInfo": f"Exact Git revision {NATIVE_COMPOSE['commit']}",
                },
                *[
                    {
                        "SPDXID": native_spdx_id(name),
                        "name": name,
                        "versionInfo": "1.2.3",
                        "licenseDeclared": "MIT",
                        "licenseConcluded": "MIT",
                    }
                    for name in native_dependency_names
                ],
            ],
            "relationships": [
                {
                    "spdxElementId": native_root_identifier,
                    "relationshipType": "DEPENDS_ON",
                    "relatedSpdxElement": native_spdx_id(name),
                }
                for name in native_dependency_names
            ],
        }
        dependencies = load_dependencies(
            REPOSITORY_ROOT / "Package.resolved",
            TOOLS / "dependency-licenses.json",
        )
        for dependency in dependencies:
            identifier = "SPDXRef-" + "".join(
                character if character.isalnum() else "-"
                for character in dependency.identity
            )
            sbom["packages"].append(
                {
                    "name": dependency.identity,
                    "versionInfo": dependency.version,
                    "downloadLocation": dependency.location,
                    "licenseDeclared": dependency.license,
                    "licenseConcluded": dependency.license,
                    "sourceInfo": f"Exact Git revision {dependency.revision}",
                }
            )
            sbom["relationships"].append(
                {
                    "spdxElementId": "SPDXRef-Package-devcontainer",
                    "relationshipType": "DEPENDS_ON",
                    "relatedSpdxElement": identifier,
                }
            )
        for name, version, revision, location, license_name in (
            (
                "devcontainers-cli",
                "0.89.0",
                "5dc7533314b5ba7ec3875c30143dfe1aec644870",
                "https://registry.npmjs.org/@devcontainers/cli/-/cli-0.89.0.tgz",
                "MIT",
            ),
            (
                "container-compose",
                NATIVE_COMPOSE["version"],
                NATIVE_COMPOSE["commit"],
                NATIVE_COMPOSE["repository"],
                "Apache-2.0",
            ),
        ):
            identifier = "SPDXRef-" + name
            sbom["packages"].append(
                {
                    "SPDXID": identifier,
                    "name": name,
                    "versionInfo": version,
                    "downloadLocation": location,
                    "licenseDeclared": license_name,
                    "licenseConcluded": license_name,
                    "filesAnalyzed": False,
                    "checksums": [
                        {
                            "algorithm": "SHA256",
                            "checksumValue": (
                                hashlib.sha256(b"binary").hexdigest()
                                if name == "container-compose" and valid_compose_checksum
                                else "a" * 64
                            ),
                        }
                    ],
                    "sourceInfo": f"Exact Git revision {revision}",
                }
            )
            sbom["relationships"].append(
                {
                    "spdxElementId": "SPDXRef-Package-devcontainer",
                    "relationshipType": "DEPENDS_ON",
                    "relatedSpdxElement": identifier,
                }
            )
        with tarfile.open(archive_path, "w:gz") as archive:
            for name in (
                f"{package_root}/bin/devcontainer",
                f"{package_root}/bin/devcontainer-docker",
                f"{package_root}/bin/devcontainer-compose",
                f"{package_root}/bin/devcontainer-engine",
                f"{package_root}/libexec/container/plugins/devcontainer/bin/devcontainer",
                f"{package_root}/libexec/devcontainer-compose/bin/compose",
                f"{package_root}/libexec/devcontainer-compose/resources/compose-normalizer",
                f"{package_root}/libexec/devcontainer-compose/resources/volume-initializer/compose-volume-initializer-linux-arm64",
                f"{package_root}/libexec/devcontainer-compose/resources/volume-initializer/compose-volume-initializer-linux-amd64",
                f"{package_root}/share/devcontainer/reference-cli/devcontainer.js",
            ):
                self.add_bytes(archive, name, b"binary", mode=0o755)
            if forbidden_runtime:
                self.add_bytes(
                    archive,
                    f"{package_root}/bin/{forbidden_runtime}",
                    b"forbidden runtime",
                    mode=0o755,
                )
            if unexpected_executable:
                self.add_bytes(
                    archive,
                    f"{package_root}/libexec/renamed-runtime",
                    b"unexpected runtime",
                    mode=0o755,
                )
            if reference_support:
                for name in (
                    "CHANGELOG.md",
                    "LICENSE.txt",
                    "README.md",
                    "ThirdPartyNotices.txt",
                    "dist/spec-node/devContainersSpecCLI.js",
                    "package.json",
                    "scripts/updateUID.Dockerfile",
                ):
                    self.add_bytes(
                        archive,
                        (
                            f"{package_root}/share/devcontainer/"
                            f"reference-cli/{name}"
                        ),
                        f"Dev Containers {name}\n".encode(),
                    )
            self.add_bytes(
                archive,
                f"{package_root}/libexec/container/plugins/devcontainer/config.toml",
                b'abstract = "fixture"\n',
            )
            self.add_bytes(
                archive,
                f"{package_root}/libexec/devcontainer-compose/resources/build-info.json",
                json.dumps(
                    {
                        "version": NATIVE_COMPOSE["version"],
                        "source": "stephenlclarke/container-compose",
                        "branch": "detached",
                        "lane": "bundled-stock",
                        "commit": NATIVE_COMPOSE["commit"],
                        "buildType": "release",
                        "containerSource": "apple/container",
                        "containerRef": NATIVE_COMPOSE["appleContainerRevision"],
                        "containerizationSource": "apple/containerization",
                        "containerizationRef": NATIVE_COMPOSE[
                            "appleContainerizationRevision"
                        ],
                        "composeGoVersion": "v2.12.1",
                        "runtimeCapabilitySchemaVersion": 1,
                        "runtimeCapabilities": [],
                    }
                ).encode(),
            )
            self.add_bytes(
                archive,
                f"{package_root}/libexec/devcontainer-compose/LICENSE",
                b"Apache License, Version 2.0\n",
            )
            if native_config:
                self.add_bytes(
                    archive,
                    f"{package_root}/libexec/devcontainer-compose/config.toml",
                    b'schemaVersion = 1\n',
                )
            native_compose_root = (
                f"{package_root}/libexec/devcontainer-compose"
            )
            self.add_bytes(
                archive,
                f"{native_compose_root}/resources/Package.resolved",
                json.dumps({"pins": [{"identity": "fixture"}]}).encode(),
            )
            self.add_bytes(
                archive,
                f"{native_compose_root}/resources/go-modules.txt",
                b"# example.com/fixture v1.2.3\n## explicit; go 1.26\n",
            )
            self.add_bytes(
                archive,
                f"{native_compose_root}/resources/container-compose.spdx.json",
                json.dumps(native_sbom).encode(),
            )
            native_notices = [
                "container-compose bundled provider third-party notices",
                "=" * 78,
                "",
            ]
            for name in native_dependency_names:
                native_notices.extend(
                    [
                        f"Dependency: {name}",
                        "Declared license: MIT",
                        "x" * 400,
                    ]
                )
            self.add_bytes(
                archive,
                f"{native_compose_root}/THIRD-PARTY-NOTICES.txt",
                ("\n".join(native_notices) + "\n").encode(),
            )
            self.add_bytes(
                archive,
                f"{metadata_root}/build-info.json",
                json.dumps(build_info).encode(),
            )
            self.add_bytes(
                archive,
                f"{metadata_root}/devcontainer.spdx.json",
                json.dumps(sbom).encode(),
            )
            if legal_files:
                for name in (
                    "LICENSE",
                    "NOTICE.md",
                    "com.github.stephenlclarke.devcontainer.plist.in",
                ):
                    self.add_bytes(
                        archive,
                        f"{metadata_root}/{name}",
                        f"{name}\n".encode(),
                    )
                self.add_bytes(
                    archive,
                    f"{metadata_root}/README.md",
                    readme,
                )
                third_party_notices = [
                    "devcontainer third-party notices",
                    "=" * 78,
                    "",
                ]
                for dependency in dependencies:
                    license_identifier = dependency.license
                    if (
                        not valid_notice_metadata
                        and dependency == dependencies[0]
                    ):
                        license_identifier = "MIT"
                    third_party_notices.extend(
                        [
                            f"Dependency: {dependency.identity}",
                            f"Version: {dependency.version}",
                            f"Revision: {dependency.revision}",
                            f"Source: {dependency.location}",
                            f"Declared license: {license_identifier}",
                            "x" * 40,
                        ]
                    )
                self.add_bytes(
                    archive,
                    f"{metadata_root}/THIRD-PARTY-NOTICES.txt",
                    ("\n".join(third_party_notices) + "\n").encode(),
                )
            if notarized:
                self.add_bytes(
                    archive,
                    f"{metadata_root}/notarization.json",
                    json.dumps(
                        {
                            "archiveSHA256": "0" * 64,
                            "id": "01234567-89ab-cdef-0123-456789abcdef",
                            "status": "Accepted",
                        }
                    ).encode(),
                )
            if unsafe:
                self.add_bytes(archive, "../escape", b"unsafe")
        checksum = root / f"{archive_path.name}.sha256"
        checksum.write_text(
            f"{hashlib.sha256(archive_path.read_bytes()).hexdigest()}  "
            f"{archive_path.name}\n",
            encoding="utf-8",
        )
        return archive_path, checksum

    def run_verifier(
        self,
        archive: Path,
        checksum: Path,
        *extra: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(VERIFIER),
                "--archive",
                str(archive),
                "--checksum",
                str(checksum),
                "--expected-version",
                VERSION,
                "--expected-lane",
                "stable",
                "--expected-commit",
                COMMIT,
                *extra,
            ],
            capture_output=True,
            text=True,
        )

    def test_valid_notarized_package_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive, checksum = self.write_fixture(Path(temporary_directory))
            result = self.run_verifier(
                archive,
                checksum,
                "--require-notarization",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertTrue(value["notarized"])
            self.assertEqual(value["commit"], COMMIT)

    def test_unsafe_archive_member_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive, checksum = self.write_fixture(
                Path(temporary_directory),
                unsafe=True,
            )
            result = self.run_verifier(archive, checksum)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe path", result.stderr)

    def test_non_apple_runtime_executables_are_rejected(self) -> None:
        for runtime in ("docker", "docker-compose", "colima", "podman", "nerdctl"):
            with self.subTest(runtime=runtime), tempfile.TemporaryDirectory() as temporary_directory:
                archive, checksum = self.write_fixture(
                    Path(temporary_directory),
                    forbidden_runtime=runtime,
                )
                result = self.run_verifier(archive, checksum)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "forbidden non-Apple runtime executable",
                    result.stderr,
                )

    def test_unexpected_renamed_runtime_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive, checksum = self.write_fixture(
                Path(temporary_directory),
                unexpected_executable=True,
            )
            result = self.run_verifier(archive, checksum)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("package executable inventory is not exact", result.stderr)

    def test_provenance_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive, checksum = self.write_fixture(
                Path(temporary_directory),
                commit="f" * 40,
            )
            result = self.run_verifier(archive, checksum)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("build-info.json", result.stderr)

    def test_checksum_must_use_archive_basename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            archive, checksum = self.write_fixture(root)
            checksum.write_text(
                f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  "
                f"{archive.resolve()}\n",
                encoding="utf-8",
            )
            result = self.run_verifier(archive, checksum)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("archive basename", result.stderr)

    def test_required_notarization_cannot_be_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive, checksum = self.write_fixture(
                Path(temporary_directory),
                notarized=False,
            )
            result = self.run_verifier(
                archive,
                checksum,
                "--require-notarization",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("notarization evidence", result.stderr)

    def test_required_legal_files_cannot_be_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive, checksum = self.write_fixture(
                Path(temporary_directory),
                legal_files=False,
            )
            result = self.run_verifier(archive, checksum)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("archive is missing", result.stderr)

    def test_native_compose_configuration_cannot_be_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive, checksum = self.write_fixture(
                Path(temporary_directory),
                native_config=False,
            )
            result = self.run_verifier(archive, checksum)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("config.toml", result.stderr)

    def test_reference_cli_support_files_cannot_be_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive, checksum = self.write_fixture(
                Path(temporary_directory),
                reference_support=False,
            )
            result = self.run_verifier(archive, checksum)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("reference-cli", result.stderr)

    def test_third_party_notice_metadata_cannot_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive, checksum = self.write_fixture(
                Path(temporary_directory),
                valid_notice_metadata=False,
            )
            result = self.run_verifier(archive, checksum)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("third-party notice metadata", result.stderr)

    def test_bundled_compose_checksum_cannot_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive, checksum = self.write_fixture(
                Path(temporary_directory),
                valid_compose_checksum=False,
            )
            result = self.run_verifier(archive, checksum)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("checksum does not match bundled container-compose", result.stderr)

    def test_package_readme_relative_target_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive, checksum = self.write_fixture(
                Path(temporary_directory),
                readme=b"[Install](INSTALL.md)\n",
            )
            result = self.run_verifier(archive, checksum)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("repository-relative target", result.stderr)

    def test_package_readme_source_target_must_match_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive, checksum = self.write_fixture(
                Path(temporary_directory),
                readme=(
                    b"[Install](https://github.com/stephenlclarke/"
                    b"devcontainer/blob/ffffffffffffffffffffffffffffffffffffffff/"
                    b"INSTALL.md)\n"
                ),
            )
            result = self.run_verifier(archive, checksum)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("source target is not bound", result.stderr)


if __name__ == "__main__":
    unittest.main()
