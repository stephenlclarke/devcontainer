# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Native Starlark regression for fail-closed private runtime admission."""

load("@bazel_skylib//lib:unittest.bzl", "asserts", "unittest")
load(":reference_runtime.bzl", "reference_lock_error")

def _reference_runtime_test_impl(ctx):
    env = unittest.begin(ctx)
    lock = {
        "schemaVersion": 1,
        "node": {
            "version": "24.21.0",
            "url": "https://nodejs.org/dist/v24.21.0/node-v24.21.0-darwin-arm64.tar.gz",
            "integrity": "sha256-vtfupTJeEQjzLOUijd1qXw8IpJnuQqp0Qq6lg3AvYFc=",
        },
        "cli": {
            "version": "0.88.0",
            "url": "https://registry.npmjs.org/@devcontainers/cli/-/cli-0.88.0.tgz",
            "integrity": "sha512-sMkruPy/icfov20mdQh2EjFYZogxvMEZptDEvg5/eMBIUOr2xr+8wlsI7nvDR6EJxoBjqoasXqgRGbiMqbaJ1w==",
        },
    }
    asserts.equals(env, None, reference_lock_error(lock))
    for tool in ["node", "cli"]:
        for field, value in [
            ("integrity", ""), ("integrity", None), ("integrity", 123),
            ("integrity", "sha256-invalid"), ("integrity", "sha512-" + "A" * 88),
            ("version", "unreviewed"), ("url", "https://unreviewed.invalid/archive"),
        ]:
            changed = dict(lock)
            changed[tool] = dict(lock[tool])
            changed[tool][field] = value
            asserts.true(env, reference_lock_error(changed) != None, "%s.%s must reject %s" % (tool, field, value))
        missing = dict(lock)
        missing[tool] = dict(lock[tool])
        missing[tool].pop("integrity")
        asserts.true(env, reference_lock_error(missing) != None)
    asserts.true(env, reference_lock_error({}) != None)
    return unittest.end(env)

reference_runtime_test = unittest.make(_reference_runtime_test_impl)
