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
    static let version = "0.88.0"

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
            environment: environment,
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
        var childEnvironment = safeEnvironment(environment)
        childEnvironment["DEVCONTAINER_REFERENCE_CLI_VERSION"] = version
        return ReferenceCLIInvocation(
            node: node,
            arguments: upstreamArguments,
            environment: childEnvironment
        )
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

    private static func referenceScript(
        environment: [String: String],
        executableDirectory: URL
    ) throws -> URL {
        let packaged = executableDirectory
            .deletingLastPathComponent()
            .appendingPathComponent("share/devcontainer/reference-cli/devcontainer.js")
        return try executablePath(
            environment["DEVCONTAINER_REFERENCE_CLI"],
            candidates: [packaged.path],
            name: "pinned @devcontainers/cli (version)"
        )
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
                return url
            }
        }
        throw DevContainerError(
            .runtimeUnavailable,
            message: "\(name) is not installed in the packaged location"
        )
    }

    private static func rejectRuntimeOverrides(_ arguments: [String]) throws {
        guard !arguments.contains(where: {
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
        Dictionary(uniqueKeysWithValues: [
            "CONTAINER_APP_ROOT",
            "CONTAINER_HOST",
            "CONTAINER_INSTALL_ROOT",
            "CONTAINER_SERVICE_NAMESPACE",
            "DEVCONTAINER_CONFIG",
            "DEVCONTAINER_SOCKET",
            "DEVCONTAINER_STATE",
            "DOCKER_HOST",
            "HOME",
            "LANG",
            "LC_ALL",
            "PATH",
            "TMPDIR",
            "XDG_CACHE_HOME",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME"
        ].compactMap { key in
            source[key].map { (key, $0) }
        })
    }
}
