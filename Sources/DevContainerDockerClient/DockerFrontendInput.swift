// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Darwin
import Foundation

/// A cancellation-aware single reader for the frontend's inherited stdin.
/// Polling bounds cancellation without changing the parent's descriptor flags.
public final class DockerFrontendInput: @unchecked Sendable {
    private let descriptor: Int32
    private let lock = NSLock()
    private let readLock = NSLock()
    private var cancelled = false

    public init(descriptor: Int32 = STDIN_FILENO) throws {
        let owned = fcntl(descriptor, F_DUPFD_CLOEXEC, 0)
        guard owned >= 0 else { throw Self.posixError() }
        self.descriptor = owned
    }

    deinit { Darwin.close(descriptor) }

    public func read() async throws -> Data? {
        try Task.checkCancellation()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                DispatchQueue.global(qos: .userInitiated).async { [self] in
                    continuation.resume(with: Result { try readLock.withLock { try readBlocking() } })
                }
            }
        } onCancel: {
            self.lock.withLock { self.cancelled = true }
        }
    }

    private func readBlocking() throws -> Data? {
        while true {
            try checkCancellation()
            var item = pollfd(fd: descriptor, events: Int16(POLLIN), revents: 0)
            let result = poll(&item, 1, 100)
            if result == 0 || (result < 0 && errno == EINTR) {
                continue
            }
            guard result > 0 else { throw Self.posixError() }
            try checkCancellation()
            guard item.revents & Int16(POLLNVAL) == 0 else { throw POSIXError(.EBADF) }
            var bytes = [UInt8](repeating: 0, count: 64 * 1024)
            let count = Darwin.read(descriptor, &bytes, bytes.count)
            try checkCancellation()
            if count == 0 {
                return nil
            }
            if count < 0, errno == EINTR {
                continue
            }
            guard count > 0 else { throw Self.posixError() }
            return Data(bytes.prefix(count))
        }
    }

    private func checkCancellation() throws {
        if lock.withLock({ cancelled }) {
            throw CancellationError()
        }
    }

    private static func posixError() -> POSIXError {
        POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
    }
}
