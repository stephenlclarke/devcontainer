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

public enum ComposeProviderKind: String, Codable, CaseIterable, Sendable {
    case docker
    case containerCompose = "container-compose"
}

public struct DevContainerConfiguration: Codable, Equatable, Sendable {
    public var backend: BackendProvider
    public var composeProvider: ComposeProviderKind
    public var socket: String
    public var strictCompatibility: Bool

    public init(
        backend: BackendProvider = .stock,
        composeProvider: ComposeProviderKind = .docker,
        socket: String,
        strictCompatibility: Bool = true
    ) {
        self.backend = backend
        self.composeProvider = composeProvider
        self.socket = socket
        self.strictCompatibility = strictCompatibility
    }
}

public enum DevContainerConfigurationStore {
    public static func load(
        from url: URL,
        defaultSocket: String
    ) throws -> DevContainerConfiguration {
        guard FileManager.default.fileExists(atPath: url.path) else {
            return DevContainerConfiguration(socket: defaultSocket)
        }
        let text = try String(contentsOf: url, encoding: .utf8)
        var values: [String: String] = [:]
        var section = ""
        for (lineNumber, rawLine) in text.split(
            separator: "\n",
            omittingEmptySubsequences: false
        ).enumerated() {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            if line.isEmpty || line.hasPrefix("#") {
                continue
            }
            if line.hasPrefix("["), line.hasSuffix("]") {
                section = String(line.dropFirst().dropLast())
                continue
            }
            let parts = line.split(separator: "=", maxSplits: 1)
            guard parts.count == 2 else {
                throw DevContainerError(
                    .invalidRequest,
                    message: "invalid configuration at line \(lineNumber + 1)"
                )
            }
            let key = parts[0].trimmingCharacters(in: .whitespaces)
            var value = parts[1].trimmingCharacters(in: .whitespaces)
            if value.hasPrefix("\""), value.hasSuffix("\""), value.count >= 2 {
                value = String(value.dropFirst().dropLast())
            }
            values[section.isEmpty ? key : "\(section).\(key)"] = value
        }

        let backendText = values["backend"] ?? BackendProvider.stock.rawValue
        guard let backend = BackendProvider(rawValue: backendText) else {
            throw DevContainerError(.invalidRequest, message: "invalid backend \(backendText)")
        }
        let composeText = values["compose.provider"] ?? ComposeProviderKind.docker.rawValue
        guard let compose = ComposeProviderKind(rawValue: composeText) else {
            throw DevContainerError(
                .invalidRequest,
                message: "invalid Compose provider \(composeText)"
            )
        }
        let socket = expandHome(values["socket"] ?? defaultSocket)
        let strictText = values["compatibility.strict"] ?? "true"
        guard let strict = bool(strictText) else {
            throw DevContainerError(
                .invalidRequest,
                message: "compatibility.strict must be true or false"
            )
        }
        return DevContainerConfiguration(
            backend: backend,
            composeProvider: compose,
            socket: socket,
            strictCompatibility: strict
        )
    }

    public static func save(_ configuration: DevContainerConfiguration, to url: URL) throws {
        let directory = url.deletingLastPathComponent()
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        try validateOwnedDirectory(directory)
        let text = """
        # Managed by devcontainer. This file contains no credentials.
        backend = "\(configuration.backend.rawValue)"
        socket = "\(configuration.socket)"

        [compose]
        provider = "\(configuration.composeProvider.rawValue)"

        [compatibility]
        strict = \(configuration.strictCompatibility ? "true" : "false")
        """
        try Data((text + "\n").utf8).write(to: url, options: .atomic)
        guard chmod(url.path, S_IRUSR | S_IWUSR) == 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
    }

    private static func bool(_ value: String) -> Bool? {
        switch value.lowercased() {
        case "true":
            true
        case "false":
            false
        default:
            nil
        }
    }

    private static func expandHome(_ path: String) -> String {
        guard path == "~" || path.hasPrefix("~/") else {
            return path
        }
        return FileManager.default.homeDirectoryForCurrentUser.path
            + String(path.dropFirst())
    }

    private static func validateOwnedDirectory(_ url: URL) throws {
        var status = stat()
        guard lstat(url.path, &status) == 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        guard
            status.st_uid == getuid(),
            status.st_mode & S_IFMT == S_IFDIR,
            status.st_mode & (S_IWGRP | S_IWOTH) == 0
        else {
            throw DevContainerError(
                .invalidRequest,
                message: "configuration directory must be owned by the current user and not group/world writable"
            )
        }
    }
}
