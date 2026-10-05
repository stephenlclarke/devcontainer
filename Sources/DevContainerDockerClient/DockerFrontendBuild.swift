// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineWire
import Foundation

public protocol DockerFrontendBuildTransport: Sendable {
    /// Serial bounded callbacks on a blocking worker, sharing the event transport's backpressure contract.
    func build(_ request: DockerHTTPRequest, onBody: @escaping @Sendable (Data) throws -> Void) async throws
}

public protocol DockerFrontendPullTransport: Sendable {
    /// Deliver image progress serially under the same bounded backpressure contract as builds.
    func pull(_ request: DockerHTTPRequest, onBody: @escaping @Sendable (Data) throws -> Void) async throws
}

extension UnixDockerFrontendTransport: DockerFrontendBuildTransport {
    public func build(_ request: DockerHTTPRequest, onBody: @escaping @Sendable (Data) throws -> Void) async throws {
        _ = try await duplexClient.stream(request, onBody: onBody)
    }
}

extension UnixDockerFrontendTransport: DockerFrontendPullTransport {
    public func pull(_ request: DockerHTTPRequest, onBody: @escaping @Sendable (Data) throws -> Void) async throws {
        _ = try await duplexClient.stream(request, onBody: onBody)
    }
}

public extension DockerFrontend {
    func executePull(
        _ command: DockerPullCommand, transport: any DockerFrontendPullTransport, output: DockerFrontendOutput
    ) async throws {
        let lines = DockerBuildLines(operation: "pull")
        try await withThrowingTaskGroup(of: Void.self) { group in
            defer { group.cancelAll() }
            group.addTask {
                try await withTaskCancellationHandler {
                    try Task.checkCancellation()
                    try await transport.pull(
                        command.request(), onBody: lines.outputHandler(output, quiet: command.quiet)
                    )
                    if let tail = try lines.finish(), !command.quiet {
                        try await output.write(tail)
                    }
                } onCancel: { output.cancel() }
            }
            group.addTask {
                try await Task.sleep(for: executionTimeout)
                throw DockerFrontendError.invalidResponse("pull exceeded its execution deadline")
            }
            _ = try await group.next()
        }
    }

    func executeBuild(
        _ spec: DockerBuildCommand, archive: DockerBuildArchive,
        transport: any DockerFrontendBuildTransport, output: DockerFrontendOutput
    ) async throws {
        let lines = DockerBuildLines()
        try await withThrowingTaskGroup(of: Void.self) { group in
            defer { group.cancelAll() }
            group.addTask {
                try await withTaskCancellationHandler {
                    try Task.checkCancellation()
                    try await transport.build(
                        spec.request(archive: archive), onBody: lines.outputHandler(output)
                    )
                    if let tail = try lines.finish() {
                        try await output.write(tail)
                    }
                } onCancel: { output.cancel() }
            }
            group.addTask {
                try await Task.sleep(for: executionTimeout)
                throw DockerFrontendError.invalidResponse("build exceeded its execution deadline")
            }
            _ = try await group.next()
        }
    }
}

/// Shares the bounded NDJSON decoder. HTTP 200 is not build success: the Engine
/// carries build failures inside the response stream, including its final line.
final class DockerBuildLines: @unchecked Sendable {
    private let lines = DockerEventLines()
    private let operation: String
    private var received = false

    init(operation: String = "build") {
        self.operation = operation
    }

    func consume(_ bytes: Data, emit: (Data) throws -> Void) throws {
        try lines.consume(bytes) { try emit(render($0)) }
    }

    func consume(_ bytes: Data, output: DockerFrontendOutput) throws {
        try consume(bytes) { try output.writeSynchronously($0) }
    }

    func outputHandler(
        _ output: DockerFrontendOutput, quiet: Bool = false
    ) -> @Sendable (Data) throws -> Void {
        { bytes in
            if quiet {
                try self.consume(bytes) { renderedProgress in
                    // Quiet mode still validates the stream before discarding rendered progress.
                    _ = renderedProgress
                }
            } else {
                try self.consume(bytes, output: output)
            }
        }
    }

    func finish() throws -> Data? {
        let tail = try lines.finish().map { try render($0) }
        guard received else { throw DockerFrontendError.invalidResponse("empty build response") }
        return tail
    }

    private func render(_ line: Data) throws -> Data {
        guard let record = try JSONSerialization.jsonObject(with: line) as? [String: Any] else {
            throw DockerFrontendError.invalidResponse("invalid build record")
        }
        received = true
        if record["error"] != nil || record["errorDetail"] != nil {
            let detail = record["errorDetail"] as? [String: Any]
            let message = detail?["message"] as? String ?? record["error"] as? String ?? "Engine \(operation) failed"
            throw DockerFrontendError.invalidResponse(message)
        }
        if let stream = record["stream"] as? String {
            return Data(stream.utf8)
        }
        if let status = record["status"] as? String {
            let prefix = (record["id"] as? String).map { $0 + ": " } ?? ""
            let progress = (record["progress"] as? String).map { " " + $0 } ?? ""
            return Data((prefix + status + progress + "\n").utf8)
        }
        if record["aux"] is [String: Any] {
            return Data()
        }
        throw DockerFrontendError.invalidResponse("unrecognized build progress record")
    }
}
