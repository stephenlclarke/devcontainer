// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerXPC
import Darwin
@testable import DevContainerAppleRuntime
import Foundation
import Testing

struct AppleXPCFileHandleTransferTests {
    @Test
    func `pinned XPC transfer preserves tail output and releases EOF`() throws {
        let pipe = Pipe()
        let transferred = try transfer(pipe.fileHandleForWriting)
        try pipe.fileHandleForWriting.close()
        let tail = Data("last output before exit".utf8)
        try transferred.write(contentsOf: tail)
        try transferred.close()

        let descriptor = pipe.fileHandleForReading.fileDescriptor
        let flags = fcntl(descriptor, F_GETFL)
        try #require(flags >= 0)
        try #require(fcntl(descriptor, F_SETFL, flags | O_NONBLOCK) == 0)
        var buffer = [UInt8](repeating: 0, count: 64)
        let bytes = read(descriptor, &buffer, buffer.count)
        #expect(bytes == tail.count)
        #expect(Data(buffer.prefix(max(0, bytes))) == tail)
        #expect(try reachesEOF(descriptor))
    }

    private func transfer(_ handle: FileHandle) throws -> FileHandle {
        let copies = try AppleXPCFileHandleTransfer.copies(of: [nil, handle])
        let message = XPCMessage(route: "test-file-handle-transfer")
        try message.set(key: "stdout", value: #require(copies[1]))
        return try #require(message.fileHandle(key: "stdout"))
    }

    @Test
    func `partial copy failure closes write duplicates`() throws {
        let pipe = Pipe()
        let invalid = FileHandle(fileDescriptor: Int32.max, closeOnDealloc: false)
        #expect(throws: POSIXError.self) {
            _ = try AppleXPCFileHandleTransfer.copies(of: [pipe.fileHandleForWriting, invalid])
        }
        try pipe.fileHandleForWriting.close()
        let descriptor = pipe.fileHandleForReading.fileDescriptor
        let flags = fcntl(descriptor, F_GETFL)
        try #require(flags >= 0)
        try #require(fcntl(descriptor, F_SETFL, flags | O_NONBLOCK) == 0)
        #expect(try reachesEOF(descriptor))
    }

    @Test
    func `EOF observation rejects a retained writer`() throws {
        let pipe = Pipe()
        #expect(try !reachesEOF(pipe.fileHandleForReading.fileDescriptor, milliseconds: 10))
        try pipe.fileHandleForWriting.close()
        #expect(try reachesEOF(pipe.fileHandleForReading.fileDescriptor))
    }

    private func reachesEOF(_ descriptor: Int32, milliseconds: Int32 = 1000) throws -> Bool {
        // Concurrent PTY tests use the pinned library's fork/exec launcher. A
        // pre-exec child briefly inherits even close-on-exec descriptors. Wait
        // for kernel EOF rather than mistaking that window for a permanent leak.
        let deadline = ContinuousClock.now + .milliseconds(Int(milliseconds))
        repeat {
            var readiness = pollfd(fd: descriptor, events: Int16(POLLIN), revents: 0)
            let ready = poll(&readiness, 1, 10)
            if ready < 0, errno != EINTR {
                throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
            }
            if ready > 0 {
                var byte: UInt8 = 0
                return read(descriptor, &byte, 1) == 0
            }
        } while ContinuousClock.now < deadline
        return false
    }
}
