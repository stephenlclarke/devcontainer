// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineWire
import Foundation

public struct DockerEventsCommand: Equatable, Sendable {
    public let filters: [String: [String]]

    static func parse(_ options: inout DockerFrontendArguments) throws -> Self {
        var format: String?
        var filters: [String: [String]] = [:]
        while let argument = options.next() {
            let key = argument.split(separator: "=", maxSplits: 1).first.map(String.init)
            switch key {
            case "--format":
                guard format == nil else { throw DockerFrontendError.usage("duplicate events format") }
                format = try options.value(for: argument)
            case "--filter", "-f":
                let value = try options.value(for: argument)
                let pair = value.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
                guard pair.count == 2, ["event", "label"].contains(pair[0]), !pair[1].isEmpty,
                      !value.contains("\0")
                else { throw DockerFrontendError.usage("events supports nonempty event/label filters") }
                filters[String(pair[0]), default: []].append(String(pair[1]))
            default: throw DockerFrontendError.usage("unsupported events option: \(argument)")
            }
        }
        guard format == "{{json .}}" else { throw DockerFrontendError.usage("events requires --format '{{json .}}'") }
        return Self(filters: filters)
    }

    func request() throws -> DockerHTTPRequest {
        let data = try JSONSerialization.data(withJSONObject: filters, options: [.sortedKeys])
        let encoded = DockerFrontend.escaped(String(data: data, encoding: .utf8)!)
        return .init(method: .get, target: "/events?filters=" + encoded)
    }
}

public protocol DockerFrontendEventTransport: Sendable {
    /// Deliver successful body bytes serially on a blocking worker. The callback
    /// must be bounded/cooperative; backpressure must not become an unbounded queue.
    func events(_ request: DockerHTTPRequest, onBody: @escaping @Sendable (Data) throws -> Void) async throws
}

extension UnixDockerFrontendTransport: DockerFrontendEventTransport {
    public func events(
        _ request: DockerHTTPRequest, onBody: @escaping @Sendable (Data) throws -> Void
    ) async throws {
        _ = try await duplexClient.stream(request, onBody: onBody)
    }
}

public extension DockerFrontend {
    func executeEvents(
        _ spec: DockerEventsCommand, transport: any DockerFrontendEventTransport, output: DockerFrontendOutput
    ) async throws {
        let lines = DockerEventLines()
        try await withThrowingTaskGroup(of: Void.self) { group in
            defer { group.cancelAll() }
            group.addTask {
                try await withTaskCancellationHandler {
                    try Task.checkCancellation()
                    try await transport.events(spec.request()) { bytes in
                        try lines.consume(bytes) { try output.writeSynchronously($0) }
                    }
                    if let tail = try lines.finish() {
                        try await output.write(tail)
                    }
                } onCancel: {
                    output.cancel()
                }
            }
            group.addTask {
                try await Task.sleep(for: executionTimeout)
                throw DockerFrontendError.invalidResponse("events exceeded its execution deadline")
            }
            _ = try await group.next()
        }
    }
}

/// One bounded partial NDJSON record; records are validated without reserializing
/// numeric IDs/timestamps or accumulating a potentially infinite event history.
final class DockerEventLines: @unchecked Sendable {
    private let lock = NSLock()
    private var pending = Data()
    private let maximumRecordBytes = 1024 * 1024

    func consume(_ bytes: Data, emit: (Data) throws -> Void) throws {
        try lock.withLock {
            guard bytes.count <= 65536 else {
                throw DockerFrontendError.invalidResponse("event transport chunk exceeds 64 KiB")
            }
            pending.append(bytes)
            while let newline = pending.firstIndex(of: 10) {
                let line = Data(pending[..<newline])
                try emit(validated(line))
                pending.removeSubrange(...newline)
            }
            guard pending.count <= maximumRecordBytes else {
                throw DockerFrontendError.invalidResponse("event record exceeds 1 MiB")
            }
        }
    }

    func finish() throws -> Data? {
        try lock.withLock {
            let tail = pending.isEmpty ? nil : try validated(pending)
            pending.removeAll(keepingCapacity: false)
            return tail
        }
    }

    private func validated(_ line: Data) throws -> Data {
        guard line.count <= maximumRecordBytes,
              (try? JSONSerialization.jsonObject(with: line)) is [String: Any]
        else { throw DockerFrontendError.invalidResponse("invalid or oversized event JSON record") }
        return line + Data([10])
    }
}
