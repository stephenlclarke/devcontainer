#!/usr/bin/env python3
##===----------------------------------------------------------------------===##
## Copyright © 2026 devcontainer project authors.
##
## Licensed under the Apache License, Version 2.0 (the "License");
## you may not use this file except in compliance with the License.
## You may obtain a copy of the License at
##
##   https://www.apache.org/licenses/LICENSE-2.0
##
## Unless required by applicable law or agreed to in writing, software
## distributed under the License is distributed on an "AS IS" BASIS,
## WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
## See the License for the specific language governing permissions and
## limitations under the License.
##===----------------------------------------------------------------------===##

"""Offline boundary checks for configured devcontainer dependency layers."""

from __future__ import annotations

import io
import json
import os
import signal
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from urllib.parse import unquote, urlparse
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from artifacts import foundation, import_layers, layer_release


class NativeLayersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    def fixture(self) -> tuple[dict[str, bytes], dict]:
        name = "foundation/swiftpkg_swift_log/binary/Logging.swiftmodule"
        files = {name: b"compiled module"}
        manifest = {
            "schema": 1, "group": "foundation", "profile": "enhanced",
            "configuredRoot": "//:products",
            "selectedTargets": ["@swiftpkg_swift_log//:Logging"],
            "compiledOutputBEP": {"eventsSHA256": "e" * 64, "files": {
                "bazel-out/bin/external/+dependencies+swiftpkg_swift_log/Logging.swiftmodule":
                {"sha256": foundation.digest(b"compiled module"), "size": len(b"compiled module")}}},
            "packages": {"swift-log": {
                "repository": "swiftpkg_swift_log", "swiftTargets": ["Logging"],
                "cTargets": [],
            }},
            "files": {name: foundation.digest(content) for name, content in files.items()},
        }
        return files, manifest

    def test_archive_requires_exact_product_closure(self) -> None:
        files, manifest = self.fixture()
        archive = self.root / "layer.tar.gz"
        archive.write_bytes(foundation.archive_bytes(files, manifest))
        self.assertEqual(foundation.inspect(archive)["manifest"], manifest)
        changed_files = {next(iter(files)): b"substituted compiled module"}
        changed_manifest = {**manifest,
                            "files": {next(iter(files)): foundation.digest(next(iter(changed_files.values())))}}
        archive.write_bytes(foundation.archive_bytes(changed_files, changed_manifest))
        with self.assertRaisesRegex(ValueError, "successful compiled BEP"):
            foundation.inspect(archive)
        for changed in (
            {"configuredRoot": "//:devcontainer"},
            {"selectedTargets": []},
            {"selectedTargets": ["@swiftpkg_swift_log//:Other"]},
        ):
            invalid = {**manifest, **changed}
            archive.write_bytes(foundation.archive_bytes(files, invalid))
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "target set"):
                foundation.inspect(archive)

    def test_archive_rejects_raw_noncanonical_member_spelling(self) -> None:
        files, manifest = self.fixture()
        for spelling in ("foundation/./alias", "foundation//alias", "foundation/../alias"):
            archive = self.root / "malformed.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                for name, data in ((spelling, b"alias"),
                                   ("foundation/layer.json", json.dumps(manifest).encode())):
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
            with self.subTest(spelling=spelling), self.assertRaisesRegex(ValueError, "canonical"):
                foundation.inspect(archive)

    def test_compiled_bytes_must_match_successful_aspect_bep(self) -> None:
        output_base = self.root / "output"
        execution_root = output_base / "execroot/main"
        relative = "bazel-out/bin/external/+dependencies+swiftpkg_swift_log/Logging.swiftmodule"
        binary = execution_root / relative
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"compiled module")
        invocation = self.root / "invocation"
        invocation.mkdir()
        aspect = "//Tools/bazel/artifacts:compiled_outputs.bzl%foundation_outputs"
        target = {"label": "//:products", "configuration": {"id": "one"}}
        rows = [
            {"unstructuredCommandLine": {"args": ["build", "--aspects=" + aspect,
                                                  "--output_groups=layer_compiled", "//:products"]}},
            {"id": {"namedSet": {"id": "one"}}, "namedSetOfFiles": {"files": [{
                "pathPrefix": relative.split("/")[:-1], "name": binary.name,
                "uri": binary.as_uri(), "digest": foundation.digest(binary.read_bytes()),
                "length": str(binary.stat().st_size)}]}},
            {"id": {"targetCompleted": target}, "completed": {"success": True}},
            {"id": {"targetCompleted": {**target, "aspect": aspect}},
             "completed": {"success": True,
                           "outputGroup": [{"name": "layer_compiled", "fileSets": [{"id": "one"}]}]}},
        ]
        (invocation / "events.json").write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        proven = foundation.compiled_build_outputs(invocation, "foundation")
        allowed = {"swiftpkg_swift_log": "swift-log"}
        members, _ = foundation.artifact_map([relative], execution_root, output_base, allowed, proven)
        self.assertEqual(members["swiftpkg_swift_log"]["binary/Logging.swiftmodule"], b"compiled module")
        binary.write_bytes(b"stale different bytes")
        with self.assertRaisesRegex(ValueError, "differs from successful aspect"):
            foundation.artifact_map([relative], execution_root, output_base, allowed, proven)
        rows[-1]["completed"]["outputGroup"][0]["name"] = "default"
        (invocation / "events.json").write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        with self.assertRaisesRegex(ValueError, "output group"):
            foundation.compiled_build_outputs(invocation, "foundation")

    def test_timed_out_producer_reaps_only_its_owned_descendants(self) -> None:
        root = self.root / "fake-root"
        script = root / "Tools/bazel/run.sh"
        script.parent.mkdir(parents=True)
        script.write_text("#!/usr/bin/env python3\n"
                          "import os, signal, time\n"
                          "pid = os.fork()\n"
                          "if pid == 0:\n"
                          "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                          "    time.sleep(30)\n"
                          "else:\n"
                          "    print(os.getsid(0), pid, flush=True)\n"
                          "    time.sleep(30)\n")
        script.chmod(0o755)
        logs = self.root / "logs"
        logs.mkdir()
        try:
            environment = os.environ.copy()
            with self.assertRaises(subprocess.TimeoutExpired):
                foundation.runner_command(root, "build", [], "//:products",
                                          environment=environment, logs=logs,
                                          name="fake-producer", timeout=0.5)
            session, child = map(int, (logs / "fake-producer.stdout.log").read_text().split())
            self.assertFalse(foundation.session_members(session))
            with self.assertRaises(ProcessLookupError):
                os.getsid(child)
        finally:
            if (logs / "fake-producer.stdout.log").exists():
                values = (logs / "fake-producer.stdout.log").read_text().split()
                if len(values) == 2:
                    session = int(values[0])
                    for member in foundation.session_members(session):
                        os.kill(member, signal.SIGKILL)

    def test_signaled_producer_reaps_its_owned_launcher_session(self) -> None:
        root = self.root / "fake-root"
        script = root / "Tools/bazel/run.sh"
        script.parent.mkdir(parents=True)
        script.write_text("#!/usr/bin/env python3\n"
                          "import os, signal, time\n"
                          "pid = os.fork()\n"
                          "if pid == 0:\n"
                          "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                          "    time.sleep(30)\n"
                          "else:\n"
                          "    print(os.getsid(0), pid, flush=True)\n"
                          "    time.sleep(30)\n")
        script.chmod(0o755)
        logs = self.root / "logs"
        logs.mkdir()
        controller = self.root / "controller.py"
        controller.write_text("import os, sys\nfrom pathlib import Path\n"
                              "from artifacts import foundation\n"
                              "try:\n"
                              "    foundation.runner_command(Path(sys.argv[1]), 'build', [], '//:products', "
                              "environment=os.environ.copy(), logs=Path(sys.argv[2]), name='owned', timeout=15)\n"
                              "except BaseException:\n    sys.exit(2)\n")
        environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        process = subprocess.Popen([sys.executable, str(controller), str(root), str(logs)],
                                   env=environment, start_new_session=True, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        session = None
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                path = logs / "owned.stdout.log"
                if path.exists() and len(path.read_text().split()) == 2:
                    session = int(path.read_text().split()[0])
                    break
                time.sleep(0.02)
            self.assertIsNotNone(session, "fake launcher did not start")
            os.kill(process.pid, signal.SIGTERM)
            self.assertEqual(process.wait(timeout=8), 2)
            self.assertFalse(foundation.session_members(session))
        finally:
            if session is not None:
                for member in foundation.session_members(session):
                    os.kill(member, signal.SIGKILL)
            if process.poll() is None:
                os.kill(process.pid, signal.SIGKILL)
                process.wait(timeout=5)

    def test_metadata_symlink_cannot_escape_or_hide_directory(self) -> None:
        external = self.root / "package"
        external.mkdir()
        (external / "BUILD.bazel").write_text("\n")
        (external / "Examples").mkdir()
        (external / "Examples/unused-link").symlink_to(self.root, target_is_directory=True)
        self.assertEqual(foundation.package_files(external, ""), {})
        (external / "Plugins").mkdir()
        (external / "Plugins/unused-link").symlink_to(self.root, target_is_directory=True)
        self.assertEqual(foundation.package_files(external, ""), {})
        with self.assertRaisesRegex(ValueError, "symlink"):
            foundation.package_files(external, 'filegroup(name = "needed", srcs = ["Plugins/unused-link"])')
        with self.assertRaisesRegex(ValueError, "symlink"):
            foundation.package_files(external, 'filegroup(name = "all", srcs = glob (\n["**"]))')
        (external / "Sources").mkdir()
        (external / "Sources/unused.swift").symlink_to(self.root / "outside.swift")
        self.assertEqual(foundation.package_files(external, ""), {})
        (external / "linked").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            foundation.package_files(external, "")
        (external / "linked").unlink()
        (external / "real").mkdir()
        (external / "linked").symlink_to(external / "real", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinked directory"):
            foundation.package_files(external, "")

    def test_loaded_canonical_repository_must_contain_sealed_import_bytes(self) -> None:
        output_base = self.root / "output"
        repository = output_base / "external/+dependencies+swiftpkg_swift_log"
        repository.mkdir(parents=True)
        (repository / "BUILD.bazel").write_bytes(b"sealed BUILD")
        (repository / "prebuilt.bzl").write_bytes(b"sealed macro")
        manifest = {"packages": {"swift-log": {"repository": "swiftpkg_swift_log"}},
                    "files": {"foundation/swiftpkg_swift_log/BUILD.bazel":
                              foundation.digest(b"sealed BUILD"),
                              "foundation/swiftpkg_swift_log/prebuilt.bzl":
                              foundation.digest(b"sealed macro")}}
        chosen = {"foundation": {"manifest": manifest}}
        import_layers.verify_origin(output_base, chosen)
        (repository / "BUILD.bazel").write_bytes(b"source fallback")
        with self.assertRaisesRegex(ValueError, "different compiled bytes"):
            import_layers.verify_origin(output_base, chosen)
        (repository / "BUILD.bazel").unlink()
        (repository / "BUILD.bazel").symlink_to(self.root / "foreign")
        with self.assertRaisesRegex(ValueError, "different compiled bytes"):
            import_layers.verify_origin(output_base, chosen)

    def test_importer_emits_encoded_file_url_for_retained_archive(self) -> None:
        archive = self.root / "Application Support/#percent%.tar.gz"
        archive.parent.mkdir()
        archive.write_bytes(b"sealed")
        output = io.StringIO()
        with (mock.patch.object(import_layers, "admitted",
                                return_value={"foundation": {"archive": str(archive)}}),
              mock.patch.object(sys, "argv", ["import_layers.py", "prepare", "--profile", "stock",
                                               "--group", "foundation"]),
              redirect_stdout(output)):
            import_layers.main()
        group, path, uri = output.getvalue().strip().split("\t")
        self.assertEqual((group, path), ("foundation", str(archive)))
        self.assertEqual((urlparse(uri).scheme, unquote(urlparse(uri).path)), ("file", str(archive)))
        self.assertIn("Application%20Support/%23percent%25.tar.gz", uri)

    def test_imported_binary_directory_cannot_alias_outside_repository(self) -> None:
        output_base = self.root / "output"
        repository = output_base / "external/+dependencies+swiftpkg_swift_log"
        repository.mkdir(parents=True)
        (repository / "BUILD.bazel").write_bytes(b"sealed BUILD")
        (repository / "prebuilt.bzl").write_bytes(b"sealed macro")
        foreign = self.root / "foreign"
        foreign.mkdir()
        (foreign / "Logging.swiftmodule").write_bytes(b"sealed module")
        (repository / "binary").symlink_to(foreign, target_is_directory=True)
        files = {"BUILD.bazel": b"sealed BUILD", "prebuilt.bzl": b"sealed macro",
                 "binary/Logging.swiftmodule": b"sealed module"}
        chosen = {"foundation": {"manifest": {
            "packages": {"swift-log": {"repository": "swiftpkg_swift_log"}},
            "files": {"foundation/swiftpkg_swift_log/" + name: foundation.digest(value)
                      for name, value in files.items()}}}}
        with self.assertRaisesRegex(ValueError, "noncanonical compiled path"):
            import_layers.verify_origin(output_base, chosen)

    def test_group_ownership_excludes_argument_parser_and_upper_packages(self) -> None:
        pins = {name: name for name in (
            "swift-argument-parser", "swift-log", "swift-nio", "container",
            "containerization", "container-engine-api", "swift-docc-plugin",
        )}
        self.assertEqual(foundation.group_pins(pins, "foundation"),
                         {"swift-log": "swift-log", "swift-nio": "swift-nio"})
        for group, package in (("containerization", "containerization"),
                               ("engine-api", "container-engine-api"),
                               ("container-sdk", "container")):
            self.assertEqual(foundation.group_pins(pins, group), {package: package})

    def test_source_mode_admission_requires_group_tests_and_exact_lower(self) -> None:
        invocation = self.root / "test-run"
        invocation.mkdir()
        source = {"commit": "a" * 40, "dirty": False}
        for name, value in (("inputs-before.json", source), ("inputs-after.json", source),
                            ("outcome.json", {"bazel_exit_code": 0, "validation_exit_code": 0})):
            (invocation / name).write_text(json.dumps(value))
        flags = ["--config=enhanced", "--config=release", "--config=prebuilt-foundation"]
        labels = sorted(layer_release.TEST_LABELS["engine-api"])
        def events(current_flags: list[str], current_labels: list[str]) -> None:
            rows = [
                {"unstructuredCommandLine": {"args": ["test", *current_flags, *current_labels]}},
                {"optionsParsed": {"cmdLine": ["--compilation_mode=opt"]}},
                {"finished": {"overallSuccess": True, "exitCode": {"name": "SUCCESS"}}},
            ] + [{"id": {"testSummary": {"label": label}},
                  "testSummary": {"overallStatus": "PASSED"}} for label in current_labels]
            (invocation / "events.json").write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        events(flags, labels)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import check_evidence
        import retained_test_xml
        with mock.patch.object(check_evidence, "validate",
                               return_value={"test_cases": {label: 64 for label in labels}}) as validator, \
                mock.patch.object(retained_test_xml, "read", return_value={}):
            admitted = layer_release.admitted_tests(invocation, source, "engine-api", "enhanced")
        self.assertEqual(admitted["passedLabels"], labels)
        self.assertEqual(set(validator.call_args.kwargs["expected"]), set(labels))
        self.assertGreater(min(validator.call_args.kwargs["expected"].values()), 0)
        events(flags, labels[:1])
        with self.assertRaisesRegex(ValueError, "not all passed"):
            layer_release.admitted_tests(invocation, source, "engine-api", "enhanced")
        events(flags + ["--config=prebuilt-engine-api"], labels)
        with self.assertRaisesRegex(ValueError, "not all passed"):
            layer_release.admitted_tests(invocation, source, "engine-api", "enhanced")
        events(flags + ["--config=prebuilt-container-sdk"], labels)
        with self.assertRaisesRegex(ValueError, "not all passed"):
            layer_release.admitted_tests(invocation, source, "engine-api", "enhanced")
        events(flags + ["--test_filter=OnlyOne"], labels)
        with self.assertRaisesRegex(ValueError, "cannot narrow"):
            layer_release.admitted_tests(invocation, source, "engine-api", "enhanced")
        events(flags, labels)
        parsed = [json.loads(row) for row in (invocation / "events.json").read_text().splitlines()]
        parsed[1]["optionsParsed"]["cmdLine"].append("--runs_per_test=0")
        (invocation / "events.json").write_text("\n".join(json.dumps(row) for row in parsed) + "\n")
        with self.assertRaisesRegex(ValueError, "cannot narrow"):
            layer_release.admitted_tests(invocation, source, "engine-api", "enhanced")

    def test_source_mode_admission_reads_real_xml_case_inventory(self) -> None:
        import check_evidence
        import retained_test_xml
        invocation = self.root / "xml-run"
        invocation.mkdir()
        source = {"commit": "a" * 40, "dirty": False}
        for name, value in (("inputs-before.json", source), ("inputs-after.json", source),
                            ("outcome.json", {"bazel_exit_code": 0, "validation_exit_code": 0})):
            (invocation / name).write_text(json.dumps(value))
        policy = check_evidence.load_policy(
            Path(__file__).resolve().parents[3] / "Tools/bazel/evidence-policy.json", "enhanced")
        labels = sorted(layer_release.TEST_LABELS["engine-api"])
        rows = [
            {"unstructuredCommandLine": {"args": ["test", "--config=release", "--config=enhanced",
                                                  "--config=prebuilt-foundation", *labels]}},
            {"optionsParsed": {"cmdLine": ["--compilation_mode=opt", "--flaky_test_attempts=1"]}},
            {"finished": {"overallSuccess": True, "exitCode": {"name": "SUCCESS", "code": 0}}},
        ]
        for index, label in enumerate(labels):
            xml = invocation / f"cases-{index}.xml"
            xml.write_text("<testsuite>" + '<testcase name="passed"/>' * policy["tests"][label] +
                           "</testsuite>")
            rows += [{"id": {"testSummary": {"label": label}},
                      "testSummary": {"overallStatus": "PASSED", "totalRunCount": 1}},
                     {"id": {"testResult": {"label": label}},
                      "testResult": {"testActionOutput": [{"name": "test.xml", "uri":
                                                         f"file:///Volumes/SSD/cf/bazel/fixtures/cases-{index}.xml"}]}}]
        (invocation / "events.json").write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        def fixture_xml(_invocation: Path, rows: list[dict], _database: Path) -> dict[str, bytes]:
            return {json.dumps(row["id"]["testResult"], sort_keys=True):
                    (invocation / f"cases-{labels.index(row['id']['testResult']['label'])}.xml").read_bytes()
                    for row in rows if "testResult" in row}
        with mock.patch.object(retained_test_xml, "read", side_effect=fixture_xml):
            accepted = layer_release.admitted_tests(invocation, source, "engine-api", "enhanced")
            self.assertEqual(accepted["caseCounts"], {label: policy["tests"][label] for label in labels})
            rows.pop()
            (invocation / "events.json").write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            with self.assertRaisesRegex(ValueError, "Missing test XML"):
                layer_release.admitted_tests(invocation, source, "engine-api", "enhanced")
            rows.append({"id": {"testResult": {"label": labels[-1]}},
                         "testResult": {"testActionOutput": [{"name": "test.xml", "uri":
                                                             "file:///Volumes/SSD/cf/bazel/fixtures/cases-1.xml"}]}})
            rows[1]["optionsParsed"]["cmdLine"][-1] = "--flaky_test_attempts=2"
            (invocation / "events.json").write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            with self.assertRaisesRegex(ValueError, "cannot narrow"):
                layer_release.admitted_tests(invocation, source, "engine-api", "enhanced")

    def test_release_target_is_owned_source_for_upper_groups(self) -> None:
        source = "a" * 40
        for group, package in (("containerization", "containerization"),
                               ("engine-api", "container-engine-api"),
                               ("container-sdk", "container")):
            manifest = {"group": group, "packages": {package: {"sourceCommit": "b" * 40}}}
            self.assertEqual(layer_release.release_target(manifest, source), "b" * 40)
        self.assertEqual(layer_release.release_target({"group": "foundation"}, source), source)

    def test_sdk_source_graph_is_bound_to_its_own_selected_profile(self) -> None:
        manifest = self.root / "Package.swift"
        manifest.write_text("source manifest")
        enhanced = {name: "a" * 40 for name in
                    ("container", "containerization", "container-engine-api", "swift-argument-parser")}
        stock = {name: "b" * 40 for name in enhanced}
        for filename, pins in (("Package.resolved", enhanced), ("Package.stock.resolved", stock)):
            (self.root / filename).write_text(json.dumps({"pins": [
                {"identity": name, "location": "https://example.invalid/" + name,
                 "state": {"revision": revision}}
                for name, revision in pins.items()]}))
        def sealed(profile: str, pins: dict[str, str]) -> dict:
            receipt = {"schema": 1, "profile": profile,
                       "source": {name: pins[name] for name in
                                  ("container", "containerization", "container-engine-api")},
                       "argumentParserSource": pins["swift-argument-parser"],
                       "devcontainerManifestSHA256": foundation.file_digest(manifest),
                       "devcontainerLockSHA256": foundation.file_digest(self.root / "Package.resolved"),
                       "stockLockSHA256": foundation.file_digest(self.root / "Package.stock.resolved"),
                       "loadedContainerManifestSHA256": "c" * 64,
                       "loadedContainerLockSHA256": "d" * 64}
            return {"receipt": receipt,
                    "receiptSHA256": foundation.digest((json.dumps(receipt, sort_keys=True) + "\n").encode())}
        graph = sealed("stock", stock)
        foundation.verify_source_graph(self.root, "stock", graph)
        with self.assertRaisesRegex(ValueError, "selected profile"):
            foundation.verify_source_graph(self.root, "enhanced", graph)
        graph["receipt"]["loadedContainerLockSHA256"] = "not-a-sha"
        with self.assertRaisesRegex(ValueError, "loaded source"):
            foundation.verify_source_graph(self.root, "stock", graph)

    def test_launcher_requires_release_and_rejects_compiler_drift(self) -> None:
        script = Path(__file__).resolve().parents[1] / "run.sh"
        def shell(command: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(["/bin/bash", "-c", f'source "$1"; {command}', "_", str(script)],
                                  text=True, capture_output=True)
        selected = shell("prebuilt_argument_parser --config=release --config=prebuilt-container-sdk")
        self.assertEqual((selected.returncode, selected.stdout.strip()), (0, "1"))
        groups = shell("prebuilt_groups --config=prebuilt-container-sdk")
        self.assertEqual(groups.stdout.splitlines(), list(import_layers.ORDER))
        for target, expected in (("//:products", "1"), ("//:candidate_archive", "1"),
                                 ("//:devcontainer-engine", "1"), ("//:devcontainer", "0"),
                                 ("//:devcontainer-compose", "0"), ("//:DevContainerModelTests", "0")):
            with self.subTest(target=target):
                self.assertEqual(shell("requires_loaded_container_source " + target).stdout.strip(), expected)
        for flags in ("--config=prebuilt-foundation",
                      "--config=release --config=prebuilt-foundation --@build_bazel_rules_swift//swift:copt=-Onone",
                      "--config=release --config=prebuilt-engine-api --host_copt=-O0"):
            with self.subTest(flags=flags):
                self.assertNotEqual(shell("prebuilt_argument_parser " + flags).returncode, 0)


if __name__ == "__main__":
    unittest.main()
