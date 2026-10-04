"""Release preparation never installs, compiles, overwrites or trusts SSD residue."""

import io
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import prepare_releases as preparation
from release_inputs import sha256


class PrepareReleasesTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name).resolve()
        self.prepared = self.root / "prepared"
        self.receipts = self.root / "receipts"
        self.prepared.mkdir()
        self.receipts.mkdir()
        self.source = self.root / "download"
        self.asset = {"repository": "stephenlclarke/container-compose", "tag": "0.15.1",
                      "name": "container-compose-plugin-release-arm64.tar.gz"}

    def archive(self, extra=(), *, executable=True):
        with tarfile.open(self.source, "w:gz") as archive:
            directory = tarfile.TarInfo("./")
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
            for name in ["compose", "compose/bin", "compose/resources"]:
                directory = tarfile.TarInfo(name)
                directory.type = tarfile.DIRTYPE
                archive.addfile(directory)
            for name in ["compose/bin/compose", "compose/resources/compose-normalizer"]:
                entry = tarfile.TarInfo(name)
                entry.size = 7
                entry.mode = 0o755 if executable else 0o644
                archive.addfile(entry, io.BytesIO(b"fixture"))
            for entry in extra:
                archive.addfile(entry, io.BytesIO(b"x" * entry.size))
        self.asset.update(size=self.source.stat().st_size, sha256=sha256(self.source))

    def prepare(self, **kwargs):
        return preparation.prepare(self.asset, self.source, self.prepared, self.receipts, **kwargs)

    def test_all_five_published_layouts_are_explicit(self):
        for repository, name, count in [
            ("stephenlclarke/devcontainer", "devcontainer-release-arm64.tar.gz", 3),
            ("stephenlclarke/container-compose", "container-compose-plugin-release-arm64.tar.gz", 2),
            ("stephenlclarke/container-compose", "container-release-arm64.tar.gz", 3),
            ("apple/container", "container-1.4.1-installer-signed.pkg", 2),
            ("docker/compose", "docker-compose-darwin-aarch64", 1),
        ]:
            value = preparation.layout({"repository": repository, "name": name, "tag": "1.4.1"})
            self.assertEqual(len(value["executables"]), count)
        with self.assertRaisesRegex(ValueError, "reviewed layout"):
            preparation.layout({"repository": "unknown/repo", "name": "tool", "tag": "1"})

    def test_signed_compose_and_published_q_runtime_layouts_are_distinct(self):
        compose = preparation.layout({"repository": "stephenlclarke/container-compose",
                                      "tag": "0.16.0", "name": "container-compose-signed-arm64.zip"})
        provenance = preparation.layout({"repository": "stephenlclarke/container-compose",
                                         "tag": "0.16.0", "name": "qualified-compose-release.json"})
        runtime = preparation.layout({"repository": "stephenlclarke/container",
                                      "tag": "layer-runtime-a1effeeaf8c7", "name": "container-homebrew-arm64.tar.gz"})
        self.assertEqual(compose["format"], "zip")
        self.assertEqual(compose["executables"]["compose"], "compose/bin/compose")
        self.assertEqual(provenance["format"], "json")
        self.assertFalse(provenance["executables"])
        self.assertEqual(runtime["executables"]["container"], "bin/container")

    def compose_release_fixture(self):
        compose_commit, runtime_commit = "a" * 40, "b" * 40
        runtime_source = self.root / "runtime.tar.gz"
        guest_source = self.root / "guest.oci.tar"
        builder_source = self.root / "builder.oci.tar"
        guest_source.write_bytes(b"authenticated q guest OCI fixture")
        builder_source.write_bytes(b"authenticated q builder OCI fixture")
        guest_asset = {"repository": "stephenlclarke/containerization", "tag": "guest-fixture",
                       "commit": "6db16197bbad8196a78132f86529daa89125aafb", "name": "guest.oci.tar",
                       "size": guest_source.stat().st_size, "sha256": sha256(guest_source)}
        builder_asset = {"repository": "stephenlclarke/container-builder-shim", "tag": "builder-fixture",
                         "commit": "016040197215684db474181b444767eb58797cfa", "name": "builder.oci.tar",
                         "size": builder_source.stat().st_size, "sha256": sha256(builder_source)}
        guest_reference = "ghcr.io/stephenlclarke/containerization/vminit:6db16197bbad8196a78132f86529daa89125aafb"
        builder_reference = "ghcr.io/stephenlclarke/container-builder-shim/builder:qualification-016040197215684db474181b444767eb58797cfa"
        runtime_payload = {"bin/container": b"container", "bin/container-engine": b"engine",
                           "bin/container-apiserver": b"api"}
        for index in range(22):
            runtime_payload[f"libexec/container/payload-{index:02d}"] = f"payload-{index}".encode()
        with tarfile.open(runtime_source, "w:gz") as archive:
            for name, payload in runtime_payload.items():
                entry = tarfile.TarInfo(name)
                entry.mode = 0o755
                entry.size = len(payload)
                archive.addfile(entry, io.BytesIO(payload))
        files = {
            "compose/bin/compose": b"signed compose executable",
            "compose/resources/compose-normalizer": b"signed normalizer",
            "compose/resources/volume-initializer/compose-volume-initializer-linux-amd64": b"amd64 init",
            "compose/resources/volume-initializer/compose-volume-initializer-linux-arm64": b"arm64 init",
            "compose/resources/build-info.json": json.dumps({
                "source": "stephenlclarke/container-compose", "commit": compose_commit,
                "version": "0.16.0", "containerSource": "stephenlclarke/container",
                "containerRef": runtime_commit,
            }, sort_keys=True).encode(),
        }
        with zipfile.ZipFile(self.source, "w") as archive:
            for directory in ("compose/", "compose/bin/", "compose/resources/",
                              "compose/resources/volume-initializer/"):
                entry = zipfile.ZipInfo(directory)
                entry.external_attr = (stat.S_IFDIR | 0o755) << 16
                archive.writestr(entry, b"")
            for name, content in files.items():
                entry = zipfile.ZipInfo(name)
                mode = 0o755 if name in {
                    "compose/bin/compose", "compose/resources/compose-normalizer",
                    "compose/resources/volume-initializer/compose-volume-initializer-linux-amd64",
                    "compose/resources/volume-initializer/compose-volume-initializer-linux-arm64",
                } else 0o644
                entry.external_attr = (stat.S_IFREG | mode) << 16
                archive.writestr(entry, content)
            # AppleDouble metadata is authenticated by the ZIP bytes but is not part of signedTree.
            archive.writestr("compose/._bin", b"metadata")
        compose_asset = {"repository": "stephenlclarke/container-compose", "tag": "0.16.0",
                         "commit": compose_commit, "name": "container-compose-signed-arm64.zip",
                         "size": self.source.stat().st_size, "sha256": sha256(self.source)}
        runtime_asset = {"repository": "stephenlclarke/container", "tag": "layer-runtime-" + runtime_commit[:12],
                         "commit": runtime_commit, "name": "container-homebrew-arm64.tar.gz",
                         "size": runtime_source.stat().st_size, "sha256": sha256(runtime_source)}
        runtime_provenance = {
            "schema": 1, "kind": "container-qualified-runtime-assets",
            "qualified_container_source": runtime_commit,
            "qualification": {"target": "bazel-qualify", "passed": True},
            "assets": {
                "runtime": {"name": runtime_asset["name"], "sha256": runtime_asset["sha256"],
                            "source": runtime_commit},
                "guest": {"name": "guest.oci.tar", "sha256": guest_asset["sha256"],
                          "source": guest_asset["commit"], "reference": guest_reference},
                "builder": {"name": "builder.oci.tar", "sha256": builder_asset["sha256"],
                            "source": builder_asset["commit"], "reference": builder_reference},
            },
            "guest": {"source": guest_asset["commit"], "reference": guest_reference},
            "builder": {"source": builder_asset["commit"], "reference": builder_reference},
            "runtime": {"payload": {path: hashlib.sha256(content).hexdigest()
                                      for path, content in runtime_payload.items()},
                        "init_archive_sha256": guest_asset["sha256"],
                        "builder_archive_sha256": builder_asset["sha256"],
                        "init_image": guest_reference, "builder_image": builder_reference,
                        "workload_image": "docker.io/library/alpine@sha256:" + "e" * 64,
                        "notary": {"status": "Accepted", "id": "notary-fixture"}},
            "native_compiled_chain": {"schema": 1, "source": runtime_commit},
        }
        runtime_provenance["native_compiled_chain_sha256"] = hashlib.sha256(
            (json.dumps(runtime_provenance["native_compiled_chain"], sort_keys=True, indent=2) + "\n")
            .encode()).hexdigest()
        runtime_provenance_path = self.root / "qualified-container-assets.json"
        runtime_provenance_path.write_text(json.dumps(runtime_provenance, sort_keys=True), encoding="utf-8")
        runtime_provenance_asset = {"repository": runtime_asset["repository"], "tag": runtime_asset["tag"],
                                    "commit": runtime_asset["commit"],
                                    "name": "qualified-container-assets.json",
                                    "size": runtime_provenance_path.stat().st_size,
                                    "sha256": sha256(runtime_provenance_path)}
        provenance = {
            "source": compose_commit, "signedArchiveSHA256": compose_asset["sha256"],
            "signedAndNotarized": True, "signedDistributionReady": False,
            "notary": {"status": "Accepted"}, "qualifiedContainer": runtime_commit,
            "signedTree": {path.removeprefix("compose/"): hashlib.sha256(content).hexdigest()
                           for path, content in files.items()},
            "compiledSdkChain": {"schema": 1, "profile": "enhanced",
                                 "selected_config": "prebuilt-container-sdk", "source": compose_commit,
                                 "locks": {"container-sdk": {"repository": "stephenlclarke/container",
                                                               "target_commit": runtime_commit}}},
            "lowerReleasedAssets": {
                "runtime": {"sha256": runtime_asset["sha256"],
                            "release": {"repository": runtime_asset["repository"],
                                        "tag": runtime_asset["tag"],
                                        "target_commit": runtime_commit}},
                "guest": {"sha256": guest_asset["sha256"],
                          "release": {"repository": guest_asset["repository"],
                                      "tag": guest_asset["tag"],
                                      "target_commit": guest_asset["commit"]}},
                "builder": {"sha256": builder_asset["sha256"],
                            "release": {"repository": builder_asset["repository"],
                                        "tag": builder_asset["tag"],
                                        "target_commit": builder_asset["commit"]}},
                "provenance": {"sha256": runtime_provenance_asset["sha256"],
                               "release": {"repository": runtime_asset["repository"],
                                           "tag": runtime_asset["tag"],
                                           "target_commit": runtime_commit}},
            },
        }
        provenance_path = self.root / "qualified-compose-release.json"
        provenance_path.write_text(json.dumps(provenance, sort_keys=True), encoding="utf-8")
        provenance_asset = {"repository": compose_asset["repository"], "tag": compose_asset["tag"],
                            "commit": compose_asset["commit"], "name": "qualified-compose-release.json",
                            "size": provenance_path.stat().st_size, "sha256": sha256(provenance_path)}
        prepared = preparation.prepare(compose_asset, self.source, self.prepared, self.receipts)
        runtime_prepared = preparation.prepare(runtime_asset, runtime_source, self.prepared, self.receipts)
        guest_prepared = preparation.prepare(guest_asset, guest_source, self.prepared, self.receipts)
        builder_prepared = preparation.prepare(builder_asset, builder_source, self.prepared, self.receipts)
        return (compose_asset, provenance_asset, runtime_asset, runtime_provenance_asset,
                provenance_path, runtime_provenance_path, provenance, runtime_provenance,
                Path(prepared["root"]), Path(runtime_prepared["root"]), runtime_source,
                guest_asset, builder_asset, Path(guest_prepared["root"]),
                Path(builder_prepared["root"]), guest_source, builder_source)

    @staticmethod
    def locked_asset(asset, *, release_id, asset_id, tag_object):
        return {**asset, "releaseID": release_id, "assetID": asset_id,
                "tagObject": tag_object, "publisher": "github-actions[bot]",
                "prerelease": False, "architecture": "arm64"}

    def test_signed_compose_provenance_binds_distinct_full_source_commits(self):
        (compose_asset, provenance_asset, runtime_asset, runtime_provenance_asset,
         provenance_path, runtime_provenance_path, provenance, runtime_provenance,
         compose_root, runtime_root, _, guest_asset, builder_asset, _, _,
         guest_source, builder_source) = self.compose_release_fixture()
        selection = {"composeArchive": compose_asset, "composeProvenance": provenance_asset,
                    "containerRuntime": runtime_asset,
                    "containerRuntimeProvenance": runtime_provenance_asset,
                    "guestImage": guest_asset, "builderImage": builder_asset}
        identity = preparation.validate_compose_runtime_association(
            selection, provenance, runtime_provenance, runtime_root)
        self.assertEqual(identity["containerCompose"]["commit"], "a" * 40)
        self.assertEqual(identity["containerRuntime"]["commit"], "b" * 40)
        self.assertEqual(identity["containerRuntime"]["archiveSHA256"], runtime_asset["sha256"])
        self.assertTrue(identity["signedAndNotarized"])
        self.assertFalse(identity["distributionReady"])

        for field, value in (("qualifiedContainer", "d" * 40),
                             ("signedArchiveSHA256", "e" * 64)):
            changed = dict(provenance)
            changed[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Compose provenance"):
                preparation.validate_compose_runtime_association(
                    selection, changed, runtime_provenance, runtime_root)
        q_changed = json.loads(json.dumps(runtime_provenance))
        q_changed["assets"]["runtime"]["sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "does not bind the selected runtime archive"):
            preparation.validate_q_runtime_provenance(
                q_changed, runtime_asset, guest_asset, builder_asset, runtime_root)
        q_changed = json.loads(json.dumps(runtime_provenance))
        q_changed["runtime"]["payload"]["bin/container"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "binary inventory differs"):
            preparation.validate_q_runtime_provenance(
                q_changed, runtime_asset, guest_asset, builder_asset, runtime_root)

    def stock_gateway_fixture(self):
        source = "8129f2b78af2305d0298d17f7880b21d7539c61d"
        pins = {
            "container": {"repository": "apple/container",
                          "commit": "9a8917ca2da5cd6ba059b9ba5ca5a74892e9bb7d"},
            "containerization": {"repository": "apple/containerization",
                                 "commit": "9eacc197d7c3663eb29cbab6d51244ede6d1cd7d"},
            "engine-api": {"repository": "stephenlclarke/container-engine-api",
                           "commit": "c04ed07b8a324a996b9d62397278b90a389fe830"},
        }
        lock_repositories = {
            "argument-parser": "stephenlclarke/container",
            "foundation": "stephenlclarke/container-compose",
            "containerization": "stephenlclarke/containerization",
            "engine-api": "stephenlclarke/container-engine-api",
            "container-sdk": "stephenlclarke/container",
        }
        lock_targets = {
            "argument-parser": "1" * 40,
            "foundation": "2" * 40,
            "containerization": pins["containerization"]["commit"],
            "engine-api": pins["engine-api"]["commit"],
            "container-sdk": pins["container"]["commit"],
        }
        locks = {}
        for name, repository in lock_repositories.items():
            suffix = "argument-parser" if name == "argument-parser" else name + "-stock"
            locks[name] = {
                "lock_sha256": ("a" if name == "argument-parser" else "b") * 64,
                "archive_sha256": ("c" if name == "argument-parser" else "d") * 64,
                "repository": repository,
                "tag": f"layer-{suffix}-fixture",
                "target_commit": lock_targets[name],
            }
        chain = {
            "schema": 1, "source": source, "profile": "stock",
            "selected_config": "prebuilt-container-sdk",
            "package_invocation": "a1ebdf2a-88b1-4ea9-b83f-dab3b07ad7e1",
            "locks": locks,
        }
        compose_root = self.root / f"stock-compose-{len(list(self.root.glob('stock-compose-*')))}"
        plugin_root = compose_root / "compose"
        resources = plugin_root / "resources"
        binaries = plugin_root / "bin"
        resources.mkdir(parents=True)
        binaries.mkdir()
        candidate = {
            "kind": "unsigned-native-candidate", "runtimeProfile": "stock",
            "commit": source, "architecture": "arm64", "compilationMode": "opt",
            "distributionReady": False,
            "dependencyLockSHA256": "8" * 64,
        }
        build_info = {
            "source": "stephenlclarke/container-compose", "commit": source,
            "version": "0.15.1", "containerSource": pins["container"]["repository"],
            "containerRef": pins["container"]["commit"],
            "containerizationSource": pins["containerization"]["repository"],
            "containerizationRef": pins["containerization"]["commit"],
        }
        (resources / "candidate.json").write_text(json.dumps(candidate, sort_keys=True))
        (resources / "build-info.json").write_text(json.dumps(build_info, sort_keys=True))
        (binaries / "compose").write_bytes(b"stock compose binary")
        (resources / "compose-normalizer").write_bytes(b"stock normalizer")
        signed_tree = {
            path.relative_to(plugin_root).as_posix(): sha256(path)
            for path in plugin_root.rglob("*") if path.is_file()
        }
        archive_sha = "9" * 64
        archive = {
            "repository": "stephenlclarke/container-compose", "tag": "stock-fixture",
            "commit": source, "name": "container-compose-signed-arm64.zip",
            "sha256": archive_sha,
        }
        provenance_asset = {
            "repository": archive["repository"], "tag": archive["tag"],
            "commit": archive["commit"], "name": "qualified-compose-release.json",
            "sha256": "7" * 64,
        }
        runtime_asset = {
            "repository": "stephenlclarke/container", "tag": "runtime-fixture",
            "commit": "f" * 40, "name": "container-homebrew-arm64.tar.gz",
            "sha256": "e" * 64,
        }
        provenance = {
            "kind": "signed-compose-stock-gateway-provenance",
            "scope": "signed-stock-gateway-compiled-layer",
            "runtimeProfile": "stock", "gatewayBackend": "engine",
            "runtimeQualification": "pending", "architecture": "arm64",
            "source": source, "signedArchiveSHA256": archive_sha,
            "signedAndNotarized": True, "signedDistributionReady": False,
            "notary": {"status": "Accepted", "id": "notary-fixture"},
            "signedPayload": {
                "bin/compose": signed_tree["bin/compose"],
                "resources/compose-normalizer": signed_tree["resources/compose-normalizer"],
            },
            "signedTree": signed_tree,
            "dependencyLockSHA256": candidate["dependencyLockSHA256"],
            "compiledSdkChain": chain,
            "compiledSourcePins": pins,
        }
        selection = {
            "composeArchive": archive, "composeProvenance": provenance_asset,
            "containerRuntime": runtime_asset,
        }
        return selection, provenance, compose_root, candidate, signed_tree

    def test_stock_gateway_admission_binds_profile_binary_and_compiled_closure(self):
        selection, provenance, compose_root, _, _ = self.stock_gateway_fixture()
        identity = preparation.validate_compose_runtime_association(
            selection, provenance, {}, self.root, compose_root)
        self.assertEqual(identity["runtimeProfile"], "stock")
        self.assertEqual(identity["gatewayBackend"], "engine")
        self.assertEqual(identity["runtimeQualification"], "pending")
        self.assertEqual(identity["containerCompose"]["commit"], "8129f2b78af2305d0298d17f7880b21d7539c61d")
        self.assertEqual(identity["containerRuntime"]["commit"], "f" * 40)
        self.assertNotIn("qRuntimeProvenance", identity)

    def test_stock_gateway_admission_rejects_profile_scope_and_identity_drift(self):
        mutations = (
            ("runtimeProfile", "enhanced"),
            ("gatewayBackend", "container"),
            ("runtimeQualification", "qualified"),
            ("scope", "fully-qualified-runtime"),
            ("source", "0" * 40),
            ("signedAndNotarized", False),
            ("signedArchiveSHA256", "0" * 64),
            ("dependencyLockSHA256", "0" * 64),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                selection, provenance, compose_root, _, _ = self.stock_gateway_fixture()
                changed = json.loads(json.dumps(provenance))
                changed[field] = value
                with self.assertRaises(ValueError):
                    preparation.validate_compose_runtime_association(
                        selection, changed, {}, self.root, compose_root)

    def test_stock_gateway_admission_rejects_enhanced_candidate_masquerading_as_stock(self):
        selection, provenance, compose_root, candidate, signed_tree = self.stock_gateway_fixture()
        candidate["runtimeProfile"] = "enhanced"
        path = compose_root / "compose/resources/candidate.json"
        path.write_text(json.dumps(candidate, sort_keys=True))
        provenance["signedTree"]["resources/candidate.json"] = sha256(path)
        with self.assertRaisesRegex(ValueError, "candidate or build metadata"):
            preparation.validate_compose_runtime_association(
                selection, provenance, {}, self.root, compose_root)

    def test_stock_gateway_admission_rejects_malformed_dependency_chain(self):
        changes = (
            lambda value: value["compiledSdkChain"].update(profile="enhanced"),
            lambda value: value["compiledSdkChain"]["locks"].pop("engine-api"),
            lambda value: value["compiledSdkChain"]["locks"]["engine-api"].update(
                target_commit="0" * 40),
            lambda value: value["compiledSourcePins"]["container"].update(
                repository="stephenlclarke/container"),
        )
        for mutate in changes:
            with self.subTest(mutate=mutate):
                selection, provenance, compose_root, _, _ = self.stock_gateway_fixture()
                changed = json.loads(json.dumps(provenance))
                mutate(changed)
                with self.assertRaises(ValueError):
                    preparation.validate_compose_runtime_association(
                        selection, changed, {}, self.root, compose_root)

    def test_stock_gateway_admission_rejects_bad_notary_and_signed_payload_or_tree(self):
        mutations = (
            lambda value: value["notary"].update(status="Invalid"),
            lambda value: value["signedPayload"].update({"bin/compose": "0" * 64}),
            lambda value: value["signedTree"].update({"resources/build-info.json": "0" * 64}),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                selection, provenance, compose_root, _, _ = self.stock_gateway_fixture()
                changed = json.loads(json.dumps(provenance))
                mutate(changed)
                with self.assertRaises(ValueError):
                    preparation.validate_compose_runtime_association(
                        selection, changed, {}, self.root, compose_root)

    def test_stock_gateway_keeps_q_runtime_validation_independent(self):
        (compose_asset, provenance_asset, runtime_asset, runtime_provenance_asset,
         _, runtime_provenance_path, _, runtime_provenance, _, runtime_root, _,
         guest_asset, builder_asset, _, _, _, _) = self.compose_release_fixture()
        selection, compose_provenance, compose_root, _, _ = self.stock_gateway_fixture()
        selection.update({
            "containerRuntime": runtime_asset,
            "containerRuntimeProvenance": runtime_provenance_asset,
            "guestImage": guest_asset, "builderImage": builder_asset,
        })
        compose_provenance_path = self.root / "stock-qualified-compose-release.json"
        compose_provenance_path.write_text(json.dumps(compose_provenance, sort_keys=True))
        prepared = {
            "composeArchive": {"root": str(compose_root)},
            "composeProvenance": {"files": {"provenance": str(compose_provenance_path)}},
            "containerRuntime": {"root": str(runtime_root)},
            "containerRuntimeProvenance": {
                "files": {"provenance": str(runtime_provenance_path)}},
            "guestImage": {}, "builderImage": {},
        }
        objects = self.root / "objects"
        objects.mkdir()
        with patch("prepare_releases.verify_object"):
            identity = preparation.admit_compose_runtime_inputs(selection, objects, prepared)
        self.assertEqual(identity["runtimeProfile"], "stock")
        self.assertEqual(identity["runtimeQualification"], "pending")
        self.assertEqual(identity["containerRuntime"]["commit"], runtime_asset["commit"])
        self.assertEqual(identity["qRuntimeProvenance"]["commit"], runtime_asset["commit"])
        self.assertNotIn("qRuntimeProvenance", compose_provenance)

        changed_q = json.loads(json.dumps(runtime_provenance))
        changed_q["assets"]["runtime"]["sha256"] = "0" * 64
        changed_q_path = self.root / "bad-q-runtime-provenance.json"
        changed_q_path.write_text(json.dumps(changed_q, sort_keys=True))
        prepared["containerRuntimeProvenance"]["files"]["provenance"] = str(changed_q_path)
        with patch("prepare_releases.verify_object"), self.assertRaisesRegex(
                ValueError, "does not bind the selected runtime archive"):
            preparation.admit_compose_runtime_inputs(selection, objects, prepared)

    def test_locked_adapter_requires_retained_zip_provenance_and_q_tar(self):
        (compose_asset, provenance_asset, runtime_asset, runtime_provenance_asset,
         provenance_path, runtime_provenance_path, _, _, _, _, runtime_source,
         guest_asset, builder_asset, guest_root, builder_root, guest_source,
         builder_source) = self.compose_release_fixture()
        tag_object = "d" * 40
        compose_asset = self.locked_asset(compose_asset, release_id=1, asset_id=2, tag_object=tag_object)
        provenance_asset = self.locked_asset(provenance_asset, release_id=1, asset_id=3, tag_object=tag_object)
        runtime_asset = self.locked_asset(runtime_asset, release_id=4, asset_id=5,
                                          tag_object="e" * 40)
        runtime_provenance_asset = self.locked_asset(runtime_provenance_asset, release_id=4, asset_id=6,
                                                     tag_object="e" * 40)
        guest_asset = self.locked_asset(guest_asset, release_id=7, asset_id=8,
                                        tag_object="f" * 40)
        builder_asset = self.locked_asset(builder_asset, release_id=9, asset_id=10,
                                          tag_object="1" * 40)
        objects = self.root / "release-objects"
        objects.mkdir(mode=0o700)
        for asset, source in ((compose_asset, self.source), (provenance_asset, provenance_path),
                              (runtime_asset, runtime_source),
                              (runtime_provenance_asset, runtime_provenance_path),
                              (guest_asset, guest_source), (builder_asset, builder_source)):
            shutil.copyfile(source, objects / asset["sha256"])
            preparation.prepare(asset, objects / asset["sha256"], self.prepared, self.receipts)
        lock = {"schemaVersion": 1, "assets": [compose_asset, provenance_asset, runtime_asset,
                                                runtime_provenance_asset, guest_asset, builder_asset]}
        without_q_provenance = {"schemaVersion": 1,
                                "assets": [compose_asset, provenance_asset, runtime_asset,
                                           guest_asset, builder_asset]}
        with self.assertRaisesRegex(ValueError, "requires locked Q runtime, provenance"):
            preparation.select_compose_runtime_assets(without_q_provenance)
        admitted = preparation.admit_locked_compose_runtime(
            lock, objects, self.prepared, self.receipts)
        self.assertEqual(admitted["format"], "signed-compose-q-runtime")
        self.assertEqual(admitted["containerRuntime"]["commit"], "b" * 40)
        self.assertEqual(admitted["containerCompose"]["commit"], "a" * 40)
        self.assertNotEqual(admitted["containerRuntime"]["commit"],
                            admitted["containerCompose"]["commit"])
        self.assertEqual(Path(admitted["executables"]["container"]).read_bytes(), b"container")
        self.assertEqual(Path(admitted["executables"]["compose"]).read_bytes(), b"signed compose executable")
        self.assertEqual(Path(admitted["guestArchive"]).read_bytes(), guest_source.read_bytes())
        self.assertEqual(Path(admitted["builderArchive"]).read_bytes(), builder_source.read_bytes())

        selection = preparation.select_compose_runtime_assets(lock)
        compose_provenance = json.loads(provenance_path.read_text())
        runtime_provenance = json.loads(runtime_provenance_path.read_text())
        compose_provenance["compiledSdkChain"]["source"] = runtime_asset["commit"]
        with self.assertRaisesRegex(ValueError, "not associated with the selected Q runtime"):
            preparation.validate_compose_runtime_association(
                selection, compose_provenance, runtime_provenance,
                Path(admitted["executables"]["container"]).parents[1])

        guest_images = {"schemaVersion": 1, "images": [{
            "name": "enhanced-vminit", "repository": "ghcr.io/stephenlclarke/containerization/vminit",
            "reference": "ghcr.io/stephenlclarke/containerization/vminit:" + guest_asset["commit"],
            "manifest": "sha256:29dd09551b3eb18a16df7a32c4e35b559551be6cb8328762bbf7f96f1122bc12",
            "config": "sha256:2d715cb803032c9031733dc1654bf5c8593e0c6107f41d39babe633ae05a7c95",
            "archiveSHA256": guest_asset["sha256"],
        }]}
        builder_images = {"schemaVersion": 1, "images": [{
            "name": "enhanced-builder", "repository": "ghcr.io/stephenlclarke/container-builder-shim/builder",
            "reference": "ghcr.io/stephenlclarke/container-builder-shim/builder:qualification-"
                        + builder_asset["commit"],
            "manifest": "sha256:34cddb8928699ea8269887c925c7d5b9fe785c250656a50cbc15fb8c689a1417",
            "config": "sha256:01cc0b8de2d7c72a2babeeb2bb66fc937e39bcd78cfa828f82e01caf21c7b1f9",
            "archiveSHA256": builder_asset["sha256"],
        }]}
        image_retained = self.root / "guest-images"
        image_retained.mkdir(mode=0o700)
        with patch("prepare_guest_images.prepare", side_effect=lambda image, scratch, retained,
                   **kwargs: {"image": image, "path": str(kwargs["source_archive"]),
                              "sha256": image["archiveSHA256"]}) as importer:
            imported = preparation.prepare_q_guest_builder_images(
                admitted, guest_images, builder_images, self.root, image_retained)
        self.assertEqual(set(imported), {"guest", "builder"})
        self.assertEqual(importer.call_count, 2)
        self.assertEqual(imported["guest"]["sha256"], guest_asset["sha256"])
        self.assertEqual(imported["builder"]["sha256"], builder_asset["sha256"])
        self.assertTrue(all(call.kwargs.get("source_archive") for call in importer.call_args_list))
        self.assertTrue(all(call.kwargs.get("offline") is None for call in importer.call_args_list))
        wrong_builder_lock = json.loads(json.dumps(builder_images))
        wrong_builder_lock["images"][0]["archiveSHA256"] = "f" * 64
        with patch("prepare_guest_images.prepare", side_effect=AssertionError("must reject before import")):
            with self.assertRaisesRegex(ValueError, "builder image lock differs"):
                preparation.prepare_q_guest_builder_images(
                    admitted, guest_images, wrong_builder_lock, self.root, image_retained)

    def test_prepare_releases_dispatches_complete_q_pair_and_rejects_lock_drift_before_import(self):
        source = "6db16197bbad8196a78132f86529daa89125aafb"
        builder_source = "016040197215684db474181b444767eb58797cfa"
        guest = {"name": "enhanced-vminit", "repository": "ghcr.io/stephenlclarke/containerization/vminit",
                 "reference": "ghcr.io/stephenlclarke/containerization/vminit:" + source,
                 "manifest": "sha256:" + "1" * 64, "config": "sha256:" + "2" * 64,
                 "archiveSHA256": "a" * 64}
        builder = {"name": "enhanced-builder",
                   "repository": "ghcr.io/stephenlclarke/container-builder-shim/builder",
                   "reference": "ghcr.io/stephenlclarke/container-builder-shim/builder:qualification-"
                               + builder_source,
                   "manifest": "sha256:" + "3" * 64, "config": "sha256:" + "4" * 64,
                   "archiveSHA256": "b" * 64}
        identity = {"format": "signed-compose-q-runtime", "qOciInputs": {
            "guest": {"reference": guest["reference"], "archiveSHA256": guest["archiveSHA256"],
                      "source": source, "path": "/retained/guest.oci.tar"},
            "builder": {"reference": builder["reference"], "archiveSHA256": builder["archiveSHA256"],
                        "source": builder_source, "path": "/retained/builder.oci.tar"},
        }}
        release = self.locked_asset({"repository": "stephenlclarke/container-compose", "tag": "compose-release",
                                     "name": "container-compose-signed-arm64.zip", "commit": "a" * 40,
                                     "size": 1, "sha256": "c" * 64, "publisher": "github-actions[bot]",
                                     "prerelease": False, "architecture": "arm64"},
                                    release_id=1, asset_id=1, tag_object="d" * 40)
        lock = {"schemaVersion": 1, "assets": [release]}
        bazel = self.root / "bazel-fixture"
        bazel.mkdir(mode=0o700)
        (bazel / "guest-images.lock.json").write_text(json.dumps({"schemaVersion": 1, "images": [guest]}))
        (bazel / "builder-images.lock.json").write_text(json.dumps({"schemaVersion": 1, "images": [builder]}))
        retained, scratch = self.root / "retained", self.root / "ssd"
        retained.mkdir(mode=0o700)
        scratch.mkdir(mode=0o700)
        with (patch.object(preparation, "__file__", str(bazel / "prepare_releases.py")),
              patch.object(preparation, "select_compose_runtime_assets",
                           return_value={"format": "signed-compose-q-runtime"}),
              patch.object(preparation, "admit_locked_compose_runtime", return_value=identity),
              patch("prepare_guest_images.prepare",
                    side_effect=lambda image, _scratch, _retained, **kwargs:
                    {"image": image, "path": str(kwargs["source_archive"]),
                     "sha256": image["archiveSHA256"]}) as importer):
            result = preparation.prepare_locked_q_guest_builder_images(
                lock, retained, scratch, self.prepared, self.receipts)
        self.assertEqual(set(result), {"guest", "builder"})
        self.assertEqual(importer.call_count, 2)
        self.assertTrue(all("source_archive" in call.kwargs and "offline" not in call.kwargs
                            for call in importer.call_args_list))
        with (patch.object(preparation, "__file__", str(bazel / "prepare_releases.py")),
              patch.object(preparation, "select_compose_runtime_assets",
                           return_value={"format": "signed-compose-q-runtime"}),
              patch.object(preparation, "admit_locked_compose_runtime", return_value=identity),
              patch("prepare_guest_images.prepare", side_effect=AssertionError("import started"))):
            changed = dict(builder, archiveSHA256="f" * 64)
            (bazel / "builder-images.lock.json").write_text(
                json.dumps({"schemaVersion": 1, "images": [changed]}))
            with self.assertRaisesRegex(ValueError, "builder image lock differs"):
                preparation.prepare_locked_q_guest_builder_images(
                    lock, retained, scratch, self.prepared, self.receipts)
        unrelated = {"schemaVersion": 1, "assets": [self.locked_asset({
            "repository": "apple/container", "tag": "1.4.1", "name": "container.pkg",
            "commit": "a" * 40, "size": 1, "sha256": "d" * 64,
            "publisher": "github-actions[bot]", "prerelease": False, "architecture": "arm64"},
            release_id=2, asset_id=2, tag_object="e" * 40)]}
        with patch.object(preparation, "admit_locked_compose_runtime",
                          side_effect=AssertionError("unrelated asset set must not admit Q")):
            self.assertEqual(preparation.prepare_locked_q_guest_builder_images(
                unrelated, retained, scratch, self.prepared, self.receipts), {})

    def test_signed_compose_zip_rejects_traversal_before_writing_outside_root(self):
        malicious = self.root / "malicious.zip"
        outside = self.root / "outside"
        with zipfile.ZipFile(malicious, "w") as archive:
            archive.writestr("../outside", b"no")
        destination = self.root / "extracted"
        destination.mkdir()
        with self.assertRaisesRegex(ValueError, "Unsafe release archive member"):
            preparation.unpack_zip(malicious, destination)
        self.assertFalse(outside.exists())

    def test_tar_reuses_authenticated_preparation_without_extraction(self):
        self.archive()
        result = self.prepare()
        self.assertEqual(Path(result["executables"]["compose"]).read_bytes(), b"fixture")
        with patch.object(preparation, "unpack_tar", side_effect=AssertionError("must not repeat")):
            self.assertEqual(self.prepare(), result)
        shutil.rmtree(Path(result["root"]))
        self.assertEqual(self.prepare(), result)  # SSD eviction never loses the retained asset.
        self.assertEqual(len(list(self.prepared.iterdir())), 1)

    def test_kernel_preparation_retains_data_not_an_executable_and_reuses_it(self):
        self.source.write_bytes(b"reviewed compressed archive fixture")
        self.asset = {"repository": "kata-containers/kata-containers", "tag": "3.32.0",
                      "name": "kata-static-3.32.0-arm64.tar.zst", "size": self.source.stat().st_size,
                      "sha256": sha256(self.source)}
        kernel = b"x" * 56 + b"ARMd" + b"x" * 4
        with patch.object(preparation, "KERNEL_BYTES", len(kernel)), \
                patch.object(preparation.subprocess, "run", return_value=SimpleNamespace(stdout=kernel, stderr=b"")) as extract:
            prepared = self.prepare()
            self.assertEqual(prepared["executables"], {})
            self.assertEqual(Path(prepared["files"]["kernel"]).read_bytes(), kernel)
            self.assertEqual(extract.call_args.args[0][-1], preparation.KERNEL_MEMBER)
            self.assertEqual(extract.call_args.kwargs["timeout"], 60)
            self.assertEqual(self.prepare(), prepared)
            extract.assert_called_once()
            retained = self.durable_root()
            result = preparation.retain_prepared(self.asset, self.source, self.prepared, retained, self.receipts)
            self.assertEqual(Path(result["files"]["kernel"]).stat().st_mode & 0o777, 0o600)
            shutil.rmtree(Path(prepared["root"]))
            self.assertEqual(preparation.require_retained(self.asset, self.source, retained, self.receipts), result)

    def test_kernel_invalid_header_duplicate_output_and_tar_warnings_fail(self):
        kernel = b"x" * 56 + b"ARMd" + b"x" * 4
        self.source.write_bytes(b"fixture")
        for payload, warning in ((kernel * 2, b""), (b"x" * 64, b""), (kernel, b"warning")):
            with self.subTest(warning=warning, size=len(payload)), \
                    patch.object(preparation, "KERNEL_BYTES", len(kernel)), \
                    patch.object(preparation.subprocess, "run", return_value=SimpleNamespace(stdout=payload, stderr=warning)), \
                    self.assertRaisesRegex(ValueError, "kernel has unexpected"):
                preparation.unpack_kernel(self.source, self.prepared)
            self.assertEqual(list(self.prepared.iterdir()), [])

    def test_kernel_member_must_exist_as_nonempty_nonexecutable_data(self):
        self.archive()
        result = self.prepare()
        root = Path(result["root"])
        receipt = json.loads((root / preparation.RECEIPT).read_text())
        specification = receipt["specification"]
        for path in ("missing", "compose/bin/compose"):
            specification["layout"]["files"] = {"kernel": path}
            (root / preparation.RECEIPT).write_text(json.dumps(receipt))
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "data file"):
                preparation.validate_prepared(root, specification, receipt)

    def test_runtime_admission_never_recreates_missing_payloads(self):
        self.archive()
        with self.assertRaises(FileNotFoundError):
            preparation.require_prepared(self.asset, self.source, self.prepared, self.receipts)
        prepared = self.prepare()
        self.assertEqual(preparation.require_prepared(self.asset, self.source, self.prepared, self.receipts), prepared)
        shutil.rmtree(Path(prepared["root"]))
        with self.assertRaisesRegex(ValueError, "root or receipt"):
            preparation.require_prepared(self.asset, self.source, self.prepared, self.receipts)
        self.assertEqual(list(self.prepared.iterdir()), [])

    def durable_root(self):
        path = self.root / "durable-assets"
        path.mkdir(mode=0o700)
        return path

    def test_retained_executables_match_release_and_survive_ssd_eviction(self):
        self.archive()
        prepared = self.prepare()
        durable = self.durable_root()
        result = preparation.retain_prepared(self.asset, self.source, self.prepared, durable, self.receipts)
        self.assertEqual(result["inventorySHA256"], prepared["inventorySHA256"])
        self.assertEqual(result["preparationSHA256"], prepared["preparationSHA256"])
        self.assertTrue(Path(result["executables"]["compose"]).is_relative_to(durable))
        with patch.object(preparation, "durable_file", side_effect=AssertionError("must reuse sealed assets")):
            self.assertEqual(preparation.retain_prepared(self.asset, self.source, self.prepared, durable, self.receipts), result)
        shutil.rmtree(Path(prepared["root"]))
        self.assertEqual(preparation.require_retained(self.asset, self.source, durable, self.receipts), result)
        self.assertEqual(len(list(durable.iterdir())), 1)

    def test_pending_publication_refuses_admission_and_resumes_without_recopying_complete_files(self):
        self.archive()
        self.prepare()
        durable = self.durable_root()
        original = preparation.durable_file
        writes = []
        def interrupt(path, source, *args, **kwargs):
            writes.append(path.name)
            if path.name == "compose-normalizer":
                path.write_bytes(b"partial")
                raise OSError("injected interruption")
            return original(path, source, *args, **kwargs)
        with patch.object(preparation, "durable_file", side_effect=interrupt), self.assertRaises(OSError):
            preparation.retain_prepared(self.asset, self.source, self.prepared, durable, self.receipts)
        with self.assertRaisesRegex(ValueError, "unfinished"):
            preparation.require_retained(self.asset, self.source, durable, self.receipts)
        with patch.object(preparation, "durable_file", wraps=original) as copy:
            result = preparation.retain_prepared(self.asset, self.source, self.prepared, durable, self.receipts)
            self.assertNotIn("compose", [call.args[0].name for call in copy.call_args_list])
        self.assertEqual(preparation.require_retained(self.asset, self.source, durable, self.receipts), result)

    def test_sealed_retained_corruption_is_not_silently_repaired(self):
        self.archive()
        self.prepare()
        durable = self.durable_root()
        result = preparation.retain_prepared(self.asset, self.source, self.prepared, durable, self.receipts)
        command = Path(result["executables"]["compose"])
        command.write_bytes(b"corruption")
        with self.assertRaisesRegex(ValueError, "bytes or modes changed"):
            preparation.retain_prepared(self.asset, self.source, self.prepared, durable, self.receipts)
        self.assertEqual(command.read_bytes(), b"corruption")

    def test_pending_foreign_files_links_or_marker_mismatch_are_never_overwritten(self):
        self.archive()
        prepared = self.prepare()
        durable = self.durable_root()
        with patch.object(preparation, "durable_file", side_effect=OSError("injected crash")), self.assertRaises(OSError):
            preparation.retain_prepared(self.asset, self.source, self.prepared, durable, self.receipts)
        destination = durable / prepared["preparationSHA256"]
        foreign = destination / "unregistered"
        foreign.write_bytes(b"preserve")
        with self.assertRaisesRegex(ValueError, "Unregistered"):
            preparation.retain_prepared(self.asset, self.source, self.prepared, durable, self.receipts)
        self.assertEqual(foreign.read_bytes(), b"preserve")
        foreign.unlink()
        target = destination / "compose/bin/compose"
        target.symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, "link or special"):
            preparation.retain_prepared(self.asset, self.source, self.prepared, durable, self.receipts)
        target.unlink()
        pending = durable / (prepared["preparationSHA256"] + ".pending.json")
        pending.write_text("different owner")
        with self.assertRaisesRegex(ValueError, "ownership changed"):
            preparation.retain_prepared(self.asset, self.source, self.prepared, durable, self.receipts)

    def test_pending_hardlinked_file_and_alias_root_fail_closed(self):
        self.archive()
        prepared = self.prepare()
        durable = self.durable_root()
        with patch.object(preparation, "durable_file", side_effect=OSError("injected crash")), self.assertRaises(OSError):
            preparation.retain_prepared(self.asset, self.source, self.prepared, durable, self.receipts)
        target = durable / prepared["preparationSHA256"] / "compose/bin/compose"
        foreign = self.root / "foreign"
        foreign.write_bytes(b"preserve")
        os.link(foreign, target)
        with self.assertRaisesRegex(ValueError, "privately owned"):
            preparation.retain_prepared(self.asset, self.source, self.prepared, durable, self.receipts)
        self.assertEqual(foreign.read_bytes(), b"preserve")
        alias = self.root / "alias"
        alias.symlink_to(durable)
        with self.assertRaisesRegex(ValueError, "canonical directory"):
            preparation.retain_prepared(self.asset, self.source, self.prepared, alias, self.receipts)

    def test_private_parent_pending_alias_and_complete_hardlink_are_rejected(self):
        self.archive()
        prepared = self.prepare()
        durable = self.durable_root()
        durable.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "must be private"):
            preparation.retain_prepared(self.asset, self.source, self.prepared, durable, self.receipts)
        durable.chmod(0o700)
        pending = durable / (prepared["preparationSHA256"] + ".pending.json")
        pending.symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, "aliases"):
            preparation.retain_prepared(self.asset, self.source, self.prepared, durable, self.receipts)
        pending.unlink()
        with patch.object(preparation, "durable_file", side_effect=OSError("injected crash")), self.assertRaises(OSError):
            preparation.retain_prepared(self.asset, self.source, self.prepared, durable, self.receipts)
        target = durable / prepared["preparationSHA256"] / "compose/bin/compose"
        foreign = self.root / "complete-linked-file"
        foreign.write_bytes(b"fixture")
        foreign.chmod(0o755)
        os.link(foreign, target)
        with self.assertRaisesRegex(ValueError, "file ownership changed"):
            preparation.retain_prepared(self.asset, self.source, self.prepared, durable, self.receipts)

    def test_changed_source_during_copy_cannot_publish_executables(self):
        self.archive()
        self.prepare()
        durable = self.durable_root()
        original = preparation.durable_file
        def changed_source(path, source, *args, **kwargs):
            original(path, source, *args, **kwargs)
            if path.name == preparation.RECEIPT:
                self.source.write_bytes(b"changed release archive")
        with patch.object(preparation, "durable_file", side_effect=changed_source), self.assertRaises(ValueError):
            preparation.retain_prepared(self.asset, self.source, self.prepared, durable, self.receipts)
        self.assertEqual(len(list(durable.glob("*.pending.json"))), 1)
        with self.assertRaisesRegex(ValueError, "unfinished"):
            preparation.require_retained(self.asset, self.source, durable, self.receipts)

    def test_raw_release_has_only_the_pinned_binary(self):
        self.source.write_bytes(b"released executable")
        self.asset.update(repository="docker/compose", name="docker-compose-darwin-aarch64",
                          size=self.source.stat().st_size, sha256=sha256(self.source))
        result = self.prepare()
        command = Path(result["executables"]["docker-compose"])
        self.assertEqual(command.read_bytes(), self.source.read_bytes())
        self.assertEqual(command.stat().st_mode & 0o777, 0o755)

    def oracle_asset(self, repository, tag, name):
        self.asset = {"repository": repository, "tag": tag, "name": name,
                      "size": self.source.stat().st_size, "sha256": sha256(self.source)}

    def lima_archive(self, *, target="../../lima/templates", kind=tarfile.SYMTYPE,
                     include_link=True, extra=None):
        with tarfile.open(self.source, "w:gz") as archive:
            for name, mode in [("bin/limactl", 0o755), ("bin/lima", 0o755),
                               ("share/lima/lima-guestagent.Linux-aarch64.gz", 0o644),
                               ("share/lima/templates/default.yaml", 0o644)]:
                entry = tarfile.TarInfo(name)
                entry.mode, entry.size = mode, 7
                archive.addfile(entry, io.BytesIO(b"fixture"))
            if include_link:
                entry = tarfile.TarInfo("./share/doc/lima/templates")
                entry.type, entry.linkname = kind, target
                archive.addfile(entry)
            if extra is not None:
                archive.addfile(extra)
        self.oracle_asset("lima-vm/lima", "v2.2.0", "lima-2.2.0-Darwin-arm64.tar.gz")

    def test_colima_raw_binary_uses_its_own_reviewed_path(self):
        self.source.write_bytes(b"released colima")
        self.oracle_asset("abiosoft/colima", "v0.10.3", "colima-Darwin-arm64")
        result = self.prepare()
        command = Path(result["executables"]["colima"])
        self.assertEqual(command.relative_to(result["root"]).as_posix(), "bin/colima")
        self.assertEqual(command.read_bytes(), self.source.read_bytes())
        self.assertEqual(command.stat().st_mode & 0o777, 0o755)
        self.assertFalse((Path(result["root"]) / "bin/docker-compose").exists())

    def test_vm_image_remains_compressed_nonexecutable_data_and_reuses_retention(self):
        self.source.write_bytes(b"compressed published disk image fixture")
        self.oracle_asset("abiosoft/colima-core", "v0.10.4", "ubuntu-24.04-minimal-cloudimg-arm64-docker.raw.gz")
        result = self.prepare()
        self.assertEqual(result["executables"], {})
        image = Path(result["files"]["disk-image"])
        self.assertEqual(image.read_bytes(), self.source.read_bytes())
        self.assertEqual(image.stat().st_mode & 0o777, 0o600)
        durable = self.durable_root()
        retained = preparation.retain_prepared(self.asset, self.source, self.prepared, durable, self.receipts)
        shutil.rmtree(Path(result["root"]))
        with patch.object(preparation, "copy_raw", side_effect=AssertionError("must not recopy")):
            self.assertEqual(preparation.require_retained(self.asset, self.source, durable, self.receipts), retained)

    def test_lima_omits_only_the_exact_documentation_alias(self):
        self.lima_archive()
        result = self.prepare()
        root = Path(result["root"])
        self.assertFalse((root / "share/doc/lima/templates").exists())
        self.assertEqual(Path(result["files"]["guest-agent"]).read_bytes(), b"fixture")
        self.assertEqual((root / "share/lima/templates/default.yaml").read_bytes(), b"fixture")
        receipt = json.loads((root / preparation.RECEIPT).read_text())
        self.assertEqual(receipt["specification"]["layout"]["omittedLinks"],
                         {"share/doc/lima/templates": "../../lima/templates"})
        with patch.object(preparation, "unpack_tar", side_effect=AssertionError("must reuse")):
            self.assertEqual(self.prepare(), result)

    def test_lima_missing_changed_or_nonlink_documentation_alias_is_rejected(self):
        for options in ({"include_link": False}, {"target": "/outside"},
                        {"kind": tarfile.REGTYPE}, {"kind": tarfile.LNKTYPE}):
            with self.subTest(options=options):
                self.lima_archive(**options)
                with self.assertRaisesRegex(ValueError, "documentation link"):
                    self.prepare()
                self.assertEqual(list(self.prepared.iterdir()), [])
                self.assertEqual(list(self.receipts.iterdir()), [])

    def test_lima_exception_does_not_admit_other_links_or_duplicate_members(self):
        for name in ("bin/alias", "share/doc/lima/templates", "../outside"):
            with self.subTest(name=name):
                extra = tarfile.TarInfo(name)
                extra.type, extra.linkname = tarfile.SYMTYPE, "../../lima/templates"
                self.lima_archive(extra=extra)
                with self.assertRaises(ValueError):
                    self.prepare()
                self.assertEqual(list(self.prepared.iterdir()), [])
        # The generic extractor retains its no-link policy.
        self.lima_archive()
        with self.assertRaisesRegex(ValueError, "only files and directories"):
            preparation.unpack_tar(self.source, self.prepared)

    def test_oracle_layouts_are_version_specific(self):
        for repository, tag, name in [
            ("abiosoft/colima", "v0.10.4", "colima-Darwin-arm64"),
            ("lima-vm/lima", "v2.3.0", "lima-2.2.0-Darwin-arm64.tar.gz"),
            ("abiosoft/colima-core", "v0.10.3", "ubuntu-24.04-minimal-cloudimg-arm64-docker.raw.gz"),
        ]:
            with self.subTest(repository=repository), self.assertRaisesRegex(ValueError, "reviewed layout"):
                preparation.layout({"repository": repository, "tag": tag, "name": name})

    def test_invalid_raw_layout_and_unknown_format_fail_before_publication(self):
        self.source.write_bytes(b"fixture")
        self.oracle_asset("abiosoft/colima", "v0.10.3", "colima-Darwin-arm64")
        for layout in ({"format": "raw", "executables": {}},
                       {"format": "unknown", "executables": {}}):
            with self.subTest(layout=layout), patch.object(preparation, "layout", return_value=layout), \
                    self.assertRaises(ValueError):
                self.prepare()
            self.assertEqual(list(self.prepared.iterdir()), [])
            self.assertEqual(list(self.receipts.iterdir()), [])

    def test_package_expansion_not_installation_and_exact_layout(self):
        self.source.write_bytes(b"signed package fixture")
        self.asset.update(repository="apple/container", tag="1.4.1", name="container-1.4.1-installer-signed.pkg",
                          size=self.source.stat().st_size, sha256=sha256(self.source))

        def expand(source, destination):
            self.assertEqual(source, self.source)
            binary_root = destination / "Payload/bin"
            binary_root.mkdir(parents=True)
            for name in ("container", "container-apiserver"):
                binary = binary_root / name
                binary.write_bytes(b"fixture")
                binary.chmod(0o755)

        result = self.prepare(expand=expand)
        self.assertTrue(Path(result["executables"]["container"]).is_file())
        with patch.object(preparation.subprocess, "run") as run:
            preparation.expand_package(self.source, self.root / "expansion")
        self.assertEqual(run.call_args.args[0][:2], ["/usr/sbin/pkgutil", "--expand-full"])
        self.assertEqual(run.call_args.kwargs["timeout"], 120)
        self.assertNotIn("HOME", run.call_args.kwargs["env"])

    def test_archive_path_and_member_types_fail_before_publication(self):
        for name, kind in [("/outside", tarfile.REGTYPE), ("../outside", tarfile.REGTYPE),
                           ("bad\\path", tarfile.REGTYPE), (preparation.RECEIPT, tarfile.REGTYPE),
                           ("compose/bin/compose", tarfile.REGTYPE), ("link", tarfile.SYMTYPE),
                           ("hardlink", tarfile.LNKTYPE), ("device", tarfile.CHRTYPE), (".", tarfile.REGTYPE)]:
            with self.subTest(name=name):
                entry = tarfile.TarInfo(name)
                entry.type = kind
                entry.linkname = "/outside"
                self.archive([entry])
                with self.assertRaises(ValueError):
                    self.prepare()
                self.assertEqual(list(self.prepared.iterdir()), [])
                self.assertEqual(list(self.receipts.iterdir()), [])

    def test_bounded_inventory_and_expansion(self):
        self.archive()
        with patch.object(preparation, "MAX_FILES", 1), self.assertRaisesRegex(ValueError, "excessive"):
            self.prepare()
        with patch.object(preparation, "MAX_BYTES", 1), self.assertRaisesRegex(ValueError, "size limit"):
            self.prepare()
        result = self.prepare()
        with patch.object(preparation, "MAX_BYTES", 1), self.assertRaisesRegex(ValueError, "inventory limits"):
            preparation.inventory(Path(result["root"]))

    def test_missing_execute_permission_is_not_a_ready_binary(self):
        self.archive(executable=False)
        with self.assertRaisesRegex(ValueError, "not executable"):
            self.prepare()
        self.assertEqual(list(self.prepared.iterdir()), [])

    def test_mutated_download_and_payload_are_rejected(self):
        self.archive()
        result = self.prepare()
        command = Path(result["executables"]["compose"])
        command.write_bytes(b"substitution")
        with self.assertRaisesRegex(ValueError, "bytes or modes"):
            self.prepare()
        self.source.write_bytes(b"changed retained archive")
        with self.assertRaisesRegex(ValueError, "failed verification"):
            self.prepare()

    def test_changed_ssd_receipt_cannot_authorize_changed_payload(self):
        self.archive()
        result = self.prepare()
        root = Path(result["root"])
        Path(result["executables"]["compose"]).write_bytes(b"substitution")
        receipt = root / preparation.RECEIPT
        value = json.loads(receipt.read_text())
        value["inventory"] = preparation.inventory(root)
        receipt.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "internally retained"):
            self.prepare()

    def test_changed_specification_and_missing_receipt_are_rejected(self):
        self.archive()
        result = self.prepare()
        root = Path(result["root"])
        receipt = json.loads((root / preparation.RECEIPT).read_text())
        with self.assertRaisesRegex(ValueError, "identity differs"):
            preparation.validate_prepared(root, {}, receipt)
        next(self.receipts.iterdir()).unlink()
        with self.assertRaises(FileNotFoundError):
            self.prepare()

    def test_symlinks_and_unknown_residue_are_never_overwritten(self):
        self.archive()
        alias = self.root / "alias"
        alias.symlink_to(self.prepared)
        with self.assertRaisesRegex(ValueError, "non-symlinked"):
            preparation.prepare(self.asset, self.source, alias, self.receipts)
        result = self.prepare()
        command = Path(result["executables"]["compose"])
        command.unlink()
        command.symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, "link or special"):
            self.prepare()
        retained = next(self.receipts.iterdir())
        retained.unlink()
        retained.symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, "Symlinked retained"):
            self.prepare()
        with self.assertRaisesRegex(ValueError, "Symlinked retained"):
            preparation.retain_receipt(retained, {})

    def test_recovery_refuses_inconsistent_retained_manifest(self):
        self.archive()
        result = self.prepare()
        shutil.rmtree(Path(result["root"]))
        next(self.receipts.iterdir()).write_text("{}")
        with self.assertRaisesRegex(ValueError, "different retained"):
            self.prepare()
        self.assertEqual(list(self.prepared.iterdir()), [])

    def test_matching_recovered_receipt_reestablishes_file_and_directory_durability(self):
        path = self.receipts / "recovered.json"
        path.write_text('{"receipt":"fixture"}')
        with patch.object(preparation.os, "fsync", wraps=os.fsync) as sync:
            preparation.retain_receipt(path, {"receipt": "fixture"})
        self.assertEqual(sync.call_count, 2)


if __name__ == "__main__":
    unittest.main()
