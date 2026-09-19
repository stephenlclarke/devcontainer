// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerTestStorage
import Foundation
import Testing

struct AppleBindSourcePolicyTests {
    private func withRoot(_ body: (URL) throws -> Void) throws {
        let root = TestStorage.temporaryDirectory.appendingPathComponent("bind-policy-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        try body(root)
    }

    @Test func `creates only opted in directories after preflight`() throws {
        try withRoot { root in
            let source = root.appendingPathComponent("parent/child")
            let mount = RuntimeMount(
                type: .bind,
                source: source.path,
                destination: "/work",
                readOnly: true,
                createSourceDirectory: true
            )
            try AppleContainerRuntime.validateNativeMounts([mount])
            #expect(!FileManager.default.fileExists(atPath: source.path))
            try AppleBindSourcePolicy.prepare(mount)
            var directory: ObjCBool = false
            #expect(FileManager.default.fileExists(atPath: source.path, isDirectory: &directory))
            #expect(directory.boolValue)
            let contents = source.appendingPathComponent("retained")
            try Data("user data".utf8).write(to: contents)
            try AppleBindSourcePolicy.prepare(mount)
            #expect(try String(contentsOf: contents, encoding: .utf8) == "user data")
            #expect(try JSONDecoder().decode(RuntimeMount.self, from: JSONEncoder().encode(mount)) == mount)
        }
    }

    @Test(arguments: [false, nil] as [Bool?])
    func `absent or disabled creation does not create`(_ policy: Bool?) throws {
        try withRoot { root in
            let source = root.appendingPathComponent("absent")
            let mount = RuntimeMount(
                type: .bind,
                source: source.path,
                destination: "/work",
                createSourceDirectory: policy
            )
            #expect(throws: (any Error).self) { try AppleContainerRuntime.validateNativeMounts([mount]) }
            try AppleBindSourcePolicy.prepare(mount)
            #expect(!FileManager.default.fileExists(atPath: source.path))
        }
    }

    @Test func `rejects invalid mount before host mutation`() throws {
        try withRoot { root in
            for source in ["relative", root.path + "/new,dir", root.path + "/new=dir", root.path + "\0bad"] {
                let mount = RuntimeMount(type: .bind, source: source, destination: "/work", createSourceDirectory: true)
                #expect(throws: DevContainerError.self) { try AppleBindSourcePolicy.prepare(mount) }
            }
            let remaining = try FileManager.default.contentsOfDirectory(atPath: root.path)
            #expect(remaining.isEmpty)
            let source = root.appendingPathComponent("not-created")
            let mount = RuntimeMount(type: .bind, source: source.path, destination: "", createSourceDirectory: true)
            #expect(throws: (any Error).self) { try AppleBindSourcePolicy.prepare(mount) }
            #expect(!FileManager.default.fileExists(atPath: source.path))
        }
    }

    @Test func `preserves files and propagates creation failure`() throws {
        try withRoot { root in
            let file = root.appendingPathComponent("existing-file")
            try Data("untouched".utf8).write(to: file)
            let existing = RuntimeMount(
                type: .bind, source: file.path, destination: "/work", createSourceDirectory: true
            )
            #if DEVCONTAINER_ENHANCED_RUNTIME
                // The enhanced parser supports file binds; preparation must
                // preserve the file rather than trying to create a directory.
                try AppleBindSourcePolicy.prepare(existing)
            #else
                #expect(throws: (any Error).self) { try AppleBindSourcePolicy.prepare(existing) }
            #endif
            let invalidChild = RuntimeMount(
                type: .bind, source: file.appendingPathComponent("child").path,
                destination: "/work", createSourceDirectory: true
            )
            #expect(throws: (any Error).self) { try AppleBindSourcePolicy.prepare(invalidChild) }
            #expect(try String(contentsOf: file, encoding: .utf8) == "untouched")
        }
    }

    @Test func `non bind mount never creates host directory`() throws {
        let mount = RuntimeMount(type: .volume, source: "named", destination: "/work", createSourceDirectory: true)
        #expect(try AppleBindSourcePolicy.validationSource(mount) == "named")
        try AppleBindSourcePolicy.prepare(mount)
    }
}
