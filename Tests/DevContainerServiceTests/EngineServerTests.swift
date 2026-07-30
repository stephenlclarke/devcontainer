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
import DevContainerRuntimeSPI
@testable import DevContainerService
import DevContainerTestSupport
import Foundation
import Logging
import Testing

@Suite(.serialized)
struct EngineServerTests {
    @Test
    func `server errors always use a valid Docker JSON envelope`() throws {
        let message = "quote \" slash \\ newline\ncontrol\u{0001} unicode \u{1F680}"
        let body = EngineResponseEncoding.dockerError(message)
        let envelope = try JSONDecoder().decode(
            DockerErrorEnvelope.self,
            from: body
        )

        #expect(envelope.message == message)
    }

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

    @Test
    func `server forwards an early client half close after hijack`() async throws {
        let fixture = try ServerFixture()
        defer { fixture.cleanup() }
        let session = EOFProcessSession()
        let runtime = InMemoryRuntime(execSession: session)
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
            let container = try fixture.createRunningContainer()
            let exec = try fixture.createExec(container: container)
            let payload = Data("input-before-upgrade".utf8)
            let process = try fixture.startEarlyHalfClose(
                exec: exec,
                payload: payload
            )
            defer {
                if process.isRunning {
                    process.terminate()
                    process.waitUntilExit()
                }
            }

            for _ in 0 ..< 100 where !session.inputClosed {
                try await Task.sleep(for: .milliseconds(10))
            }
            #expect(session.inputClosed)
            #expect(session.input == payload)
        } catch {
            try? await server.shutdown()
            throw error
        }

        try await server.shutdown()
    }

    @Test
    func `server serializes pipelined HTTP responses`() async throws {
        let fixture = try ServerFixture()
        defer { fixture.cleanup() }
        let server = fixture.server(runtime: InMemoryRuntime())
        try await server.start()

        do {
            let response = try fixture.pipelined(
                "GET /v1.53/version HTTP/1.1\r\n"
                    + "Host: localhost\r\n\r\n"
                    + "GET /_ping HTTP/1.1\r\n"
                    + "Host: localhost\r\n\r\n"
            )
            let version = try #require(response.range(of: "\"ApiVersion\""))
            let ping = try #require(response.range(of: "\r\n\r\nOK"))
            #expect(version.lowerBound < ping.lowerBound)
            #expect(response.components(separatedBy: "HTTP/1.1 200 OK").count == 3)
        } catch {
            try? await server.shutdown()
            throw error
        }

        try await server.shutdown()
    }

    @Test
    func `server bounds request and pipeline buffering`() async throws {
        let fixture = try ServerFixture()
        defer { fixture.cleanup() }
        let limits = EngineServerLimits(
            maximumRequestBodyBytes: 64,
            maximumBufferedRequestBodyBytes: 64,
            maximumPendingRequests: 1
        )
        let server = fixture.server(
            runtime: InMemoryRuntime(),
            limits: limits
        )
        try await server.start()

        do {
            let oversized = try fixture.curl(
                "/_ping",
                method: "POST",
                body: String(repeating: "x", count: 65)
            )
            #expect(oversized.status == 413)

            let body = String(repeating: "x", count: 40)
            let boundedBodies = try fixture.pipelined(
                "POST /_ping HTTP/1.1\r\n"
                    + "Host: localhost\r\n"
                    + "Content-Length: \(body.utf8.count)\r\n\r\n"
                    + body
                    + "POST /_ping HTTP/1.1\r\n"
                    + "Host: localhost\r\n"
                    + "Content-Length: \(body.utf8.count)\r\n\r\n"
                    + body
            )
            #expect(
                boundedBodies.components(separatedBy: "HTTP/1.1").count < 3
            )

            let boundedQueue = try fixture.pipelined(
                "GET /_ping HTTP/1.1\r\nHost: localhost\r\n\r\n"
                    + "GET /_ping HTTP/1.1\r\nHost: localhost\r\n\r\n"
                    + "GET /_ping HTTP/1.1\r\nHost: localhost\r\n\r\n"
            )
            #expect(
                boundedQueue.components(separatedBy: "HTTP/1.1").count < 4
            )
        } catch {
            try? await server.shutdown()
            throw error
        }

        try await server.shutdown()
    }

    @Test
    func `disconnect cancels an in flight ordinary request`() async throws {
        let fixture = try ServerFixture()
        defer { fixture.cleanup() }
        let runtime = InMemoryRuntime(descriptorDelay: .seconds(30))
        let server = fixture.server(runtime: runtime)
        try await server.start()

        try fixture.requestAndDisconnect(
            "GET /v1.53/version HTTP/1.1\r\nHost: localhost\r\n\r\n"
        )
        for _ in 0 ..< 100 where await runtime.observedRequestCancellationCount == 0 {
            try await Task.sleep(for: .milliseconds(10))
        }

        #expect(await runtime.observedRequestCancellationCount == 1)
        try await server.shutdown()
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

    func server(
        runtime: InMemoryRuntime,
        limits: EngineServerLimits = .production
    ) -> EngineServer {
        EngineServer(
            router: DockerRouter(runtime: runtime),
            socketPath: socketPath,
            logger: Logger(label: "devcontainer-engine-tests"),
            limits: limits
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

    func createRunningContainer() throws -> String {
        let create = try curl(
            "/v1.53/containers/create?name=half-close-fixture",
            method: "POST",
            body: """
            {"Image":"alpine:3.22","Cmd":["sleep","30"]}
            """
        )
        let decoded = try JSONSerialization.jsonObject(
            with: Data(create.body.utf8)
        ) as? [String: Any]
        guard create.status == 201, let identifier = decoded?["Id"] as? String else {
            throw CurlError("container create returned \(create.status): \(create.body)")
        }
        let start = try curl(
            "/v1.53/containers/\(identifier)/start",
            method: "POST"
        )
        guard start.status == 204 else {
            throw CurlError("container start returned \(start.status): \(start.body)")
        }
        return identifier
    }

    func pipelined(_ request: String) throws -> String {
        let input = Pipe()
        let output = Pipe()
        let error = Pipe()
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/nc")
        process.arguments = ["-U", socketPath]
        process.standardInput = input
        process.standardOutput = output
        process.standardError = error
        try process.run()
        input.fileHandleForWriting.write(Data(request.utf8))
        Thread.sleep(forTimeInterval: 0.2)
        try input.fileHandleForWriting.close()
        process.waitUntilExit()
        let standardOutput = try output.fileHandleForReading.readToEnd() ?? Data()
        let standardError = try error.fileHandleForReading.readToEnd() ?? Data()
        guard process.terminationStatus == 0 else {
            throw CurlError(
                "pipelined request: "
                    + (
                        String(data: standardError, encoding: .utf8)
                            ?? "non-UTF-8 nc diagnostic"
                    )
            )
        }
        return String(data: standardOutput, encoding: .utf8)
            ?? "non-UTF-8 pipelined response"
    }

    func requestAndDisconnect(_ request: String) throws {
        let input = Pipe()
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/nc")
        process.arguments = ["-U", socketPath]
        process.standardInput = input
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()
        input.fileHandleForWriting.write(Data(request.utf8))
        Thread.sleep(forTimeInterval: 0.05)
        process.terminate()
        process.waitUntilExit()
        try input.fileHandleForWriting.close()
    }

    func createExec(container: String) throws -> String {
        let create = try curl(
            "/v1.53/containers/\(container)/exec",
            method: "POST",
            body: """
            {"AttachStdin":true,"AttachStdout":true,"AttachStderr":true,"Cmd":["cat"]}
            """
        )
        let decoded = try JSONSerialization.jsonObject(
            with: Data(create.body.utf8)
        ) as? [String: Any]
        guard create.status == 201, let identifier = decoded?["Id"] as? String else {
            throw CurlError("exec create returned \(create.status): \(create.body)")
        }
        return identifier
    }

    func startEarlyHalfClose(
        exec: String,
        payload: Data
    ) throws -> Process {
        let body = Data(#"{"Detach":false,"Tty":false}"#.utf8)
        let headers = Data(
            (
                "POST /v1.53/exec/\(exec)/start HTTP/1.1\r\n"
                    + "Host: localhost\r\n"
                    + "Connection: Upgrade\r\n"
                    + "Upgrade: tcp\r\n"
                    + "Content-Type: application/json\r\n"
                    + "Content-Length: \(body.count)\r\n\r\n"
            ).utf8
        )
        var request = headers
        request.append(body)
        request.append(payload)

        let input = Pipe()
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/nc")
        process.arguments = ["-U", socketPath]
        process.standardInput = input
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()
        input.fileHandleForWriting.write(request)
        try input.fileHandleForWriting.close()
        return process
    }
}

private struct CurlError: Error {
    let message: String

    init(_ message: String) {
        self.message = message
    }
}

private final class EOFProcessSession: RuntimeProcessSession, @unchecked Sendable {
    let frames: AsyncThrowingStream<RuntimeIOFrame, any Error>

    private let frameContinuation:
        AsyncThrowingStream<RuntimeIOFrame, any Error>.Continuation
    private let completionContinuation: AsyncStream<Int32>.Continuation
    private let completionTask: Task<Int32, Never>
    private let lock = NSLock()
    private var bytes = Data()
    private var closed = false

    init() {
        (frames, frameContinuation) = AsyncThrowingStream.makeStream()
        let (completion, continuation) = AsyncStream<Int32>.makeStream()
        completionContinuation = continuation
        completionTask = Task {
            for await status in completion {
                return status
            }
            return 255
        }
    }

    var input: Data {
        lock.withLock { bytes }
    }

    var inputClosed: Bool {
        lock.withLock { closed }
    }

    func write(_ data: Data) {
        lock.withLock {
            bytes.append(data)
        }
    }

    func closeStandardInput() {
        let output: Data? = lock.withLock {
            guard !closed else {
                return nil
            }
            closed = true
            return bytes
        }
        guard let output else {
            return
        }
        frameContinuation.yield(
            RuntimeIOFrame(channel: .standardOutput, data: output)
        )
        frameContinuation.finish()
        completionContinuation.yield(0)
        completionContinuation.finish()
    }

    func resize(width _: UInt16, height _: UInt16) {}

    func wait() async -> Int32 {
        await completionTask.value
    }

    func cancel() {
        frameContinuation.finish()
        completionContinuation.finish()
    }
}
