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
    static func requiresRuntimeAdapterAliases(arguments: [String]) -> Bool
    var arguments: [String] { get }
}

extension ReferenceCLICommand {
    static var injectRuntimeAdapters: Bool {
        true
    }

    static func requiresRuntimeAdapterAliases(arguments _: [String]) -> Bool {
        false
    }

    mutating func run() async throws {
        let invocation = try ReferenceCLIInvocation.configured(
            command: Self.referenceCommand,
            arguments: arguments,
            injectRuntimeAdapters: Self.injectRuntimeAdapters,
            injectRuntimeAdapterAliases: Self.requiresRuntimeAdapterAliases(
                arguments: arguments
            )
        )
        defer { invocation.removeRuntimeAdapterAliases() }
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

    static func requiresRuntimeAdapterAliases(arguments: [String]) -> Bool {
        var index = 0
        while index < arguments.count {
            let argument = arguments[index]
            if argument == "--" {
                return false
            }
            if argument == "--allow-cross-origin-auth-host" {
                index += 2
                continue
            }
            if argument.hasPrefix("-") {
                index += 1
                continue
            }
            return argument == "test"
        }
        return false
    }
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
    var runtimeAdapterAliasDirectory: URL?

    static func configured(
        command: String,
        arguments: [String],
        injectRuntimeAdapters: Bool,
        injectRuntimeAdapterAliases: Bool = false,
        environment: [String: String] = ProcessInfo.processInfo.environment,
        executable: URL? = Bundle.main.executableURL
    ) throws -> ReferenceCLIInvocation {
        let executable = try absoluteExecutable(executable)
        let directory = executable.deletingLastPathComponent()
        let layout = try installationLayout(executableDirectory: directory)
        let script = layout.script
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
            upstreamArguments += try runtimeAdapterArguments(
                adapters: layout,
                userArguments: arguments
            )
        }
        upstreamArguments += arguments
        let selection = try DevContainerRuntimeSelectionResolver.resolve(
            environment: environment
        )
        var childEnvironment = configuredEnvironment(
            inherited: environment,
            selection: selection
        )
        let aliasDirectory = if injectRuntimeAdapterAliases {
            try runtimeAdapterAliasDirectory(adapters: layout)
        } else {
            URL?.none
        }
        if let aliasDirectory {
            childEnvironment["PATH"] = [
                aliasDirectory.path, "/usr/bin", "/bin", "/usr/sbin", "/sbin"
            ].joined(separator: ":")
        }
        return ReferenceCLIInvocation(
            node: node,
            arguments: upstreamArguments,
            environment: childEnvironment,
            runtimeAdapterAliasDirectory: aliasDirectory
        )
    }

    func removeRuntimeAdapterAliases() {
        guard let runtimeAdapterAliasDirectory else { return }
        try? FileManager.default.removeItem(at: runtimeAdapterAliasDirectory)
    }

    private static func runtimeAdapterArguments(
        adapters: InstallationLayout,
        userArguments: [String]
    ) throws -> [String] {
        try rejectRuntimeOverrides(userArguments)
        return [
            "--docker-path", adapters.docker.path,
            "--docker-compose-path", adapters.compose.path
        ]
    }

    private static func runtimeAdapterAliasDirectory(
        adapters: InstallationLayout
    ) throws -> URL {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("devcontainer-runtime-adapters-\(UUID().uuidString)")
        do {
            try FileManager.default.createDirectory(
                at: directory,
                withIntermediateDirectories: false,
                attributes: [.posixPermissions: 0o700]
            )
            try FileManager.default.createSymbolicLink(
                at: directory.appendingPathComponent("docker"),
                withDestinationURL: adapters.docker
            )
            try FileManager.default.createSymbolicLink(
                at: directory.appendingPathComponent("docker-compose"),
                withDestinationURL: adapters.compose
            )
            return directory
        } catch {
            try? FileManager.default.removeItem(at: directory)
            throw error
        }
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

    private static func installationLayout(
        executableDirectory: URL
    ) throws -> InstallationLayout {
        var candidates: [InstallationLayout] = []
        let developmentRoot = executableDirectory.deletingLastPathComponent()
        candidates.append(InstallationLayout(
            script: developmentRoot.appendingPathComponent(
                "share/devcontainer/reference-cli/devcontainer.js"
            ),
            docker: executableDirectory.appendingPathComponent("devcontainer-docker"),
            compose: executableDirectory.appendingPathComponent("devcontainer-compose")
        ))
        var root = executableDirectory
        for _ in 0 ..< 8 {
            candidates.append(InstallationLayout(
                script: root.appendingPathComponent(
                    "share/devcontainer/reference-cli/devcontainer.js"
                ),
                docker: root.appendingPathComponent("bin/devcontainer-docker"),
                compose: root.appendingPathComponent("bin/devcontainer-compose")
            ))
            let parent = root.deletingLastPathComponent()
            if parent.path == root.path {
                break
            }
            root = parent
        }
        for candidate in candidates where candidate.exists {
            try DevContainerExecutablePolicy.requireDockerless(
                candidate.script.path,
                name: "pinned @devcontainers/cli (version)"
            )
            try DevContainerExecutablePolicy.requireDockerless(
                candidate.docker.path,
                name: "packaged Apple runtime adapter"
            )
            try DevContainerExecutablePolicy.requireDockerless(
                candidate.compose.path,
                name: "packaged native Compose adapter"
            )
            return candidate
        }
        throw DevContainerError(
            .runtimeUnavailable,
            message: "packaged Dev Containers CLI and Apple runtime adapters are missing"
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

private struct InstallationLayout {
    let script: URL
    let docker: URL
    let compose: URL

    var exists: Bool {
        FileManager.default.isReadableFile(atPath: script.path)
            && FileManager.default.isExecutableFile(atPath: docker.path)
            && FileManager.default.isExecutableFile(atPath: compose.path)
    }
}
