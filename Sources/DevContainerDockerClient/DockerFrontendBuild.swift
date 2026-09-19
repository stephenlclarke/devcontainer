// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineWire
import Foundation

public protocol DockerFrontendBuildTransport: Sendable {
    /// Serial bounded callbacks on a blocking worker, sharing the event transport's backpressure contract.
    func build(_ request: DockerHTTPRequest, onBody: @escaping @Sendable (Data) throws -> Void) async throws
}

extension UnixDockerFrontendTransport: DockerFrontendBuildTransport {
    public func build(_ request: DockerHTTPRequest, onBody: @escaping @Sendable (Data) throws -> Void) async throws {
        _ = try await duplexClient.stream(request, onBody: onBody)
    }
}

public extension DockerFrontend {
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
                    try await transport.build(spec.request(archive: archive)) { bytes in
                        try lines.consume(bytes) { try output.writeSynchronously($0) }
                    }
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
    private var received = false

    func consume(_ bytes: Data, emit: (Data) throws -> Void) throws {
        try lines.consume(bytes) { try emit(render($0)) }
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
            let message = detail?["message"] as? String ?? record["error"] as? String ?? "Engine build failed"
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
