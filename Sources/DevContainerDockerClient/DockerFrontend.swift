// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineWire
import ContainerUnixHTTPClient
import Foundation

public protocol DockerFrontendTransport: Sendable {
    func send(_ request: DockerHTTPRequest) async throws -> Data
}

/// Reuses the shared current-user-only socket client; never discovers or launches Docker.
public struct UnixDockerFrontendTransport: DockerFrontendTransport {
    let client: ContainerUnixHTTPClient
    let duplexClient: ContainerUnixHTTPClient

    public init(socketPath: String, timeoutSeconds: Int = 30) throws {
        client = try ContainerUnixHTTPClient(socketPath: socketPath, timeoutSeconds: min(30, timeoutSeconds))
        duplexClient = try ContainerUnixHTTPClient(socketPath: socketPath, timeoutSeconds: timeoutSeconds)
    }

    public func send(_ request: DockerHTTPRequest) async throws -> Data {
        try await client.send(request, maximumBodyBytes: 8 * 1024 * 1024).body
    }
}

public struct DockerFrontend: Sendable {
    public let version: String
    let executionTimeout: Duration

    public init(version: String, executionTimeout: Duration = .seconds(86400)) {
        self.version = version
        self.executionTimeout = executionTimeout
    }

    public var versionOutput: Data {
        Data("devcontainer-docker version \(version)\n".utf8)
    }

    public func execute(_ command: DockerFrontendCommand, transport: any DockerFrontendTransport) async throws -> Data {
        switch command {
        case .exec, .run, .events:
            throw DockerFrontendError.usage("exec, run and events require their streaming execution entry points")
        case .clientVersion:
            return versionOutput
        case let .version(format):
            let response = try await transport.send(.init(method: .get, target: "/version"))
            let server = try object(response)
            if format == "{{json .}}" {
                let client: [String: Any] = ["Version": version, "Name": "devcontainer-docker"]
                return try json(["Client": client, "Server": server])
            }
            guard let serverVersion = server["Version"] as? String, !serverVersion.isEmpty,
                  !serverVersion.contains(where: { $0.isNewline || $0 == "\0" })
            else {
                throw DockerFrontendError.invalidResponse("missing or invalid server Version")
            }
            return Data((format == nil
                    ? "Client: devcontainer-docker\n Version: \(version)\n\nServer:\n Version: \(serverVersion)\n"
                    : "\(serverVersion)\n").utf8)
        case .info:
            let response = try await transport.send(.init(method: .get, target: "/info"))
            _ = try object(response)
            return response + Data("\n".utf8)
        case let .inspect(kind, name):
            let collection = kind == "image" ? "images" : "containers"
            let target = "/\(collection)/\(Self.escaped(name))/json"
            let response = try await transport.send(.init(method: .get, target: target))
            _ = try object(response)
            // Preserve the gateway's numeric and string representations byte-for-byte.
            return Data("[".utf8) + response + Data("]\n".utf8)
        case let .containers(all, truncate, filters):
            let filterData = try JSONSerialization.data(withJSONObject: filters, options: [.sortedKeys])
            let filterValue = Self.escaped(String(data: filterData, encoding: .utf8)!)
            let target = "/containers/json?all=\(all ? "1" : "0")&filters=\(filterValue)"
            let response = try await transport.send(.init(method: .get, target: target))
            guard let entries = try JSONSerialization.jsonObject(with: response) as? [[String: Any]] else {
                throw DockerFrontendError.invalidResponse("container list is not an array of objects")
            }
            let identifiers = try entries.map { entry in
                guard let identifier = entry["Id"] as? String, !identifier.isEmpty,
                      identifier.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber) })
                else {
                    throw DockerFrontendError.invalidResponse("missing or invalid container Id")
                }
                return (truncate ? String(identifier.prefix(12)) : identifier) + "\n"
            }
            return Data(identifiers.joined().utf8)
        }
    }

    private func object(_ data: Data) throws -> [String: Any] {
        guard let value = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw DockerFrontendError.invalidResponse("expected an object")
        }
        return value
    }

    private func json(_ value: [String: Any]) throws -> Data {
        try JSONSerialization.data(
            withJSONObject: value, options: [.sortedKeys, .withoutEscapingSlashes]
        ) + Data("\n".utf8)
    }

    static func escaped(_ value: String) -> String {
        // Only RFC 3986 unreserved bytes; in particular '/' and '?' cannot alter routing.
        value.utf8.map { byte in
            switch byte {
            case 65 ... 90, 97 ... 122, 48 ... 57, 45, 46, 95, 126: String(UnicodeScalar(byte))
            default: String(format: "%%%02X", byte)
            }
        }.joined()
    }
}
