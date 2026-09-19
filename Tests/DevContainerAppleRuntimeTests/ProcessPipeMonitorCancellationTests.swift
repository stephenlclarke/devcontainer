// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Darwin
@testable import DevContainerAppleRuntime
import DevContainerModel
import Foundation
import Testing

struct ProcessPipeMonitorCancellationTests {
    @Test
    func `idle monitor cancellation finishes without closing a caller owned descriptor`() async throws {
        let pipe = Pipe()
        let (end, endContinuation) = AsyncStream<Void>.makeStream()
        let (_, frames) = AsyncThrowingStream<RuntimeIOFrame, any Error>.makeStream()
        let monitor = ProcessPipeMonitor(
            handle: pipe.fileHandleForReading, channel: .standardOutput,
            end: endContinuation, frames: frames, closeHandleOnFinish: false
        )
        monitor.cancel()
        for await _ in end { /* Wait for the worker to relinquish its descriptor. */ }
        #expect(fcntl(pipe.fileHandleForReading.fileDescriptor, F_GETFD) >= 0)
        try pipe.fileHandleForWriting.write(contentsOf: Data([42]))
        #expect(try pipe.fileHandleForReading.read(upToCount: 1) == Data([42]))
    }

    @Test
    func `nonblocking monitor drains complete output before EOF`() async throws {
        let pipe = Pipe()
        let (end, endContinuation) = AsyncStream<Void>.makeStream()
        let (stream, frames) = AsyncThrowingStream<RuntimeIOFrame, any Error>.makeStream()
        let monitor = ProcessPipeMonitor(
            pipe: pipe, channel: .standardError, end: endContinuation, frames: frames
        )
        defer { monitor.cancel() }
        let payload = Data(repeating: 23, count: 1024 * 1024)
        let write = Task.detached {
            try pipe.fileHandleForWriting.write(contentsOf: payload)
            try pipe.fileHandleForWriting.close()
        }
        let finish = Task {
            for await _ in end { /* EOF must follow the last delivered frame. */ }
            frames.finish()
        }
        var observed = Data()
        for try await frame in stream {
            #expect(frame.channel == .standardError)
            observed.append(frame.data)
        }
        try await write.value
        await finish.value
        #expect(observed == payload)
    }
}
