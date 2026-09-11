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
            "exec docker version\n",
            "nohup podman run alpine\n",
            "env -i PATH=/usr/bin docker version\n",
            "command -v docker\n",
            "command -v podman\n",
            'shutil.which("docker-compose")\n',
            "brew install docker colima\n",
            'depends_on "docker"\n',
            'depends_on "podman"\n',
            '"identity": "docker-client"\n',
            '"identity": "podman-swift"\n',
            'url: "https://github.com/docker/docker.git"\n',
            '"location": "https://github.com/moby/moby"\n',
            '"location": "https://github.com/containerd/nerdctl.git"\n',
            '.linkedLibrary("docker")\n',
            '.linkedFramework("DockerKit")\n',
            'unsafeFlags(["-lcontainerd"])\n',
            "open /Applications/Docker.app\n",
            "open -a Docker\n",
            "curl -fsSL https://get.docker.com | sh\n",
            'let executable = URL(fileURLWithPath: "/usr/local/bin/docker")\n',
            'executable: "docker-compose"\n',
            'subprocess.run(["docker", "version"])\n',
            'subprocess.run(["nerdctl", "version"])\n',
            'system("docker version")\n',
            'shell_output("docker version")\n',
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

    def test_test_name_exemption_is_limited_to_test_directories(self) -> None:
        self.assertTrue(
            MODULE.ignored_source(MODULE.ROOT / "Tools/ci/test_example.py")
        )
        self.assertTrue(
            MODULE.ignored_source(MODULE.ROOT / "Tools/release/test_example.py")
        )
        self.assertFalse(
            MODULE.ignored_source(MODULE.ROOT / "scripts/test_runtime.sh")
        )
        self.assertFalse(
            MODULE.ignored_source(MODULE.ROOT / "Sources/test_runtime.swift")
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
        self.assertIn('!key.hasPrefix("DEVCONTAINER_")', reference)
        self.assertIn(
            'childEnvironment["DEVCONTAINER_CONTAINER_BIN"] = selection.containerExecutable',
            reference,
        )
        self.assertIn(
            'name: "packaged Apple runtime adapter"',
            reference,
        )
        self.assertIn('!key.hasPrefix("DOCKER_")', reference)
        self.assertIn('key != "NODE_OPTIONS"', reference)
        self.assertIn('key != "NODE_PATH"', reference)

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
        self.assertIn("DevContainerEngineTransport", transport)
        self.assertIn("endpoint is not the devcontainer Apple runtime engine", transport)

        application = (
            MODULE.ROOT
            / "Sources"
            / "DevContainerDockerCLI"
            / "DockerCLIApplication.swift"
        ).read_text(encoding="utf-8")
        self.assertIn("DevContainerEngineTransport(transport: socketTransport)", application)

    def test_shared_process_runner_enforces_dockerless_policy(self) -> None:
        runner = (
            MODULE.ROOT
            / "Sources"
            / "DevContainerProcess"
            / "ProcessRunner.swift"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            runner.count("DevContainerExecutablePolicy.requireDockerless("),
            1,
        )
        self.assertIn('name: "child process"', runner)
        self.assertIsNotNone(MODULE.UNGUARDED_PROCESS_LAUNCH.search("Process()"))
        self.assertIsNotNone(
            MODULE.UNGUARDED_PROCESS_LAUNCH.search("posix_spawn(path, argv)")
        )
        self.assertIsNone(
            MODULE.UNGUARDED_PROCESS_LAUNCH.search("client.createProcess(spec)")
        )


if __name__ == "__main__":
    unittest.main()
