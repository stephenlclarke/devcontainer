#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors.
# Licensed under the Apache License, Version 2.0.

"""Security-focused tests for the live parity lane environment."""

from __future__ import annotations

import unittest

from run_lane import safe_environment


class SafeEnvironmentTests(unittest.TestCase):
    def test_environment_uses_an_explicit_non_secret_allowlist(self) -> None:
        environment = safe_environment(
            {
                "BASH_ENV": "/tmp/host-shell-hook",
                "DEVCONTAINER_DOCKER_ORACLE_HOST": "unix:///tmp/docker.sock",
                "DOCKER_CONTEXT": "fixture",
                "GITHUB_TOKEN": "must-not-leak",
                "HOME": "/Users/operator",
                "LD_PRELOAD": "/tmp/injected.dylib",
                "PATH": "/usr/bin:/bin",
                "SONAR_TOKEN": "must-not-leak",
            }
        )

        self.assertEqual(
            environment,
            {
                "DEVCONTAINER_DOCKER_ORACLE_HOST": "unix:///tmp/docker.sock",
                "DOCKER_CONTEXT": "fixture",
                "HOME": "/Users/operator",
                "PATH": "/usr/bin:/bin",
            },
        )


if __name__ == "__main__":
    unittest.main()
