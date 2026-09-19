// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Darwin
@testable import DevContainerAppleRuntime
import DevContainerModel
import Foundation
import Testing

struct ProcessPipeMonitorCancellationTests {
    @Test
    func `cancellation joins in flight delivery before signaling completion`() async throws {
        let pipe = Pipe()
        let (entered, enteredContinuation) = AsyncStream<Void>.makeStream()
        let (_, frames) = AsyncThrowingStream<RuntimeIOFrame, any Error>.makeStream()
        let release = DispatchSemaphore(value: 0)
        let delivery = MonitorDelivery()
        let monitor = ProcessPipeMonitor(
            handle: pipe.fileHandleForReading, channel: .standardOutput,
            frames: frames,
            onRead: { count in
                delivery.recordRead(count)
                enteredContinuation.finish()
                #expect(release.wait(timeout: .now() + 10) == .success)
            },
            onFinish: { delivery.recordFinish() }
        )
        defer { release.signal(); monitor.cancel() }
        try pipe.fileHandleForWriting.write(contentsOf: Data([42]))
        for await _ in entered { /* Reader entered its delivery callback. */ }
        let joined = MonitorDelivery()
        let (waiting, waitingContinuation) = AsyncStream<Void>.makeStream()
        let waiter = Task {
            waitingContinuation.finish()
            await monitor.waitForCompletion()
            joined.recordFinish()
        }
        waiter.cancel()
        for await _ in waiting { /* The cancelled waiter has begun its join. */ }
        // Allow a cancellation-short-circuited join to expose premature return
        // while the worker remains deterministically held in delivery.
        try await Task.sleep(for: .milliseconds(50))
        monitor.cancel()
        #expect(delivery.snapshot() == [1])
        #expect(joined.snapshot().isEmpty)
        release.signal()
        await waiter.value
        #expect(delivery.snapshot() == [1, 0])
        #expect(joined.snapshot() == [0])
        // Completion observed after the worker exits remains immediately usable.
        await monitor.waitForCompletion()
        monitor.cancel()
        #expect(delivery.snapshot() == [1, 0])
    }

    @Test
    func `idle monitor cancellation finishes without closing a caller owned descriptor`() async throws {
        let pipe = Pipe()
        let (_, frames) = AsyncThrowingStream<RuntimeIOFrame, any Error>.makeStream()
        let monitor = ProcessPipeMonitor(
            handle: pipe.fileHandleForReading, channel: .standardOutput,
            frames: frames, closeHandleOnFinish: false
        )
        monitor.cancel()
        await monitor.waitForCompletion()
        #expect(fcntl(pipe.fileHandleForReading.fileDescriptor, F_GETFD) >= 0)
        try pipe.fileHandleForWriting.write(contentsOf: Data([42]))
        #expect(try pipe.fileHandleForReading.read(upToCount: 1) == Data([42]))
    }

    @Test
    func `nonblocking monitor drains complete output before EOF`() async throws {
        let pipe = Pipe()
        let (stream, frames) = AsyncThrowingStream<RuntimeIOFrame, any Error>.makeStream()
        let monitor = ProcessPipeMonitor(
            handle: pipe.fileHandleForReading, channel: .standardError, frames: frames
        )
        defer { monitor.cancel() }
        let payload = Data(repeating: 23, count: 1024 * 1024)
        let write = Task.detached {
            try pipe.fileHandleForWriting.write(contentsOf: payload)
            try pipe.fileHandleForWriting.close()
        }
        let finish = Task {
            await monitor.waitForCompletion()
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

private final class MonitorDelivery: @unchecked Sendable {
    private let lock = NSLock()
    private var events: [Int] = []

    func recordRead(_ bytes: Int) {
        lock.withLock { events.append(bytes) }
    }

    func recordFinish() {
        lock.withLock { events.append(0) }
    }

    func snapshot() -> [Int] {
        lock.withLock { events }
    }
}
