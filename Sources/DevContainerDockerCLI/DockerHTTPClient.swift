//===----------------------------------------------------------------------===//
// Copyright 2026 devcontainer project authors.
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

import Darwin
import DevContainerModel
import Foundation

public struct DockerHTTPRequest: Equatable, Sendable {
    public var method: String
    public var target: String
    public var headers: [String: String]
    public var body: Data

    public init(
        method: String,
        target: String,
        headers: [String: String] = [:],
        body: Data = Data()
    ) {
        self.method = method
        self.target = target
        self.headers = headers
        self.body = body
    }
}

public struct DockerHTTPResponse: Equatable, Sendable {
    public var status: Int
    public var headers: [String: String]
    public var body: Data

    public init(status: Int, headers: [String: String], body: Data) {
        self.status = status
        self.headers = headers
        self.body = body
    }
}

public enum DockerHTTPClientError: Error, Equatable, CustomStringConvertible {
    case invalidSocketPath(String)
    case unsafeSocket(String)
    case invalidResponse(String)
    case responseTooLarge(Int)
    case server(status: Int, message: String)

    public var description: String {
        switch self {
        case let .invalidSocketPath(path):
            "invalid local engine socket path: \(path)"
        case let .unsafeSocket(message):
            "unsafe local engine socket: \(message)"
        case let .invalidResponse(message):
            "invalid engine response: \(message)"
        case let .responseTooLarge(limit):
            "engine response exceeded \(limit) bytes"
        case let .server(status, message):
            "engine returned HTTP \(status): \(message)"
        }
    }
}

public protocol DockerEngineTransport: Sendable {
    func send(
        _ request: DockerHTTPRequest,
        maximumBodyBytes: Int?,
        onBody: @escaping (Data) throws -> Void
    ) throws -> DockerHTTPResponse
}

public protocol DockerEngineHijackTransport: DockerEngineTransport {
    func hijack(
        _ request: DockerHTTPRequest,
        input: Data?,
        inputFileDescriptor: Int32?,
        maximumBodyBytes: Int?,
        onBody: @escaping (Data) throws -> Void
    ) throws -> DockerHTTPResponse
}

public protocol DockerEngineRequestNotificationTransport: DockerEngineTransport {
    func send(
        _ request: DockerHTTPRequest,
        maximumBodyBytes: Int?,
        onRequestSent: @escaping @Sendable () -> Void,
        onBody: @escaping (Data) throws -> Void
    ) throws -> DockerHTTPResponse
}

public extension DockerEngineTransport {
    func send(
        _ request: DockerHTTPRequest,
        maximumBodyBytes: Int = 16 * 1024 * 1024
    ) throws -> DockerHTTPResponse {
        var body = Data()
        let response = try send(
            request,
            maximumBodyBytes: maximumBodyBytes
        ) { chunk in
            guard body.count <= maximumBodyBytes - chunk.count else {
                throw DockerHTTPClientError.responseTooLarge(maximumBodyBytes)
            }
            body.append(chunk)
        }
        return DockerHTTPResponse(
            status: response.status,
            headers: response.headers,
            body: body
        )
    }
}

public extension DockerEngineRequestNotificationTransport {
    func send(
        _ request: DockerHTTPRequest,
        maximumBodyBytes: Int = 16 * 1024 * 1024,
        onRequestSent: @escaping @Sendable () -> Void
    ) throws -> DockerHTTPResponse {
        var body = Data()
        let response = try send(
            request,
            maximumBodyBytes: maximumBodyBytes,
            onRequestSent: onRequestSent
        ) { chunk in
            guard body.count <= maximumBodyBytes - chunk.count else {
                throw DockerHTTPClientError.responseTooLarge(maximumBodyBytes)
            }
            body.append(chunk)
        }
        return DockerHTTPResponse(
            status: response.status,
            headers: response.headers,
            body: body
        )
    }
}

/// Refuses to send compatibility requests to anything except this project's
/// Apple-container-backed engine. The identity probe is side-effect free and is
/// cached for the lifetime of the short-lived adapter process.
final class DevContainerEngineTransport:
    DockerEngineHijackTransport,
    DockerEngineRequestNotificationTransport,
    @unchecked Sendable
{
    private let transport: any DockerEngineHijackTransport
        & DockerEngineRequestNotificationTransport
    private let verificationLock = NSLock()
    private var verified = false

    init(
        transport: any DockerEngineHijackTransport
            & DockerEngineRequestNotificationTransport
    ) {
        self.transport = transport
    }

    func send(
        _ request: DockerHTTPRequest,
        maximumBodyBytes: Int?,
        onBody: @escaping (Data) throws -> Void
    ) throws -> DockerHTTPResponse {
        try verifyEngine()
        return try transport.send(
            request,
            maximumBodyBytes: maximumBodyBytes,
            onBody: onBody
        )
    }

    func send(
        _ request: DockerHTTPRequest,
        maximumBodyBytes: Int?,
        onRequestSent: @escaping @Sendable () -> Void,
        onBody: @escaping (Data) throws -> Void
    ) throws -> DockerHTTPResponse {
        try verifyEngine()
        return try transport.send(
            request,
            maximumBodyBytes: maximumBodyBytes,
            onRequestSent: onRequestSent,
            onBody: onBody
        )
    }

    func hijack(
        _ request: DockerHTTPRequest,
        input: Data?,
        inputFileDescriptor: Int32?,
        maximumBodyBytes: Int?,
        onBody: @escaping (Data) throws -> Void
    ) throws -> DockerHTTPResponse {
        try verifyEngine()
        return try transport.hijack(
            request,
            input: input,
            inputFileDescriptor: inputFileDescriptor,
            maximumBodyBytes: maximumBodyBytes,
            onBody: onBody
        )
    }

    private func verifyEngine() throws {
        verificationLock.lock()
        defer { verificationLock.unlock() }
        guard !verified else { return }
        let response = try transport.send(
            DockerHTTPRequest(method: "HEAD", target: "/_ping"),
            maximumBodyBytes: 0,
            onBody: { _ in
                // HEAD identity probes deliberately discard an absent body.
            }
        )
        let identity = response.headers.first {
            $0.key.caseInsensitiveCompare(DevContainerEngineIdentity.header) == .orderedSame
        }?.value
        guard response.status == 200, identity == DevContainerEngineIdentity.value else {
            throw DockerHTTPClientError.unsafeSocket(
                "endpoint is not the devcontainer Apple runtime engine"
            )
        }
        verified = true
    }
}

public final class UnixSocketDockerTransport:
    DockerEngineHijackTransport,
    DockerEngineRequestNotificationTransport,
    @unchecked Sendable
{
    fileprivate static let readSize = 64 * 1024
    private static let maximumHeaderBytes = 64 * 1024

    private let socketPath: String

    public init(socketPath: String) throws {
        let capacity = withUnsafeBytes(of: sockaddr_un().sun_path) { $0.count }
        let socketURL = URL(fileURLWithPath: socketPath).standardizedFileURL
        guard
            socketPath.hasPrefix("/"),
            !socketPath.contains("\0"),
            socketURL.path.utf8.count < capacity
        else {
            throw DockerHTTPClientError.invalidSocketPath(socketPath)
        }
        let names = [socketURL, socketURL.resolvingSymlinksInPath()]
            .map { $0.lastPathComponent.lowercased() }
        guard names.allSatisfy({ $0 != "docker.sock" && $0 != "docker.raw.sock" }) else {
            throw DockerHTTPClientError.unsafeSocket(
                "Docker runtime socket names are disabled in the Docker-less product"
            )
        }
        self.socketPath = socketURL.path
    }

    public func send(
        _ request: DockerHTTPRequest,
        maximumBodyBytes: Int?,
        onBody: @escaping (Data) throws -> Void
    ) throws -> DockerHTTPResponse {
        try send(
            request,
            maximumBodyBytes: maximumBodyBytes,
            onRequestSent: {
                // Ordinary requests do not need a post-write notification.
            },
            onBody: onBody
        )
    }

    public func send(
        _ request: DockerHTTPRequest,
        maximumBodyBytes: Int?,
        onRequestSent: @escaping @Sendable () -> Void,
        onBody: @escaping (Data) throws -> Void
    ) throws -> DockerHTTPResponse {
        let descriptor = try connect()
        defer { Darwin.close(descriptor) }
        try write(Self.serialized(request), to: descriptor)
        onRequestSent()

        var reader = SocketReader(descriptor: descriptor)
        let head = try reader.readHead(maximumBytes: Self.maximumHeaderBytes)
        let responseHead = try Self.parseHead(head)
        let collector = BodyCollector(maximumBytes: maximumBodyBytes, handler: onBody)
        if request.method == "HEAD" || Self.statusHasNoBody(responseHead.status) {
            // RFC 9110 forbids response content for these statuses even when
            // the peer keeps the HTTP/1.1 connection open.
        } else if responseHead.status == 101 {
            try reader.readUntilEOF(collector.accept)
        } else if responseHead.headers["transfer-encoding"]?.lowercased().contains("chunked") == true {
            try reader.readChunked(collector.accept)
        } else if let value = responseHead.headers["content-length"], let length = Int(value) {
            try reader.readExactly(length, handler: collector.accept)
        } else {
            try reader.readUntilEOF(collector.accept)
        }

        let response = DockerHTTPResponse(
            status: responseHead.status,
            headers: responseHead.headers,
            body: Data()
        )
        guard (200 ... 299).contains(response.status) || response.status == 101 else {
            throw DockerHTTPClientError.server(
                status: response.status,
                message: collector.errorMessage
            )
        }
        return response
    }

    private static func statusHasNoBody(_ status: Int) -> Bool {
        (100 ... 199).contains(status) && status != 101
            || status == 204
            || status == 304
    }

    public func hijack(
        _ request: DockerHTTPRequest,
        input: Data?,
        inputFileDescriptor: Int32?,
        maximumBodyBytes: Int?,
        onBody: @escaping (Data) throws -> Void
    ) throws -> DockerHTTPResponse {
        let descriptor = try connect()
        defer { Darwin.close(descriptor) }
        try write(Self.serialized(request), to: descriptor)

        var reader = SocketReader(descriptor: descriptor)
        let head = try reader.readHead(maximumBytes: Self.maximumHeaderBytes)
        let responseHead = try Self.parseHead(head)
        let collector = BodyCollector(maximumBytes: maximumBodyBytes, handler: onBody)
        guard responseHead.status == 101 else {
            try reader.readUntilEOF(collector.accept)
            throw DockerHTTPClientError.server(
                status: responseHead.status,
                message: collector.errorMessage
            )
        }
        var bufferedInput: DockerHijackInputCompletion?
        if let inputFileDescriptor {
            try streamInput(
                from: inputFileDescriptor,
                to: descriptor
            )
        } else {
            let writerDescriptor = Darwin.dup(descriptor)
            guard writerDescriptor >= 0 else { throw Self.posixError() }
            let completion = DockerHijackInputCompletion()
            bufferedInput = completion
            DispatchQueue.global(qos: .userInitiated).async { [self] in
                defer { Darwin.close(writerDescriptor) }
                completion.store(Result {
                    try finishHijackInput(input, to: writerDescriptor)
                })
            }
        }
        do {
            try reader.readUntilEOF(collector.accept)
        } catch {
            _ = Darwin.shutdown(descriptor, SHUT_RDWR)
            _ = bufferedInput?.load()
            throw error
        }
        try bufferedInput?.load().get()
        return DockerHTTPResponse(
            status: responseHead.status,
            headers: responseHead.headers,
            body: Data()
        )
    }

    private func finishHijackInput(_ input: Data?, to descriptor: Int32) throws {
        if let input, !input.isEmpty {
            do {
                try write(input, to: descriptor)
            } catch let error as POSIXError where Self.peerClosed(error.code) {
                // A short-lived command can finish and close its read side
                // after the successful takeover response but before all
                // caller input reaches it. Its buffered output and exit
                // status remain authoritative.
                return
            }
        }
        guard Darwin.shutdown(descriptor, SHUT_WR) == 0 else {
            let error = Self.posixError()
            guard Self.peerClosed(error.code) else { throw error }
            return
        }
    }

    private static func peerClosed(_ code: POSIXErrorCode) -> Bool {
        code == .EPIPE || code == .ECONNRESET || code == .ENOTCONN
    }

    private func streamInput(from input: Int32, to socket: Int32) throws {
        let inputCopy = Darwin.dup(input)
        guard inputCopy >= 0 else { throw Self.posixError() }
        let socketCopy = Darwin.dup(socket)
        guard socketCopy >= 0 else {
            Darwin.close(inputCopy)
            throw Self.posixError()
        }
        DispatchQueue.global(qos: .userInitiated).async {
            defer {
                _ = Darwin.shutdown(socketCopy, SHUT_WR)
                Darwin.close(socketCopy)
                Darwin.close(inputCopy)
            }
            var buffer = [UInt8](repeating: 0, count: Self.readSize)
            while true {
                let count = Darwin.read(inputCopy, &buffer, buffer.count)
                if count == 0 {
                    return
                }
                if count < 0 {
                    if errno == EINTR {
                        continue
                    }
                    return
                }
                var offset = 0
                while offset < count {
                    let written = buffer.withUnsafeBytes { bytes in
                        Darwin.write(
                            socketCopy,
                            bytes.baseAddress!.advanced(by: offset),
                            count - offset
                        )
                    }
                    if written < 0 {
                        if errno == EINTR {
                            continue
                        }
                        return
                    }
                    offset += written
                }
            }
        }
    }

    private func connect() throws -> Int32 {
        try validateSocket()
        let descriptor = socket(AF_UNIX, SOCK_STREAM, 0)
        guard descriptor >= 0 else { throw Self.posixError() }
        do {
            var noSignal: Int32 = 1
            guard
                setsockopt(
                    descriptor,
                    SOL_SOCKET,
                    SO_NOSIGPIPE,
                    &noSignal,
                    socklen_t(MemoryLayout.size(ofValue: noSignal))
                ) == 0
            else {
                throw Self.posixError()
            }
            var address = sockaddr_un()
            address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
            address.sun_family = sa_family_t(AF_UNIX)
            withUnsafeMutableBytes(of: &address.sun_path) { bytes in
                socketPath.withCString { value in
                    bytes.copyMemory(
                        from: UnsafeRawBufferPointer(
                            start: value,
                            count: socketPath.utf8.count + 1
                        )
                    )
                }
            }
            let result = withUnsafePointer(to: &address) { pointer in
                pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.connect(
                        descriptor,
                        $0,
                        socklen_t(MemoryLayout<sockaddr_un>.size)
                    )
                }
            }
            guard result == 0 else { throw Self.posixError() }
            return descriptor
        } catch {
            Darwin.close(descriptor)
            throw error
        }
    }

    private func validateSocket() throws {
        var status = stat()
        guard lstat(socketPath, &status) == 0 else { throw Self.posixError() }
        guard status.st_mode & S_IFMT == S_IFSOCK else {
            throw DockerHTTPClientError.unsafeSocket("path is not a Unix socket")
        }
        guard status.st_uid == geteuid() else {
            throw DockerHTTPClientError.unsafeSocket("socket is not owned by the current user")
        }
        guard status.st_mode & (S_IRWXG | S_IRWXO) == 0 else {
            throw DockerHTTPClientError.unsafeSocket("group or other permissions are present")
        }
        guard status.st_nlink == 1 else {
            throw DockerHTTPClientError.unsafeSocket("socket has an unexpected link count")
        }
    }

    private static func serialized(_ request: DockerHTTPRequest) -> Data {
        var headers = request.headers
        headers["Host"] = headers["Host"] ?? "localhost"
        headers["User-Agent"] = headers["User-Agent"] ?? "devcontainer-docker/1"
        headers["Content-Length"] = String(request.body.count)
        headers["Connection"] = headers["Connection"] ?? "close"
        let lines = headers.sorted { $0.key.lowercased() < $1.key.lowercased() }
            .map { "\($0.key): \($0.value)" }
            .joined(separator: "\r\n")
        var data = Data("\(request.method) \(request.target) HTTP/1.1\r\n\(lines)\r\n\r\n".utf8)
        data.append(request.body)
        return data
    }

    private static func parseHead(_ data: Data) throws -> (status: Int, headers: [String: String]) {
        guard let text = String(data: data, encoding: .utf8) else {
            throw DockerHTTPClientError.invalidResponse("headers are not UTF-8")
        }
        let lines = text.components(separatedBy: "\r\n")
        guard
            let statusLine = lines.first,
            statusLine.hasPrefix("HTTP/"),
            statusLine.split(separator: " ").count >= 2,
            let status = Int(statusLine.split(separator: " ")[1])
        else {
            throw DockerHTTPClientError.invalidResponse("missing HTTP status")
        }
        var headers: [String: String] = [:]
        for line in lines.dropFirst() where !line.isEmpty {
            guard let colon = line.firstIndex(of: ":") else {
                throw DockerHTTPClientError.invalidResponse("malformed header")
            }
            let name = line[..<colon].lowercased()
            let value = line[line.index(after: colon)...]
                .trimmingCharacters(in: .whitespaces)
            headers[name] = headers[name].map { "\($0), \(value)" } ?? value
        }
        return (status, headers)
    }

    private func write(_ data: Data, to descriptor: Int32) throws {
        try data.withUnsafeBytes { bytes in
            guard let base = bytes.baseAddress else { return }
            var offset = 0
            while offset < bytes.count {
                let count = Darwin.write(
                    descriptor,
                    base.advanced(by: offset),
                    bytes.count - offset
                )
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

private final class DockerHijackInputCompletion: @unchecked Sendable {
    private let group = DispatchGroup()
    private let lock = NSLock()
    private var result: Result<Void, any Error>?

    init() {
        group.enter()
    }

    func store(_ result: Result<Void, any Error>) {
        lock.withLock {
            self.result = result
        }
        group.leave()
    }

    func load() -> Result<Void, any Error> {
        group.wait()
        return lock.withLock {
            result ?? .failure(
                DockerHTTPClientError.invalidResponse(
                    "hijack input completed without a result"
                )
            )
        }
    }
}

private final class BodyCollector: @unchecked Sendable {
    private let maximumBytes: Int?
    private let handler: (Data) throws -> Void
    private(set) var captured = Data()
    private var total = 0

    init(maximumBytes: Int?, handler: @escaping (Data) throws -> Void) {
        self.maximumBytes = maximumBytes
        self.handler = handler
    }

    func accept(_ data: Data) throws {
        total += data.count
        if let maximumBytes, total > maximumBytes {
            throw DockerHTTPClientError.responseTooLarge(maximumBytes)
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
        return String(data: captured, encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? "unknown error"
    }
}

private struct SocketReader {
    let descriptor: Int32
    private var buffer = Data()

    init(descriptor: Int32) {
        self.descriptor = descriptor
    }

    mutating func readHead(maximumBytes: Int) throws -> Data {
        let marker = Data("\r\n\r\n".utf8)
        while buffer.range(of: marker) == nil {
            guard buffer.count < maximumBytes else {
                throw DockerHTTPClientError.responseTooLarge(maximumBytes)
            }
            try readMore()
        }
        guard let range = buffer.range(of: marker) else {
            throw DockerHTTPClientError.invalidResponse("missing header terminator")
        }
        let head = Data(buffer[..<range.lowerBound])
        buffer.removeSubrange(..<range.upperBound)
        return head
    }

    mutating func readExactly(
        _ count: Int,
        handler: (Data) throws -> Void
    ) throws {
        var remaining = count
        while remaining > 0 {
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
            guard let size = Int(line.split(separator: ";", maxSplits: 1)[0], radix: 16) else {
                throw DockerHTTPClientError.invalidResponse("invalid chunk size")
            }
            if size == 0 {
                while try !readLine().isEmpty {
                    // Trailer fields are not part of the Docker API payload.
                }
                return
            }
            try readExactly(size, handler: handler)
            guard try readLine().isEmpty else {
                throw DockerHTTPClientError.invalidResponse("invalid chunk terminator")
            }
        }
    }

    private mutating func readLine() throws -> String {
        let marker = Data("\r\n".utf8)
        while buffer.range(of: marker) == nil {
            try readMore()
        }
        guard let range = buffer.range(of: marker) else {
            throw DockerHTTPClientError.invalidResponse("missing line terminator")
        }
        let data = Data(buffer[..<range.lowerBound])
        buffer.removeSubrange(..<range.upperBound)
        guard let line = String(data: data, encoding: .utf8) else {
            throw DockerHTTPClientError.invalidResponse("line is not UTF-8")
        }
        return line
    }

    private mutating func readMore() throws {
        let data = try readChunk()
        guard !data.isEmpty else {
            throw DockerHTTPClientError.invalidResponse("unexpected end of response")
        }
        buffer.append(data)
    }

    private func readChunk() throws -> Data {
        var bytes = [UInt8](repeating: 0, count: UnixSocketDockerTransport.readSize)
        while true {
            let count = Darwin.read(descriptor, &bytes, bytes.count)
            if count < 0, errno == EINTR {
                continue
            }
            guard count >= 0 else {
                throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
            }
            return Data(bytes.prefix(count))
        }
    }
}
