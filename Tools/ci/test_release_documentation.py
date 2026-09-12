"""Keep release-facing documentation aligned with authoritative pins."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def json_document(relative: str) -> dict[str, object]:
    """Read one repository-owned JSON authority document."""
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def product_version() -> str:
    """Return the Makefile-owned product version."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    match = re.search(
        r"^DEVCONTAINER_VERSION \?= ([0-9]+\.[0-9]+\.[0-9]+)$",
        makefile,
        re.MULTILINE,
    )
    if match is None:
        raise AssertionError("Makefile has no unique semantic product version")
    return match.group(1)


def resolved_pin(identity: str) -> dict[str, object]:
    """Return one exact dependency pin from the stock lock file."""
    resolved = json_document("Package.stock.resolved")
    matches = [
        pin for pin in resolved["pins"] if pin["identity"] == identity
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one stock pin for {identity}")
    return matches[0]


class ReleaseDocumentationTests(unittest.TestCase):
    def test_machine_readable_runtime_pins_agree(self) -> None:
        native = json_document("Tools/release/native-compose.json")
        manifest = json_document("Tests/Parity/manifest.json")["referencePins"]

        self.assertEqual(
            native["appleContainerVersion"],
            manifest["appleContainer"]["stableVersion"],
        )
        self.assertEqual(
            native["appleContainerRevision"],
            manifest["appleContainer"]["stableCommit"],
        )
        self.assertEqual(
            native["version"],
            manifest["containerCompose"]["stableVersion"],
        )
        self.assertEqual(
            native["commit"],
            manifest["containerCompose"]["stableCommit"],
        )

        apple_container = resolved_pin("container")
        apple_containerization = resolved_pin("containerization")
        self.assertEqual(
            apple_container["state"]["version"],
            native["appleContainerVersion"],
        )
        self.assertEqual(
            apple_container["state"]["revision"],
            native["appleContainerRevision"],
        )
        self.assertEqual(
            apple_containerization["state"]["version"],
            native["appleContainerizationVersion"],
        )
        self.assertEqual(
            apple_containerization["state"]["revision"],
            native["appleContainerizationRevision"],
        )

    def test_current_release_statements_use_the_makefile_version(self) -> None:
        version = product_version()
        expected = {
            "README.md": (
                f"Version {version} is the current release candidate",
                f"Version {version} is the latest immutable stable baseline.",
            ),
            "INSTALL.md": (
                f"> Version {version} is the current release candidate.",
                f"> Version {version} is the stable release.",
            ),
            "USER_GUIDE.md": (f"`devcontainer` {version}.",),
            "COMPATIBILITY.md": (
                f"> Version {version} is the current source candidate",
                f"> Version {version} is the latest immutable stable "
                "compatibility baseline.",
            ),
            "CONFORMANCE.md": (f"`devcontainer` {version} conforms",),
            "RELEASE.md": (f"> Version {version} uses the release process",),
            "Sources/DevContainerCore/DevContainerCore.docc/Conformance.md": (
                f"Version {version} is the current release candidate.",
                f"Version {version} is conformant",
            ),
            "docs/homebrew-tap-README.md": (
                "releases/download/"
                f"{version}/devcontainer-release-arm64.tar.gz",
            ),
        }
        for relative, statements in expected.items():
            with self.subTest(path=relative):
                contents = (ROOT / relative).read_text(encoding="utf-8")
                if not any(statement in contents for statement in statements):
                    self.fail(
                        f"{relative} is missing a current release statement: "
                        f"{statements}"
                    )

    def test_release_matrix_versions_are_documented(self) -> None:
        native = json_document("Tools/release/native-compose.json")
        pins = json_document("Tests/Parity/manifest.json")["referencePins"]
        expected = {
            "README.md": (
                native["appleContainerVersion"],
                native["appleContainerizationVersion"],
                native["version"],
                pins["devcontainersCli"]["version"],
            ),
            "COMPATIBILITY.md": (
                native["appleContainerVersion"],
                native["appleContainerizationVersion"],
                native["version"],
                pins["devcontainersCli"]["version"],
                pins["docker"]["cliVersion"],
                pins["docker"]["engineVersion"],
                pins["docker"]["composeVersion"],
                pins["vscode"]["version"],
                pins["vscode"]["devContainersExtension"]["version"],
            ),
            "TESTING.md": (
                native["appleContainerVersion"],
                native["version"],
                pins["devcontainersCli"]["version"],
                pins["vscode"]["version"],
                pins["vscode"]["devContainersExtension"]["version"],
            ),
            "Sources/DevContainerCore/DevContainerCore.docc/Testing.md": (
                native["appleContainerVersion"],
                native["version"],
                pins["vscode"]["version"],
            ),
        }
        for relative, versions in expected.items():
            contents = (ROOT / relative).read_text(encoding="utf-8")
            for version in versions:
                with self.subTest(path=relative, version=version):
                    if str(version) not in contents:
                        self.fail(f"{relative} does not document version {version}")

    def test_compatibility_matrix_matches_runtime_authority(self) -> None:
        native = json_document("Tools/release/native-compose.json")
        contents = (ROOT / "COMPATIBILITY.md").read_text(encoding="utf-8")
        expected_rows = {
            "`apple/container` stable": (
                native["appleContainerVersion"],
                native["appleContainerRevision"],
            ),
            (
                "`apple/containerization` for `container` "
                f"{native['appleContainerVersion']}"
            ): (
                native["appleContainerizationVersion"],
                native["appleContainerizationRevision"],
            ),
            "`container-compose` stable": (
                native["version"],
                native["commit"],
            ),
        }
        for label, values in expected_rows.items():
            with self.subTest(component=label):
                match = re.search(
                    rf"^\| {re.escape(label)} \|.*$",
                    contents,
                    re.MULTILINE,
                )
                if match is None:
                    self.fail(f"COMPATIBILITY.md has no matrix row for {label}")
                row = match.group(0)
                for value in values:
                    if str(value) not in row:
                        self.fail(
                            f"COMPATIBILITY.md row for {label} does not contain {value}"
                        )


if __name__ == "__main__":
    unittest.main()
