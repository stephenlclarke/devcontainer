#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Unit tests for the real VS Code parity orchestrator."""

from __future__ import annotations

import gzip
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from parity_lib import ParityError
from run_vscode import (
    VSCodePins,
    code_command,
    decode_vsix_response,
    download_vsix,
    isolated_vscode_processes,
    parse_code_version,
    scrub_sensitive_evidence,
    validate_driver_result,
    verify_code_version,
    verify_signing_output,
    verify_vsix_metadata,
    vscode_environment,
    vscode_settings,
)


def pins(**overrides: str) -> VSCodePins:
    """Create concise immutable pins for focused tests."""

    values = {
        "version": "1.2.3",
        "commit": "a" * 40,
        "platform": "darwin-arm64",
        "archive_url": "https://example.invalid/code.zip",
        "archive_sha256": "1" * 64,
        "application_identifier": "com.microsoft.VSCode",
        "signing_team_identifier": "UBF8T346G9",
        "extension_version": "4.5.6",
        "extension_url": "https://example.invalid/extension.vsix",
        "extension_sha256": "",
        "embedded_cli_version": "7.8.9",
        "embedded_cli_commit": "b" * 40,
        "embedded_cli_sha256": "",
    }
    values.update(overrides)
    return VSCodePins(**values)


def vsix_bytes(cli: bytes = b"embedded-cli") -> bytes:
    """Build a minimal in-memory VSIX with the authenticated identity fields."""

    output = io.BytesIO()
    package = {
        "dependencies": {
            "@devcontainers/cli": f"https://github.com/devcontainers/cli.git#{'b' * 40}"
        },
        "name": "remote-containers",
        "publisher": "ms-vscode-remote",
        "version": "4.5.6",
    }
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("extension/package.json", json.dumps(package))
        archive.writestr(
            "extension/dist/spec-node/devContainersSpecCLI.js",
            cli,
        )
    return output.getvalue()


class Response:
    """Context-managed fake URL response."""

    def __init__(self, value: bytes) -> None:
        self.value = value

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, _: int) -> bytes:
        return self.value


class VSCodeParityTests(unittest.TestCase):
    def test_code_version_requires_all_three_identity_lines(self) -> None:
        self.assertEqual(
            parse_code_version(f"1.2.3\n{'a' * 40}\narm64\n"),
            ("1.2.3", "a" * 40, "arm64"),
        )
        with self.assertRaisesRegex(ParityError, "2 non-empty lines"):
            parse_code_version("1.2.3\narm64\n")

    def test_code_version_rejects_a_different_commit(self) -> None:
        output = f"1.2.3\n{'c' * 40}\narm64\n"
        reference_pins = pins()
        with self.assertRaisesRegex(ParityError, "identity differs"):
            verify_code_version(output, reference_pins)

    def test_signing_identity_rejects_a_different_team(self) -> None:
        output = "Identifier=com.microsoft.VSCode\nTeamIdentifier=DIFFERENT\n"
        reference_pins = pins()
        with self.assertRaisesRegex(ParityError, "signing identity differs"):
            verify_signing_output(output, reference_pins)

    def test_gzip_marketplace_response_is_expanded(self) -> None:
        value = vsix_bytes()
        self.assertEqual(decode_vsix_response(gzip.compress(value)), value)
        with self.assertRaisesRegex(ParityError, "not a VSIX"):
            decode_vsix_response(b"not-a-zip")

    def test_download_authenticates_expanded_vsix(self) -> None:
        value = vsix_bytes()
        digest = __import__("hashlib").sha256(value).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "extension.vsix"
            result = download_vsix(
                "https://example.invalid/extension.vsix",
                destination,
                digest,
                opener=lambda *_args, **_kwargs: Response(gzip.compress(value)),
            )
            self.assertEqual(result.read_bytes(), value)

    def test_vsix_metadata_binds_extension_and_embedded_cli(self) -> None:
        value = vsix_bytes()
        cli_digest = __import__("hashlib").sha256(b"embedded-cli").hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            extension = Path(temporary) / "extension.vsix"
            extension.write_bytes(value)
            identity = verify_vsix_metadata(
                extension,
                pins(embedded_cli_sha256=cli_digest),
            )
        self.assertEqual(identity["version"], "4.5.6")
        self.assertEqual(identity["embeddedCliCommit"], "b" * 40)

    def test_driver_result_requires_exact_true_observations(self) -> None:
        observations = {
            "attach": True,
            "extension_activation": True,
            "forward_port": True,
            "integrated_command": True,
            "open": True,
            "rebuild": True,
            "reopen": True,
            "vscode_server": True,
        }
        self.assertEqual(
            validate_driver_result(
                {
                    "observations": observations,
                    "status": "ready-for-cleanup",
                }
            )["rebuild"],
            "true",
        )
        observations["rebuild"] = False
        with self.assertRaisesRegex(ParityError, "failed observation"):
            validate_driver_result(
                {
                    "observations": observations,
                    "status": "ready-for-cleanup",
                }
            )

    def test_settings_disable_updates_and_bind_explicit_tools(self) -> None:
        settings = vscode_settings("/tool/docker", "/tool/compose")
        self.assertEqual(settings["dev.containers.dockerPath"], "/tool/docker")
        self.assertEqual(
            settings["dev.containers.dockerComposePath"],
            "/tool/compose",
        )
        self.assertEqual(settings["update.mode"], "none")
        self.assertFalse(settings["security.workspace.trust.enabled"])

    def test_launch_command_uses_isolated_profile_and_extension_root(self) -> None:
        command = code_command(
            "/tool/code",
            Path("/evidence/user"),
            Path("/evidence/extensions"),
            Path("/evidence/workspace"),
            Path("/source/driver"),
        )
        self.assertEqual(command[0], "/tool/code")
        self.assertIn("/evidence/user", command)
        self.assertIn("/evidence/extensions", command)
        self.assertIn("--force-disable-user-env", command)
        self.assertIn("--use-inmemory-secretstorage", command)
        self.assertIn("--extensionDevelopmentPath=/source/driver", command)
        self.assertEqual(command[-1], "/evidence/workspace")

    def test_process_cleanup_selects_only_the_unique_isolated_profile(self) -> None:
        output = "\n".join(
            [
                "  101 /Applications/Visual Studio Code.app/Code "
                "--user-data-dir /tmp/dc-vscode-docker-unique/data",
                "  102 Code Helper --user-data-dir=/tmp/dc-vscode-other/data",
                "invalid command /tmp/dc-vscode-docker-unique/data",
                "  101 duplicate /tmp/dc-vscode-docker-unique/data",
                "  103 shutdownMonitor "
                "/tmp/dc-vscode-docker-unique/data/logs/window1",
            ]
        )
        self.assertEqual(
            isolated_vscode_processes(
                output,
                Path("/tmp/dc-vscode-docker-unique/data"),
            ),
            [101, 103],
        )

    def test_gui_environment_is_fail_closed_and_uses_isolated_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = vscode_environment(
                {
                    "DEVCONTAINER_CONFIG": "/private/tmp/config.toml",
                    "DEVCONTAINER_SOCKET": "/private/tmp/devcontainer.sock",
                    "DEVCONTAINER_STATE": "/private/tmp/state.sqlite",
                    "DOCKER_HOST": "unix:///private/tmp/docker.sock",
                    "GITHUB_TOKEN": "must-not-leak",
                    "HOME": "/Users/operator",
                    "PATH": "/usr/bin:/bin",
                    "SONAR_TOKEN": "must-not-leak",
                },
                Path(temporary),
            )
            self.assertEqual(
                environment["DOCKER_HOST"],
                "unix:///private/tmp/docker.sock",
            )
            self.assertEqual(
                environment["DEVCONTAINER_CONFIG"],
                "/private/tmp/config.toml",
            )
            self.assertEqual(
                environment["DEVCONTAINER_SOCKET"],
                "/private/tmp/devcontainer.sock",
            )
            self.assertEqual(
                environment["DEVCONTAINER_STATE"],
                "/private/tmp/state.sqlite",
            )
            self.assertEqual(environment["HOME"], f"{temporary}/home")
            self.assertEqual(environment["LOGNAME"], "devcontainer-runner")
            self.assertEqual(environment["SHELL"], "/bin/zsh")
            self.assertEqual(environment["TMPDIR"], f"{temporary}/tmp")
            self.assertEqual(environment["USER"], "devcontainer-runner")
            self.assertNotIn("GITHUB_TOKEN", environment)
            self.assertNotIn("SONAR_TOKEN", environment)

    def test_sensitive_evidence_is_removed_without_retaining_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            safe = root / "safe.log"
            leaked = root / "leaked.json"
            safe.write_text("status=passed\n", encoding="utf-8")
            leaked.write_text(
                '{"GITHUB_TOKEN":"must-not-survive"}\n',
                encoding="utf-8",
            )

            names, removed = scrub_sensitive_evidence(root)

            self.assertEqual(names, ["GITHUB_TOKEN"])
            self.assertEqual(removed, ["leaked.json"])
            self.assertTrue(safe.is_file())
            self.assertFalse(leaked.exists())

            (root / "security-scan.json").write_text(
                '{"detectedNames":["GITHUB_TOKEN"]}\n',
                encoding="utf-8",
            )
            names, removed = scrub_sensitive_evidence(root)
            self.assertEqual((names, removed), ([], []))


if __name__ == "__main__":
    unittest.main()
