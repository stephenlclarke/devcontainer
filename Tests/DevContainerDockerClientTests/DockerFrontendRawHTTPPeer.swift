// Copyright 2026 container-engine-api project authors. SPDX-License-Identifier: Apache-2.0

import Darwin
import Foundation

/// A deliberately raw peer for malformed HTTP and trickle regressions. Both the
/// accept loop and each write are bounded; finish joins the worker before cleanup.
final class DockerFrontendRawHTTPPeer: Sendable {
    let socketPath: String
    private let root: URL
    private let listener: Int32
    private let state: RawPeerState
    private let worker: Task<Void, Never>

    convenience init(response: String, trickle: Bool = false, echoUntilEOF: Bool = false) throws {
        try self.init(response: Data(response.utf8), trickle: trickle, echoUntilEOF: echoUntilEOF)
    }

    init(response: Data, trickle: Bool = false, echoUntilEOF: Bool = false) throws {
        root = URL(fileURLWithPath: ProcessInfo.processInfo.environment["TEST_TMPDIR"]
            ?? FileManager.default.temporaryDirectory.path)
            .appendingPathComponent("raw-\(UUID().uuidString.prefix(8))")
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700]
        )
        socketPath = root.appendingPathComponent("s").path
        listener = socket(AF_UNIX, SOCK_STREAM, 0)
        guard listener >= 0 else {
            try FileManager.default.removeItem(at: root)
            throw POSIXError(.EIO)
        }
        let listener = listener
        let socketPath = socketPath
        do {
            try Self.bind(listener, socketPath: socketPath)
        } catch {
            Darwin.close(listener)
            try FileManager.default.removeItem(at: root)
            throw error
        }
        let state = RawPeerState()
        self.state = state
        worker = Task {
            await withCheckedContinuation { continuation in
                DispatchQueue.global().async {
                    Self.serve(
                        listener: listener,
                        state: state,
                        response: response,
                        trickle: trickle,
                        echoUntilEOF: echoUntilEOF
                    )
                    continuation.resume()
                }
            }
        }
    }

    private static func bind(_ listener: Int32, socketPath: String) throws {
        var address = sockaddr_un()
        guard socketPath.utf8.count < MemoryLayout.size(ofValue: address.sun_path)
        else { throw POSIXError(.ENAMETOOLONG) }
        address.sun_family = sa_family_t(AF_UNIX)
        address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
        withUnsafeMutableBytes(of: &address.sun_path) { bytes in
            socketPath.withCString { pointer in
                bytes.copyMemory(from: UnsafeRawBufferPointer(start: pointer, count: socketPath.utf8.count + 1))
            }
        }
        let result = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(listener, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard result == 0, chmod(socketPath, 0o600) == 0, listen(listener, 1) == 0,
              fcntl(listener, F_SETFL, O_NONBLOCK) == 0 else { throw POSIXError(.EIO) }
    }

    func waitForConnection() async throws {
        let deadline = ContinuousClock.now.advanced(by: .seconds(3))
        while !state.connected {
            guard ContinuousClock.now < deadline else { throw POSIXError(.ETIMEDOUT) }
            try await Task.sleep(for: .milliseconds(10))
        }
    }

    func finish() async {
        state.stop()
        worker.cancel()
        await worker.value
        Darwin.close(listener)
        try? FileManager.default.removeItem(at: root)
    }

    private static func serve(listener: Int32, state: RawPeerState, response: Data, trickle: Bool, echoUntilEOF: Bool) {
        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        var accepted: Int32 = -1
        while accepted < 0, !state.isStopped, ContinuousClock.now < deadline {
            accepted = accept(listener, nil, nil)
            if accepted < 0 {
                Thread.sleep(forTimeInterval: 0.01)
            }
        }
        guard accepted >= 0 else { return }
        state.register(accepted)
        defer { state.close(accepted) }
        var noSignal: Int32 = 1
        var timeout = timeval(tv_sec: 2, tv_usec: 0)
        let flags = fcntl(accepted, F_GETFL)
        guard setsockopt(
            accepted,
            SOL_SOCKET,
            SO_NOSIGPIPE,
            &noSignal,
            socklen_t(MemoryLayout.size(ofValue: noSignal))
        ) == 0,
            setsockopt(accepted, SOL_SOCKET, SO_SNDTIMEO, &timeout, socklen_t(MemoryLayout.size(ofValue: timeout))) ==
            0,
            setsockopt(accepted, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout.size(ofValue: timeout))) ==
            0,
            flags >= 0, fcntl(accepted, F_SETFL, flags & ~O_NONBLOCK) == 0
        else { return }
        // Drain the request before close, avoiding a reset caused by unread input.
        var request = Data()
        var input = [UInt8](repeating: 0, count: 4096)
        while request.range(of: Data("\r\n\r\n".utf8)) == nil, request.count < 65536 {
            let count = Darwin.read(accepted, &input, input.count)
            guard count > 0 else { return }
            request.append(contentsOf: input.prefix(count))
        }
        guard write(response, descriptor: accepted) else { return }
        if echoUntilEOF {
            echo(descriptor: accepted, state: state, deadline: deadline)
        }
        if trickle {
            drip(descriptor: accepted, state: state, deadline: deadline)
        }
    }

    private static func drip(descriptor: Int32, state: RawPeerState, deadline: ContinuousClock.Instant) {
        while !state.isStopped, ContinuousClock.now < deadline {
            guard write(Data("x".utf8), descriptor: descriptor) else { return }
            Thread.sleep(forTimeInterval: 0.1)
        }
    }

    private static func echo(descriptor: Int32, state: RawPeerState, deadline: ContinuousClock.Instant) {
        var input = [UInt8](repeating: 0, count: 4096)
        while !state.isStopped, ContinuousClock.now < deadline {
            let count = Darwin.read(descriptor, &input, input.count)
            if count == 0 {
                _ = write(Data("after-stdin-eof".utf8), descriptor: descriptor)
                return
            }
            guard count > 0, write(Data(input.prefix(count)), descriptor: descriptor) else { return }
        }
    }

    private static func write(_ data: Data, descriptor: Int32) -> Bool {
        data.withUnsafeBytes { bytes in
            guard let pointer = bytes.baseAddress else { return true }
            var offset = 0
            while offset < bytes.count {
                let count = Darwin.write(descriptor, pointer.advanced(by: offset), bytes.count - offset)
                guard count > 0 else { return false }
                offset += count
            }
            return true
        }
    }
}

private final class RawPeerState: @unchecked Sendable {
    private let lock = NSLock()
    private var descriptor: Int32?
    private var stopped = false
    var isStopped: Bool {
        lock.withLock { stopped }
    }

    var connected: Bool {
        lock.withLock { descriptor != nil }
    }

    func register(_ descriptor: Int32) {
        lock.withLock {
            self.descriptor = descriptor
            if stopped {
                _ = Darwin.shutdown(descriptor, SHUT_RDWR)
            }
        }
    }

    func stop() {
        lock.withLock {
            stopped = true
            if let descriptor {
                _ = Darwin.shutdown(descriptor, SHUT_RDWR)
            }
        }
    }

    func close(_ descriptor: Int32) {
        lock.withLock {
            self.descriptor = nil
            Darwin.close(descriptor)
        }
    }
}
