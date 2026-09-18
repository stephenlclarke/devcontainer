// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Darwin
import DevContainerCore
import DevContainerDockerClient
import DevContainerModel
import Foundation

@main
enum DevContainerDockerCommand {
    static func main() async {
        do {
            let invocation = try DockerFrontendInvocation(
                arguments: Array(CommandLine.arguments.dropFirst()), environment: ProcessInfo.processInfo.environment
            )
            let frontend = DockerFrontend(version: BuildInfo.current.version)
            let output: Data
            if invocation.command == .clientVersion {
                output = frontend.versionOutput
            } else {
                let socket = try invocation.socketPath ?? DevContainerRuntimeSelectionResolver.resolve().socket
                let transport = try UnixDockerFrontendTransport(socketPath: socket)
                output = try await frontend.execute(invocation.command, transport: transport)
            }
            try FileHandle.standardOutput.write(contentsOf: output)
        } catch {
            try? FileHandle.standardError.write(contentsOf: Data("devcontainer-docker: \(error)\n".utf8))
            exit(1)
        }
    }
}
