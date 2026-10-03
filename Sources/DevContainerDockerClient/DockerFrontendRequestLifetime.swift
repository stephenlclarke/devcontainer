// Copyright 2026 container-engine-api project authors. SPDX-License-Identifier: Apache-2.0

// Adapted from container-engine-api 40436017e1e93012b8dab7cfc3c79783538065c3 for the Devcontainer Docker frontend.
import ContainerUnixHTTPClient
import Darwin
import Foundation

/// Interrupt with shutdown, never close from another thread: the blocking worker
/// alone closes its descriptor, so cancellation cannot act on a reused fd.
final class DockerFrontendRequestLifetime: @unchecked Sendable {
    enum Interruption { case cancelled, deadlineExceeded }

    private let lock = NSLock()
    private let deadline: ContinuousClock.Instant
    private var descriptor: Int32?
    private var interruption: Interruption?

    init(timeoutSeconds: Int) {
        deadline = .now.advanced(by: .seconds(timeoutSeconds))
    }

    func register(_ descriptor: Int32) throws {
        try lock.withLock {
            self.descriptor = descriptor
            try checkLocked()
        }
    }

    func check() throws {
        try lock.withLock { try checkLocked() }
    }

    func interrupt(_ reason: Interruption) {
        lock.withLock { interruptLocked(reason) }
    }

    func close(_ descriptor: Int32) {
        lock.withLock {
            self.descriptor = nil
            Darwin.close(descriptor)
        }
    }

    /// Duplicate under the ownership lock, so close cannot recycle the fd before
    /// an asynchronous duplex operation acquires its own reference to the socket.
    func duplicateDescriptor() throws -> Int32 {
        try lock.withLock {
            try checkLocked()
            guard let descriptor else { throw ContainerUnixHTTPClientError.invalidResponse("connection is closed") }
            let duplicate = fcntl(descriptor, F_DUPFD_CLOEXEC, 0)
            guard duplicate >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
            return duplicate
        }
    }

    func closeConnection() {
        lock.withLock {
            guard let descriptor else { return }
            _ = Darwin.shutdown(descriptor, SHUT_RDWR)
            Darwin.close(descriptor)
            self.descriptor = nil
        }
    }

    private func checkLocked() throws {
        if interruption == nil, ContinuousClock.now >= deadline {
            interruptLocked(.deadlineExceeded)
        }
        switch interruption {
        case .cancelled: throw CancellationError()
        case .deadlineExceeded: throw DockerFrontendUnixHTTPError.deadlineExceeded
        case nil: break
        }
    }

    private func interruptLocked(_ reason: Interruption) {
        guard interruption == nil else { return }
        interruption = reason
        if let descriptor {
            _ = Darwin.shutdown(descriptor, SHUT_RDWR)
        }
    }
}
