// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
import DevContainerModel
import DevContainerTestStorage
import Foundation
import Testing

struct AtomicFileTests {
    @Test
    func `replaces contents privately without residue`() throws {
        let root = TestStorage.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let output = root.appendingPathComponent("output")
        try AtomicFile.write(Data("before".utf8), to: output)
        try AtomicFile.write(Data("after".utf8), to: output)
        #expect(try String(contentsOf: output, encoding: .utf8) == "after")
        let attributes = try FileManager.default.attributesOfItem(atPath: output.path)
        #expect(attributes[.posixPermissions] as? Int == 0o600)
        #expect(try FileManager.default.contentsOfDirectory(atPath: root.path) == ["output"])
    }

    @Test
    func `failed rename keeps directory and removes temporary sibling`() throws {
        let root = TestStorage.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let output = root.appendingPathComponent("output")
        try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        #expect(throws: (any Error).self) { try AtomicFile.write(Data("content".utf8), to: output) }
        #expect(try FileManager.default.contentsOfDirectory(atPath: root.path) == ["output"])
        #expect(try output.resourceValues(forKeys: [.isDirectoryKey]).isDirectory == true)
    }

    @Test
    func `missing parent does not create unexpected directories`() {
        let root = TestStorage.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        #expect(throws: (any Error).self) {
            try AtomicFile.write(Data(), to: root.appendingPathComponent("output"))
        }
        #expect(!FileManager.default.fileExists(atPath: root.path))
    }
}
