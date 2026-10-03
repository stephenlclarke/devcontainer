// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerAPIClient
import ContainerizationOS
import ContainerXPC
import Darwin
@testable import DevContainerAppleRuntime
import DevContainerModel
import Foundation
import Testing

struct AppleContainerBootstrapClientTests {
    @Test func `bootstrap input owns EOF and retains the same process connection`() async throws {
        let recorder = BootstrapRequests()
        let client = LiveAppleContainerBootstrapClient(send: { await recorder.send($0) }, disconnect: {})
        let pipe = Pipe()
        defer {
            try? pipe.fileHandleForReading.close()
            try? pipe.fileHandleForWriting.close()
        }
        let process = try await client.bootstrap(id: "owned", stdio: [pipe.fileHandleForReading, nil, nil])
        #expect(process.id == "owned")
        let bootstrap = try #require(await recorder.messages.first)
        #expect(bootstrap.bool(key: "closeStdinOnEOF"))
        #expect(bootstrap.string(key: .id) == "owned")
        #expect(bootstrap.dataNoCopy(key: .dynamicEnv) == Data("{}".utf8))
        // Transfer owns duplicates, never the generation's retained descriptor.
        #expect(fcntl(pipe.fileHandleForReading.fileDescriptor, F_GETFD) >= 0)
        try await process.start()
        try await process.resize(Terminal.Size(width: 91, height: 37))
        try await process.kill(SIGUSR1)
        #expect(try await process.wait() == 17)
        let requests = await recorder.messages
        #expect(requests.count == 5)
        #expect(requests.dropFirst().allSatisfy {
            $0.string(key: .id) == "owned" && $0.string(key: .processIdentifier) == "owned"
        })
        #expect(requests[2].uint64(key: .width) == 91)
        #expect(requests[2].uint64(key: .height) == 37)
        #expect(requests[3].string(key: .signal) == "USR1")
    }

    @Test func `absent bootstrap input does not claim EOF ownership`() async throws {
        let recorder = BootstrapRequests()
        let client = LiveAppleContainerBootstrapClient(send: { await recorder.send($0) }, disconnect: {})
        _ = try await client.bootstrap(id: "closed", stdio: [])
        let request = try #require(await recorder.messages.first)
        #expect(!request.bool(key: "closeStdinOnEOF"))
        await #expect(throws: DevContainerError.self) {
            _ = try await client.bootstrap(id: "invalid", stdio: [nil, nil, nil, nil])
        }
        #expect(await recorder.messages.count == 1)
    }

    @Test func `bootstrap and process failures propagate without retries`() async throws {
        let client = LiveAppleContainerBootstrapClient(send: { _ in throw CancellationError() }, disconnect: {})
        await #expect(throws: CancellationError.self) {
            _ = try await client.bootstrap(id: "failed", stdio: [])
        }
        let recorder = BootstrapRequests(exitCode: Int64.max)
        let badExit = LiveAppleContainerBootstrapClient(send: { await recorder.send($0) }, disconnect: {})
        let process = try await badExit.bootstrap(id: "bad-exit", stdio: [])
        await #expect(throws: DevContainerError.self) { try await process.wait() }
    }

    @Test func `process transport failures do not retry or prevent disconnect`() async throws {
        let recorder = BootstrapRequests(failAfterBootstrap: true)
        let disconnects = BootstrapDisconnects()
        let client = LiveAppleContainerBootstrapClient(
            send: { try await recorder.sendOrFail($0) }, disconnect: { disconnects.record() }
        )
        let process = try await client.bootstrap(id: "connection-lost", stdio: [])
        let operations: [() async throws -> Void] = [
            { try await process.start() },
            { try await process.resize(Terminal.Size(width: 80, height: 24)) },
            { try await process.kill(SIGTERM) },
            { _ = try await process.wait() }
        ]
        for operation in operations {
            await #expect(throws: CancellationError.self) { try await operation() }
        }
        let requests = await recorder.messages
        #expect(requests.count == 5)
        #expect(requests.dropFirst().allSatisfy {
            $0.string(key: .id) == "connection-lost" && $0.string(key: .processIdentifier) == "connection-lost"
        })
        #expect(disconnects.count == 0)
        #if DEVCONTAINER_ENHANCED_RUNTIME
            // Only the enhanced ClientProcess protocol exposes explicit disconnect.
            process.disconnect()
            #expect(disconnects.count == 1)
        #endif
        #expect(await recorder.messages.count == 5)
    }

    @Test func `output only bootstrap and unknown signal preserve wire values`() async throws {
        let recorder = BootstrapRequests(exitCode: Int64(Int32.min))
        let client = LiveAppleContainerBootstrapClient(send: { await recorder.send($0) }, disconnect: {})
        let output = Pipe()
        defer {
            try? output.fileHandleForReading.close()
            try? output.fileHandleForWriting.close()
        }
        let process = try await client.bootstrap(id: "output-only", stdio: [nil, output.fileHandleForWriting, nil])
        let request = try #require(await recorder.messages.first)
        #expect(!request.bool(key: "closeStdinOnEOF"))
        #expect(fcntl(output.fileHandleForWriting.fileDescriptor, F_GETFD) >= 0)
        try await process.kill(Int32.max)
        #expect(await recorder.messages.last?.string(key: .signal) == String(Int32.max))
        #expect(try await process.wait() == Int32.min)
    }
}

private actor BootstrapRequests {
    var messages: [XPCMessage] = []
    let exitCode: Int64
    let failAfterBootstrap: Bool

    init(exitCode: Int64 = 17, failAfterBootstrap: Bool = false) {
        self.exitCode = exitCode
        self.failAfterBootstrap = failAfterBootstrap
    }

    func sendOrFail(_ request: XPCMessage) throws -> XPCMessage {
        let reply = send(request)
        if failAfterBootstrap, messages.count > 1 {
            throw CancellationError()
        }
        return reply
    }

    func send(_ request: XPCMessage) -> XPCMessage {
        messages.append(request)
        let reply = XPCMessage(route: "test-reply")
        reply.set(key: .exitCode, value: exitCode)
        return reply
    }
}

private final class BootstrapDisconnects: @unchecked Sendable {
    private let lock = NSLock()
    private var value = 0

    var count: Int {
        lock.withLock { value }
    }

    func record() {
        lock.withLock { value += 1 }
    }
}
