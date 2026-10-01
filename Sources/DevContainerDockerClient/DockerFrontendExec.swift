// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineWire
import ContainerUnixHTTPClient
import Foundation

public protocol DockerFrontendConnection: Sendable {
    func read() async throws -> Data?
    func write(_ data: Data) async throws
    func finishInput() async throws
    func close()
}

extension ContainerUnixHTTPConnection: DockerFrontendConnection {}

public protocol DockerFrontendExecTransport: DockerFrontendTransport {
    func open(_ request: DockerHTTPRequest) async throws -> any DockerFrontendConnection
}

extension UnixDockerFrontendTransport: DockerFrontendExecTransport {
    public func open(_ request: DockerHTTPRequest) async throws -> any DockerFrontendConnection {
        try await duplexClient.openDuplex(request)
    }
}

extension DockerFrontend {
    /// Both callbacks must observe task cancellation. Output is awaited to
    /// apply backpressure instead of retaining an unbounded stream of shell data.
    public func executeExec(
        _ spec: DockerExecCommand,
        transport: any DockerFrontendExecTransport,
        input: @escaping @Sendable () async throws -> Data?,
        output: @escaping @Sendable (DockerStreamFrame) async throws -> Void
    ) async throws -> Int32 {
        let response = try await transport.send(.init(
            method: .post, target: "/containers/\(Self.escaped(spec.container))/exec",
            headers: .init([.init(name: "Content-Type", value: "application/json")]), body: spec.createBody()
        ))
        let created = try JSONDecoder().decode(CreatedExec.self, from: response)
        guard !created.id.isEmpty, created.id.utf8.allSatisfy({
            (48 ... 57).contains($0) || (65 ... 90).contains($0) || (97 ... 122).contains($0) || $0 == 45
        }) else { throw DockerFrontendError.invalidResponse("invalid exec Id") }
        let path = "/exec/\(created.id)"
        let connection = try await transport.open(.init(
            method: .post, target: path + "/start",
            headers: .init([.init(name: "Content-Type", value: "application/json")]),
            body: Data(#"{"Detach":false,"Tty":false}"#.utf8)
        ))
        defer { connection.close() }
        try await exchange(connection, interactive: spec.interactive, input: input, output: output)
        return try await terminalStatus(id: created.id, transport: transport)
    }

    private func terminalStatus(id: String, transport: any DockerFrontendExecTransport) async throws -> Int32 {
        try await withThrowingTaskGroup(of: Int32.self) { group in
            defer { group.cancelAll() }
            group.addTask {
                while true {
                    try Task.checkCancellation()
                    let data = try await transport.send(.init(method: .get, target: "/exec/\(id)/json"))
                    let state = try JSONDecoder().decode(ExecState.self, from: data)
                    guard state.id == id else { throw DockerFrontendError.invalidResponse("exec identity changed") }
                    if !state.running {
                        guard (0 ... 255).contains(state.exitCode) else {
                            throw DockerFrontendError.invalidResponse("invalid exec exit status")
                        }
                        return state.exitCode
                    }
                    try await Task.sleep(for: .milliseconds(50))
                }
            }
            group.addTask {
                try await Task.sleep(for: .seconds(5))
                throw DockerFrontendError.invalidResponse("exec terminal status was not published within five seconds")
            }
            guard let result = try await group.next() else { throw CancellationError() }
            return result
        }
    }

    private func exchange(
        _ connection: any DockerFrontendConnection, interactive: Bool,
        input: @escaping @Sendable () async throws -> Data?,
        output: @escaping @Sendable (DockerStreamFrame) async throws -> Void
    ) async throws {
        try await withThrowingTaskGroup(of: Bool.self) { group in
            group.addTask {
                try await Task.sleep(for: executionTimeout)
                throw DockerFrontendError.invalidResponse("exec stream exceeded its execution deadline")
            }
            group.addTask {
                if interactive {
                    while let bytes = try await input() {
                        try Task.checkCancellation()
                        try await connection.write(bytes)
                    }
                }
                try await connection.finishInput()
                return false
            }
            group.addTask {
                var decoder = DockerMultiplexDecoder()
                while let bytes = try await connection.read() {
                    for frame in try decoder.consume(bytes) {
                        try await output(frame)
                    }
                }
                try decoder.finish()
                return true
            }
            do {
                while let outputFinished = try await group.next() {
                    if outputFinished {
                        group.cancelAll()
                        connection.close()
                        break
                    }
                }
            } catch {
                group.cancelAll()
                connection.close()
                throw error
            }
        }
    }
}

private struct CreatedExec: Decodable {
    let id: String
    enum CodingKeys: String, CodingKey { case id = "Id" }
}

private struct ExecState: Decodable {
    let id: String
    let running: Bool
    let exitCode: Int32
    enum CodingKeys: String, CodingKey {
        case id = "ID"
        case running = "Running"
        case exitCode = "ExitCode"
    }
}
