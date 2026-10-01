// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
import DevContainerTestStorage
import Foundation
import Testing

@Test func `scratch selection and precedence`() throws {
    let manager = FileManager.default
    let parent = TestStorage.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? manager.removeItem(at: parent) }
    let fallback = parent.appendingPathComponent("fallback", isDirectory: true)
    let temporary = parent.appendingPathComponent("temporary", isDirectory: true)
    let selected = parent.appendingPathComponent("selected", isDirectory: true)
    for directory in [fallback, temporary, selected] {
        try manager.createDirectory(at: directory, withIntermediateDirectories: true)
    }
    #expect(TestStorage.resolve(environment: [:], fallback: fallback.path)?.path == fallback.path)
    #expect(TestStorage.resolve(
        environment: ["TMPDIR": temporary.path], fallback: fallback.path
    )?.path == temporary.path)
    #expect(TestStorage.resolve(
        environment: ["TEST_TMPDIR": selected.path, "TMPDIR": temporary.path], fallback: fallback.path
    )?.path == selected.path)
    #expect(TestStorage.resolve(environment: ["TMPDIR": "relative"], fallback: fallback.path) == nil)
    #expect(TestStorage.resolve(environment: [:], fallback: "") == nil)
    #expect(TestStorage.resolve(
        environment: ["TMPDIR": parent.appendingPathComponent("missing").path], fallback: ""
    ) == nil)
}

@Test func `macOS var scratch alias resolves to the physical path`() {
    #expect(TestStorage.resolve(environment: ["TMPDIR": "/var/tmp"], fallback: "")?.path == "/private/var/tmp")
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
    try manager.createDirectory(at: directory.appendingPathComponent("child"), withIntermediateDirectories: false)
    #expect(TestStorage.resolve(
        environment: [
            "BAZEL_TEST": "1", "DEVCONTAINER_TEST_SCRATCH_ROOT": alias.path, "TEST_TMPDIR": alias.path + "/child"
        ],
        fallback: ""
    ) == nil)
}

@Test func `bazel scratch rejects missing root and path escapes`() throws {
    let manager = FileManager.default
    let parent = TestStorage.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? manager.removeItem(at: parent) }
    let root = parent.appendingPathComponent("root", isDirectory: true).path
    let existingPaths = [
        root + "/t/test",
        root + "/t/nested",
        root + "-other/test",
        parent.appendingPathComponent("escape").path
    ]
    for path in existingPaths {
        try manager.createDirectory(atPath: path, withIntermediateDirectories: true)
    }
    let base = ["BAZEL_TEST": "1", "DEVCONTAINER_TEST_SCRATCH_ROOT": root]
    for path in [root + "/t/test", root + "/t/nested/../test"] {
        let environment = base.merging(["TEST_TMPDIR": path]) { _, new in new }
        #expect(TestStorage.resolve(environment: environment, fallback: "") != nil)
    }
    for path in [root, root + "-other/test", root + "/../escape", "/var/tmp", "relative"] {
        let environment = base.merging(["TEST_TMPDIR": path]) { _, new in new }
        #expect(TestStorage.resolve(environment: environment, fallback: "") == nil)
    }
    for invalidRoot in ["", "/", "relative", root + "/../"] {
        #expect(TestStorage.resolve(
            environment: ["BAZEL_TEST": "1", "DEVCONTAINER_TEST_SCRATCH_ROOT": invalidRoot, "TEST_TMPDIR": root + "/t"],
            fallback: ""
        ) == nil)
    }
    #expect(TestStorage.resolve(environment: ["BAZEL_TEST": "1", "TMPDIR": root + "/t"], fallback: "") == nil)
}
