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

import ContainerUnixHTTPServer
import DevContainerDockerAPI
@testable import DevContainerDockerCLI
import DevContainerModel
import DevContainerTestSupport
import Foundation
import Logging
import Testing

@Suite("Docker CLI Unix socket integration", .serialized)
struct DockerCLIUnixSocketIntegrationTests {
    @Test
    // swiftlint:disable:next function_body_length
    func `uses the real HTTP server for fixed and chunked responses`() async throws {
        let root = URL(fileURLWithPath: "/tmp", isDirectory: true)
            .appendingPathComponent("dccli-\(UUID().uuidString.prefix(8))")
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700]
        )
        defer { try? FileManager.default.removeItem(at: root) }

        let socket = root.appendingPathComponent("engine.sock").path
        let runtime = InMemoryRuntime(version: "1.4.1")
        let server = ContainerUnixHTTPServer(
            responder: DockerRouter(runtime: runtime),
            socketPath: socket,
            logger: Logger(label: "DockerCLIUnixSocketIntegrationTests")
        )
        try await server.start()
        do {
            let application = try DockerCLIApplication(
                transport: UnixSocketDockerTransport(socketPath: socket)
            )
            let version = try await Task.detached {
                try application.run(arguments: ["version", "--format", "{{.Server.Version}}"])
            }.value
            #expect(version.standardOutput == Data("1.4.1\n".utf8))

            let pull = try await Task.detached {
                try application.run(arguments: ["pull", "alpine:3.22"])
            }.value
            let pullOutput = try #require(String(data: pull.standardOutput, encoding: .utf8))
            #expect(pullOutput.contains("Download complete"))

            let inspect = try await Task.detached {
                try application.run(arguments: ["inspect", "--type", "image", "alpine:3.22"])
            }.value
            let values = try #require(
                JSONSerialization.jsonObject(with: inspect.standardOutput) as? [[String: Any]]
            )
            #expect(values.count == 1)
            #expect(values[0]["Os"] as? String == "linux")

            let container = try await Task.detached {
                try application.run(arguments: ["run", "--detach", "alpine:3.22", "sleep", "1"])
            }.value
            let containerID = try #require(
                String(data: container.standardOutput, encoding: .utf8)?
                    .trimmingCharacters(in: .whitespacesAndNewlines)
            )
            #expect(!containerID.isEmpty)
            let exec = try await Task.detached {
                try application.run(
                    arguments: ["exec", "--interactive", containerID, "cat"],
                    standardInput: Data("from-stdin\n".utf8)
                )
            }.value
            #expect(exec.exitCode == 0)
            #expect(exec.standardOutput == Data("cat\n".utf8))

            let interactive = try await Task.detached {
                try application.run(
                    arguments: ["run", "--interactive", "alpine:3.22", "cat"],
                    standardInput: Data("run-stdin\n".utf8)
                )
            }.value
            #expect(interactive.exitCode == 0)
            #expect(interactive.standardOutput == Data("attached\n".utf8))
            try await server.shutdown()
        } catch {
            try? await server.shutdown()
            throw error
        }
    }
}
