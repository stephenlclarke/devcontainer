//===----------------------------------------------------------------------===//
// Copyright 2026 devcontainer and container-engine-api project authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//===----------------------------------------------------------------------===//

// Adapted from container-engine-api 40436017e1e93012b8dab7cfc3c79783538065c3 for the Devcontainer Docker frontend.
import ContainerEngineWire
import ContainerUnixHTTPClient
import Darwin
import Foundation

/// The API 48 shared error enum has no absolute-deadline case.
enum DockerFrontendUnixHTTPError: Error, Equatable, CustomStringConvertible {
    case deadlineExceeded

    var description: String {
        "Engine request exceeded its absolute deadline"
    }
}

/// A current-user, Unix-domain HTTP/1.1 client for the shared Container Engine gateway.
///
/// Each request owns one connection. Socket work runs on a blocking dispatch worker so it
/// never occupies the cooperative Swift executor. Response chunks are delivered in
/// order and are bounded before the caller callback is invoked.
final class DockerFrontendUnixHTTPClient: @unchecked Sendable {
    typealias BodyHandler = @Sendable (Data) throws -> Void
    typealias ResponseHeadHandler = @Sendable (ContainerUnixHTTPClientResponse) throws -> Void

    private static let readSize = 64 * 1024
    private static let maximumHeaderBytes = 64 * 1024
    private let socketPath: String
    private let timeoutSeconds: Int

    init(socketPath: String, timeoutSeconds: Int = 300) throws {
        let capacity = withUnsafeBytes(of: sockaddr_un().sun_path) { $0.count }
        guard socketPath.hasPrefix("/"), !socketPath.contains("\0"), socketPath.utf8.count < capacity else {
            throw ContainerUnixHTTPClientError.invalidSocketPath(socketPath)
        }
        guard timeoutSeconds > 0, timeoutSeconds <= Int(Int64.max / 1_000_000_000) else {
            throw ContainerUnixHTTPClientError.invalidResponse("timeout must be a positive representable duration")
        }
        self.socketPath = socketPath
        self.timeoutSeconds = timeoutSeconds
    }

    func send(
        _ request: DockerHTTPRequest,
        maximumBodyBytes: Int = 16 * 1024 * 1024,
        onResponseHead: ResponseHeadHandler? = nil
    ) async throws -> ContainerUnixHTTPClientResponse {
        let body = DataAccumulator()
        let response = try await stream(request, maximumBodyBytes: maximumBodyBytes, onResponseHead: onResponseHead) {
            body.append($0)
        }
        return ContainerUnixHTTPClientResponse(
            status: response.status,
            headers: response.headers,
            body: body.value
        )
    }

    /// The optional callback acknowledges a parsed successful response head
    /// exactly once, before any body bytes. Its body is empty. Throwing aborts
    /// the request; an HTTP failure never invokes this callback. A successful
    /// head does not guarantee that the later response body will complete.
    func stream(
        _ request: DockerHTTPRequest,
        maximumBodyBytes: Int? = nil,
        onResponseHead: ResponseHeadHandler? = nil,
        onBody: @escaping BodyHandler
    ) async throws -> ContainerUnixHTTPClientResponse {
        try Task.checkCancellation()
        guard maximumBodyBytes.map({ $0 >= 0 }) ?? true else {
            throw ContainerUnixHTTPClientError.invalidResponse("body limit must not be negative")
        }
        let lifetime = DockerFrontendRequestLifetime(timeoutSeconds: timeoutSeconds)
        let timer = DockerFrontendRequestDeadline(lifetime: lifetime, timeoutSeconds: timeoutSeconds)
        defer { timer.cancel() }
        return try await withTaskCancellationHandler {
            do {
                let response: ContainerUnixHTTPClientResponse =
                    try await withCheckedThrowingContinuation { continuation in
                        DispatchQueue.global(qos: .userInitiated).async { [self] in
                            continuation.resume(with: Result {
                                try sendBlocking(
                                    request,
                                    lifetime: lifetime,
                                    maximumBodyBytes: maximumBodyBytes,
                                    onResponseHead: onResponseHead,
                                    onBody: onBody
                                )
                            })
                        }
                    }
                try lifetime.check()
                return response
            } catch {
                try lifetime.check()
                throw error
            }
        } onCancel: {
            lifetime.interrupt(.cancelled)
        }
    }

    /// Opens a Docker TCP-style HTTP upgrade over the same protected Unix socket.
    /// The deadline covers the handshake and the entire returned connection.
    func openDuplex(_ request: DockerHTTPRequest) async throws -> DockerFrontendUnixHTTPConnection {
        try Task.checkCancellation()
        var upgraded = request
        guard request.headers.values(for: "Connection").isEmpty,
              request.headers.values(for: "Upgrade").isEmpty
        else {
            throw ContainerUnixHTTPClientError.invalidResponse("upgrade headers are owned by openDuplex")
        }
        upgraded.headers.append(name: "Connection", value: "Upgrade")
        upgraded.headers.append(name: "Upgrade", value: "tcp")
        let upgradeRequest = upgraded
        let lifetime = DockerFrontendRequestLifetime(timeoutSeconds: timeoutSeconds)
        let timer = DockerFrontendRequestDeadline(lifetime: lifetime, timeoutSeconds: timeoutSeconds)
        return try await withTaskCancellationHandler {
            do {
                let connection: DockerFrontendUnixHTTPConnection =
                    try await withCheckedThrowingContinuation { continuation in
                        DispatchQueue.global(qos: .userInitiated).async { [self] in
                            continuation.resume(with: Result {
                                try openDuplexBlocking(upgradeRequest, lifetime: lifetime, timer: timer)
                            })
                        }
                    }
                try lifetime.check()
                return connection
            } catch {
                timer.cancel()
                lifetime.closeConnection()
                try lifetime.check()
                throw error
            }
        } onCancel: {
            lifetime.interrupt(.cancelled)
        }
    }

    private func openDuplexBlocking(
        _ request: DockerHTTPRequest, lifetime: DockerFrontendRequestLifetime, timer: DockerFrontendRequestDeadline
    ) throws -> DockerFrontendUnixHTTPConnection {
        let descriptor = try connect(lifetime: lifetime)
        try write(Self.serialized(request), to: descriptor, lifetime: lifetime)
        var reader = SocketReader(descriptor: descriptor, lifetime: lifetime)
        let head = try Self.parseHead(reader.readHead(maximumBytes: Self.maximumHeaderBytes))
        guard head.status == 101 else {
            let collector = BodyCollector(maximumBytes: 64 * 1024, handler: { _ in /* Retain error only. */ })
            try readBody(request: request, head: head, reader: &reader, collector: collector)
            throw ContainerUnixHTTPClientError.server(status: head.status, message: collector.errorMessage)
        }
        let connectionTokens = head.headers["connection"]?.lowercased().split(separator: ",").map {
            $0.trimmingCharacters(in: .whitespaces)
        } ?? []
        guard connectionTokens.contains("upgrade"), head.headers["upgrade"]?.lowercased() == "tcp",
              head.headers["transfer-encoding"] == nil, head.headers["content-length"] == nil
        else {
            throw ContainerUnixHTTPClientError.invalidResponse("invalid Engine upgrade response")
        }
        return DockerFrontendUnixHTTPConnection(lifetime: lifetime, timer: timer, buffered: reader.takeBuffered())
    }

    private func sendBlocking(
        _ request: DockerHTTPRequest,
        lifetime: DockerFrontendRequestLifetime,
        maximumBodyBytes: Int?,
        onResponseHead: ResponseHeadHandler?,
        onBody: @escaping BodyHandler
    ) throws -> ContainerUnixHTTPClientResponse {
        let descriptor = try connect(lifetime: lifetime)
        defer { lifetime.close(descriptor) }
        try write(Self.serialized(request), to: descriptor, lifetime: lifetime)

        var reader = SocketReader(descriptor: descriptor, lifetime: lifetime)
        let head = try reader.readHead(maximumBytes: Self.maximumHeaderBytes)
        let responseHead = try Self.parseHead(head)
        let success = (200 ... 299).contains(responseHead.status)
        if success {
            try lifetime.check()
            try onResponseHead?(.init(status: responseHead.status, headers: responseHead.headers, body: Data()))
            try lifetime.check()
        }
        // An error document is diagnostic data, never part of a successful
        // event/log stream. Bound it even when the successful stream is unbounded.
        let collector = BodyCollector(
            maximumBytes: success ? maximumBodyBytes : min(maximumBodyBytes ?? 65536, 65536),
            handler: { data in
                if success {
                    try onBody(data)
                }
            }
        )
        try readBody(request: request, head: responseHead, reader: &reader, collector: collector)

        guard success else {
            throw ContainerUnixHTTPClientError.server(
                status: responseHead.status,
                message: collector.errorMessage
            )
        }
        return ContainerUnixHTTPClientResponse(
            status: responseHead.status,
            headers: responseHead.headers,
            body: Data()
        )
    }

    private func readBody(
        request: DockerHTTPRequest, head: (status: Int, headers: [String: String]),
        reader: inout SocketReader, collector: BodyCollector
    ) throws {
        if request.method == .head || Self.statusHasNoBody(head.status) {
            // RFC 9110 responses with no content may keep the connection open.
        } else if head.headers["transfer-encoding"]?.lowercased().contains("chunked") == true {
            try reader.readChunked(collector.accept)
        } else if let value = head.headers["content-length"], let length = Int(value), length >= 0 {
            try reader.readExactly(length, handler: collector.accept)
        } else {
            try reader.readUntilEOF(collector.accept)
        }
    }

    private static func statusHasNoBody(_ status: Int) -> Bool {
        (100 ... 199).contains(status) || status == 204 || status == 304
    }

    private func connect(lifetime: DockerFrontendRequestLifetime) throws -> Int32 {
        try lifetime.check()
        try validateSocket()
        let descriptor = socket(AF_UNIX, SOCK_STREAM, 0)
        guard descriptor >= 0 else { throw Self.posixError() }
        do {
            try lifetime.register(descriptor)
            try configureSocket(descriptor)
            var address = sockaddr_un()
            address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
            address.sun_family = sa_family_t(AF_UNIX)
            withUnsafeMutableBytes(of: &address.sun_path) { bytes in
                socketPath.withCString { value in
                    bytes.copyMemory(from: UnsafeRawBufferPointer(start: value, count: socketPath.utf8.count + 1))
                }
            }
            // A full accept backlog must remain cancellable before the socket is connected.
            let flags = fcntl(descriptor, F_GETFL)
            guard flags >= 0, fcntl(descriptor, F_SETFL, flags | O_NONBLOCK) == 0 else {
                throw Self.posixError()
            }
            let result = withUnsafePointer(to: &address) { pointer in
                pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.connect(descriptor, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
                }
            }
            if result != 0 {
                guard errno == EINPROGRESS else { throw Self.posixError() }
                try waitForConnection(descriptor, lifetime: lifetime)
            }
            try lifetime.check()
            guard fcntl(descriptor, F_SETFL, flags) == 0 else { throw Self.posixError() }
            return descriptor
        } catch {
            lifetime.close(descriptor)
            throw error
        }
    }

    private func configureSocket(_ descriptor: Int32) throws {
        var noSignal: Int32 = 1
        let signalSize = socklen_t(MemoryLayout.size(ofValue: noSignal))
        guard setsockopt(descriptor, SOL_SOCKET, SO_NOSIGPIPE, &noSignal, signalSize) == 0 else {
            throw Self.posixError()
        }
        var timeout = timeval(tv_sec: timeoutSeconds, tv_usec: 0)
        let timeoutSize = socklen_t(MemoryLayout.size(ofValue: timeout))
        guard setsockopt(descriptor, SOL_SOCKET, SO_RCVTIMEO, &timeout, timeoutSize) == 0,
              setsockopt(descriptor, SOL_SOCKET, SO_SNDTIMEO, &timeout, timeoutSize) == 0
        else {
            throw Self.posixError()
        }
    }

    private func waitForConnection(_ descriptor: Int32, lifetime: DockerFrontendRequestLifetime) throws {
        while true {
            try lifetime.check()
            var item = pollfd(fd: descriptor, events: Int16(POLLOUT), revents: 0)
            let result = poll(&item, 1, 100)
            if result == 0 || (result < 0 && errno == EINTR) {
                continue
            }
            guard result > 0 else { throw Self.posixError() }
            var error: Int32 = 0
            var length = socklen_t(MemoryLayout.size(ofValue: error))
            guard getsockopt(descriptor, SOL_SOCKET, SO_ERROR, &error, &length) == 0 else { throw Self.posixError() }
            guard error == 0 else { throw POSIXError(POSIXErrorCode(rawValue: error) ?? .EIO) }
            return
        }
    }

    private func validateSocket() throws {
        var status = stat()
        guard lstat(socketPath, &status) == 0 else { throw Self.posixError() }
        guard status.st_mode & S_IFMT == S_IFSOCK else {
            throw ContainerUnixHTTPClientError.unsafeSocket("path is not a Unix socket")
        }
        guard status.st_uid == geteuid() else {
            throw ContainerUnixHTTPClientError.unsafeSocket("socket is not owned by the current user")
        }
        guard status.st_mode & (S_IRWXG | S_IRWXO) == 0 else {
            throw ContainerUnixHTTPClientError.unsafeSocket("group or other permissions are present")
        }
        guard status.st_nlink == 1 else {
            throw ContainerUnixHTTPClientError.unsafeSocket("socket has an unexpected link count")
        }
    }

    private static func serialized(_ request: DockerHTTPRequest) -> Data {
        var headers = Array(request.headers).map { ($0.name, $0.value) }
        if request.headers.values(for: "Host").isEmpty {
            headers.append(("Host", "localhost"))
        }
        if request.headers.values(for: "User-Agent").isEmpty {
            headers.append(("User-Agent", "container-engine-client/1"))
        }
        if request.headers.values(for: "Content-Length").isEmpty {
            headers.append(("Content-Length", String(request.body.count)))
        }
        if request.headers.values(for: "Connection").isEmpty {
            headers.append(("Connection", "close"))
        }
        let lines = headers.sorted { lhs, rhs in
            lhs.0.lowercased() == rhs.0.lowercased() ? lhs.1 < rhs.1 : lhs.0.lowercased() < rhs.0.lowercased()
        }.map { "\($0.0): \($0.1)" }.joined(separator: "\r\n")
        var data = Data("\(request.method.rawValue) \(request.target) HTTP/1.1\r\n\(lines)\r\n\r\n".utf8)
        data.append(request.body)
        return data
    }

    private static func parseHead(_ data: Data) throws -> (status: Int, headers: [String: String]) {
        guard let text = String(data: data, encoding: .utf8) else {
            throw ContainerUnixHTTPClientError.invalidResponse("headers are not UTF-8")
        }
        let lines = text.components(separatedBy: "\r\n")
        guard let statusLine = lines.first,
              statusLine.hasPrefix("HTTP/"),
              statusLine.split(separator: " ").count >= 2,
              let status = Int(statusLine.split(separator: " ")[1])
        else {
            throw ContainerUnixHTTPClientError.invalidResponse("missing HTTP status")
        }
        var headers: [String: String] = [:]
        for line in lines.dropFirst() where !line.isEmpty {
            guard let colon = line.firstIndex(of: ":") else {
                throw ContainerUnixHTTPClientError.invalidResponse("malformed header")
            }
            let name = line[..<colon].lowercased()
            let value = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
            headers[name] = headers[name].map { "\($0), \(value)" } ?? value
        }
        return (status, headers)
    }

    private func write(_ data: Data, to descriptor: Int32, lifetime: DockerFrontendRequestLifetime) throws {
        try data.withUnsafeBytes { bytes in
            guard let base = bytes.baseAddress else { return }
            var offset = 0
            while offset < bytes.count {
                try lifetime.check()
                let count = Darwin.write(descriptor, base.advanced(by: offset), bytes.count - offset)
                if count < 0, errno == EINTR {
                    continue
                }
                guard count > 0 else { throw Self.posixError() }
                offset += count
            }
        }
    }

    private static func posixError() -> POSIXError {
        POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
    }
}

private final class DataAccumulator: @unchecked Sendable {
    private let lock = NSLock()
    private var data = Data()

    func append(_ chunk: Data) {
        lock.withLock { data.append(chunk) }
    }

    var value: Data {
        lock.withLock { data }
    }
}

private final class BodyCollector: @unchecked Sendable {
    private let maximumBytes: Int?
    private let handler: DockerFrontendUnixHTTPClient.BodyHandler
    private(set) var captured = Data()
    private var total = 0

    init(maximumBytes: Int?, handler: @escaping DockerFrontendUnixHTTPClient.BodyHandler) {
        self.maximumBytes = maximumBytes
        self.handler = handler
    }

    func accept(_ data: Data) throws {
        total += data.count
        if let maximumBytes, total > maximumBytes {
            throw ContainerUnixHTTPClientError.responseTooLarge(maximumBytes)
        }
        if captured.count < 64 * 1024 {
            captured.append(data.prefix(64 * 1024 - captured.count))
        }
        try handler(data)
    }

    var errorMessage: String {
        if let object = try? JSONSerialization.jsonObject(with: captured) as? [String: Any],
           let message = object["message"] as? String
        {
            return message
        }
        return String(data: captured, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
            ?? "unknown error"
    }
}

private struct SocketReader {
    let descriptor: Int32
    let lifetime: DockerFrontendRequestLifetime
    private var buffer = Data()

    init(descriptor: Int32, lifetime: DockerFrontendRequestLifetime) {
        self.descriptor = descriptor
        self.lifetime = lifetime
    }

    mutating func takeBuffered() -> Data {
        defer { buffer.removeAll() }
        return buffer
    }

    mutating func readHead(maximumBytes: Int) throws -> Data {
        let marker = Data("\r\n\r\n".utf8)
        while buffer.range(of: marker) == nil {
            guard buffer.count < maximumBytes else {
                throw ContainerUnixHTTPClientError.responseTooLarge(maximumBytes)
            }
            try readMore()
        }
        guard let range = buffer.range(of: marker) else {
            throw ContainerUnixHTTPClientError.invalidResponse("missing header terminator")
        }
        guard buffer.distance(from: buffer.startIndex, to: range.upperBound) <= maximumBytes else {
            throw ContainerUnixHTTPClientError.responseTooLarge(maximumBytes)
        }
        let head = Data(buffer[..<range.lowerBound])
        buffer.removeSubrange(..<range.upperBound)
        return head
    }

    mutating func readExactly(_ count: Int, handler: (Data) throws -> Void) throws {
        var remaining = count
        while remaining > 0 {
            try lifetime.check()
            if buffer.isEmpty {
                try readMore()
            }
            let size = min(remaining, buffer.count)
            try handler(Data(buffer.prefix(size)))
            buffer.removeFirst(size)
            remaining -= size
        }
    }

    mutating func readUntilEOF(_ handler: (Data) throws -> Void) throws {
        if !buffer.isEmpty {
            try handler(buffer)
            buffer.removeAll(keepingCapacity: true)
        }
        while true {
            let data = try readChunk()
            guard !data.isEmpty else { return }
            try handler(data)
        }
    }

    mutating func readChunked(_ handler: (Data) throws -> Void) throws {
        while true {
            let line = try readLine()
            let token = line.split(separator: ";", maxSplits: 1, omittingEmptySubsequences: false)[0]
            guard !token.isEmpty, token.utf8.allSatisfy({ byte in
                (48 ... 57).contains(byte) || (65 ... 70).contains(byte) || (97 ... 102).contains(byte)
            }), let size = Int(token, radix: 16) else {
                throw ContainerUnixHTTPClientError.invalidResponse("invalid chunk size")
            }
            if size == 0 {
                var trailerBytes = 0
                while true {
                    let trailer = try readLine()
                    trailerBytes += trailer.utf8.count + 2
                    guard trailerBytes <= 64 * 1024 else {
                        throw ContainerUnixHTTPClientError.responseTooLarge(64 * 1024)
                    }
                    if trailer.isEmpty {
                        break
                    }
                }
                return
            }
            try readExactly(size, handler: handler)
            guard try readLine().isEmpty else {
                throw ContainerUnixHTTPClientError.invalidResponse("invalid chunk terminator")
            }
        }
    }

    private mutating func readLine() throws -> String {
        let maximumBytes = 8192
        let marker = Data("\r\n".utf8)
        while buffer.range(of: marker) == nil {
            guard buffer.count < maximumBytes else {
                throw ContainerUnixHTTPClientError.responseTooLarge(maximumBytes)
            }
            try readMore()
        }
        guard let range = buffer.range(of: marker) else {
            throw ContainerUnixHTTPClientError.invalidResponse("missing line terminator")
        }
        guard buffer.distance(from: buffer.startIndex, to: range.upperBound) <= maximumBytes else {
            throw ContainerUnixHTTPClientError.responseTooLarge(maximumBytes)
        }
        let data = Data(buffer[..<range.lowerBound])
        buffer.removeSubrange(..<range.upperBound)
        guard let line = String(data: data, encoding: .utf8) else {
            throw ContainerUnixHTTPClientError.invalidResponse("line is not UTF-8")
        }
        return line
    }

    private mutating func readMore() throws {
        let data = try readChunk()
        guard !data.isEmpty else {
            throw ContainerUnixHTTPClientError.invalidResponse("unexpected end of response")
        }
        buffer.append(data)
    }

    private func readChunk() throws -> Data {
        var bytes = [UInt8](repeating: 0, count: 64 * 1024)
        while true {
            try lifetime.check()
            let count = Darwin.read(descriptor, &bytes, bytes.count)
            try lifetime.check()
            if count == 0 {
                return Data()
            }
            if count < 0, errno == EINTR {
                continue
            }
            guard count > 0 else {
                throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
            }
            return Data(bytes.prefix(count))
        }
    }
}
