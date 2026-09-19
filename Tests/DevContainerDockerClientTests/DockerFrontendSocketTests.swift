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
    func `real executable builds through selected socket and propagates streamed failures without Docker`(
    ) async throws {
        let root = TestStorage.temporaryDirectory.appendingPathComponent("db-\(UUID().uuidString.prefix(8))")
        let context = root.appendingPathComponent("context")
        try FileManager.default.createDirectory(
            at: context,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("FROM scratch\n".utf8).write(to: context.appendingPathComponent("Dockerfile"))
        let socket = root.appendingPathComponent("engine.sock").path
        let server = ContainerUnixHTTPServer(
            responder: FrontendTestResponder(),
            socketPath: socket,
            logger: Logger(label: "frontend-build-test")
        )
        try await server.start()
        do {
            try await checkBuildClient(context: context, socket: socket, outputRoot: root)
            let success = try await FrontendExecutable.run(["build", "-t", "good", context.path], socket: socket)
            #expect(success.exitCode == 0)
            #expect(success.standardOutput == Data("Step 1\nBuilt\n".utf8))
            #expect(success.standardError.isEmpty)
            let failure = try await FrontendExecutable.run(["build", "-t", "bad", context.path], socket: socket)
            #expect(failure.exitCode == 1)
            #expect(failure.standardOutput == Data("Step 1\n".utf8))
            #expect(String(data: failure.standardError, encoding: .utf8)?.contains("build failed") == true)
        } catch {
            try await server.shutdown()
            throw error
        }
        try await server.shutdown()
    }

    private func checkBuildClient(context: URL, socket: String, outputRoot: URL) async throws {
        for tag in ["good", "bad"] {
            guard case let .build(spec) = try DockerFrontendCommand.parse(["build", "-t", tag, context.path]) else {
                throw DockerFrontendError.usage("expected build")
            }
            let archive = try await DockerBuildArchive.prepare(spec)
            let output = outputRoot.appendingPathComponent("client-" + tag)
            try Data().write(to: output)
            let handle = try FileHandle(forWritingTo: output)
            defer { try? handle.close() }
            let frontend = DockerFrontend(version: "test", executionTimeout: .seconds(2))
            let transport = try UnixDockerFrontendTransport(socketPath: socket)
            let writer = try DockerFrontendOutput(descriptor: handle.fileDescriptor)
            if tag == "good" {
                try await frontend.executeBuild(spec, archive: archive, transport: transport, output: writer)
                #expect(try Data(contentsOf: output) == Data("Step 1\nBuilt\n".utf8))
            } else {
                await #expect(throws: DockerFrontendError.self) {
                    try await frontend.executeBuild(spec, archive: archive, transport: transport, output: writer)
                }
                #expect(try Data(contentsOf: output) == Data("Step 1\n".utf8))
            }
        }
    }

    @Test
    func `real executable streams events and keeps HTTP error bodies off stdout`() async throws {
        let root = TestStorage.temporaryDirectory.appendingPathComponent("df-\(UUID().uuidString.prefix(8))")
        try FileManager.default.createDirectory(
            at: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700]
        )
        defer { try? FileManager.default.removeItem(at: root) }
        let socket = root.appendingPathComponent("engine.sock").path
        let server = ContainerUnixHTTPServer(
            responder: FrontendTestResponder(), socketPath: socket, logger: Logger(label: "frontend-events-test")
        )
        try await server.start()
        do {
            let result = try await FrontendExecutable.run(
                ["events", "--format", "{{json .}}", "--filter", "event=start"], socket: socket
            )
            #expect(result.exitCode == 0)
            #expect(result.standardOutput == Data("{\"Action\":\"start\",\"timeNano\":9223372036854775807}\n".utf8))
            #expect(result.standardError.isEmpty)
            let failure = try await FrontendExecutable.run(
                ["events", "--format", "{{json .}}", "--filter", "label=error"], socket: socket
            )
            #expect(failure.exitCode == 1)
            #expect(failure.standardOutput.isEmpty)
            #expect(String(data: failure.standardError, encoding: .utf8)?.contains("event failure") == true)
        } catch {
            try await server.shutdown()
            throw error
        }
        try await server.shutdown()
        #expect(!FileManager.default.fileExists(atPath: socket))
    }

    @Test
    func `real executable attaches before startup and preserves run output warnings and status`() async throws {
        let root = TestStorage.temporaryDirectory.appendingPathComponent("df-\(UUID().uuidString.prefix(8))")
        try FileManager.default.createDirectory(
            at: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700]
        )
        defer { try? FileManager.default.removeItem(at: root) }
        let socket = root.appendingPathComponent("engine.sock").path
        let responder = RunSocketResponder()
        let server = ContainerUnixHTTPServer(
            responder: responder, socketPath: socket, logger: Logger(label: "frontend-run-test")
        )
        try await server.start()
        do {
            let result = try await FrontendExecutable.run(
                [
                    "run",
                    "--sig-proxy=false",
                    "-p",
                    "127.0.0.1:49277:8123",
                    "-u",
                    "vscode",
                    "-a",
                    "STDOUT",
                    "-a",
                    "STDERR",
                    "--entrypoint",
                    "/bin/sh",
                    "image",
                    "-c",
                    "exit 9"
                ],
                socket: socket
            )
            #expect(result.exitCode == 9)
            #expect(result.standardOutput == Data("ready\n".utf8))
            #expect(result.standardError == Data("WARNING: test warning\nerror\n".utf8))
            #expect(await responder.attachedBeforeStart)
            #expect(await responder.createdUser == "vscode")
            #expect(await responder.createdPorts == ["8123/tcp": [["HostIp": "127.0.0.1", "HostPort": "49277"]]])
        } catch {
            try await server.shutdown()
            throw error
        }
        try await server.shutdown()
        #expect(!FileManager.default.fileExists(atPath: socket))
    }

    @Test
    func `real executable sends interactive input and separates Engine output without Docker`() async throws {
        let root = TestStorage.temporaryDirectory.appendingPathComponent("df-\(UUID().uuidString.prefix(8))")
        try FileManager.default.createDirectory(
            at: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700]
        )
        defer { try? FileManager.default.removeItem(at: root) }
        let socket = root.appendingPathComponent("engine.sock").path
        let server = ContainerUnixHTTPServer(
            responder: FrontendTestResponder(), socketPath: socket, logger: Logger(label: "frontend-exec-test")
        )
        try await server.start()
        do {
            let result = try await FrontendExecutable.run(
                ["exec", "-i", "-u", "root", "-w", "/work", "box", "/bin/sh"],
                socket: socket, input: Data("printf hello\n".utf8)
            )
            #expect(result.exitCode == 7)
            #expect(result.standardOutput == Data("printf hello\n".utf8))
            #expect(result.standardError == Data("stderr\n".utf8))
        } catch {
            try await server.shutdown()
            throw error
        }
        try await server.shutdown()
        #expect(!FileManager.default.fileExists(atPath: socket))
    }

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

enum FrontendExecutable {
    static var executable: URL {
        let environment = ProcessInfo.processInfo.environment
        if let runfile = environment["DEVCONTAINER_DOCKER_TEST_RUNFILE"],
           let directory = environment["TEST_SRCDIR"],
           let workspace = environment["TEST_WORKSPACE"]
        {
            return URL(fileURLWithPath: directory).appendingPathComponent(workspace).appendingPathComponent(runfile)
        } else {
            return Bundle(for: FrontendTestBundle.self).bundleURL.deletingLastPathComponent()
                .appendingPathComponent("devcontainer-docker")
        }
    }

    static func run(
        _ arguments: [String],
        socket: String? = nil,
        input: Data? = nil
    ) async throws -> CapturedProcessResult {
        var childEnvironment = ["PATH": "/no-external-clients", "DEVCONTAINER_CONFIG": "/no-config"]
        childEnvironment["TMPDIR"] = TestStorage.temporaryDirectory.path
        if let socket {
            childEnvironment["DOCKER_HOST"] = "unix://\(socket)"
        }
        return try await RuntimeRequestScope.$context.withValue(
            RuntimeRequestContext(deadline: Date().addingTimeInterval(5))
        ) {
            try await ProcessRunner.captured(
                executable: executable, arguments: arguments,
                environment: childEnvironment, input: input, maximumOutputBytes: 65536
            )
        }
    }
}

private final class FrontendTestBundle: NSObject {}

private struct FrontendTestResponder: DockerHTTPResponder {
    func respond(to request: DockerHTTPRequest) async -> DockerHTTPResponse {
        if request.method == .post, request.target.hasPrefix("/build?") {
            guard request.body.count > 512 else { return .text("missing archive", status: 400) }
            let last = request.target.contains("&t=bad")
                ? "{\"errorDetail\":{\"message\":\"build failed\"}}" : "{\"stream\":\"Built\\n\"}"
            return .text("{\"stream\":\"Step 1\\n\"}\n" + last, contentType: "application/json")
        }
        if request.target.hasPrefix("/events?") {
            if request.target.contains("%22error%22") {
                return .text(#"{"message":"event failure"}"#, status: 500, contentType: "application/json")
            }
            let stream = AsyncThrowingStream<Data, any Error> { continuation in
                continuation.yield(Data("{\"Action\":\"start\",\"timeNano\":".utf8))
                continuation.yield(Data("9223372036854775807}\n".utf8))
                continuation.finish()
            }
            return .init(status: 200, headers: ["Content-Type": "application/json"], body: .stream(stream))
        }
        if request.method == .post, request.target == "/containers/box/exec" {
            return .text(#"{"Id":"c9e1deac-d20d-4b9a-ace8-4456740fed63"}"#, contentType: "application/json")
        }
        if request.method == .get, request.target == "/exec/c9e1deac-d20d-4b9a-ace8-4456740fed63/json" {
            return .text(
                #"{"ID":"c9e1deac-d20d-4b9a-ace8-4456740fed63","Running":false,"ExitCode":7}"#,
                contentType: "application/json"
            )
        }
        if request.method == .post, request.target == "/exec/c9e1deac-d20d-4b9a-ace8-4456740fed63/start" {
            return DockerHTTPResponse(
                status: 200,
                headers: ["Connection": "Upgrade", "Upgrade": "tcp"],
                body: .hijack(FrontendEchoSession(), terminal: false)
            )
        }
        if request.method == .get, request.target == "/version" {
            return .text(#"{"Version":"selected-test-engine"}"#, contentType: "application/json")
        }
        return .text(#"{"message":"image not found"}"#, status: 404, contentType: "application/json")
    }
}

private actor FrontendEchoSession: DockerHijackSession {
    nonisolated let frames: AsyncThrowingStream<DockerStreamFrame, any Error>
    private let continuation: AsyncThrowingStream<DockerStreamFrame, any Error>.Continuation
    private var input = Data()

    init() {
        (frames, continuation) = AsyncThrowingStream.makeStream()
    }

    func write(_ data: Data) {
        input.append(data)
    }

    func closeStandardInput() {
        continuation.yield(.init(channel: .standardOutput, data: input))
        continuation.yield(.init(channel: .standardError, data: Data("stderr\n".utf8)))
        continuation.finish()
    }

    func wait() -> Int32 {
        7
    }

    func cancel() {
        continuation.finish()
    }
}

private actor RunSocketResponder: DockerHTTPResponder {
    let session = RunSocketSession()
    var attachedBeforeStart = false
    var createdUser: String?
    var createdPorts: [String: [[String: String]]]?
    private var attached = false

    func respond(to request: DockerHTTPRequest) async -> DockerHTTPResponse {
        switch request.target {
        case "/containers/create":
            let fields = try? JSONSerialization.jsonObject(with: request.body) as? [String: Any]
            createdUser = fields?["User"] as? String
            createdPorts = (fields?["HostConfig"] as? [String: Any])?["PortBindings"] as? [String: [[String: String]]]
            return .text(#"{"Id":"run123","Warnings":["test warning"]}"#, contentType: "application/json")
        case "/containers/run123/attach?stream=1&stdin=0&stdout=1&stderr=1":
            attached = true
            return .init(
                status: 101,
                headers: ["Connection": "Upgrade", "Upgrade": "tcp"],
                body: .hijack(session, terminal: false)
            )
        case "/containers/run123/start":
            attachedBeforeStart = attached
            await session.start()
            return .empty(status: 204)
        case "/containers/run123/wait?condition=not-running":
            return .text(#"{"StatusCode":9}"#, contentType: "application/json")
        default:
            return .text("unexpected request", status: 400)
        }
    }
}

private actor RunSocketSession: DockerHijackSession {
    nonisolated let frames: AsyncThrowingStream<DockerStreamFrame, any Error>
    private let continuation: AsyncThrowingStream<DockerStreamFrame, any Error>.Continuation
    init() {
        (frames, continuation) = AsyncThrowingStream.makeStream()
    }

    func start() {
        continuation.yield(.init(channel: .standardOutput, data: Data("ready\n".utf8)))
        continuation.yield(.init(channel: .standardError, data: Data("error\n".utf8)))
        continuation.finish()
    }

    func write(_: Data) { /* Output-only attachment does not accept data. */ }
    func closeStandardInput() { /* Closing input must not finish the pre-start output stream. */ }
    func wait() -> Int32 {
        9
    }

    func cancel() {
        continuation.finish()
    }
}
