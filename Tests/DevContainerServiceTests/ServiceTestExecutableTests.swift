// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
import DevContainerTestStorage
import Foundation
import Testing

struct ServiceTestExecutableTests {
    @Test
    func `bundle sibling resolves exactly without adopting other build products`() throws {
        let root = TestStorage.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let bundle = root.appendingPathComponent("tests.xctest")
        try FileManager.default.createDirectory(at: bundle, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let executable = root.appendingPathComponent("devcontainer-engine")
        try Data("#!/bin/sh\nexit 0\n".utf8).write(to: executable)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: executable.path)
        #expect(try ServiceTestExecutable.resolve(beside: bundle) == executable.resolvingSymlinksInPath())

        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: executable.path)
        #expect(throws: CocoaError.self) { try ServiceTestExecutable.resolve(beside: bundle) }
        try FileManager.default.removeItem(at: executable)
        try FileManager.default.createDirectory(at: executable, withIntermediateDirectories: false)
        #expect(throws: CocoaError.self) { try ServiceTestExecutable.resolve(beside: bundle) }
        try FileManager.default.removeItem(at: executable)
        #expect(throws: CocoaError.self) { try ServiceTestExecutable.resolve(beside: bundle) }
        #expect(throws: CocoaError.self) { try ServiceTestExecutable.resolve(beside: root) }
    }
}
