//===----------------------------------------------------------------------===//
// Copyright 2026 devcontainer project authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//===----------------------------------------------------------------------===//

@testable import DevContainerCLI
import Foundation
import Testing

struct PluginCommandTests {
    @Test
    func `root command exposes plug-in management`() {
        #expect(
            DevContainerCommand.configuredSubcommands().contains {
                ObjectIdentifier($0) == ObjectIdentifier(PluginCommand.self)
            }
        )
    }

    @Test
    func `registration is idempotent and removes only its own link`() throws {
        let fixture = try PluginRegistrationFixture()
        let registration = try fixture.registration()

        #expect(registration.status() == .missing)
        try registration.register()
        try registration.register()
        #expect(registration.status() == .registered)
        #expect(
            try FileManager.default.destinationOfSymbolicLink(
                atPath: registration.destination.path
            ) == registration.source.path
        )
        try registration.unregister()
        try registration.unregister()
        #expect(registration.status() == .missing)
    }

    @Test
    func `registration rejects invalid payloads roots and foreign targets`() throws {
        let fixture = try PluginRegistrationFixture()
        #expect(throws: (any Error).self) {
            _ = try PluginRegistration(
                source: fixture.root.appendingPathComponent("missing"),
                installRoot: fixture.installRoot
            )
        }
        #expect(throws: (any Error).self) {
            _ = try PluginRegistration(
                source: fixture.source,
                installRoot: fixture.root.appendingPathComponent("missing")
            )
        }
        #expect(throws: (any Error).self) {
            _ = try PluginRegistration(
                source: fixture.source,
                installRoot: URL(fileURLWithPath: "/")
            )
        }

        let registration = try fixture.registration()
        try FileManager.default.createDirectory(
            at: registration.destination,
            withIntermediateDirectories: true
        )
        #expect(registration.status() == .conflicting)
        #expect(throws: (any Error).self) {
            try registration.register()
        }
        #expect(throws: (any Error).self) {
            try registration.unregister()
        }
        #expect(FileManager.default.fileExists(atPath: registration.destination.path))
    }

    @Test
    func `container status resolves a validated installation root`() throws {
        let root = try ContainerInstallRootResolver.installRoot(
            from: Data(#"{"status":"running","installRoot":"/usr/local/"}"#.utf8)
        )
        #expect(root.path == "/usr/local")
        #expect(throws: (any Error).self) {
            try ContainerInstallRootResolver.installRoot(
                from: Data(#"{"status":"running"}"#.utf8)
            )
        }
        #expect(throws: (any Error).self) {
            try ContainerInstallRootResolver.installRoot(
                from: Data("not-json".utf8)
            )
        }
    }

    @Test
    func `packaged plug-in path is relative to the resolved executable`() {
        #expect(
            PluginRegistration.packagedPluginURL(
                executable: "/opt/example/bin/devcontainer"
            ).path == "/opt/example/libexec/container/plugins/devcontainer"
        )
        #expect(
            PluginRegistration.packagedPluginURL(
                executable: "/opt/homebrew/Cellar/devcontainer/0.1.0/bin/devcontainer"
            ).path == "/opt/homebrew/opt/devcontainer/libexec/container/plugins/devcontainer"
        )
    }
}

private final class PluginRegistrationFixture {
    let root: URL
    let source: URL
    let installRoot: URL

    init() throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent("devcontainer-plugin-tests-\(UUID().uuidString)")
        source = root.appendingPathComponent("payload")
        installRoot = root.appendingPathComponent("container")
        try FileManager.default.createDirectory(
            at: source.appendingPathComponent("bin"),
            withIntermediateDirectories: true
        )
        try FileManager.default.createDirectory(
            at: installRoot,
            withIntermediateDirectories: true
        )
        try Data("abstract = \"fixture\"\n".utf8).write(
            to: source.appendingPathComponent("config.toml")
        )
        let executable = source.appendingPathComponent("bin/devcontainer")
        try Data("#!/bin/sh\n".utf8).write(to: executable)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o755],
            ofItemAtPath: executable.path
        )
    }

    deinit {
        try? FileManager.default.removeItem(at: root)
    }

    func registration() throws -> PluginRegistration {
        try PluginRegistration(source: source, installRoot: installRoot)
    }
}
