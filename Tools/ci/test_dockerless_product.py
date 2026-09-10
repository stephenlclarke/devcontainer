"""Tests for the Docker-less product dependency audit."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check-dockerless-product.py")
SPEC = importlib.util.spec_from_file_location("dockerless_product", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DockerlessProductTests(unittest.TestCase):
    def test_repository_product_paths_are_dockerless(self) -> None:
        self.assertEqual(MODULE.violations(), [])

    def test_forbidden_patterns_reject_runtime_acquisition_and_execution(self) -> None:
        rejected = (
            "docker version\n",
            "  docker-compose up\n",
            "colima start\n",
            "command -v docker\n",
            'shutil.which("docker-compose")\n',
            "brew install docker colima\n",
            'depends_on "docker"\n',
            "open /Applications/Docker.app\n",
        )
        for contents in rejected:
            with self.subTest(contents=contents):
                self.assertTrue(
                    any(pattern.search(contents) for pattern in MODULE.FORBIDDEN)
                )

    def test_protocol_compatibility_names_remain_permitted(self) -> None:
        permitted = (
            "devcontainer-docker --version\n",
            "DockerHTTPRequest\n",
            'environment["DOCKER_HOST"]\n',
            '"dev.containers.dockerPath"\n',
            "Docker Engine reference oracle\n",
        )
        for contents in permitted:
            with self.subTest(contents=contents):
                self.assertFalse(
                    any(pattern.search(contents) for pattern in MODULE.FORBIDDEN)
                )


if __name__ == "__main__":
    unittest.main()
