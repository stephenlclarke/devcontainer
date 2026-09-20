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
}

private actor BootstrapRequests {
    var messages: [XPCMessage] = []
    let exitCode: Int64

    init(exitCode: Int64 = 17) {
        self.exitCode = exitCode
    }

    func send(_ request: XPCMessage) -> XPCMessage {
        messages.append(request)
        let reply = XPCMessage(route: "test-reply")
        reply.set(key: .exitCode, value: exitCode)
        return reply
    }
}
