# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Checksum-pinned prebuilt lifecycle runtime; no npm hooks or source rebuilds."""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")

def reference_lock_error(lock):
    """Return a diagnostic before registering any unchecked download."""
    node = lock.get("node", {})
    cli = lock.get("cli", {})
    if lock.get("schemaVersion") != 1 or node.get("version") != "24.21.0" or cli.get("version") != "0.88.0":
        return "Private runtime requires the reviewed Node24.21.0 / CLI0.88.0 lock"
    if node.get("url") != "https://nodejs.org/dist/v24.21.0/node-v24.21.0-darwin-arm64.tar.gz" or cli.get("url") != "https://registry.npmjs.org/@devcontainers/cli/-/cli-0.88.0.tgz":
        return "Private runtime source URL differs from its reviewed official distribution"
    if node.get("integrity") != "sha256-vtfupTJeEQjzLOUijd1qXw8IpJnuQqp0Qq6lg3AvYFc=" or cli.get("integrity") != "sha512-sMkruPy/icfov20mdQh2EjFYZogxvMEZptDEvg5/eMBIUOr2xr+8wlsI7nvDR6EJxoBjqoasXqgRGbiMqbaJ1w==":
        return "Private runtime integrity differs from the reviewed exact bytes"
    return None

def _reference_runtime_impl(ctx):
    for mod in ctx.modules:
        for config in mod.tags.lockfile:
            lock = json.decode(ctx.read(config.path))
            error = reference_lock_error(lock)
            if error:
                fail(error)
            node = lock["node"]
            cli = lock["cli"]
            http_archive(
                name = "devcontainer_reference_node",
                urls = [node["url"]],
                integrity = node["integrity"],
                strip_prefix = "node-v24.21.0-darwin-arm64",
                build_file_content = "exports_files([\"bin/node\", \"LICENSE\"], visibility=[\"//visibility:public\"])\n",
            )
            http_archive(
                name = "devcontainer_reference_cli",
                urls = [cli["url"]],
                integrity = cli["integrity"],
                strip_prefix = "package",
                build_file_content = """
filegroup(
    name = "runtime",
    srcs = [
        "devcontainer.js", "dist/spec-node/devContainersSpecCLI.js",
        "scripts/updateUID.Dockerfile", "package.json", "LICENSE.txt", "ThirdPartyNotices.txt",
    ],
    visibility = ["//visibility:public"],
)
""",
            )
    return ctx.extension_metadata(reproducible = True)

reference_runtime = module_extension(
    implementation = _reference_runtime_impl,
    tag_classes = {"lockfile": tag_class(attrs = {"path": attr.label(mandatory = True)})},
)
