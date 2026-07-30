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
        try await run(
            arguments: arguments,
            environment: ProcessInfo.processInfo.environment
        )
    }

    static func run(
        arguments: [String],
        environment: [String: String]
    ) async throws -> Int32 {
        let paths = Paths(environment: environment)
        let configuration = try DevContainerConfigurationStore.load(
            from: paths.configuration,
            defaultSocket: paths.socket
        )
        let provider = environment["DEVCONTAINER_COMPOSE_PROVIDER"]
            .flatMap(ComposeProviderKind.init(rawValue:))
            ?? configuration.composeProvider
        let envelope = try ComposeCommandEnvelope(arguments: arguments)
        let child = childCommand(
            provider: provider,
            arguments: arguments,
            paths: paths,
            environment: environment,
            socket: configuration.socket
        )
        let claim = try await claimIfNeeded(
            envelope: envelope,
            provider: provider,
            paths: paths,
            environment: environment,
            socket: configuration.socket
        )

        let result = try await execute(
            executable: child.executable,
            arguments: child.arguments,
            environment: child.environment
        )
        if result == 0, envelope.removesProject, let claim {
            try await claim.store.releaseProject(key: claim.key)
        }
        return result
    }

    private static func claimIfNeeded(
        envelope: ComposeCommandEnvelope,
        provider: ComposeProviderKind,
        paths: Paths,
        environment: [String: String],
        socket: String
    ) async throws -> ComposeProjectClaim? {
        guard envelope.mutating else {
            return nil
        }
        let projectName = try await resolvedProjectName(
            envelope: envelope,
            provider: provider,
            paths: paths,
            environment: environment,
            socket: socket
        )
        let projectKey = ProjectKey(rawValue: "\(getuid()):\(projectName)")
        let backend: BackendProvider = provider == .docker ? .stock : .containerCompose
        let store = try SQLiteStateStore(path: paths.state)
        _ = try await store.claimProject(
            key: projectKey,
            provider: backend,
            composeProject: projectName,
            projectDirectory: envelope.projectDirectory
                ?? FileManager.default.currentDirectoryPath,
            configurationHash: nil
        )
        return ComposeProjectClaim(key: projectKey, store: store)
    }

    private static func resolvedProjectName(
        envelope: ComposeCommandEnvelope,
        provider: ComposeProviderKind,
        paths: Paths,
        environment: [String: String],
        socket: String
    ) async throws -> String {
        if let explicit = envelope.projectName ?? environment["COMPOSE_PROJECT_NAME"] {
            return try validatedProjectName(explicit)
        }
        let child = childCommand(
            provider: provider,
            arguments: envelope.configurationArguments,
            paths: paths,
            environment: environment,
            socket: socket
        )
        let result = try await executeCaptured(
            executable: child.executable,
            arguments: child.arguments,
            environment: child.environment
        )
        guard result.exitCode == 0 else {
            throw DevContainerError(
                .invalidRequest,
                message: "cannot resolve Compose project name: \(boundedError(result.standardError))"
            )
        }
        let configuration: ComposeConfiguration
        do {
            configuration = try JSONDecoder().decode(
                ComposeConfiguration.self,
                from: result.standardOutput
            )
        } catch {
            throw DevContainerError(
                .providerProtocolMismatch,
                message: "Compose config returned invalid project JSON: \(error)"
            )
        }
        return try validatedProjectName(configuration.name)
    }

    private static func validatedProjectName(_ value: String) throws -> String {
        let scalars = value.unicodeScalars
        let validFirst = scalars.first.map {
            (97 ... 122).contains($0.value) || (48 ... 57).contains($0.value)
        } ?? false
        let validBody = scalars.allSatisfy {
            (97 ... 122).contains($0.value)
                || (48 ... 57).contains($0.value)
                || $0 == "-"
                || $0 == "_"
        }
        guard validFirst, validBody else {
            throw DevContainerError(
                .invalidRequest,
                message: "invalid Compose project name \(value)"
            )
        }
        return value
    }

    private static func childCommand(
        provider: ComposeProviderKind,
        arguments: [String],
        paths: Paths,
        environment: [String: String],
        socket: String
    ) -> ComposeChildCommand {
        var childEnvironment = safeChildEnvironment(environment)
        var childArguments = arguments
        let executable: URL
        switch provider {
        case .docker:
            let command = DockerComposeCommand(
                arguments: arguments,
                docker: paths.docker,
                standaloneCompose: paths.dockerCompose
            )
            executable = command.executable
            childArguments = command.arguments
            childEnvironment["DOCKER_HOST"] = "unix://\(socket)"
        case .containerCompose:
            executable = paths.containerCompose
        }
        if childArguments.isEmpty {
            childArguments = ["help"]
        }
        return ComposeChildCommand(
            executable: executable,
            arguments: childArguments,
            environment: childEnvironment
        )
    }

    private static func execute(
        executable: URL,
        arguments: [String],
        environment: [String: String]
    ) async throws -> Int32 {
        try requireExecutable(executable)
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
            await run(process, executable: executable)
        } onCancel: {
            if process.isRunning {
                process.terminate()
            }
        }
    }

    private static func executeCaptured(
        executable: URL,
        arguments: [String],
        environment: [String: String]
    ) async throws -> CapturedProcessResult {
        try requireExecutable(executable)
        let process = Process()
        let standardOutput = Pipe()
        let standardError = Pipe()
        process.executableURL = executable
        process.arguments = arguments
        process.environment = environment
        process.currentDirectoryURL = URL(
            fileURLWithPath: FileManager.default.currentDirectoryPath,
            isDirectory: true
        )
        process.standardOutput = standardOutput
        process.standardError = standardError
        let (termination, continuation) = AsyncStream<Int32>.makeStream()
        process.terminationHandler = { process in
            continuation.yield(process.terminationStatus)
            continuation.finish()
        }
        do {
            try process.run()
        } catch {
            continuation.finish()
            throw DevContainerError(
                .runtimeUnavailable,
                message: "cannot launch \(executable.path): \(error)"
            )
        }
        let outputTask = Task.detached {
            standardOutput.fileHandleForReading.readDataToEndOfFile()
        }
        let errorTask = Task.detached {
            standardError.fileHandleForReading.readDataToEndOfFile()
        }
        let exitCode = await withTaskCancellationHandler {
            for await status in termination {
                return status
            }
            return 255
        } onCancel: {
            if process.isRunning {
                process.terminate()
            }
        }
        return await CapturedProcessResult(
            standardOutput: outputTask.value,
            standardError: errorTask.value,
            exitCode: exitCode
        )
    }

    private static func requireExecutable(_ executable: URL) throws {
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            throw DevContainerError(
                .runtimeUnavailable,
                message: "required executable is missing at \(executable.path)"
            )
        }
    }

    private static func boundedError(_ data: Data) -> String {
        let message = String(bytes: data.prefix(4096), encoding: .utf8)
            ?? "non-UTF-8 diagnostic output"
        return message.isEmpty ? "no diagnostic output" : message
    }

    private static func run(_ process: Process, executable: URL) async -> Int32 {
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

private struct ComposeProjectClaim {
    let key: ProjectKey
    let store: SQLiteStateStore
}

private struct ComposeConfiguration: Decodable {
    let name: String
}

private struct CapturedProcessResult {
    let standardOutput: Data
    let standardError: Data
    let exitCode: Int32
}

private struct ComposeChildCommand {
    let executable: URL
    let arguments: [String]
    let environment: [String: String]
}

private struct Paths {
    let configuration: URL
    let state: URL
    let socket: String
    let docker: URL
    let dockerCompose: URL?
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
        if let configured = environment["DEVCONTAINER_DOCKER_COMPOSE_BIN"] {
            dockerCompose = configured.isEmpty
                ? nil
                : URL(fileURLWithPath: configured)
        } else {
            dockerCompose = Self.firstExecutableURL([
                "/opt/homebrew/bin/docker-compose",
                "/usr/local/bin/docker-compose"
            ])
        }
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

    private static func firstExecutableURL(_ candidates: [String]) -> URL? {
        candidates.first(where: FileManager.default.isExecutableFile(atPath:))
            .map(URL.init(fileURLWithPath:))
    }
}
