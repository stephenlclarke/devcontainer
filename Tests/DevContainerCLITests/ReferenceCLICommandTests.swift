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

import ArgumentParser
@testable import DevContainerCLI
import Foundation
import Testing

@Suite("Reference Dev Containers CLI")
struct ReferenceCLICommandTests {
    @Test
    func `all upstream commands are registered`() {
        let registered = DevContainerCommand.configuredSubcommands()
        let expected: [(String, ParsableCommand.Type)] = [
            ("up", ReferenceUpCommand.self),
            ("set-up", ReferenceSetUpCommand.self),
            ("build", ReferenceBuildCommand.self),
            ("run-user-commands", ReferenceRunUserCommandsCommand.self),
            ("read-configuration", ReferenceReadConfigurationCommand.self),
            ("outdated", ReferenceOutdatedCommand.self),
            ("upgrade", ReferenceUpgradeCommand.self),
            ("features", ReferenceFeaturesCommand.self),
            ("templates", ReferenceTemplatesCommand.self),
            ("exec", ReferenceExecCommand.self)
        ]

        for (name, command) in expected {
            #expect(command.configuration.commandName == name)
            #expect(registered.contains { $0 == command })
        }
    }

    @Test
    func `invocation pins the packaged CLI and Apple adapters`() throws {
        let fixture = try InvocationFixture()
        defer { fixture.remove() }
        let invocation = try ReferenceCLIInvocation.configured(
            command: "up",
            arguments: ["--workspace-folder", "/work"],
            injectRuntimeAdapters: true,
            environment: fixture.environment,
            executable: fixture.devcontainer
        )

        #expect(invocation.node == fixture.node)
        #expect(invocation.arguments == [
            fixture.script.path,
            "up",
            "--docker-path", fixture.docker.path,
            "--docker-compose-path", fixture.compose.path,
            "--workspace-folder", "/work"
        ])
        #expect(invocation.environment["DEVCONTAINER_REFERENCE_CLI_VERSION"] == "0.89.0")
        #expect(invocation.environment["DOCKER_HOST"] == "unix://\(fixture.socket.path)")
        #expect(invocation.environment["DEVCONTAINER_BACKEND"] == "stock")
        #expect(invocation.environment["DEVCONTAINER_COMPOSE_PROVIDER"] == "container-compose")
        #expect(invocation.environment["DEVCONTAINER_CONFIG"] == fixture.configuration.path)
        #expect(invocation.environment["DEVCONTAINER_CONTAINER_BIN"] == "/usr/local/bin/container")
        #expect(invocation.environment["DEVCONTAINER_SOCKET"] == fixture.socket.path)
        #expect(invocation.environment["DEVCONTAINER_STATE"] == fixture.state.path)
        #expect(invocation.environment["LOCAL_ENV_FIXTURE"] == "preserved")
        #expect(invocation.environment["SSH_AUTH_SOCK"] == "/tmp/agent.sock")
        #expect(invocation.environment["DYLD_INSERT_LIBRARIES"] == nil)
        #expect(invocation.environment["BASH_ENV"] == nil)
        #expect(invocation.environment["DEVCONTAINER_NODE_BIN"] == nil)
        #expect(invocation.environment["DEVCONTAINER_REFERENCE_CLI"] == nil)
        #expect(invocation.environment["DOCKER_CONTEXT"] == nil)
        #expect(invocation.environment["DOCKER_CONFIG"] == nil)
        #expect(invocation.environment["DOCKER_API_VERSION"] == nil)
        #expect(invocation.environment["NODE_OPTIONS"] == nil)
        #expect(invocation.environment["NODE_PATH"] == nil)
        #expect(invocation.environment["DEVCONTAINER_UNTRUSTED"] == nil)
    }

    @Test
    func `runtime override is rejected`() throws {
        let fixture = try InvocationFixture()
        defer { fixture.remove() }

        #expect(throws: Error.self) {
            try ReferenceCLIInvocation.configured(
                command: "up",
                arguments: ["--docker-path", "/usr/bin/docker"],
                injectRuntimeAdapters: true,
                environment: fixture.environment,
                executable: fixture.devcontainer
            )
        }
    }

    @Test
    func `node override cannot launch Docker tooling`() throws {
        let fixture = try InvocationFixture()
        defer { fixture.remove() }
        let docker = fixture.root.appendingPathComponent("docker")
        #expect(FileManager.default.createFile(atPath: docker.path, contents: Data()))
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o700],
            ofItemAtPath: docker.path
        )
        var environment = fixture.environment
        environment["DEVCONTAINER_NODE_BIN"] = docker.path

        #expect(throws: Error.self) {
            try ReferenceCLIInvocation.configured(
                command: "up",
                arguments: [],
                injectRuntimeAdapters: true,
                environment: environment,
                executable: fixture.devcontainer
            )
        }
    }

    @Test
    func `packaged CLI cannot be replaced by an environment override`() throws {
        let fixture = try InvocationFixture()
        defer { fixture.remove() }
        var environment = fixture.environment
        environment["DEVCONTAINER_REFERENCE_CLI"] = "/usr/local/bin/docker"

        let invocation = try ReferenceCLIInvocation.configured(
            command: "up",
            arguments: [],
            injectRuntimeAdapters: true,
            environment: environment,
            executable: fixture.devcontainer
        )

        #expect(invocation.arguments.first == fixture.script.path)
        #expect(invocation.environment["DEVCONTAINER_REFERENCE_CLI"] == nil)
    }

    @Test
    func `node override must resolve to an executable named node`() throws {
        let fixture = try InvocationFixture()
        defer { fixture.remove() }
        let runtime = fixture.root.appendingPathComponent("runtime")
        #expect(FileManager.default.createFile(atPath: runtime.path, contents: Data()))
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o700],
            ofItemAtPath: runtime.path
        )
        var environment = fixture.environment
        environment["DEVCONTAINER_NODE_BIN"] = runtime.path

        #expect(throws: Error.self) {
            try ReferenceCLIInvocation.configured(
                command: "up",
                arguments: [],
                injectRuntimeAdapters: true,
                environment: environment,
                executable: fixture.devcontainer
            )
        }
    }

    @Test
    func `exec payload may contain runtime-shaped arguments after separator`() throws {
        let fixture = try InvocationFixture()
        defer { fixture.remove() }

        let invocation = try ReferenceCLIInvocation.configured(
            command: "exec",
            arguments: [
                "--workspace-folder", "/work", "--", "tool",
                "--docker-path", "/payload/value"
            ],
            injectRuntimeAdapters: true,
            environment: fixture.environment,
            executable: fixture.devcontainer
        )

        #expect(invocation.arguments.suffix(3) == [
            "tool", "--docker-path", "/payload/value"
        ])
    }
}

private struct InvocationFixture {
    let root: URL
    let devcontainer: URL
    let docker: URL
    let compose: URL
    let node: URL
    let script: URL
    let socket: URL
    let configuration: URL
    let state: URL

    init() throws {
        root = URL(fileURLWithPath: "/tmp", isDirectory: true)
            .appendingPathComponent("dcref-\(UUID().uuidString.prefix(8))")
        let bin = root.appendingPathComponent("bin", isDirectory: true)
        let share = root.appendingPathComponent(
            "share/devcontainer/reference-cli",
            isDirectory: true
        )
        try FileManager.default.createDirectory(at: bin, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: share, withIntermediateDirectories: true)
        devcontainer = bin.appendingPathComponent("devcontainer")
        docker = bin.appendingPathComponent("devcontainer-docker")
        compose = bin.appendingPathComponent("devcontainer-compose")
        node = bin.appendingPathComponent("node")
        script = share.appendingPathComponent("devcontainer.js")
        socket = root.appendingPathComponent("engine.sock")
        configuration = root.appendingPathComponent("config.toml")
        state = root.appendingPathComponent("state.sqlite")
        for executable in [devcontainer, docker, compose, node] {
            #expect(FileManager.default.createFile(atPath: executable.path, contents: Data()))
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o700],
                ofItemAtPath: executable.path
            )
        }
        #expect(FileManager.default.createFile(atPath: script.path, contents: Data()))
    }

    var environment: [String: String] {
        [
            "DEVCONTAINER_NODE_BIN": node.path,
            "DEVCONTAINER_REFERENCE_CLI": script.path,
            "DEVCONTAINER_BACKEND": "stock",
            "DEVCONTAINER_COMPOSE_PROVIDER": "container-compose",
            "DEVCONTAINER_CONFIG": configuration.path,
            "DEVCONTAINER_CONTAINER_BIN": "/usr/local/bin/container",
            "DEVCONTAINER_SOCKET": socket.path,
            "DEVCONTAINER_STATE": state.path,
            "DEVCONTAINER_UNTRUSTED": "must-not-reach-child",
            "DOCKER_HOST": "unix:///tmp/fixture.sock",
            "DOCKER_CONTEXT": "desktop-linux",
            "DOCKER_CONFIG": "/tmp/docker-config",
            "DOCKER_API_VERSION": "1.24",
            "NODE_OPTIONS": "--require=/tmp/injected-node.js",
            "NODE_PATH": "/tmp/injected-node-modules",
            "HOME": "/tmp",
            "PATH": "/usr/bin:/bin",
            "LOCAL_ENV_FIXTURE": "preserved",
            "SSH_AUTH_SOCK": "/tmp/agent.sock",
            "DYLD_INSERT_LIBRARIES": "/tmp/injected.dylib",
            "BASH_ENV": "/tmp/injected-shell"
        ]
    }

    func remove() {
        try? FileManager.default.removeItem(at: root)
    }
}
