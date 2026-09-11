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
import DevContainerCore
import DevContainerModel
import DevContainerProcess
import Foundation

private let referenceAbstract = "Run the pinned Dev Containers CLI using Apple container"

protocol ReferenceCLICommand: AsyncParsableCommand {
    static var referenceCommand: String { get }
    static var injectRuntimeAdapters: Bool { get }
    var arguments: [String] { get }
}

extension ReferenceCLICommand {
    static var injectRuntimeAdapters: Bool {
        true
    }

    mutating func run() async throws {
        let invocation = try ReferenceCLIInvocation.configured(
            command: Self.referenceCommand,
            arguments: arguments,
            injectRuntimeAdapters: Self.injectRuntimeAdapters
        )
        let status = try await ProcessRunner.inherited(
            executable: invocation.node,
            arguments: invocation.arguments,
            environment: invocation.environment
        )
        guard status == 0 else {
            throw ExitCode(status)
        }
    }
}

struct ReferenceUpCommand: ReferenceCLICommand {
    static let referenceCommand = "up"
    static let configuration = commandConfiguration(referenceCommand)
    @Argument(parsing: .captureForPassthrough) var arguments: [String] = []
}

struct ReferenceSetUpCommand: ReferenceCLICommand {
    static let referenceCommand = "set-up"
    static let configuration = commandConfiguration(referenceCommand)
    @Argument(parsing: .captureForPassthrough) var arguments: [String] = []
}

struct ReferenceBuildCommand: ReferenceCLICommand {
    static let referenceCommand = "build"
    static let configuration = commandConfiguration(referenceCommand)
    @Argument(parsing: .captureForPassthrough) var arguments: [String] = []
}

struct ReferenceRunUserCommandsCommand: ReferenceCLICommand {
    static let referenceCommand = "run-user-commands"
    static let configuration = commandConfiguration(referenceCommand)
    @Argument(parsing: .captureForPassthrough) var arguments: [String] = []
}

struct ReferenceReadConfigurationCommand: ReferenceCLICommand {
    static let referenceCommand = "read-configuration"
    static let configuration = commandConfiguration(referenceCommand)
    @Argument(parsing: .captureForPassthrough) var arguments: [String] = []
}

struct ReferenceOutdatedCommand: ReferenceCLICommand {
    static let referenceCommand = "outdated"
    static let configuration = commandConfiguration(referenceCommand)
    @Argument(parsing: .captureForPassthrough) var arguments: [String] = []
}

struct ReferenceUpgradeCommand: ReferenceCLICommand {
    static let referenceCommand = "upgrade"
    static let configuration = commandConfiguration(referenceCommand)
    @Argument(parsing: .captureForPassthrough) var arguments: [String] = []
}

struct ReferenceFeaturesCommand: ReferenceCLICommand {
    static let referenceCommand = "features"
    static let injectRuntimeAdapters = false
    static let configuration = commandConfiguration(referenceCommand)
    @Argument(parsing: .captureForPassthrough) var arguments: [String] = []
}

struct ReferenceTemplatesCommand: ReferenceCLICommand {
    static let referenceCommand = "templates"
    static let injectRuntimeAdapters = false
    static let configuration = commandConfiguration(referenceCommand)
    @Argument(parsing: .captureForPassthrough) var arguments: [String] = []
}

struct ReferenceExecCommand: ReferenceCLICommand {
    static let referenceCommand = "exec"
    static let configuration = commandConfiguration(referenceCommand)
    @Argument(parsing: .captureForPassthrough) var arguments: [String] = []
}

private func commandConfiguration(_ name: String) -> CommandConfiguration {
    CommandConfiguration(commandName: name, abstract: referenceAbstract)
}

struct ReferenceCLIInvocation: Equatable {
    static let version = "0.89.0"

    var node: URL
    var arguments: [String]
    var environment: [String: String]

    static func configured(
        command: String,
        arguments: [String],
        injectRuntimeAdapters: Bool,
        environment: [String: String] = ProcessInfo.processInfo.environment,
        executable: URL? = Bundle.main.executableURL
    ) throws -> ReferenceCLIInvocation {
        let executable = try absoluteExecutable(executable)
        let directory = executable.deletingLastPathComponent()
        let script = try referenceScript(
            executableDirectory: directory
        )
        let node = try executablePath(
            environment["DEVCONTAINER_NODE_BIN"],
            candidates: [
                "/opt/homebrew/bin/node",
                "/usr/local/bin/node",
                "/usr/bin/node"
            ],
            name: "Node.js"
        )
        try requireNodeExecutable(node)
        var upstreamArguments = [script.path, command]
        if injectRuntimeAdapters {
            try rejectRuntimeOverrides(arguments)
            let docker = directory.appendingPathComponent("devcontainer-docker")
            let compose = directory.appendingPathComponent("devcontainer-compose")
            guard
                FileManager.default.isExecutableFile(atPath: docker.path),
                FileManager.default.isExecutableFile(atPath: compose.path)
            else {
                throw DevContainerError(
                    .runtimeUnavailable,
                    message: "packaged Apple runtime adapters are missing"
                )
            }
            upstreamArguments += [
                "--docker-path", docker.path,
                "--docker-compose-path", compose.path
            ]
        }
        upstreamArguments += arguments
        let selection = try DevContainerRuntimeSelectionResolver.resolve(
            environment: environment
        )
        let childEnvironment = configuredEnvironment(
            inherited: environment,
            selection: selection
        )
        return ReferenceCLIInvocation(
            node: node,
            arguments: upstreamArguments,
            environment: childEnvironment
        )
    }

    private static func configuredEnvironment(
        inherited: [String: String],
        selection: DevContainerRuntimeSelection
    ) -> [String: String] {
        var childEnvironment = safeEnvironment(inherited)
        childEnvironment["DEVCONTAINER_BACKEND"] = selection.backend.rawValue
        childEnvironment["DEVCONTAINER_COMPOSE_PROVIDER"] = selection.composeProvider.rawValue
        childEnvironment["DEVCONTAINER_CONFIG"] = selection.configuration.path
        childEnvironment["DEVCONTAINER_CONTAINER_BIN"] = selection.containerExecutable
        childEnvironment["DEVCONTAINER_SOCKET"] = selection.socket
        childEnvironment["DEVCONTAINER_STATE"] = selection.stateDatabase
        childEnvironment["DOCKER_HOST"] = "unix://\(selection.socket)"
        childEnvironment["DEVCONTAINER_REFERENCE_CLI_VERSION"] = version
        return childEnvironment
    }

    private static func absoluteExecutable(_ executable: URL?) throws -> URL {
        guard let executable, executable.path.hasPrefix("/") else {
            throw DevContainerError(
                .runtimeUnavailable,
                message: "could not locate the devcontainer executable"
            )
        }
        return executable.resolvingSymlinksInPath()
    }

    private static func referenceScript(executableDirectory: URL) throws -> URL {
        let packaged = executableDirectory
            .deletingLastPathComponent()
            .appendingPathComponent("share/devcontainer/reference-cli/devcontainer.js")
        return try executablePath(
            nil,
            candidates: [packaged.path],
            name: "pinned @devcontainers/cli (version)"
        )
    }

    private static func requireNodeExecutable(_ node: URL) throws {
        let resolved = node.resolvingSymlinksInPath()
        guard node.lastPathComponent == "node", resolved.lastPathComponent == "node" else {
            throw DevContainerError(
                .invalidRequest,
                message: "Node.js path and resolved target must be named node"
            )
        }
    }

    private static func executablePath(
        _ explicit: String?,
        candidates: [String],
        name: String
    ) throws -> URL {
        let values = explicit.map { [$0] } ?? candidates
        for value in values {
            let url = URL(fileURLWithPath: value).standardizedFileURL
            if value.hasPrefix("/"), FileManager.default.fileExists(atPath: url.path) {
                try DevContainerExecutablePolicy.requireDockerless(
                    url.path,
                    name: name
                )
                return url
            }
        }
        throw DevContainerError(
            .runtimeUnavailable,
            message: "\(name) is not installed in the packaged location"
        )
    }

    private static func rejectRuntimeOverrides(_ arguments: [String]) throws {
        let options = arguments.prefix { $0 != "--" }
        guard !options.contains(where: {
            $0 == "--docker-path" || $0.hasPrefix("--docker-path=")
                || $0 == "--docker-compose-path" || $0.hasPrefix("--docker-compose-path=")
        }) else {
            throw DevContainerError(
                .invalidRequest,
                message: "runtime path overrides are disabled; devcontainer always uses its Apple adapters"
            )
        }
    }

    private static func safeEnvironment(_ source: [String: String]) -> [String: String] {
        source.filter { key, _ in
            !key.hasPrefix("DYLD_")
                && !key.hasPrefix("LD_")
                && key != "BASH_ENV"
                && !key.hasPrefix("DOCKER_")
                && key != "ENV"
                && !key.hasPrefix("DEVCONTAINER_")
                && key != "NODE_OPTIONS"
                && key != "NODE_PATH"
        }
    }
}
