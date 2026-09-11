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
        self.assertIn(
            MODULE.ROOT / "Tools" / "release" / "devcontainer.rb.in",
            MODULE.source_files(),
        )
        self.assertIn(
            MODULE.ROOT / "Tools" / "ci" / "compose-cli-smoke-fixture.sh",
            MODULE.source_files(),
        )

    def test_forbidden_patterns_reject_runtime_acquisition_and_execution(self) -> None:
        rejected = (
            "docker version\n",
            "  docker-compose up\n",
            "colima start\n",
            "podman run alpine\n",
            "nerdctl ps\n",
            "command -v docker\n",
            "command -v podman\n",
            'shutil.which("docker-compose")\n',
            "brew install docker colima\n",
            'depends_on "docker"\n',
            'depends_on "podman"\n',
            "open /Applications/Docker.app\n",
            'let executable = URL(fileURLWithPath: "/usr/local/bin/docker")\n',
            'executable: "docker-compose"\n',
            'subprocess.run(["docker", "version"])\n',
            'subprocess.run(["nerdctl", "version"])\n',
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

    def test_only_parity_workflow_is_excluded(self) -> None:
        workflows = MODULE.ROOT / ".github" / "workflows"
        audited = set(MODULE.source_files())
        self.assertNotIn(workflows / "parity.yml", audited)
        self.assertTrue(
            {
                path
                for path in workflows.iterdir()
                if path.is_file() and path != workflows / "parity.yml"
            }.issubset(audited)
        )

    def test_product_socket_is_named_for_the_project_engine(self) -> None:
        for relative in (
            "Sources/DevContainerCore/DevContainerConfiguration.swift",
            "Sources/DevContainerComposeCLI/DevContainerComposeCommand.swift",
        ):
            contents = (MODULE.ROOT / relative).read_text(encoding="utf-8")
            self.assertIn('appendingPathComponent("engine.sock")', contents)
            self.assertNotIn('appendingPathComponent("docker.sock")', contents)

        resolver = (
            MODULE.ROOT
            / "Sources"
            / "DevContainerCore"
            / "DevContainerConfiguration.swift"
        ).read_text(encoding="utf-8")
        self.assertIn('message: "engine socket cannot select a Docker runtime socket"', resolver)
        self.assertIn('$0 != "docker.sock" && $0 != "docker.raw.sock"', resolver)
        self.assertIn("requireAppleContainer(path, name: name)", resolver)

        compose = (
            MODULE.ROOT
            / "Sources"
            / "DevContainerComposeCLI"
            / "DevContainerComposeCommand.swift"
        ).read_text(encoding="utf-8")
        self.assertNotIn(
            'environment["DEVCONTAINER_DOCKER_BIN"]\n                ??',
            compose,
        )
        self.assertIn('key != "DEVCONTAINER_DOCKER_BIN"', compose)

        reference = (
            MODULE.ROOT
            / "Sources"
            / "DevContainerCLI"
            / "ReferenceCLICommand.swift"
        ).read_text(encoding="utf-8")
        self.assertNotIn('environment["DEVCONTAINER_REFERENCE_CLI"]', reference)
        self.assertIn('key != "DEVCONTAINER_REFERENCE_CLI"', reference)
        self.assertIn('key != "DOCKER_CONTEXT"', reference)
        self.assertIn('key != "DOCKER_CONFIG"', reference)

    def test_router_rejects_host_docker_runtime_sockets(self) -> None:
        router = (
            MODULE.ROOT
            / "Sources"
            / "DevContainerDockerAPI"
            / "DockerRouterSupport.swift"
        ).read_text(encoding="utf-8")
        self.assertIn('name != "docker.sock"', router)
        self.assertIn('name != "docker.raw.sock"', router)
        self.assertIn("mounting a Docker runtime socket is disabled", router)

        transport = (
            MODULE.ROOT
            / "Sources"
            / "DevContainerDockerCLI"
            / "DockerHTTPClient.swift"
        ).read_text(encoding="utf-8")
        self.assertIn('$0 != "docker.sock" && $0 != "docker.raw.sock"', transport)
        self.assertIn("Docker runtime socket names are disabled", transport)


if __name__ == "__main__":
    unittest.main()
