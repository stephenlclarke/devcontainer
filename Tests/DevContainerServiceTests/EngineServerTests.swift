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

import DevContainerDockerAPI
import DevContainerModel
@testable import DevContainerService
import DevContainerTestSupport
import Foundation
import Logging
import Testing

@Suite(.serialized)
struct EngineServerTests {
    @Test
    func `server exposes byte stream and hijacked Docker responses`() async throws {
        let fixture = try ServerFixture()
        defer { fixture.cleanup() }
        let runtime = InMemoryRuntime()
        await runtime.seedImage(
            ImageSnapshot(
                id: "sha256:fixture",
                references: ["alpine:3.22"],
                createdAt: Date(timeIntervalSince1970: 1),
                size: 42
            )
        )
        let server = fixture.server(runtime: runtime)
        try await server.start()
        do {
            try exerciseServer(fixture)
        } catch {
            try? await server.shutdown()
            throw error
        }
        for _ in 0 ..< 100 where server.activeConnectionCount != 0 {
            try await Task.sleep(for: .milliseconds(10))
        }
        #expect(server.activeConnectionCount == 0)
        try await server.shutdown()

        #expect(!FileManager.default.fileExists(atPath: fixture.socketPath))
    }

    private func exerciseServer(_ fixture: ServerFixture) throws {
        let ping = try fixture.curl("/_ping")
        #expect(ping.status == 200)
        #expect(ping.body == "OK")

        let version = try fixture.curl("/v1.53/version")
        #expect(version.status == 200)
        #expect(version.body.contains("\"ApiVersion\":\"1.53\""))

        let create = try fixture.curl(
            "/v1.53/containers/create?name=service-fixture",
            method: "POST",
            body: """
            {"Image":"alpine:3.22","Cmd":["printf","hello"]}
            """
        )
        #expect(create.status == 201)
        let created = try #require(
            JSONSerialization.jsonObject(
                with: Data(create.body.utf8)
            ) as? [String: Any]
        )
        let identifier = try #require(created["Id"] as? String)

        #expect(try fixture.curl(
            "/v1.53/containers/\(identifier)/start",
            method: "POST"
        ).status == 204)
        #expect(try fixture.curl(
            "/v1.53/containers/\(identifier)/logs?stdout=1&stderr=1"
        ).status == 200)
        let events = try fixture.curl("/v1.53/events")
        #expect(events.status == 200)
        #expect(events.body.contains("\"Action\":\"create\""))
        #expect(try fixture.curl(
            "/v1.53/containers/\(identifier)/attach?stream=1&stdout=1&stderr=1",
            method: "POST"
        ).status == 200)

        let unsupported = try fixture.curl("/_ping", method: "OPTIONS")
        #expect(unsupported.status == 405)
        #expect(unsupported.body.contains("unsupported HTTP method"))
    }

    @Test
    func `server rejects unsafe paths and concurrent ownership`() async throws {
        let unsafeFixture = try ServerFixture()
        defer { unsafeFixture.cleanup() }
        try Data("not a socket".utf8).write(
            to: URL(fileURLWithPath: unsafeFixture.socketPath)
        )
        let unsafeServer = unsafeFixture.server(runtime: InMemoryRuntime())
        await #expect(throws: EngineServerError.self) {
            try await unsafeServer.wait()
        }
        await #expect(throws: EngineServerError.self) {
            try await unsafeServer.start()
        }
        try await unsafeServer.shutdown()

        let fixture = try ServerFixture()
        defer { fixture.cleanup() }
        let first = fixture.server(runtime: InMemoryRuntime())
        let second = fixture.server(runtime: InMemoryRuntime())
        try await first.start()
        await #expect(throws: EngineServerError.self) {
            try await second.start()
        }
        try await second.shutdown()
        #expect(FileManager.default.fileExists(atPath: fixture.socketPath))
        try await first.shutdown()
    }

    @Test
    func `default paths are user scoped and executable selection is absolute`() {
        #expect(DefaultPaths.socket.hasSuffix("/devcontainer/docker.sock"))
        #expect(DefaultPaths.stateDatabase.hasSuffix("/devcontainer/state.sqlite"))
        #expect(DefaultPaths.containerExecutable.hasPrefix("/"))
        if FileManager.default.isExecutableFile(atPath: "/usr/local/bin/container") {
            #expect(DefaultPaths.containerExecutable == "/usr/local/bin/container")
        } else if FileManager.default.isExecutableFile(
            atPath: "/opt/homebrew/bin/container"
        ) {
            #expect(DefaultPaths.containerExecutable == "/opt/homebrew/bin/container")
        } else {
            #expect(DefaultPaths.containerExecutable == "/usr/local/bin/container")
        }
    }
}

private struct ServerFixture {
    let root: URL
    let socketPath: String

    init() throws {
        root = URL(fileURLWithPath: "/tmp", isDirectory: true)
            .appendingPathComponent(
                "dce-\(UUID().uuidString.prefix(8))",
                isDirectory: true
            )
        socketPath = root.appendingPathComponent("docker.sock").path
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700]
        )
    }

    func server(runtime: InMemoryRuntime) -> EngineServer {
        EngineServer(
            router: DockerRouter(runtime: runtime),
            socketPath: socketPath,
            logger: Logger(label: "devcontainer-engine-tests")
        )
    }

    func cleanup() {
        try? FileManager.default.removeItem(at: root)
    }

    func curl(
        _ path: String,
        method: String = "GET",
        body: String? = nil
    ) throws -> (status: Int, body: String) {
        let process = Process()
        let output = Pipe()
        let error = Pipe()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/curl")
        process.arguments = [
            "--silent",
            "--show-error",
            "--verbose",
            "--unix-socket",
            socketPath,
            "--request",
            method,
            "--header",
            "Content-Type: application/json",
            "--write-out",
            "\n%{http_code}",
            "http://localhost\(path)"
        ]
        if let body {
            process.arguments?.insert(contentsOf: ["--data-binary", body], at: 9)
        }
        process.standardOutput = output
        process.standardError = error
        try process.run()
        process.waitUntilExit()
        let standardOutput = try output.fileHandleForReading.readToEnd() ?? Data()
        let standardError = try error.fileHandleForReading.readToEnd() ?? Data()
        guard process.terminationStatus == 0 else {
            throw CurlError(
                "\(method) \(path): "
                    + (
                        String(data: standardError, encoding: .utf8)
                            ?? "non-UTF-8 curl diagnostic"
                    )
            )
        }
        let value = String(data: standardOutput, encoding: .utf8)
            ?? "non-UTF-8 curl response"
        let components = value.split(
            separator: "\n",
            omittingEmptySubsequences: false
        )
        guard let statusText = components.last, let status = Int(statusText) else {
            throw CurlError("curl did not emit an HTTP status: \(value)")
        }
        return (
            status,
            components.dropLast().joined(separator: "\n")
        )
    }
}

private struct CurlError: Error {
    let message: String

    init(_ message: String) {
        self.message = message
    }
}
