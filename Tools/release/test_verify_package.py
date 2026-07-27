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
        with tarfile.open(archive_path, "w:gz") as archive:
            for name in (
                f"{package_root}/bin/devcontainer",
                f"{package_root}/bin/devcontainer-compose",
                f"{package_root}/bin/devcontainer-engine",
                f"{package_root}/libexec/container/plugins/devcontainer/bin/devcontainer",
            ):
                self.add_bytes(archive, name, b"binary", mode=0o755)
            self.add_bytes(
                archive,
                f"{package_root}/libexec/container/plugins/devcontainer/config.toml",
                b'abstract = "fixture"\n',
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
                    "README.md",
                    "com.github.stephenlclarke.devcontainer.plist.in",
                ):
                    self.add_bytes(
                        archive,
                        f"{metadata_root}/{name}",
                        f"{name}\n".encode(),
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

    def test_third_party_notice_metadata_cannot_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive, checksum = self.write_fixture(
                Path(temporary_directory),
                valid_notice_metadata=False,
            )
            result = self.run_verifier(archive, checksum)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("third-party notice metadata", result.stderr)


if __name__ == "__main__":
    unittest.main()
