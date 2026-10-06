# Copyright 2026 devcontainer project authors.
# SPDX-License-Identifier: Apache-2.0
"""Readiness-build and E04 cache-isolation regressions; no provider services."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from e04_build_readiness import command_arguments, readiness_fixture
from run_engine_fixture import Probe


class E04ReadinessTests(unittest.TestCase):
    def test_docker_readiness_imports_engine_probe_without_inherited_pythonpath(self):
        repository = Path(__file__).resolve().parents[2]
        parity = repository / "Tools/parity"
        script = (
            "import sys; from pathlib import Path; "
            f"sys.path.insert(0, {str(parity)!r}); "
            "from e04_build_readiness import engine_request; "
            f"request = engine_request(Path({str(repository)!r})); "
            "assert request.__module__ == 'engine_probe'"
        )
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        result = subprocess.run([sys.executable, "-I", "-c", script], cwd=repository,
                                env=environment, capture_output=True, text=True,
                                timeout=20, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_readiness_context_uses_unique_nonce_in_the_executed_run(self):
        first = readiness_fixture()
        second = readiness_fixture()
        try:
            self.assertNotEqual(first[2], second[2])
            self.assertNotEqual(first[3], second[3])
            dockerfile = (first[1] / "Dockerfile").read_text()
            self.assertIn("ARG PARITY_READINESS_NONCE", dockerfile)
            self.assertIn(f'RUN test "$PARITY_READINESS_NONCE" = {first[3]}', dockerfile)
            args = command_arguments(first[2], first[3], first[1])
            self.assertIn(f"PARITY_READINESS_NONCE={first[3]}", args)
        finally:
            first[0].cleanup()
            second[0].cleanup()

    def test_docker_readiness_requires_exact_absence_and_removal(self):
        from e04_build_readiness import docker_readiness

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "receipt.json"
            repository = Path(__file__).resolve().parents[2]
            image_id = "sha256:" + "a" * 64
            response = {"Id": image_id, "RepoTags": []}
            with patch("engine_probe.request", side_effect=[
                    (404, b""), (200, json.dumps({**response, "RepoTags": []}).encode()),
                    (200, json.dumps({**response, "RepoTags": []}).encode()), (404, b"")]) as request, \
                    patch("e04_build_readiness.command_arguments", return_value=["build", "tag"]) as arguments, \
                    patch("e04_build_readiness.readiness_fixture") as fixture, \
                    patch("e04_build_readiness.subprocess.run", side_effect=[
                        subprocess.CompletedProcess([], 0, b"built", b"progress"),
                        subprocess.CompletedProcess([], 0, b"removed", b"")]) as command:
                directory = tempfile.TemporaryDirectory()
                self.addCleanup(directory.cleanup)
                context = Path(directory.name)
                (context / "Dockerfile").write_text("FROM scratch\n")
                fixture.return_value = (directory, context, "unique:latest", "nonce")
                # Tag ownership is independently confirmed by RepoTags.
                request.side_effect = [
                    (404, b""),
                    (200, json.dumps({"Id": image_id, "RepoTags": ["unique:latest"]}).encode()),
                    (200, json.dumps({"Id": image_id, "RepoTags": ["unique:latest"]}).encode()),
                    (404, b""),
                ]
                receipt = docker_readiness("/docker", repository, {}, Path("/socket"), Path(output))
            self.assertEqual(receipt["status"], "passed")
            self.assertEqual(receipt["imageID"], image_id)
            self.assertEqual(request.call_count, 4)
            self.assertEqual(command.call_count, 2)

    def test_e04_positive_and_negative_runs_consume_per_invocation_nonce(self):
        probe = Probe.__new__(Probe)
        probe.name = "dcparity-e04-image-build-421"
        probe.images, probe.containers, probe.networks, probe.volumes = [], [], [], []
        observed = []

        def command(*arguments, check=True, **_kwargs):
            if arguments[0] == "build":
                dockerfile = Path(arguments[-1]) / "Dockerfile"
                observed.append((tuple(arguments), dockerfile.read_text()))
                return subprocess.CompletedProcess(arguments, 1 if "&& false" in dockerfile.read_text() else 0,
                                                    b"build", b"")
            return subprocess.CompletedProcess(arguments, 0, b"true\n", b"")

        with patch.object(probe, "command", side_effect=command):
            probe.image_build()
            probe.image_build()
        self.assertEqual(len(observed), 4)
        nonces = []
        for positive, negative in (observed[:2], observed[2:]):
            pair_nonces = []
            for arguments, dockerfile in (positive, negative):
                self.assertIn("ARG PARITY_CACHE_NONCE", dockerfile)
                nonce_arg = next(value for value in arguments
                                 if value.startswith("PARITY_CACHE_NONCE="))
                nonce = nonce_arg.split("=", 1)[1]
                self.assertIn(f'test "$PARITY_CACHE_NONCE" = {nonce}', dockerfile)
                pair_nonces.append(nonce)
            self.assertEqual(pair_nonces[0], pair_nonces[1])
            nonces.append(pair_nonces[0])
        self.assertNotEqual(nonces[0], nonces[1])
        self.assertIn("&& false", observed[1][1])

    def test_docker_inspection_error_does_not_start_or_adopt_a_readiness_build(self):
        from e04_build_readiness import docker_readiness

        with tempfile.TemporaryDirectory() as temporary:
            receipt = Path(temporary) / "receipt.json"
            repository = Path(__file__).resolve().parents[2]
            with patch("engine_probe.request", return_value=(500, b"unavailable")), \
                    patch("e04_build_readiness.subprocess.run") as command:
                with self.assertRaisesRegex(RuntimeError, "already exists"):
                    docker_readiness("/docker", repository, {}, Path("/socket"), receipt)
            command.assert_not_called()
            retained = json.loads(receipt.read_bytes())
            self.assertEqual(retained["status"], "failed")
            self.assertNotIn("imageAbsentAfterCleanup", retained)


if __name__ == "__main__":
    unittest.main()
