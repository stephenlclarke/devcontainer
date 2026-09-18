// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Darwin
import Foundation

/// The frontend is the sole writer of its stdout/stderr streams. Poll before
/// writes no larger than POSIX's minimum PIPE_BUF, so an undrained pipe remains
/// cancellable without setting O_NONBLOCK on the parent's open-file description.
/// The owning executable ignores SIGPIPE so a concurrent reader disconnect is
/// reported as EPIPE; this reusable type never changes process signal policy.
public final class DockerFrontendOutput: @unchecked Sendable {
    private let descriptor: Int32
    private let stateLock = NSLock()
    private let writeLock = NSLock()
    private var cancelled = false

    public init(descriptor: Int32) throws {
        let owned = fcntl(descriptor, F_DUPFD_CLOEXEC, 0)
        guard owned >= 0 else { throw Self.posixError() }
        self.descriptor = owned
    }

    deinit { Darwin.close(descriptor) }

    public func write(_ data: Data) async throws {
        try Task.checkCancellation()
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, any Error>) in
                DispatchQueue.global(qos: .userInitiated).async { [self] in
                    continuation.resume(with: Result { try writeLock.withLock { try writeBlocking(data) } })
                }
            }
        } onCancel: {
            self.stateLock.withLock { self.cancelled = true }
        }
    }

    private func writeBlocking(_ data: Data) throws {
        try data.withUnsafeBytes { bytes in
            guard let base = bytes.baseAddress else { return }
            var offset = 0
            while offset < bytes.count {
                try checkCancellation()
                var item = pollfd(fd: descriptor, events: Int16(POLLOUT), revents: 0)
                let ready = poll(&item, 1, 100)
                if ready == 0 || (ready < 0 && errno == EINTR) {
                    continue
                }
                guard ready > 0 else { throw Self.posixError() }
                try checkCancellation()
                guard item.revents & Int16(POLLNVAL) == 0 else { throw POSIXError(.EBADF) }
                guard item.revents & Int16(POLLERR | POLLHUP) == 0 else { throw POSIXError(.EPIPE) }
                let count = Darwin.write(descriptor, base.advanced(by: offset), min(512, bytes.count - offset))
                let code = errno
                try checkCancellation()
                if count < 0, code == EINTR {
                    continue
                }
                guard count > 0 else { throw POSIXError(POSIXErrorCode(rawValue: code) ?? .EIO) }
                offset += count
            }
        }
    }

    private func checkCancellation() throws {
        if stateLock.withLock({ cancelled }) {
            throw CancellationError()
        }
    }

    private static func posixError() -> POSIXError {
        POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
    }
}
