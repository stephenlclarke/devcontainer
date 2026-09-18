// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ArgumentParser
@testable import DevContainerCLI
import DevContainerCore
import DevContainerModel
import DevContainerState
import DevContainerTestStorage
import Foundation
import Testing

struct ConfigurationCommandTests {
    @Test(arguments: [BackendProvider.stock, .containerCompose], [ComposeProviderKind.docker, .containerCompose])
    func `configure persists independent backend and frontend choices`(
        backend: BackendProvider, frontend: ComposeProviderKind
    ) throws {
        let fixture = try ConfigurationFixture()
        let command = try ConfigureCommand.parse([
            "--config", fixture.configuration.path,
            "--backend", backend.rawValue, "--compose-provider", frontend.rawValue,
            "--socket", fixture.socket.path, "--state", fixture.state.path,
            "--container", fixture.runtime.path, "--no-strict"
        ])
        try command.run()
        #expect(try fixture.load() == DevContainerConfiguration(
            backend: backend, composeProvider: frontend, containerExecutable: fixture.runtime.path,
            socket: fixture.socket.path, stateDatabase: fixture.state.path, strictCompatibility: false
        ))
        let attributes = try FileManager.default.attributesOfItem(atPath: fixture.configuration.path)
        #expect((attributes[.posixPermissions] as? NSNumber)?.intValue == 0o600)
        #expect(!FileManager.default.fileExists(atPath: fixture.state.path))
    }

    @Test
    func `configure preserves omitted settings including strictness`() throws {
        let fixture = try ConfigurationFixture()
        var expected = fixture.value(strict: false)
        try DevContainerConfigurationStore.save(expected, to: fixture.configuration)
        let replacement = fixture.root.appendingPathComponent("replacement.sock").path
        let command = try ConfigureCommand.parse(["--config", fixture.configuration.path, "--socket", replacement])
        try command.run()
        expected.socket = replacement
        #expect(try fixture.load() == expected)
    }

    @Test(arguments: [true, false])
    func `configure explicit strictness overrides stored value`(strict: Bool) throws {
        let fixture = try ConfigurationFixture()
        var expected = fixture.value(strict: !strict)
        try DevContainerConfigurationStore.save(expected, to: fixture.configuration)
        let command = try ConfigureCommand.parse([
            "--config", fixture.configuration.path, strict ? "--strict" : "--no-strict"
        ])
        try command.run()
        expected.strictCompatibility = strict
        #expect(try fixture.load() == expected)
    }

    @Test
    func `new configuration remains strict by default`() throws {
        let fixture = try ConfigurationFixture()
        let command = try ConfigureCommand.parse(["--config", fixture.configuration.path])
        try command.run()
        #expect(try fixture.load().strictCompatibility)
    }

    @Test(arguments: ["--backend", "--compose-provider"])
    func `invalid selection does not replace existing configuration`(option: String) throws {
        let fixture = try ConfigurationFixture()
        try DevContainerConfigurationStore.save(fixture.value(strict: false), to: fixture.configuration)
        let original = try Data(contentsOf: fixture.configuration)
        let command = try ConfigureCommand.parse(["--config", fixture.configuration.path, option, "invalid"])
        #expect(throws: (any Error).self) { try command.run() }
        #expect(try Data(contentsOf: fixture.configuration) == original)
    }

    @Test
    func `malformed stored configuration is not silently replaced`() throws {
        let fixture = try ConfigurationFixture()
        let original = Data("unknown = true\n".utf8)
        try original.write(to: fixture.configuration)
        let command = try ConfigureCommand.parse(["--config", fixture.configuration.path, "--backend", "stock"])
        #expect(throws: (any Error).self) { try command.run() }
        #expect(try Data(contentsOf: fixture.configuration) == original)
    }

    @Test(arguments: [BackendProvider.stock, .containerCompose])
    func `backend claim is durable idempotent and explicitly resettable`(provider: BackendProvider) async throws {
        let fixture = try ConfigurationFixture()
        try await fixture.backend(["set", provider.rawValue])
        try await fixture.backend(["set", provider.rawValue])
        try await fixture.backend(["show"])
        let store = try SQLiteStateStore(path: fixture.state)
        #expect(try await store.project(key: fixture.project)?.provider == provider)
        let other: BackendProvider = provider == .stock ? .containerCompose : .stock
        await #expect(throws: (any Error).self) { try await fixture.backend(["set", other.rawValue]) }
        #expect(try await store.project(key: fixture.project)?.provider == provider)
        try await fixture.backend(["reset"])
        #expect(try await store.project(key: fixture.project) == nil)
        await #expect(throws: (any Error).self) { try await fixture.backend(["show"]) }
        try await fixture.backend(["set", other.rawValue])
        #expect(try await store.project(key: fixture.project)?.provider == other)
    }

    @Test
    func `invalid backend does not create a database`() async throws {
        let fixture = try ConfigurationFixture()
        await #expect(throws: (any Error).self) { try await fixture.backend(["set", "invalid"]) }
        #expect(!FileManager.default.fileExists(atPath: fixture.state.path))
    }
}

private final class ConfigurationFixture {
    let root: URL
    var configuration: URL {
        root.appendingPathComponent("config.toml")
    }

    var state: URL {
        root.appendingPathComponent("state.sqlite")
    }

    var socket: URL {
        root.appendingPathComponent("engine.sock")
    }

    var runtime: URL {
        root.appendingPathComponent("missing-runtime")
    }

    let project = ProjectKey(rawValue: "configuration-command-fixture")

    init() throws {
        root = TestStorage.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(
            at: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700]
        )
    }

    deinit { try? FileManager.default.removeItem(at: root) }

    func value(strict: Bool) -> DevContainerConfiguration {
        DevContainerConfiguration(
            backend: .containerCompose, composeProvider: .containerCompose,
            containerExecutable: runtime.path, socket: socket.path,
            stateDatabase: state.path, strictCompatibility: strict
        )
    }

    func load() throws -> DevContainerConfiguration {
        try DevContainerConfigurationStore.load(from: configuration, defaultSocket: socket.path)
    }

    func backend(_ arguments: [String]) async throws {
        let parsed = try BackendCommand.parseAsRoot(
            arguments + ["--project", project.rawValue, "--state", state.path]
        )
        var command = try #require(parsed as? any AsyncParsableCommand)
        try await command.run()
    }
}
