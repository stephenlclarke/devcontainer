# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
def _terminal_launcher_identity_impl(ctx):
    output = ctx.actions.declare_file(ctx.label.name + ".swift")
    ctx.actions.run(
        executable = "/usr/bin/python3",
        arguments = [ctx.file._generator.path, output.path, ctx.file.arm64.path, ctx.file.amd64.path],
        inputs = [ctx.file._generator, ctx.file.arm64, ctx.file.amd64],
        outputs = [output],
        mnemonic = "TerminalLauncherIdentity",
        progress_message = "Embedding verified Linux terminal launcher identities",
        env = {"PYTHONDONTWRITEBYTECODE": "1"},
    )
    return [DefaultInfo(files = depset([output]))]

terminal_launcher_identity = rule(
    implementation = _terminal_launcher_identity_impl,
    attrs = {
        "arm64": attr.label(allow_single_file = True, mandatory = True),
        "amd64": attr.label(allow_single_file = True, mandatory = True),
        "_generator": attr.label(
            default = Label("//Tools/bazel:terminal_launcher_identity.py"),
            allow_single_file = True,
        ),
    },
)
