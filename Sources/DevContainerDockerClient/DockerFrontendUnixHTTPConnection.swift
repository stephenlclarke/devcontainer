// Copyright 2026 container-engine-api project authors. SPDX-License-Identifier: Apache-2.0

// Adapted from container-engine-api 40436017e1e93012b8dab7cfc3c79783538065c3 for the Devcontainer Docker frontend.
import ContainerUnixHTTPClient
import Darwin
import Foundation

/// A raw Engine upgrade connection. One read and one write may progress together;
/// calls within each direction serialize without occupying Swift executor threads.
/// Cancelling any operation interrupts the entire connection. Call `close()` when
/// finished; the absolute client deadline interrupts I/O and deinitialization
/// releases the connection if the caller did not close it explicitly.
final class DockerFrontendUnixHTTPConnection: @unchecked Sendable {
    private let lifetime: DockerFrontendRequestLifetime
    private let timer: DockerFrontendRequestDeadline
    private let readLock = NSLock()
    private let writeLock = NSLock()
    private var buffered: Data
    private var inputClosed = false

    init(lifetime: DockerFrontendRequestLifetime, timer: DockerFrontendRequestDeadline, buffered: Data) {
        self.lifetime = lifetime
        self.timer = timer
        self.buffered = buffered
    }

    deinit { close() }

    /// Returns at most 64 KiB, preserving any bytes received with the HTTP head.
    /// Nil is a clean peer EOF; raw Docker multiplex framing is left to the caller.
    func read() async throws -> Data? {
        try await perform {
            try self.readLock.withLock {
                let descriptor = try self.lifetime.duplicateDescriptor()
                defer { Darwin.close(descriptor) }
                if !self.buffered.isEmpty {
                    let result = self.buffered
                    self.buffered.removeAll()
                    return result
                }
                var bytes = [UInt8](repeating: 0, count: 64 * 1024)
                while true {
                    try self.lifetime.check()
                    let count = Darwin.read(descriptor, &bytes, bytes.count)
                    try self.lifetime.check()
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
        }
    }

    func write(_ data: Data) async throws {
        try await perform {
            try self.writeLock.withLock {
                guard !self.inputClosed else {
                    throw ContainerUnixHTTPClientError.invalidResponse("connection input is closed")
                }
                let descriptor = try self.lifetime.duplicateDescriptor()
                defer { Darwin.close(descriptor) }
                try self.writeBytes(data, to: descriptor)
            }
        }
    }

    private func writeBytes(_ data: Data, to descriptor: Int32) throws {
        try data.withUnsafeBytes { bytes in
            guard let base = bytes.baseAddress else { return }
            var offset = 0
            while offset < bytes.count {
                try lifetime.check()
                let count = Darwin.write(descriptor, base.advanced(by: offset), bytes.count - offset)
                try lifetime.check()
                if count < 0, errno == EINTR {
                    continue
                }
                guard count > 0 else { throw Self.posixError() }
                offset += count
            }
        }
    }

    /// Delivers stdin EOF without discarding stdout/stderr still arriving.
    func finishInput() async throws {
        try await perform {
            try self.writeLock.withLock {
                let descriptor = try self.lifetime.duplicateDescriptor()
                defer { Darwin.close(descriptor) }
                guard !self.inputClosed else { return }
                guard Darwin.shutdown(descriptor, SHUT_WR) == 0 else { throw Self.posixError() }
                self.inputClosed = true
            }
        }
    }

    /// Idempotent and safe concurrently with in-flight reads/writes.
    func close() {
        timer.cancel()
        lifetime.closeConnection()
    }

    private func perform<Result: Sendable>(_ operation: @escaping @Sendable () throws -> Result) async throws
        -> Result
    {
        try Task.checkCancellation()
        return try await withTaskCancellationHandler {
            do {
                let result: Result = try await withCheckedThrowingContinuation { continuation in
                    DispatchQueue.global(qos: .userInitiated).async {
                        continuation.resume(with: Swift.Result { try operation() })
                    }
                }
                try lifetime.check()
                return result
            } catch {
                try lifetime.check()
                throw error
            }
        } onCancel: {
            lifetime.interrupt(.cancelled)
        }
    }

    private static func posixError() -> POSIXError {
        POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
    }
}
