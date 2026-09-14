"""Keep release-facing documentation aligned with authoritative pins."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[2]

DOCUMENTATION_FILES = (
    *sorted(ROOT.glob("*.md")),
    *sorted((ROOT / "docs").rglob("*.md")),
    *sorted((ROOT / "Sources").rglob("*.md")),
    *sorted((ROOT / "Tests" / "Parity").glob("*.md")),
)

MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\((?P<target>[^)]+)\)")


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
    def test_tracked_documentation_has_no_broken_local_links(self) -> None:
        failures: list[str] = []
        for document in DOCUMENTATION_FILES:
            contents = document.read_text(encoding="utf-8")
            for match in MARKDOWN_LINK.finditer(contents):
                target = match.group("target").strip().strip("<>")
                if (
                    not target
                    or target.startswith("#")
                    or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE)
                ):
                    continue
                relative = unquote(target.split("#", 1)[0])
                resolved = document.parent / relative
                if not resolved.exists():
                    line = contents.count("\n", 0, match.start()) + 1
                    failures.append(
                        f"{document.relative_to(ROOT)}:{line}: {relative}"
                    )
        self.assertEqual(failures, [], "broken local documentation links")

    def test_project_terminology_does_not_attribute_compose_to_apple(self) -> None:
        for document in DOCUMENTATION_FILES:
            with self.subTest(path=document.relative_to(ROOT)):
                contents = document.read_text(encoding="utf-8")
                self.assertNotRegex(contents, r"(?i)\bApple Compose\b")

    def test_primary_upstream_references_remain_documented(self) -> None:
        expected = {
            "README.md": (
                "https://github.com/devcontainers",
                "https://github.com/devcontainers/spec",
                "https://github.com/devcontainers/cli",
                "https://github.com/apple/container",
                "https://github.com/apple/containerization",
                "https://code.visualstudio.com/docs/devcontainers/containers",
            ),
            "DESIGN.md": (
                "https://github.com/devcontainers/spec",
                "https://github.com/devcontainers/cli",
                "https://github.com/apple/container",
                "https://github.com/apple/containerization",
            ),
            "CONFORMANCE.md": (
                "https://github.com/devcontainers/spec",
                "https://github.com/devcontainers/cli",
            ),
            "Sources/DevContainerCore/DevContainerCore.docc/Compatibility.md": (
                "https://github.com/devcontainers",
                "https://github.com/devcontainers/spec",
                "https://github.com/devcontainers/cli",
            ),
        }
        for relative, references in expected.items():
            contents = (ROOT / relative).read_text(encoding="utf-8")
            for reference in references:
                with self.subTest(path=relative, reference=reference):
                    self.assertIn(reference, contents)

    def test_readme_indexes_every_user_facing_guide(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        guides = (
            "BUILD.md",
            "COMPATIBILITY.md",
            "CONFORMANCE.md",
            "DESIGN.md",
            "INSTALL.md",
            "PARITY-ROADMAP.md",
            "PERFORMANCE.md",
            "QUALITY.md",
            "RELEASE.md",
            "SECURITY.md",
            "TESTING.md",
            "UNSUPPORTED-CAPABILITIES.md",
            "USER_GUIDE.md",
        )
        for guide in guides:
            with self.subTest(guide=guide):
                self.assertIn(f"]({guide})", readme)

    def test_docc_catalog_indexes_every_article(self) -> None:
        catalog = (
            ROOT
            / "Sources"
            / "DevContainerCore"
            / "DevContainerCore.docc"
            / "DevContainerCore.md"
        ).read_text(encoding="utf-8")
        for article in (
            "Architecture",
            "Compatibility",
            "Conformance",
            "Performance",
            "Testing",
            "UserGuide",
        ):
            with self.subTest(article=article):
                self.assertIn(f"<doc:{article}>", catalog)

    def test_build_guide_lists_every_packaged_command(self) -> None:
        build = (ROOT / "BUILD.md").read_text(encoding="utf-8")
        install = (ROOT / "INSTALL.md").read_text(encoding="utf-8")
        user_guide = (ROOT / "USER_GUIDE.md").read_text(encoding="utf-8")
        commands = (
            "devcontainer",
            "devcontainer-engine",
            "devcontainer-docker",
            "devcontainer-compose",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(f"| `{command}` |", build)
                self.assertIn(f"bin/{command}", install)
                self.assertIn(f"| `{command}` |", user_guide)

    def test_install_guide_lists_release_runtime_payloads(self) -> None:
        install = (ROOT / "INSTALL.md").read_text(encoding="utf-8")
        for payload in (
            "compose-volume-initializer-linux-arm64",
            "compose-volume-initializer-linux-amd64",
            "libexec/devcontainer-compose/resources/Package.resolved",
            "libexec/devcontainer-compose/resources/go-modules.txt",
            "libexec/devcontainer-compose/resources/container-compose.spdx.json",
            "libexec/devcontainer-compose/THIRD-PARTY-NOTICES.txt",
            "share/devcontainer/notarization.json",
            "share/devcontainer/reference-cli/devcontainer.js",
            "share/devcontainer/reference-cli/package.json",
        ):
            with self.subTest(payload=payload):
                self.assertIn(payload, install)

    def test_quality_and_release_guides_inventory_every_workflow(self) -> None:
        quality = (ROOT / "QUALITY.md").read_text(encoding="utf-8")
        release = (ROOT / "RELEASE.md").read_text(encoding="utf-8")
        workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
        for workflow in workflows:
            with self.subTest(workflow=workflow.name):
                self.assertIn(f"`{workflow.name}`", quality)
                self.assertIn(f"`{workflow.name}`", release)

    def test_release_build_info_example_matches_emitted_schema(self) -> None:
        release = (ROOT / "RELEASE.md").read_text(encoding="utf-8")
        match = re.search(
            r"The minimum payload is:\s*```json\s*(\{.*?\})\s*```",
            release,
            re.DOTALL,
        )
        if match is None:
            self.fail("RELEASE.md build-info example is missing")
        payload = json.loads(match.group(1))
        self.assertEqual(
            set(payload),
            {
                "architecture",
                "buildType",
                "commit",
                "containerDistribution",
                "lane",
                "provider",
                "source",
                "version",
            },
        )

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
