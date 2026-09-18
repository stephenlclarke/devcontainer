// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ArgumentParser
@testable import DevContainerCLI
import DevContainerModel
import DevContainerProcess
import DevContainerTestStorage
import Foundation
import Testing

struct LifecycleCommandTests {
    @Test(arguments: ["up", "build", "exec", "read-configuration", "run-user-commands"])
    func `public lifecycle verbs preserve reference flags terminators and help`(_ verb: String) throws {
        let input = ["--workspace-folder", "/work space", "--", "sh", "-c", "echo $HOME"]
        let command = try #require(try DevContainerCommand
            .parseAsRoot([verb] + input) as? any LifecycleForwardingCommand)
        #expect(command.arguments == input)
        let help = try #require(try DevContainerCommand.parseAsRoot([
            verb,
            "--help"
        ]) as? any LifecycleForwardingCommand)
        #expect(help.arguments == ["--help"])
    }

    @Test
    func `lifecycle invocation uses exact installed paths and removes Node startup hooks`() throws {
        let fixture = try LifecycleFixture()
        defer { fixture.remove() }
        let installation = try LifecycleInstallation(executable: fixture.executable)
        let args = ["--workspace-folder", "/work", "--", "echo", "--docker-path", "literal"]
        let invocation = try LifecycleInvocation(
            verb: "exec", arguments: args, installation: installation,
            environment: [
                "NODE_OPTIONS": "--require=bad",
                "NODE_PATH": "/bad",
                "DYLD_INSERT_LIBRARIES": "/bad",
                "PATH": "/no-clients",
                "HOME": "/home",
                "DOCKER_HOST": "unix:///selected.sock"
            ]
        )
        #expect(invocation.executable == installation.node.path)
        #expect(invocation.arguments == [
            installation.cli.path,
            "exec",
            "--docker-path",
            installation.docker.path,
            "--docker-compose-path",
            installation.compose.path
        ] + args)
        #expect(invocation.environment == [
            "PATH": "/no-clients",
            "HOME": "/home",
            "DOCKER_HOST": "unix:///selected.sock"
        ])
    }

    @Test(arguments: [
        ["--docker-path", "/wrong"], ["--docker-path=/wrong"], ["--docker-compose-path", "wrong"],
        ["--docker-compose-path=wrong"], ["--dockerPath", "/wrong"], ["--dockerPath=/wrong"],
        ["--dockerComposePath", "/wrong"], ["--dockerComposePath=/wrong"], ["--", "bad\0"]
    ])
    func `backend overrides and NUL arguments fail before executing Node`(_ arguments: [String]) throws {
        let fixture = try LifecycleFixture()
        defer { fixture.remove() }
        let installation = try LifecycleInstallation(executable: fixture.executable)
        #expect(throws: ValidationError.self) {
            try LifecycleInvocation(verb: "up", arguments: arguments, installation: installation, environment: [:])
        }
    }

    @Test
    func `plugin and symlink entry points resolve the same private installation`() throws {
        let fixture = try LifecycleFixture()
        defer { fixture.remove() }
        let plugin = fixture.root.appendingPathComponent("libexec/container/plugins/devcontainer/bin/devcontainer")
        let direct = try LifecycleInstallation(executable: fixture.executable)
        #expect(try LifecycleInstallation(executable: plugin).node == direct.node)
        let alias = fixture.root.appendingPathComponent("alias")
        try FileManager.default.createSymbolicLink(at: alias, withDestinationURL: fixture.executable)
        #expect(try LifecycleInstallation(executable: alias).cli == direct.cli)
    }

    @Test(arguments: [
        "libexec/devcontainer/reference/node",
        "libexec/devcontainer/reference/cli/devcontainer.js",
        "bin/devcontainer-docker",
        "bin/devcontainer-compose"
    ])
    func `incomplete installation fails without global executable fallback`(_ missing: String) throws {
        let fixture = try LifecycleFixture()
        defer { fixture.remove() }
        try FileManager.default.removeItem(at: fixture.root.appendingPathComponent(missing))
        #expect(throws: ValidationError.self) { try LifecycleInstallation(executable: fixture.executable) }
    }

    @Test
    func `installation rejects nonexecutable files and escaped symlinks`() throws {
        let fixture = try LifecycleFixture()
        defer { fixture.remove() }
        let node = fixture.root.appendingPathComponent("libexec/devcontainer/reference/node")
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: node.path)
        #expect(throws: ValidationError.self) { try LifecycleInstallation(executable: fixture.executable) }
        try FileManager.default.removeItem(at: node)
        try FileManager.default.createSymbolicLink(at: node, withDestinationURL: URL(fileURLWithPath: "/bin/sh"))
        #expect(throws: ValidationError.self) { try LifecycleInstallation(executable: fixture.executable) }
    }

    @Test
    func `failed process replacement reports an error rather than falling back`() throws {
        let fixture = try LifecycleFixture()
        defer { fixture.remove() }
        let installation = try LifecycleInstallation(executable: fixture.executable)
        let invocation = try LifecycleInvocation(
            verb: "up",
            arguments: [],
            installation: installation,
            environment: [:]
        )
        try FileManager.default.removeItem(at: installation.node)
        #expect(throws: POSIXError.self) { try invocation.replaceProcess() }
        // The test runner is not a packaged installation and must not discover npm.
        #expect(throws: ValidationError.self) { try LifecycleUpCommand.parse([]).run() }
    }

    @Test
    func `actual public executable replaces itself preserving input output arguments and exit`() async throws {
        let environment = ProcessInfo.processInfo.environment
        let built: URL = if let relative = environment["DEVCONTAINER_LIFECYCLE_TEST_RUNFILE"],
                            let directory = environment["TEST_SRCDIR"], let workspace = environment["TEST_WORKSPACE"]
        {
            URL(fileURLWithPath: directory).appendingPathComponent(workspace).appendingPathComponent(relative)
        } else {
            Bundle(for: LifecycleTestBundle.self).bundleURL.deletingLastPathComponent()
                .appendingPathComponent("devcontainer")
        }
        let fixture = try LifecycleFixture()
        defer { fixture.remove() }
        try FileManager.default.removeItem(at: fixture.executable)
        // Bazel runfiles are symlinks. Copy the actual binary, not an alias back
        // into the build tree, so this is a genuine relocated installation.
        try FileManager.default.copyItem(at: built.resolvingSymlinksInPath(), to: fixture.executable)
        let node = fixture.root.appendingPathComponent("libexec/devcontainer/reference/node")
        let script = """
        #!/bin/sh
        printf '%s\\n' "$@"
        read -r input
        printf 'input=%s\\n' "$input"
        printf 'fixture stderr\\n' >&2
        exit 7

        """
        try Data(script.utf8).write(to: node)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: node.path)
        let result = try await RuntimeRequestScope.$context
            .withValue(RuntimeRequestContext(deadline: Date().addingTimeInterval(5))) {
                try await ProcessRunner.captured(
                    executable: fixture.executable, arguments: [
                        "exec",
                        "--workspace-folder",
                        "/work space",
                        "--",
                        "echo",
                        "hello"
                    ],
                    environment: ["PATH": "/no-clients", "NODE_OPTIONS": "--require=/must-not-run"],
                    input: Data("hello input\n".utf8), maximumOutputBytes: 16384
                )
            }
        let output = try #require(String(data: result.standardOutput, encoding: .utf8))
        #expect(
            result.exitCode == 7,
            Comment(rawValue: String(data: result.standardError, encoding: .utf8) ?? "invalid stderr")
        )
        #expect(output
            .contains("exec\n--docker-path\n" + fixture.root.appendingPathComponent("bin/devcontainer-docker").path))
        #expect(output.hasSuffix("--workspace-folder\n/work space\n--\necho\nhello\ninput=hello input\n"))
        #expect(result.standardError == Data("fixture stderr\n".utf8))
    }
}

private final class LifecycleTestBundle: NSObject {}

private struct LifecycleFixture {
    let root: URL
    var executable: URL {
        root.appendingPathComponent("bin/devcontainer")
    }

    init() throws {
        root = TestStorage.temporaryDirectory.appendingPathComponent("lifecycle-\(UUID().uuidString)")
        for name in [
            "bin/devcontainer",
            "bin/devcontainer-docker",
            "bin/devcontainer-compose",
            "libexec/devcontainer/reference/node",
            "libexec/devcontainer/reference/cli/devcontainer.js"
        ] {
            let file = root.appendingPathComponent(name)
            try FileManager.default.createDirectory(
                at: file.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            try Data("fixture".utf8).write(to: file)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: file.path)
        }
    }

    func remove() {
        try? FileManager.default.removeItem(at: root)
    }
}
