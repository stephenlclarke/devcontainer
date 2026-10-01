// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Darwin
import DevContainerModel
import Foundation

/// Drains process output on dedicated OS threads. stdout and stderr can
/// block independently without occupying Swift's cooperative executor or
/// relying on a one-shot readiness edge while a child performs duplex I/O.
final class ProcessPipeMonitor: @unchecked Sendable {
    private let handle: FileHandle
    private let descriptor: Int32
    private let channel: RuntimeIOChannel
    private let frames: AsyncThrowingStream<
        RuntimeIOFrame,
        any Error
    >.Continuation?
    private let onFrame: (@Sendable (RuntimeIOFrame) -> Void)?
    private let onError: (@Sendable (any Error) -> Void)?
    private let endOnEIO: Bool
    private let closeHandleOnFinish: Bool
    private let onRead: (@Sendable (Int) -> Void)?
    private let onFinish: (@Sendable () -> Void)?
    private let onEOF: (@Sendable () -> Void)?
    private let stateLock = NSLock()
    private var finished = false
    private var cancelled = false
    private var completionWaiters: [CheckedContinuation<Void, Never>] = []

    init(
        handle: FileHandle,
        channel: RuntimeIOChannel,
        frames: AsyncThrowingStream<
            RuntimeIOFrame,
            any Error
        >.Continuation? = nil,
        endOnEIO: Bool = false,
        closeHandleOnFinish: Bool = true,
        onRead: (@Sendable (Int) -> Void)? = nil,
        onFinish: (@Sendable () -> Void)? = nil,
        onEOF: (@Sendable () -> Void)? = nil,
        onFrame: (@Sendable (RuntimeIOFrame) -> Void)? = nil,
        onError: (@Sendable (any Error) -> Void)? = nil
    ) {
        self.handle = handle
        descriptor = handle.fileDescriptor
        self.channel = channel
        self.frames = frames
        self.endOnEIO = endOnEIO
        self.closeHandleOnFinish = closeHandleOnFinish
        self.onRead = onRead
        self.onFinish = onFinish
        self.onEOF = onEOF
        self.onFrame = onFrame
        self.onError = onError
        Thread.detachNewThread { [self] in
            Thread.current.name =
                "io.github.stephenlclarke.devcontainer.cli-process-\(channel)"
            drain()
        }
    }

    func cancel() {
        stateLock.withLock { cancelled = true }
    }

    func waitForCompletion() async {
        // Unlike AsyncStream iteration, this join must not terminate early
        // merely because its caller is cancelled. The reader owns completion.
        await withCheckedContinuation { continuation in
            let completed = stateLock.withLock {
                if finished {
                    return true
                }
                completionWaiters.append(continuation)
                return false
            }
            if completed {
                continuation.resume()
            }
        }
    }

    private func drain() {
        defer { finish() }
        let flags = fcntl(descriptor, F_GETFL)
        guard flags >= 0, fcntl(descriptor, F_SETFL, flags | O_NONBLOCK) >= 0 else {
            fail(POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO))
            return
        }
        var buffer = [UInt8](repeating: 0, count: 64 * 1024)
        while !stateLock.withLock({ cancelled }) {
            var readiness = pollfd(fd: descriptor, events: Int16(POLLIN), revents: 0)
            let ready = Darwin.poll(&readiness, 1, 100)
            if stateLock.withLock({ cancelled }) {
                return
            }
            if ready == 0 || (ready < 0 && errno == EINTR) {
                continue
            }
            guard ready > 0 else {
                fail(POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO))
                return
            }
            let count = buffer.withUnsafeMutableBytes { bytes in
                Darwin.read(descriptor, bytes.baseAddress, bytes.count)
            }
            if count > 0 {
                onRead?(count)
                let frame = RuntimeIOFrame(channel: channel, data: Data(buffer.prefix(count)))
                frames?.yield(frame)
                onFrame?(frame)
                continue
            }
            if count == 0 {
                naturalEOF()
                return
            }
            if !retryRead(after: errno) {
                return
            }
        }
    }

    private func retryRead(after readError: Int32) -> Bool {
        // Capture errno before any locking. On Darwin EWOULDBLOCK is
        // EAGAIN, so the two names need only one retry branch.
        switch readError {
        case EINTR, EAGAIN:
            return true
        case EIO where endOnEIO:
            naturalEOF()
            return false
        default:
            if !stateLock.withLock({ cancelled }) {
                fail(POSIXError(POSIXErrorCode(rawValue: readError) ?? .EIO))
            }
            return false
        }
    }

    private func naturalEOF() {
        // EOF is distinct from cancellation, read failure, and joining the
        // monitor. Only the reader can establish the end of a source stream.
        if !stateLock.withLock({ cancelled }) {
            onEOF?()
        }
    }

    private func finish() {
        // Called once, by the reader's defer; cancellation never calls finish.
        if closeHandleOnFinish {
            try? handle.close()
        }
        onFinish?()
        let waiters = stateLock.withLock {
            finished = true
            defer { completionWaiters.removeAll() }
            return completionWaiters
        }
        for waiter in waiters {
            waiter.resume()
        }
    }

    private func fail(_ error: any Error) {
        frames?.finish(throwing: error)
        onError?(error)
    }
}
