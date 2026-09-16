# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Run the same version generator as SwiftPM with explicit, cacheable inputs."""

def _build_identity_impl(ctx):
    output = ctx.actions.declare_file(ctx.label.name + ".swift")
    args = ctx.actions.args()
    args.add(ctx.file.makefile)
    args.add(output)
    args.add(ctx.var.get("DEVCONTAINER_COMMIT", "unspecified"))
    args.add(ctx.var.get("DEVCONTAINER_BUILD_LANE", "development"))
    ctx.actions.run(
        executable = ctx.executable.generator,
        arguments = [args],
        inputs = [ctx.file.makefile],
        outputs = [output],
        mnemonic = "DevContainerBuildIdentity",
        progress_message = "Generating devcontainer build identity",
    )
    return [DefaultInfo(files = depset([output]))]

build_identity = rule(
    implementation = _build_identity_impl,
    attrs = {
        "generator": attr.label(executable = True, cfg = "exec", mandatory = True),
        "makefile": attr.label(allow_single_file = True, mandatory = True),
    },
)
