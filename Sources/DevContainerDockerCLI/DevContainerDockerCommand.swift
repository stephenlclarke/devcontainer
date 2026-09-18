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
        do {
            let invocation = try DockerFrontendInvocation(
                arguments: Array(CommandLine.arguments.dropFirst()), environment: ProcessInfo.processInfo.environment
            )
            let frontend = DockerFrontend(version: BuildInfo.current.version)
            if case let .exec(spec) = invocation.command {
                let socket = try invocation.socketPath ?? DevContainerRuntimeSelectionResolver.resolve().socket
                let transport = try UnixDockerFrontendTransport(socketPath: socket, timeoutSeconds: 86400)
                let input = try spec.interactive ? DockerFrontendInput() : nil
                let standardOutput = try DockerFrontendOutput(descriptor: STDOUT_FILENO)
                let standardError = try DockerFrontendOutput(descriptor: STDERR_FILENO)
                let status = try await frontend.executeExec(
                    spec,
                    transport: transport,
                    input: { try await input?.read() }, output: { frame in
                        let writer = frame.channel == .standardError ? standardError : standardOutput
                        try await writer.write(frame.data)
                    }
                )
                exit(status)
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
        } catch {
            try? await emit(
                Data("devcontainer-docker: \(error)\n".utf8),
                descriptor: STDERR_FILENO,
                timeout: .seconds(5)
            )
            exit(1)
        }
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
