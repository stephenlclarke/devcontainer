// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineWire
import ContainerUnixHTTPServer
import Darwin
@testable import DevContainerDockerClient
import DevContainerProcess
import DevContainerTestStorage
import Foundation
import Logging
import Testing

struct DockerBuildCancellationTests {
    @Test
    func `archive cancellation reaps acknowledged child before removing staged Dockerfile`() async throws {
        let fixture = try BuildCancellationFixture()
        defer { fixture.remove() }
        let marker = fixture.root.appendingPathComponent("child")
        let stageRecord = fixture.root.appendingPathComponent("stage")
        let operation = Task {
            try await DockerBuildArchive.create(fixture.command) { arguments, limit in
                #expect(limit == 64 * 1024 * 1024)
                let stage = try #require(arguments.suffix(3).first)
                try Data(stage.utf8).write(to: stageRecord)
                // Announce the real child before holding it. The finite sleep
                // bounds a broken cancellation path, which must fail the test.
                return try await ProcessRunner.captured(
                    executable: URL(fileURLWithPath: "/bin/sh"),
                    arguments: ["-c", "printf '%s\\n' $$ > \"$1\"; exec /bin/sleep 5", "sh", marker.path],
                    environment: [:], maximumOutputBytes: limit
                )
            }
        }
        defer { operation.cancel() }
        let acknowledged = await fixture.waitForText(marker)
        let pid = acknowledged.flatMap { pid_t($0.trimmingCharacters(in: .whitespacesAndNewlines)) }
        #expect(pid != nil)
        let stage = try? String(contentsOf: stageRecord, encoding: .utf8)
        #expect(stage.map { FileManager.default.fileExists(atPath: $0) } == true)
        let start = ContinuousClock.now
        operation.cancel()
        await #expect(throws: CancellationError.self) { try await operation.value }
        #expect(start.duration(to: .now) < .seconds(2))
        if let pid {
            errno = 0
            #expect(kill(pid, 0) == -1 && errno == ESRCH)
        }
        #expect(stage.map { FileManager.default.fileExists(atPath: $0) } == false)
    }

    @Test(arguments: [SIGINT, SIGTERM])
    func `executable signal cancels an acknowledged pending build`(_ signal: Int32) async throws {
        let fixture = try BuildCancellationFixture()
        defer { fixture.remove() }
        let socket = fixture.root.appendingPathComponent("engine.sock").path
        let responder = HeldBuildResponder()
        let server = ContainerUnixHTTPServer(
            responder: responder, socketPath: socket, logger: Logger(label: "build-cancel")
        )
        try await server.start()
        do {
            try await interruptBuild(fixture, socket: socket, signal: signal)
        } catch {
            await responder.finish()
            try await server.shutdown()
            throw error
        }
        await responder.finish()
        try await server.shutdown()
        #expect(!FileManager.default.fileExists(atPath: socket))
    }

    private func interruptBuild(_ fixture: BuildCancellationFixture, socket: String, signal: Int32) async throws {
        let output = fixture.root.appendingPathComponent("stdout")
        try Data().write(to: output)
        let handle = try FileHandle(forWritingTo: output)
        defer { try? handle.close() }
        let process = ProcessCommand(
            FrontendExecutable.executable.path, arguments: fixture.arguments,
            environment: [
                "PATH=/no-docker", "TMPDIR=" + fixture.temporary.path,
                "DOCKER_HOST=unix://" + socket, "DEVCONTAINER_CONFIG=/no-config"
            ], directory: nil
        )
        process.stdout = handle
        process.attributes.setProcessGroup = true
        try process.start()
        let termination = OwnedProcessTermination()
        termination.didLaunch(processGroup: process.pid)
        let watchdog = Task {
            do { try await Task.sleep(for: .seconds(5)); termination.cancel() } catch {
                // Normal completion relinquishes the watchdog before reaping.
            }
        }
        let ready = await fixture.waitForText(output)
        #expect(ready == "building\n")
        let start = ContinuousClock.now
        #expect(kill(process.pid, signal) == 0)
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            DispatchQueue.global().async {
                _ = try? process.waitUntilExit()
                continuation.resume()
            }
        }
        watchdog.cancel()
        termination.didExit()
        #expect(try process.wait() == 1)
        #expect(start.duration(to: .now) < .seconds(2))
        #expect(try FileManager.default.contentsOfDirectory(atPath: fixture.temporary.path).isEmpty)
    }
}

private struct BuildCancellationFixture: Sendable {
    let root: URL
    let temporary: URL
    let arguments: [String]
    let command: DockerBuildCommand

    init() throws {
        root = TestStorage.temporaryDirectory.appendingPathComponent("bc-\(UUID().uuidString.prefix(8))")
        temporary = root.appendingPathComponent("temporary")
        let context = root.appendingPathComponent("context")
        let files = FileManager.default
        try files.createDirectory(at: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        do {
            for directory in [temporary, context] {
                try files.createDirectory(at: directory, withIntermediateDirectories: false)
            }
            let dockerfile = root.appendingPathComponent("external.Dockerfile")
            try Data("FROM scratch\n".utf8).write(to: dockerfile)
            arguments = ["build", "-f", dockerfile.path, context.path]
            guard case let .build(parsed) = try DockerFrontendCommand.parse(arguments) else {
                throw DockerFrontendError.usage("expected build")
            }
            command = parsed
        } catch {
            try? files.removeItem(at: root)
            throw error
        }
    }

    func waitForText(_ file: URL) async -> String? {
        let end = ContinuousClock.now.advanced(by: .seconds(3))
        while ContinuousClock.now < end {
            if let text = try? String(contentsOf: file, encoding: .utf8), text.hasSuffix("\n") {
                return text
            }
            try? await Task.sleep(for: .milliseconds(1))
        }
        return nil
    }

    func remove() {
        try? FileManager.default.removeItem(at: root)
    }
}

private actor HeldBuildResponder: DockerHTTPResponder {
    private var continuation: AsyncThrowingStream<Data, any Error>.Continuation?

    func respond(to request: DockerHTTPRequest) async -> DockerHTTPResponse {
        guard request.method == .post, request.target.hasPrefix("/build?"), request.body.count > 512 else {
            return .text("unexpected build request", status: 400)
        }
        let (stream, continuation) = AsyncThrowingStream<Data, any Error>.makeStream()
        self.continuation = continuation
        continuation.yield(Data("{\"stream\":\"building\\n\"}\n".utf8))
        return .init(status: 200, headers: ["Content-Type": "application/json"], body: .stream(stream))
    }

    func finish() {
        continuation?.finish()
        continuation = nil
    }
}
