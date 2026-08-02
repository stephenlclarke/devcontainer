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

import CryptoKit
import Darwin
import DevContainerComposeProvider
import DevContainerCore
import DevContainerModel
import DevContainerProcess
import DevContainerState
import Foundation

@main
enum DevContainerComposeCommand {
    private struct ChildCommandFailure: Error {
        let status: Int32
    }

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

    // Provider setup and coordinator ownership intentionally share one
    // top-level lifecycle.
    // swiftlint:disable:next function_body_length
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

        let result: Int32
        if let claim {
            let encodedArguments = Data(arguments.joined(separator: "\u{0}").utf8)
            let requestHash = digest(encodedArguments)
            if provider == .docker {
                _ = try await claim.coordinator.claim(
                    project: claim.key,
                    provider: claim.provider,
                    composeProject: claim.projectName,
                    projectDirectory: claim.projectDirectory,
                    configurationHash: requestHash
                )
                result = try await execute(
                    executable: child.executable,
                    arguments: child.arguments,
                    environment: child.environment
                )
            } else {
                do {
                    result = try await claim.coordinator.withMutation(
                        request: ProjectMutation(
                            project: claim.key,
                            provider: claim.provider,
                            composeProject: claim.projectName,
                            projectDirectory: claim.projectDirectory,
                            configurationHash: requestHash,
                            requestKind: "compose \(envelope.command ?? "unknown")",
                            requestHash: requestHash,
                            resourceKey: "compose-project:\(claim.projectName)"
                        ),
                        context: RuntimeRequestContext(
                            deadline: Date().addingTimeInterval(30 * 60)
                        )
                    ) { context in
                        try context.checkActive()
                        let status = try await execute(
                            executable: child.executable,
                            arguments: child.arguments,
                            environment: child.environment
                        )
                        guard status == 0 else {
                            throw ChildCommandFailure(status: status)
                        }
                        return status
                    }
                } catch let failure as ChildCommandFailure {
                    result = failure.status
                }
            }
        } else {
            result = try await execute(
                executable: child.executable,
                arguments: child.arguments,
                environment: child.environment
            )
        }
        if result == 0, envelope.removesProject, let claim {
            try await reconcileProjectRemoval(
                envelope: envelope,
                provider: provider,
                claim: claim,
                execution: ComposeExecutionEnvironment(
                    paths: paths,
                    environment: environment,
                    socket: configuration.socket
                )
            )
        }
        return result
    }

    private static func reconcileProjectRemoval(
        envelope: ComposeCommandEnvelope,
        provider: ComposeProviderKind,
        claim: ComposeProjectClaim,
        execution: ComposeExecutionEnvironment
    ) async throws {
        if provider == .docker {
            try await releaseProjectIfEmpty(claim)
            return
        }
        guard let liveVolumes = await liveContainerComposeVolumes(
            envelope: envelope,
            execution: execution
        ) else {
            return
        }
        let resources = try await claim.store.resources(project: claim.key)
        for resource in resources where !Self.isLiveVolume(resource, names: liveVolumes) {
            try await claim.store.removeResource(runtimeID: resource.runtimeID)
        }
        guard liveVolumes.isEmpty else {
            return
        }
        try await releaseProjectIfEmpty(claim)
    }

    private static func liveContainerComposeVolumes(
        envelope: ComposeCommandEnvelope,
        execution: ComposeExecutionEnvironment
    ) async -> Set<String>? {
        let child = childCommand(
            provider: .containerCompose,
            arguments: envelope.projectArguments + ["volumes", "--quiet"],
            paths: execution.paths,
            environment: execution.environment,
            socket: execution.socket
        )
        guard let result = try? await executeCaptured(
            executable: child.executable,
            arguments: child.arguments,
            environment: child.environment
        ), result.exitCode == 0 else {
            return nil
        }
        guard let output = String(bytes: result.standardOutput, encoding: .utf8) else {
            return nil
        }
        return Set(
            output.split(whereSeparator: \.isNewline)
                .map(String.init)
        )
    }

    private static func isLiveVolume(
        _ resource: ResourceRecord,
        names: Set<String>
    ) -> Bool {
        resource.runtimeKind == "volume"
            && (
                names.contains(resource.runtimeID.rawValue)
                    || names.contains(resource.logicalName)
            )
    }

    private static func releaseProjectIfEmpty(
        _ claim: ComposeProjectClaim
    ) async throws {
        guard try await claim.store.resources(project: claim.key).isEmpty else {
            return
        }
        do {
            try await claim.store.releaseProject(key: claim.key)
        } catch let error as DevContainerError where error.code == .conflict {
            // A concurrent Engine API mutation won the cross-process race and
            // attached a resource after the empty read. Its claim must survive.
        }
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
        return ComposeProjectClaim(
            key: projectKey,
            store: store,
            coordinator: ProjectCoordinator(store: store),
            provider: backend,
            projectName: projectName,
            projectDirectory: envelope.projectDirectory
                ?? FileManager.default.currentDirectoryPath
        )
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
        return try await ProcessRunner.inherited(
            executable: executable,
            arguments: arguments,
            environment: environment,
            workingDirectory: URL(
                fileURLWithPath: FileManager.default.currentDirectoryPath,
                isDirectory: true
            )
        )
    }

    private static func executeCaptured(
        executable: URL,
        arguments: [String],
        environment: [String: String]
    ) async throws -> CapturedProcessResult {
        try requireExecutable(executable)
        return try await ProcessRunner.captured(
            executable: executable,
            arguments: arguments,
            environment: environment,
            workingDirectory: URL(
                fileURLWithPath: FileManager.default.currentDirectoryPath,
                isDirectory: true
            )
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

    private static func digest(_ data: Data) -> String {
        SHA256.hash(data: data)
            .map { String(format: "%02x", $0) }
            .joined()
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
    let coordinator: ProjectCoordinator
    let provider: BackendProvider
    let projectName: String
    let projectDirectory: String
}

private struct ComposeConfiguration: Decodable {
    let name: String
}

private struct ComposeChildCommand {
    let executable: URL
    let arguments: [String]
    let environment: [String: String]
}

private struct ComposeExecutionEnvironment {
    let paths: Paths
    let environment: [String: String]
    let socket: String
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
