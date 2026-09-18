// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineWire
import Foundation

public protocol DockerFrontendRunTransport: DockerFrontendExecTransport {
    /// A long-poll request, cancelled by the enclosing execution deadline.
    func wait(_ request: DockerHTTPRequest) async throws -> Data
}

extension UnixDockerFrontendTransport: DockerFrontendRunTransport {
    public func wait(_ request: DockerHTTPRequest) async throws -> Data {
        try await duplexClient.send(request, maximumBodyBytes: 65536).body
    }
}

extension DockerFrontend {
    /// Attach before start so even a short-lived process cannot lose its output.
    /// As with Docker, failure/disconnection does not delete a created container.
    /// Callers own cleanup through Engine inventory and ownership labels.
    public func executeRun(
        _ spec: DockerRunCommand, transport: any DockerFrontendRunTransport,
        output: @escaping @Sendable (DockerStreamFrame) async throws -> Void,
        warning: @escaping @Sendable (String) async throws -> Void
    ) async throws -> Int32 {
        try await withThrowingTaskGroup(of: Int32.self) { group in
            defer { group.cancelAll() }
            group.addTask {
                try await runToCompletion(spec, transport: transport, output: output, warning: warning)
            }
            group.addTask {
                try await Task.sleep(for: executionTimeout)
                throw DockerFrontendError.invalidResponse("run exceeded its execution deadline")
            }
            guard let result = try await group.next() else { throw CancellationError() }
            return result
        }
    }

    private func runToCompletion(
        _ spec: DockerRunCommand, transport: any DockerFrontendRunTransport,
        output: @escaping @Sendable (DockerStreamFrame) async throws -> Void,
        warning: @escaping @Sendable (String) async throws -> Void
    ) async throws -> Int32 {
        let body = try await transport.send(.init(
            method: .post, target: "/containers/create",
            headers: .init([.init(name: "Content-Type", value: "application/json")]), body: spec.createBody()
        ))
        let created = try JSONDecoder().decode(CreatedRun.self, from: body)
        guard !created.id.isEmpty, created.id.utf8.allSatisfy({
            (48 ... 57).contains($0) || (65 ... 90).contains($0) || (97 ... 122).contains($0) || $0 == 45
        }) else { throw DockerFrontendError.invalidResponse("invalid created container Id") }
        for message in created.warnings ?? [] {
            try await warning(message)
        }
        let path = "/containers/\(created.id)"
        let connection = try await transport.open(.init(
            method: .post,
            target: path + "/attach?stream=1&stdin=0&stdout=\(spec.standardOutput ? 1 : 0)&stderr=\(spec.standardError ? 1 : 0)"
        ))
        defer { connection.close() }
        // Output-only attach never consumes the invoking process's standard input.
        try await connection.finishInput()
        _ = try await transport.send(.init(method: .post, target: path + "/start"))
        try await runOutput(connection, spec: spec, output: output)
        let response = try await transport.wait(.init(method: .post, target: path + "/wait?condition=not-running"))
        let stopped = try JSONDecoder().decode(StoppedRun.self, from: response)
        if let error = stopped.error, !error.message.isEmpty {
            throw DockerFrontendError.invalidResponse(error.message)
        }
        guard (0 ... 255).contains(stopped.statusCode) else {
            throw DockerFrontendError.invalidResponse("invalid container exit status")
        }
        return stopped.statusCode
    }

    private func runOutput(
        _ connection: any DockerFrontendConnection, spec: DockerRunCommand,
        output: @escaping @Sendable (DockerStreamFrame) async throws -> Void
    ) async throws {
        var decoder = DockerMultiplexDecoder()
        while let chunk = try await connection.read() {
            try Task.checkCancellation()
            for frame in try decoder.consume(chunk) {
                if (frame.channel == .standardOutput && spec.standardOutput)
                    || (frame.channel == .standardError && spec.standardError)
                {
                    try await output(frame)
                }
            }
        }
        try decoder.finish()
    }
}

private struct CreatedRun: Decodable {
    let id: String
    let warnings: [String]?
    enum CodingKeys: String, CodingKey { case id = "Id", warnings = "Warnings" }
}

private struct StoppedRun: Decodable {
    let statusCode: Int32
    let error: RunFailure?
    enum CodingKeys: String, CodingKey { case statusCode = "StatusCode", error = "Error" }
}

private struct RunFailure: Decodable {
    let message: String
    enum CodingKeys: String, CodingKey { case message = "Message" }
}
