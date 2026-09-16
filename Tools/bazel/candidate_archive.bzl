# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Package native outputs without another compilation, signing or publication."""

load("@rules_license//rules:gather_licenses_info.bzl", "gather_licenses_info", "write_licenses_info")

def _candidate_archive_impl(ctx):
    if ctx.var["COMPILATION_MODE"] != "opt":
        fail("Candidate archives require --config=release; debug bytes are not release candidates")
    archive = ctx.actions.declare_file(ctx.label.name + ".tar.gz")
    receipt = ctx.actions.declare_file(ctx.label.name + ".json")
    manifest = ctx.actions.declare_file(ctx.label.name + ".inputs.json")
    licenses = ctx.actions.declare_file(ctx.label.name + ".licenses.json")
    license_files = write_licenses_info(ctx, ctx.attr.binaries, licenses)
    binaries = [target[DefaultInfo].files_to_run.executable for target in ctx.attr.binaries]
    if None in binaries:
        fail("Every candidate product must be an executable")
    ctx.actions.write(manifest, json.encode({
        "binaries": {binary.basename: binary.path for binary in binaries},
        "files": {file.basename: file.path for file in ctx.files.resources},
        "licenses": licenses.path,
        "makefile": ctx.file.makefile.path,
        "resolved": ctx.file.resolved.path,
        "profile": ctx.attr.profile,
        "commit": ctx.var.get("DEVCONTAINER_COMMIT", "unspecified"),
        "epoch": ctx.var.get("SOURCE_DATE_EPOCH", "0"),
    }))
    ctx.actions.run(
        executable = "/usr/bin/python3",
        arguments = [ctx.file._packager.path, manifest.path, archive.path, receipt.path, ctx.file._archive_tool.path],
        inputs = depset([manifest, licenses, ctx.file.makefile, ctx.file.resolved, ctx.file._packager, ctx.file._archive_tool] + binaries + ctx.files.resources + license_files),
        outputs = [archive, receipt],
        mnemonic = "DevContainerCandidateArchive",
        progress_message = "Archiving native devcontainer candidate (no signing or publishing)",
        env = {"PYTHONDONTWRITEBYTECODE": "1"},
    )
    return [DefaultInfo(files = depset([archive, receipt]))]

candidate_archive = rule(
    implementation = _candidate_archive_impl,
    attrs = {
        "binaries": attr.label_list(aspects = [gather_licenses_info], mandatory = True),
        "resources": attr.label_list(allow_files = True),
        "makefile": attr.label(allow_single_file = True, mandatory = True),
        "resolved": attr.label(allow_single_file = True, mandatory = True),
        "profile": attr.string(values = ["stock", "enhanced"], mandatory = True),
        "_packager": attr.label(default = Label("//Tools/bazel:candidate_archive.py"), allow_single_file = True),
        "_archive_tool": attr.label(default = Label("//:Tools/release/create-reproducible-archive.py"), allow_single_file = True),
    },
)
