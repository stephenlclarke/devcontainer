// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
import DevContainerTestStorage
import Foundation
import Testing

@Test func `scratch selection and precedence`() {
    #expect(TestStorage.resolve(environment: [:], fallback: "/fallback")?.path == "/fallback")
    #expect(TestStorage.resolve(environment: ["TMPDIR": "/tmpdir"], fallback: "/fallback")?.path == "/tmpdir")
    #expect(TestStorage.resolve(
        environment: ["TEST_TMPDIR": "/testdir", "TMPDIR": "/tmpdir"], fallback: "/fallback"
    )?.path == "/testdir")
    #expect(TestStorage.resolve(environment: ["TMPDIR": "relative"], fallback: "/fallback") == nil)
    #expect(TestStorage.resolve(environment: [:], fallback: "") == nil)
}

@Test func `bazel scratch rejects symlink escapes and aliased roots`() throws {
    let manager = FileManager.default
    let directory = TestStorage.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
    try manager.createDirectory(at: directory, withIntermediateDirectories: true)
    defer { try? manager.removeItem(at: directory) }
    let outside = directory.appendingPathComponent("outside")
    try manager.createSymbolicLink(atPath: outside.path, withDestinationPath: "/private/tmp")
    let environment = ["BAZEL_TEST": "1", "DEVCONTAINER_TEST_SCRATCH_ROOT": directory.path, "TEST_TMPDIR": outside.path]
    #expect(TestStorage.resolve(environment: environment, fallback: "") == nil)
    let alias = directory.appendingPathComponent("alias")
    try manager.createSymbolicLink(at: alias, withDestinationURL: directory)
    #expect(TestStorage.resolve(
        environment: [
            "BAZEL_TEST": "1", "DEVCONTAINER_TEST_SCRATCH_ROOT": alias.path, "TEST_TMPDIR": alias.path + "/child"
        ],
        fallback: ""
    ) == nil)
}

@Test func `bazel scratch rejects missing root and path escapes`() {
    let root = "/Volumes/SSD/cf/bazel"
    let base = ["BAZEL_TEST": "1", "DEVCONTAINER_TEST_SCRATCH_ROOT": root]
    for path in [root + "/t/test", root + "/t/nested/../test"] {
        let environment = base.merging(["TEST_TMPDIR": path]) { _, new in new }
        #expect(TestStorage.resolve(environment: environment, fallback: "") != nil)
    }
    for path in [root, root + "-other/test", root + "/../escape", "/tmp/test", "relative"] {
        let environment = base.merging(["TEST_TMPDIR": path]) { _, new in new }
        #expect(TestStorage.resolve(environment: environment, fallback: "") == nil)
    }
    for invalidRoot in ["", "/", "relative", "/Volumes/../"] {
        #expect(TestStorage.resolve(
            environment: ["BAZEL_TEST": "1", "DEVCONTAINER_TEST_SCRATCH_ROOT": invalidRoot, "TEST_TMPDIR": root + "/t"],
            fallback: ""
        ) == nil)
    }
    #expect(TestStorage.resolve(environment: ["BAZEL_TEST": "1", "TMPDIR": root + "/t"], fallback: "") == nil)
}
