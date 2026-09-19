// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Darwin
@testable import DevContainerAppleRuntime
import Foundation
import Testing

struct ProcessInputWriterCancellationTests {
    @Test(arguments: [false, true])
    func `cancellation interrupts a backpressured writer`(cancelTask: Bool) async throws {
        let channel = try AppleProcessInputChannel.socketPair()
        let writer = ProcessInputWriter(channel: channel, label: "test.backpressured-input")
        defer { writer.cancel() }
        let payload = Data(repeating: 7, count: 4 * 1024 * 1024)
        let pending = Task { try await writer.write(payload) }
        // A real byte proves the write entered its queue before cancellation.
        // The peer then stops reading, so this payload cannot fit in the socket.
        let first = try await Task.detached {
            try Self.readAvailable(channel.processEnd, maximum: 1)
        }.value
        #expect(first.count == 1)
        if cancelTask {
            pending.cancel()
        } else {
            writer.cancel()
        }
        // Draining also makes the old implementation terminate rather than
        // hanging the test: it wrongly delivers the entire cancelled payload.
        let remainder = try await Task.detached {
            var count = 0
            while true {
                let bytes = try Self.readAvailable(channel.processEnd, maximum: 65536)
                if bytes.isEmpty {
                    return count
                }
                count += bytes.count
            }
        }.value
        await #expect(throws: CancellationError.self) { try await pending.value }
        #expect(remainder + first.count < payload.count)
        await #expect(throws: Error.self) { try await writer.write(Data([8])) }
    }

    @Test
    func `writer cancellation releases a blocked queue without a reader`() async throws {
        let channel = try AppleProcessInputChannel.socketPair()
        let writer = ProcessInputWriter(channel: channel, label: "test.unread-input")
        defer { writer.cancel() }
        let pending = Task { try await writer.write(Data(count: 4 * 1024 * 1024)) }
        _ = try await Task.detached { try Self.readAvailable(channel.processEnd, maximum: 1) }.value
        writer.cancel()
        await #expect(throws: CancellationError.self) { try await pending.value }
        // close is queued behind the failed write and must now be able to run.
        try await writer.close()
    }

    @Test
    func `PTY input cancellation releases a full terminal queue`() async throws {
        var controller: Int32 = -1
        var terminal: Int32 = -1
        guard openpty(&controller, &terminal, nil, nil, nil) == 0 else { throw POSIXError(.EIO) }
        let host = FileHandle(fileDescriptor: controller, closeOnDealloc: true)
        let peer = FileHandle(fileDescriptor: terminal, closeOnDealloc: true)
        var mode = termios()
        try #require(tcgetattr(terminal, &mode) == 0)
        cfmakeraw(&mode)
        try #require(tcsetattr(terminal, TCSANOW, &mode) == 0)
        let writer = ProcessInputWriter(handle: host, label: "test.terminal-input")
        defer { writer.cancel() }
        let pending = Task { try await writer.write(Data(count: 4 * 1024 * 1024)) }
        _ = try await Task.detached { try Self.readAvailable(peer, maximum: 1) }.value
        pending.cancel()
        await #expect(throws: CancellationError.self) { try await pending.value }
        try await writer.close()
    }

    @Test
    func `invalid descriptor fails before attempting a blocking write`() async throws {
        let writer = ProcessInputWriter(
            handle: FileHandle(fileDescriptor: Int32.max, closeOnDealloc: false), label: "test.invalid-input"
        )
        await #expect(throws: POSIXError(.EBADF)) { try await writer.write(Data([1])) }
        writer.cancel()
    }

    private static func readAvailable(_ handle: FileHandle, maximum: Int) throws -> Data {
        var descriptor = pollfd(fd: handle.fileDescriptor, events: Int16(POLLIN), revents: 0)
        let ready = Darwin.poll(&descriptor, 1, 2000)
        guard ready > 0 else { throw POSIXError(.ETIMEDOUT) }
        var bytes = [UInt8](repeating: 0, count: maximum)
        let count = bytes.withUnsafeMutableBytes { Darwin.read(handle.fileDescriptor, $0.baseAddress, $0.count) }
        guard count >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        return Data(bytes.prefix(count))
    }
}
