// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineWire
import ContainerUnixHTTPServer
import DevContainerDockerClient
import DevContainerModel
import DevContainerProcess
import DevContainerTestStorage
import Foundation
import Logging
import Testing

struct DockerFrontendSocketTests {
    @Test
    func `frontend reaches the selected private socket and preserves server failures`() async throws {
        let root = TestStorage.temporaryDirectory.appendingPathComponent("df-\(UUID().uuidString.prefix(8))")
        try FileManager.default.createDirectory(
            at: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700]
        )
        defer { try? FileManager.default.removeItem(at: root) }
        let socket = root.appendingPathComponent("engine.sock").path
        let server = ContainerUnixHTTPServer(
            responder: FrontendTestResponder(), socketPath: socket, logger: Logger(label: "frontend-test")
        )
        try await server.start()
        do {
            let frontend = DockerFrontend(version: "1.2.3")
            let transport = try UnixDockerFrontendTransport(socketPath: socket)
            let output = try await frontend.execute(.version(format: "{{.Server.Version}}"), transport: transport)
            #expect(output == Data("selected-test-engine\n".utf8))
            let executableOutput = try await FrontendExecutable.run(
                ["version", "--format", "{{.Server.Version}}"], socket: socket
            )
            #expect(executableOutput.exitCode == 0)
            #expect(executableOutput.standardOutput == output)
            #expect(executableOutput.standardError.isEmpty)
            await #expect(throws: (any Error).self) {
                try await frontend.execute(.inspect(kind: "image", name: "absent"), transport: transport)
            }
            try await server.shutdown()
        } catch {
            try await server.shutdown()
            throw error
        }
        #expect(!FileManager.default.fileExists(atPath: socket))
    }

    @Test
    func `executable reports its own version and rejects Buildx without Docker on PATH`() async throws {
        let version = try await FrontendExecutable.run(["-v"])
        #expect(version.exitCode == 0)
        let banner = String(data: version.standardOutput, encoding: .utf8)
        #expect(banner?.hasPrefix("devcontainer-docker version ") == true)
        #expect(version.standardError.isEmpty)
        let buildx = try await FrontendExecutable.run(["buildx", "version"])
        #expect(buildx.exitCode == 1)
        #expect(buildx.standardOutput.isEmpty)
        #expect(String(data: buildx.standardError, encoding: .utf8)?.contains("unsupported") == true)
        let remote = try await FrontendExecutable.run(["--host", "tcp://localhost:2375", "version"])
        #expect(remote.exitCode == 1)
        #expect(remote.standardOutput.isEmpty)
    }
}

private enum FrontendExecutable {
    static func run(_ arguments: [String], socket: String? = nil) async throws -> CapturedProcessResult {
        let environment = ProcessInfo.processInfo.environment
        let executable: URL = if let runfile = environment["DEVCONTAINER_DOCKER_TEST_RUNFILE"],
                                 let directory = environment["TEST_SRCDIR"],
                                 let workspace = environment["TEST_WORKSPACE"]
        {
            URL(fileURLWithPath: directory).appendingPathComponent(workspace).appendingPathComponent(runfile)
        } else {
            Bundle(for: FrontendTestBundle.self).bundleURL.deletingLastPathComponent()
                .appendingPathComponent("devcontainer-docker")
        }
        var childEnvironment = ["PATH": "/no-external-clients", "DEVCONTAINER_CONFIG": "/no-config"]
        if let socket {
            childEnvironment["DOCKER_HOST"] = "unix://\(socket)"
        }
        return try await RuntimeRequestScope.$context.withValue(
            RuntimeRequestContext(deadline: Date().addingTimeInterval(5))
        ) {
            try await ProcessRunner.captured(
                executable: executable, arguments: arguments,
                environment: childEnvironment, maximumOutputBytes: 65536
            )
        }
    }
}

private final class FrontendTestBundle: NSObject {}

private struct FrontendTestResponder: DockerHTTPResponder {
    func respond(to request: DockerHTTPRequest) async -> DockerHTTPResponse {
        if request.method == .get, request.target == "/version" {
            return .text(#"{"Version":"selected-test-engine"}"#, contentType: "application/json")
        }
        return .text(#"{"message":"image not found"}"#, status: 404, contentType: "application/json")
    }
}
