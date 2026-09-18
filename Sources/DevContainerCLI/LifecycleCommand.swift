// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ArgumentParser
import Darwin
import Foundation

/// Forward the reference CLI's options unchanged, including its exec terminator.
/// Backend paths belong to this installation, never to PATH or npm resolution.
protocol LifecycleForwardingCommand: ParsableCommand {
    static var verb: String { get }
    var arguments: [String] { get }
}

extension LifecycleForwardingCommand {
    static var configuration: CommandConfiguration {
        .init(commandName: verb, abstract: "Run \(verb) using the privately bundled Dev Containers CLI", helpNames: [])
    }

    func run() throws {
        guard let executable = Bundle.main.executableURL else {
            throw ValidationError("cannot locate the devcontainer installation")
        }
        let installation = try LifecycleInstallation(executable: executable)
        let invocation = try LifecycleInvocation(
            verb: Self.verb, arguments: arguments, installation: installation,
            environment: ProcessInfo.processInfo.environment
        )
        try invocation.replaceProcess()
    }
}

struct LifecycleUpCommand: LifecycleForwardingCommand {
    static let verb = "up"
    @Argument(parsing: .captureForPassthrough) var arguments: [String] = []
}

struct LifecycleBuildCommand: LifecycleForwardingCommand {
    static let verb = "build"
    @Argument(parsing: .captureForPassthrough) var arguments: [String] = []
}

struct LifecycleExecCommand: LifecycleForwardingCommand {
    static let verb = "exec"
    @Argument(parsing: .captureForPassthrough) var arguments: [String] = []
}

struct LifecycleReadConfigurationCommand: LifecycleForwardingCommand {
    static let verb = "read-configuration"
    @Argument(parsing: .captureForPassthrough) var arguments: [String] = []
}

struct LifecycleRunUserCommandsCommand: LifecycleForwardingCommand {
    static let verb = "run-user-commands"
    @Argument(parsing: .captureForPassthrough) var arguments: [String] = []
}

struct LifecycleInstallation: Sendable {
    let node: URL
    let cli: URL
    let docker: URL
    let compose: URL

    init(executable: URL) throws {
        let location = executable.resolvingSymlinksInPath().standardizedFileURL
        let suffix = "/libexec/container/plugins/devcontainer/bin/devcontainer"
        let prefix = location.path.hasSuffix(suffix)
            ? URL(fileURLWithPath: String(location.path.dropLast(suffix.count)), isDirectory: true)
            : location.deletingLastPathComponent().deletingLastPathComponent()
        node = prefix.appendingPathComponent("libexec/devcontainer/reference/node")
        cli = prefix.appendingPathComponent("libexec/devcontainer/reference/cli/devcontainer.js")
        docker = prefix.appendingPathComponent("bin/devcontainer-docker")
        compose = prefix.appendingPathComponent("bin/devcontainer-compose")
        for file in [node, cli, docker, compose] {
            let canonical = file.resolvingSymlinksInPath().standardizedFileURL
            let values = try? canonical.resourceValues(forKeys: [.isRegularFileKey])
            guard canonical.path.hasPrefix(prefix.path + "/"), values?.isRegularFile == true,
                  file == cli || FileManager.default.isExecutableFile(atPath: file.path)
            else {
                throw ValidationError(
                    "missing or unsafe private lifecycle asset: \(file.path); install a complete package"
                )
            }
        }
    }
}

struct LifecycleInvocation: Sendable {
    let executable: String
    let arguments: [String]
    let environment: [String: String]

    init(verb: String, arguments: [String], installation: LifecycleInstallation, environment: [String: String]) throws {
        let options = arguments.prefix { $0 != "--" }
        guard !options.contains(where: {
            ["--docker-path", "--docker-compose-path", "--dockerPath", "--dockerComposePath"]
                .contains($0.split(separator: "=", maxSplits: 1).first.map(String.init) ?? "")
        }), !arguments.contains(where: { $0.contains("\0") }) else {
            throw ValidationError(
                "lifecycle backend paths are installation-owned; do not override --docker-path or --docker-compose-path"
            )
        }
        executable = installation.node.path
        self.arguments = [
            installation.cli.path,
            verb,
            "--docker-path",
            installation.docker.path,
            "--docker-compose-path",
            installation.compose.path
        ] + arguments
        // Startup hooks could replace the pinned program before it can enforce
        // backend selection. Ordinary workspace/credential environment is kept.
        self.environment = environment.filter { key, _ in
            key != "NODE_OPTIONS" && key != "NODE_PATH" && !key.hasPrefix("DYLD_")
        }
    }

    func replaceProcess() throws {
        var argv = ([executable] + arguments).map { strdup($0) } + [nil]
        var env = environment.sorted { $0.key < $1.key }.map { strdup("\($0.key)=\($0.value)") } + [nil]
        defer { for value in argv {
            free(value)
        }; for value in env {
            free(value)
        } }
        let result = argv.withUnsafeMutableBufferPointer { args in
            env.withUnsafeMutableBufferPointer { environment in
                execve(executable, args.baseAddress!, environment.baseAddress!)
            }
        }
        guard result == -1 else { return }
        throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
    }
}
