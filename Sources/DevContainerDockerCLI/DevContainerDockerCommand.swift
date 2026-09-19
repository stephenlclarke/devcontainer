// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Darwin
import DevContainerCore
import DevContainerDockerClient
import DevContainerModel
import Foundation

@main
enum DevContainerDockerCommand {
    static func main() async {
        // This process owns its signal disposition; inherited pipe/socket flags
        // remain unchanged, and disconnected consumers become write errors.
        signal(SIGPIPE, SIG_IGN)
        let signals = [SIGINT, SIGTERM].map { number in
            signal(number, SIG_IGN)
            return DispatchSource.makeSignalSource(signal: number, queue: .global())
        }
        let operation = Task { await execute() }
        for source in signals {
            source.setEventHandler { @Sendable in operation.cancel() }
            source.resume()
        }
        let status = await operation.value
        for source in signals {
            source.cancel()
        }
        exit(status)
    }

    private static func execute() async -> Int32 {
        do {
            let invocation = try DockerFrontendInvocation(
                arguments: Array(CommandLine.arguments.dropFirst()), environment: ProcessInfo.processInfo.environment
            )
            let frontend = DockerFrontend(version: BuildInfo.current.version)
            if case let .build(spec) = invocation.command {
                try await build(spec, invocation: invocation, frontend: frontend)
                return 0
            }
            if case let .events(spec) = invocation.command {
                try await events(spec, invocation: invocation, frontend: frontend)
                return 0
            }
            if case let .run(spec) = invocation.command {
                return try await run(spec, invocation: invocation, frontend: frontend)
            }
            if case let .exec(spec) = invocation.command {
                let socket = try invocation.socketPath ?? DevContainerRuntimeSelectionResolver.resolve().socket
                let transport = try UnixDockerFrontendTransport(socketPath: socket, timeoutSeconds: 86400)
                let input = try spec.interactive ? DockerFrontendInput() : nil
                let standardOutput = try DockerFrontendOutput(descriptor: STDOUT_FILENO)
                let standardError = try DockerFrontendOutput(descriptor: STDERR_FILENO)
                return try await frontend.executeExec(
                    spec,
                    transport: transport,
                    input: { try await input?.read() }, output: { frame in
                        let writer = frame.channel == .standardError ? standardError : standardOutput
                        try await writer.write(frame.data)
                    }
                )
            }
            let output: Data
            if invocation.command == .clientVersion {
                output = frontend.versionOutput
            } else {
                let socket = try invocation.socketPath ?? DevContainerRuntimeSelectionResolver.resolve().socket
                let transport = try UnixDockerFrontendTransport(socketPath: socket)
                output = try await frontend.execute(invocation.command, transport: transport)
            }
            try await emit(output, descriptor: STDOUT_FILENO, timeout: .seconds(30))
            return 0
        } catch {
            try? await emit(
                Data("devcontainer-docker: \(error)\n".utf8),
                descriptor: STDERR_FILENO,
                timeout: .seconds(5)
            )
            return 1
        }
    }

    private static func build(
        _ spec: DockerBuildCommand, invocation: DockerFrontendInvocation, frontend: DockerFrontend
    ) async throws {
        let archive = try await DockerBuildArchive.prepare(spec)
        let socket = try invocation.socketPath ?? DevContainerRuntimeSelectionResolver.resolve().socket
        let transport = try UnixDockerFrontendTransport(socketPath: socket, timeoutSeconds: 86400)
        try await frontend.executeBuild(
            spec, archive: archive, transport: transport,
            output: DockerFrontendOutput(descriptor: STDOUT_FILENO)
        )
    }

    private static func events(
        _ spec: DockerEventsCommand, invocation: DockerFrontendInvocation, frontend: DockerFrontend
    ) async throws {
        let socket = try invocation.socketPath ?? DevContainerRuntimeSelectionResolver.resolve().socket
        let transport = try UnixDockerFrontendTransport(socketPath: socket, timeoutSeconds: 86400)
        try await frontend.executeEvents(
            spec,
            transport: transport,
            output: DockerFrontendOutput(descriptor: STDOUT_FILENO)
        )
    }

    private static func run(
        _ spec: DockerRunCommand, invocation: DockerFrontendInvocation, frontend: DockerFrontend
    ) async throws -> Int32 {
        let socket = try invocation.socketPath ?? DevContainerRuntimeSelectionResolver.resolve().socket
        let transport = try UnixDockerFrontendTransport(socketPath: socket, timeoutSeconds: 86400)
        let standardOutput = try DockerFrontendOutput(descriptor: STDOUT_FILENO)
        let standardError = try DockerFrontendOutput(descriptor: STDERR_FILENO)
        return try await frontend.executeRun(spec, transport: transport, output: { frame in
            let writer = frame.channel == .standardError ? standardError : standardOutput
            try await writer.write(frame.data)
        }, warning: { message in
            try await emit(Data(("WARNING: " + message + "\n").utf8), descriptor: STDERR_FILENO, timeout: .seconds(5))
        })
    }

    private static func emit(_ data: Data, descriptor: Int32, timeout: Duration) async throws {
        let writer = try DockerFrontendOutput(descriptor: descriptor)
        try await withThrowingTaskGroup(of: Void.self) { group in
            defer { group.cancelAll() }
            group.addTask { try await writer.write(data) }
            group.addTask {
                try await Task.sleep(for: timeout)
                throw POSIXError(.ETIMEDOUT)
            }
            _ = try await group.next()
        }
    }
}
