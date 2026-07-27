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

import Darwin
import DevContainerComposeProvider
import DevContainerCore
import DevContainerModel
import DevContainerState
import Foundation

@main
enum DevContainerComposeCommand {
    static func main() async {
        do {
            let exitCode = try await run(arguments: Array(CommandLine.arguments.dropFirst()))
            exit(exitCode)
        } catch {
            FileHandle.standardError.write(Data("devcontainer-compose: \(error)\n".utf8))
            exit(1)
        }
    }

    static func run(arguments: [String]) async throws -> Int32 {
        let environment = ProcessInfo.processInfo.environment
        let paths = Paths(environment: environment)
        let configuration = try DevContainerConfigurationStore.load(
            from: paths.configuration,
            defaultSocket: paths.socket
        )
        let provider = environment["DEVCONTAINER_COMPOSE_PROVIDER"]
            .flatMap(ComposeProviderKind.init(rawValue:))
            ?? configuration.composeProvider
        let envelope = try ComposeCommandEnvelope(arguments: arguments)
        let projectName = envelope.projectName
            ?? environment["COMPOSE_PROJECT_NAME"]
            ?? URL(
                fileURLWithPath: envelope.projectDirectory
                    ?? FileManager.default.currentDirectoryPath,
                isDirectory: true
            ).lastPathComponent.lowercased()
        let projectKey = ProjectKey(rawValue: "\(getuid()):\(projectName)")
        let backend: BackendProvider = provider == .docker ? .stock : .containerCompose
        let store = try SQLiteStateStore(path: paths.state)

        if envelope.mutating {
            _ = try await store.claimProject(
                key: projectKey,
                provider: backend,
                composeProject: projectName,
                projectDirectory: envelope.projectDirectory
                    ?? FileManager.default.currentDirectoryPath,
                configurationHash: nil
            )
        }

        var childEnvironment = Self.safeChildEnvironment(environment)
        let executable: URL
        var childArguments: [String]
        switch provider {
        case .docker:
            executable = paths.docker
            childArguments = ["compose"] + arguments
            childEnvironment["DOCKER_HOST"] = "unix://\(configuration.socket)"
        case .containerCompose:
            executable = paths.containerCompose
            childArguments = arguments
        }
        if childArguments.isEmpty {
            childArguments = ["help"]
        }

        let result = try await execute(
            executable: executable,
            arguments: childArguments,
            environment: childEnvironment
        )
        if result == 0, envelope.command == "down" {
            try await store.releaseProject(key: projectKey)
        }
        return result
    }

    private static func execute(
        executable: URL,
        arguments: [String],
        environment: [String: String]
    ) async throws -> Int32 {
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            throw DevContainerError(
                .runtimeUnavailable,
                message: "required executable is missing at \(executable.path)"
            )
        }
        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
        process.environment = environment
        process.currentDirectoryURL = URL(
            fileURLWithPath: FileManager.default.currentDirectoryPath,
            isDirectory: true
        )
        process.standardInput = FileHandle.standardInput
        process.standardOutput = FileHandle.standardOutput
        process.standardError = FileHandle.standardError
        return await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                process.terminationHandler = { process in
                    continuation.resume(returning: process.terminationStatus)
                }
                do {
                    try process.run()
                } catch {
                    FileHandle.standardError.write(
                        Data("devcontainer-compose: cannot launch \(executable.path): \(error)\n".utf8)
                    )
                    continuation.resume(returning: 127)
                }
            }
        } onCancel: {
            if process.isRunning {
                process.terminate()
            }
        }
    }

    private static func safeChildEnvironment(
        _ environment: [String: String]
    ) -> [String: String] {
        environment.filter { key, _ in
            !key.hasPrefix("DYLD_")
                && !key.hasPrefix("LD_")
                && key != "BASH_ENV"
                && key != "ENV"
        }
    }
}

private struct Paths {
    let configuration: URL
    let state: URL
    let socket: String
    let docker: URL
    let containerCompose: URL

    init(environment: [String: String]) {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let configRoot = environment["XDG_CONFIG_HOME"]
            .map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? home.appendingPathComponent(".config", isDirectory: true)
        configuration = environment["DEVCONTAINER_CONFIG"]
            .map(URL.init(fileURLWithPath:))
            ?? configRoot
            .appendingPathComponent("devcontainer", isDirectory: true)
            .appendingPathComponent("config.toml")

        let support = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first ?? home
        state = environment["DEVCONTAINER_STATE"]
            .map(URL.init(fileURLWithPath:))
            ?? support
            .appendingPathComponent("devcontainer", isDirectory: true)
            .appendingPathComponent("state.sqlite")
        if let configured = environment["DEVCONTAINER_SOCKET"], !configured.isEmpty {
            socket = configured
        } else if let host = environment["DOCKER_HOST"], host.hasPrefix("unix://") {
            socket = String(host.dropFirst("unix://".count))
        } else {
            socket = FileManager.default.temporaryDirectory
                .appendingPathComponent("devcontainer", isDirectory: true)
                .appendingPathComponent("docker.sock")
                .path
        }
        docker = URL(
            fileURLWithPath: environment["DEVCONTAINER_DOCKER_BIN"]
                ?? Self.firstExecutable(["/opt/homebrew/bin/docker", "/usr/local/bin/docker"])
        )
        containerCompose = URL(
            fileURLWithPath: environment["DEVCONTAINER_COMPOSE_BIN"]
                ?? Self.firstExecutable([
                    "/opt/homebrew/bin/container-compose",
                    "/usr/local/bin/container-compose"
                ])
        )
    }

    private static func firstExecutable(_ candidates: [String]) -> String {
        candidates.first(where: FileManager.default.isExecutableFile(atPath:))
            ?? candidates[0]
    }
}
