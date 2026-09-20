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
        // This must be EOF, not EAGAIN from a leaked caller-side write handle.
        // Nonblocking reads make the regression fail without hanging the suite.
        #expect(read(descriptor, &buffer, buffer.count) == 0)
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
        var byte: UInt8 = 0
        #expect(read(descriptor, &byte, 1) == 0)
    }
}
