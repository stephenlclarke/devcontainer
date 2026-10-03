"""Execute the real unsigned package layout, never a developer build-tree binary."""

import hashlib
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import re
import shutil
import socketserver
import struct
import tarfile
import tempfile
import threading
import unittest

from cli_process import run


PRODUCTS = {"devcontainer", "devcontainer-engine", "devcontainer-compose", "devcontainer-docker"}
PLUGIN = "libexec/container/plugins/devcontainer"
REFERENCE = "libexec/devcontainer/reference/"
REFERENCE_FILES = {"node", "NODE-LICENSE.txt", "runtime-lock.json", "cli/devcontainer.js",
                   "cli/dist/spec-node/devContainersSpecCLI.js", "cli/scripts/updateUID.Dockerfile",
                   "cli/package.json", "cli/LICENSE.txt", "cli/ThirdPartyNotices.txt"}


class ArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.archive = Path(os.environ["DEVCONTAINER_TEST_ARCHIVE"]).resolve(strict=True)
        cls.receipt = json.loads(Path(os.environ["DEVCONTAINER_TEST_RECEIPT"]).read_bytes())
        cls.preserve_package = False
        cls.base = Path(tempfile.mkdtemp(dir=os.environ["TEST_TMPDIR"]))
        cls.addClassCleanup(cls.cleanup_package)
        cls.root = cls.base / ("devcontainer-" + cls.receipt["version"])
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", cls.receipt["version"]):
            raise ValueError("Invalid archive version")
        expected = {"bin/" + name for name in PRODUCTS} | {
            "share/devcontainer/" + name for name in (
                "LICENSE", "NOTICE.md", "THIRD-PARTY-NOTICES.txt", "Package.resolved", "candidate.json",
                "com.github.stephenlclarke.devcontainer.plist.in",
            )
        } | {PLUGIN + "/config.toml", PLUGIN + "/bin/devcontainer"} | {REFERENCE + name for name in REFERENCE_FILES}
        directories = {cls.root.name}
        files = {cls.root.name + "/" + name for name in expected}
        for name in files:
            directories.update(str(parent) for parent in Path(name).parents if str(parent) != ".")
        seen, total = set(), 0
        with tarfile.open(cls.archive) as archive:
            for entry in archive:
                if entry.name in seen or entry.name not in files | directories:
                    raise ValueError("Duplicate or unexpected archive path")
                seen.add(entry.name)
                if entry.name in directories:
                    if not entry.isdir() or entry.mode != 0o755:
                        raise ValueError("Invalid archive directory")
                    (cls.base / entry.name).mkdir(parents=True, exist_ok=True)
                    continue
                if not entry.isfile() or entry.size <= 0 or entry.size > 256 * 1024**2:
                    raise ValueError("Invalid archive file")
                total += entry.size
                if total > 1024**3:
                    raise ValueError("Archive exceeds package size bound")
                mode = 0o755 if Path(entry.name).parent.name == "bin" or entry.name == cls.root.name + "/" + REFERENCE + "node" else 0o644
                if entry.mode != mode or (entry.uid, entry.gid) != (0, 0):
                    raise ValueError("Invalid archive file ownership or mode")
                output = cls.base / entry.name
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(entry) as source, output.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
                output.chmod(mode)
        if seen != files | directories:
            raise ValueError("Missing declared archive entries")
        # Authenticate every extracted executable before any smoke case can run.
        # A separate test retains this boundary as an explicit JUnit result.
        cls("test_layout_and_receipt_bind_all_binary_bytes").test_layout_and_receipt_bind_all_binary_bytes()

    @classmethod
    def cleanup_package(cls):
        if not cls.preserve_package:
            shutil.rmtree(cls.base)

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(dir=os.environ["TEST_TMPDIR"]))
        self.cleanup_verified = True
        self.addCleanup(self.cleanup_home)
        self.trap = self.home / "forbidden-runtime"
        self.marker = self.home / "runtime-invoked"
        self.trap.write_text('#!/bin/sh\n: > "$HOME/runtime-invoked"\nexit 97\n')
        self.trap.chmod(0o700)
        self.environment = {
            "PATH": "/usr/bin:/bin", "HOME": str(self.home), "TMPDIR": str(self.home), "LANG": "en_US.UTF-8",
            "XDG_CONFIG_HOME": str(self.home / "config"), "DEVCONTAINER_CONFIG": str(self.home / "config.toml"),
            "DEVCONTAINER_STATE": str(self.home / "state.sqlite"), "DEVCONTAINER_SOCKET": str(self.home / "engine.sock"),
            "DEVCONTAINER_COMPOSE_PROVIDER": "container-compose", "DEVCONTAINER_COMPOSE_BIN": str(self.trap),
            "DEVCONTAINER_DOCKER_BIN": str(self.trap), "DEVCONTAINER_DOCKER_COMPOSE_BIN": str(self.trap),
            "DEVCONTAINER_CONTAINER_BIN": str(self.trap),
        }

    def cleanup_home(self):
        if self.cleanup_verified:
            shutil.rmtree(self.home)

    def invoke(self, relative, *arguments, expected_status=0):
        with (self.home / "stdout").open("x+b") as stdout, (self.home / "stderr").open("x+b") as stderr:
            self.cleanup_verified = False
            try:
                status = run([str(self.root / relative), *arguments], cwd=self.home, env=self.environment,
                             stdout=stdout, stderr=stderr, timeout=15)
            except BaseException:
                type(self).preserve_package = True
                raise
            self.cleanup_verified = True
            values = []
            for stream in (stdout, stderr):
                stream.seek(0)
                data = stream.read(1024**2 + 1)
                self.assertLessEqual(len(data), 1024**2)
                values.append(data.decode())
        self.assertFalse(self.marker.exists(), "Package smoke invoked an external runtime")
        self.assertFalse((self.home / "state.sqlite").exists(), "Read-only smoke created runtime state")
        self.assertEqual(status, expected_status, "stdout: " + values[0] + "\nstderr: " + values[1])
        return values

    def test_layout_and_receipt_bind_all_binary_bytes(self):
        receipt = self.receipt
        self.assertEqual(hashlib.sha256(self.archive.read_bytes()).hexdigest(), receipt["archiveSHA256"])
        self.assertEqual(self.archive.stat().st_size, receipt["archiveSize"])
        shared = self.root / "share/devcontainer"
        identity = json.loads((shared / "candidate.json").read_bytes())
        self.assertEqual(identity, {key: value for key, value in receipt.items() if key not in {"archiveSHA256", "archiveSize"}})
        self.assertEqual(identity["kind"], "unsigned-native-candidate")
        self.assertEqual(identity["compilationMode"], "opt")
        self.assertFalse(identity["distributionReady"])
        self.assertEqual(identity["architecture"], "arm64")
        self.assertIn(identity["runtimeProfile"], {"stock", "enhanced"})
        self.assertRegex(identity["commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(set(identity["products"]), PRODUCTS)
        self.assertEqual(identity["schemaVersion"], 2)
        reference = identity["referenceRuntime"]
        self.assertEqual(set(reference["files"]), REFERENCE_FILES)
        for name, expected in reference["files"].items():
            self.assertEqual(hashlib.sha256((self.root / REFERENCE / name).read_bytes()).hexdigest(), expected)
        self.assertEqual(reference["lockSHA256"], reference["files"]["runtime-lock.json"])
        self.assertEqual(json.loads((self.root / REFERENCE / "cli/package.json").read_bytes())["version"], reference["cliVersion"])
        for name in PRODUCTS:
            data = (self.root / "bin" / name).read_bytes()
            self.assertEqual(data[:8], struct.pack("<II", 0xFEEDFACF, 0x0100000C))
            self.assertEqual(hashlib.sha256(data).hexdigest(), identity["products"][name])
        self.assertEqual((self.root / PLUGIN / "bin/devcontainer").read_bytes(), (self.root / "bin/devcontainer").read_bytes())
        self.assertEqual(hashlib.sha256((shared / "Package.resolved").read_bytes()).hexdigest(), identity["dependencyLockSHA256"])
        self.assertIn("abstract =", (self.root / PLUGIN / "config.toml").read_text())

    def test_packaged_cli_provenance_matches_archive(self):
        output, _ = self.invoke("bin/devcontainer", "version", "--format", "json")
        info = json.loads(output)
        self.assertEqual(info["version"], self.receipt["version"])
        self.assertEqual(info["commit"], self.receipt["commit"])
        self.assertEqual(info["architecture"], "arm64")
        self.assertEqual(info["lane"], "candidate")
        self.assertEqual(info["buildType"], "release")

    def test_notices_include_cli_and_engine_dependencies(self):
        notices = (self.root / "share/devcontainer/THIRD-PARTY-NOTICES.txt").read_text()
        for dependency in ("swift_argument_parser", "container", "containerization", "swift_nio"):
            with self.subTest(dependency=dependency):
                self.assertIn("external/+dependencies+swiftpkg_" + dependency + "/LICENSE", notices)
        self.assertIn("Apache License", notices)

    def test_plugin_layout_executes_same_version(self):
        output, _ = self.invoke(PLUGIN + "/bin/devcontainer", "--version")
        self.assertEqual(output.strip(), self.receipt["version"])

    def test_engine_help_does_not_start_service(self):
        output, _ = self.invoke("bin/devcontainer-engine", "--help")
        self.assertIn("USAGE: devcontainer-engine", output)
        self.assertIn("--provider-socket", output)

    def test_main_help_exposes_installed_command_tree(self):
        output, _ = self.invoke("bin/devcontainer", "--help")
        self.assertIn("USAGE: devcontainer", output)
        self.assertIn("configure", output)
        self.assertIn("diagnostics", output)

    def test_compose_bridge_uses_only_explicit_native_provider(self):
        provider = self.home / "compose-provider"
        provider.write_text('#!/bin/sh\n[ "$#" -eq 2 ] && [ "$1" = "version" ] && [ "$2" = "--short" ] || exit 98\nprintf "package-provider-fixture\\n"\n')
        provider.chmod(0o700)
        self.environment["DEVCONTAINER_COMPOSE_BIN"] = str(provider)
        output, _ = self.invoke("bin/devcontainer-compose", "version", "--short")
        self.assertEqual(output, "container-compose package-provider-fixture\n")

    def test_cli_invalid_format_fails_without_runtime(self):
        _, error = self.invoke("bin/devcontainer", "version", "--format", "invalid", expected_status=64)
        self.assertIn("unsupported format invalid", error)

    def test_private_node_has_exact_version(self):
        output, _ = self.invoke(REFERENCE + "node", "--version")
        self.assertEqual(output.strip(), "v" + self.receipt["referenceRuntime"]["nodeVersion"])

    def test_public_lifecycle_reads_configuration_with_real_private_cli(self):
        workspace = self.home / "workspace"
        configuration = workspace / ".devcontainer/devcontainer.json"
        configuration.parent.mkdir(parents=True)
        configuration.write_text(json.dumps({"image": "fixture.invalid/no-pull:1", "containerEnv": {"BUNDLE_PROBE": "packaged"}}))
        # Injected Node hooks would abort startup if the facade leaked them.
        self.environment["NODE_OPTIONS"] = "--require=/missing-must-not-be-loaded.js"
        # The official read-configuration command queries existing containers.
        # Only that boundary is faked; Node, CLI and our frontend are real.
        requests = []

        class InventoryHandler(BaseHTTPRequestHandler):
            def setup(self):
                self.request.settimeout(5)
                super().setup()

            def do_GET(self):
                requests.append(self.path)
                accepted = re.fullmatch(r"(?:/v[0-9.]+)?/containers/json\?.*", self.path)
                self.send_response(200 if accepted else 500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"[]")

            def log_message(self, *_):
                pass  # Requests are retained above; suppress generic server logging.

        socket_path = self.home / "engine.sock"
        with socketserver.UnixStreamServer(str(socket_path), InventoryHandler) as server:
            socket_path.chmod(0o600)
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
            thread.start()
            try:
                output, _ = self.invoke("bin/devcontainer", "read-configuration", "--workspace-folder", str(workspace),
                                       "--log-level", "debug")
            finally:
                server.shutdown()
                thread.join(timeout=6)
                self.assertFalse(thread.is_alive(), "Package inventory server did not stop")
        self.assertTrue(requests)
        self.assertTrue(all(re.fullmatch(r"(?:/v[0-9.]+)?/containers/json\?.*", path) for path in requests))
        result = json.loads(output)
        self.assertEqual(result["configuration"]["image"], "fixture.invalid/no-pull:1")
        self.assertEqual(result["configuration"]["containerEnv"], {"BUNDLE_PROBE": "packaged"})

    def test_plugin_lifecycle_help_uses_real_private_cli(self):
        output, _ = self.invoke(PLUGIN + "/bin/devcontainer", "up", "--help")
        self.assertIn("workspace-folder", output)
        self.assertIn("docker-path", output)
