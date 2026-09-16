#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "Tools" / "parity" / "configure-runtime-environment.sh"


class ConfigureRuntimeEnvironmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.container = self.root / "prefix" / "bin" / "container"
        self.container.parent.mkdir(parents=True)
        self.container.write_bytes(b"runtime fixture\n")
        self.container.chmod(self.container.stat().st_mode | stat.S_IXUSR)
        self.runtime_binary = self.root / "prefix" / "libexec" / "bin" / "container"
        self.runtime_binary.parent.mkdir(parents=True)
        self.runtime_binary.write_bytes(b"real packaged runtime fixture\n")
        self.runtime_binary.chmod(
            self.runtime_binary.stat().st_mode | stat.S_IXUSR
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_script(
        self,
        lane: str,
        github_environment: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["DEVCONTAINER_CONTAINER_BIN"] = str(self.container)
        if github_environment is not None:
            environment["GITHUB_ENV"] = str(github_environment)
        else:
            environment.pop("GITHUB_ENV", None)
        return subprocess.run(
            [str(SCRIPT), lane],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_enhanced_lane_uses_short_revision_bound_isolation(self) -> None:
        result = self.run_script("container-compose")

        self.assertEqual(result.returncode, 0, result.stderr)
        digest = hashlib.sha256(self.runtime_binary.read_bytes()).hexdigest()[:12]
        self.assertEqual(
            result.stdout.splitlines(),
            [
                (
                    "export CONTAINER_APP_ROOT="
                    f"/tmp/dcparity-container-compose-{digest}"
                ),
                f"export CONTAINER_INSTALL_ROOT={self.container.parent.parent.resolve()}",
                (
                    "export CONTAINER_SERVICE_NAMESPACE="
                    "io.github.stephenlclarke.devcontainer.parity."
                    f"{digest}"
                ),
                (
                    "export XDG_CONFIG_HOME="
                    f"/tmp/dcparity-container-compose-{digest}/xdg"
                ),
            ],
        )

    def test_stock_lane_writes_the_default_namespace_to_github_env(self) -> None:
        github_environment = self.root / "github-env"

        result = self.run_script("apple-stock", github_environment)

        self.assertEqual(result.returncode, 0, result.stderr)
        values = dict(
            line.split("=", 1)
            for line in github_environment.read_text(encoding="utf-8").splitlines()
        )
        self.assertEqual(values["CONTAINER_SERVICE_NAMESPACE"], "com.apple.container")
        self.assertTrue(values["CONTAINER_APP_ROOT"].startswith("/tmp/dcparity-"))
        self.assertEqual(
            values["CONTAINER_INSTALL_ROOT"],
            str(self.container.parent.parent.resolve()),
        )
        self.assertEqual(
            values["XDG_CONFIG_HOME"],
            f"{values['CONTAINER_APP_ROOT']}/xdg",
        )

    def test_rejects_non_apple_lanes(self) -> None:
        result = self.run_script("docker")

        self.assertEqual(result.returncode, 1)
        self.assertIn("unsupported Apple runtime lane", result.stderr)


if __name__ == "__main__":
    unittest.main()
