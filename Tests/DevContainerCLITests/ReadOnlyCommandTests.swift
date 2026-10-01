// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ArgumentParser
@testable import DevContainerCLI
import DevContainerCore
import DevContainerModel
import DevContainerProcess
import DevContainerTestStorage
import Foundation
import Testing

struct ReadOnlyCommandTests {
    @Test
    func `version formats agree on exact generated provenance`() throws {
        let info = DevContainerProject.buildInfo
        let pretty = try VersionCommand.parse([]).outputData()
        let expected = "devcontainer \(info.version) (lane: \(info.lane), commit: \(info.commit), source: \(info.source))"
        #expect(pretty == Data(expected.utf8))
        let json = try VersionCommand.parse(["--format", "json"]).outputData()
        #expect(try JSONDecoder().decode(BuildInfo.self, from: json) == info)
        #expect(json.range(of: Data("\\/".utf8)) == nil)
        #expect(try VersionCommand.parse(["--short"]).outputData() == Data(info.version.utf8))
        #expect(try VersionCommand.parse(["--short", "--format", "json"]).outputData() == Data(info.version.utf8))
    }

    @Test
    func `version rejects unsupported format without producing output`() throws {
        let command = try VersionCommand.parse(["--format", "yaml"])
        #expect(throws: ValidationError.self) { try command.outputData() }
    }

    @Test(arguments: ["engine.sock", "space and 'quote'.sock", "$(touch injected);$HOME`id`.sock", "line\nbreak.sock"])
    func `context shell output round trips socket literally without running substitutions`(name: String) async throws {
        let root = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let socket = root.appendingPathComponent(name).path
        let arguments = ["--config", root.appendingPathComponent("missing.toml").path, "--socket", socket]
        let value = try ContextCommand.parse(arguments + ["--format", "value"]).renderedOutput()
        #expect(value == "unix://\(socket)")
        let shell = try ContextCommand.parse(arguments).renderedOutput()
        let result = try await ProcessRunner.captured(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: ["-c", shell + "\nprintf '%s' \"$DOCKER_HOST\""],
            environment: ["PATH": "/usr/bin:/bin", "HOME": "/should-not-expand"],
            workingDirectory: root,
            maximumOutputBytes: 8192
        )
        #expect(result.exitCode == 0)
        #expect(result.standardOutput == Data(value.utf8))
        #expect(result.standardError.isEmpty)
        #expect(result.omittedStandardOutputBytes == 0)
        #expect(try FileManager.default.contentsOfDirectory(atPath: root.path).isEmpty)
    }

    @Test
    func `context refuses malformed configuration and unknown format without mutation`() throws {
        let root = try temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let configuration = root.appendingPathComponent("config.toml")
        let original = Data("unrecognised = true\n".utf8)
        try original.write(to: configuration)
        let arguments = ["--config", configuration.path, "--socket", root.appendingPathComponent("engine.sock").path]
        #expect(throws: DevContainerError.self) { try ContextCommand.parse(arguments).renderedOutput() }
        #expect(try Data(contentsOf: configuration) == original)
        try FileManager.default.removeItem(at: configuration)
        let command = try ContextCommand.parse(arguments + ["--format", "json"])
        #expect(throws: ValidationError.self) { try command.renderedOutput() }
        #expect(try FileManager.default.contentsOfDirectory(atPath: root.path).isEmpty)
    }

    private func temporaryRoot() throws -> URL {
        let root = TestStorage.temporaryDirectory.appendingPathComponent("read-only-cli-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700]
        )
        return root
    }
}
