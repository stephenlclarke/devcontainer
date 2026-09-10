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
    case containerCompose = "container-compose"
}

public struct DevContainerConfiguration: Codable, Equatable, Sendable {
    public var backend: BackendProvider
    public var composeProvider: ComposeProviderKind
    public var containerExecutable: String
    public var socket: String
    public var stateDatabase: String
    public var strictCompatibility: Bool

    public init(
        backend: BackendProvider = .stock,
        composeProvider: ComposeProviderKind = .containerCompose,
        containerExecutable: String = DevContainerPathDefaults.containerExecutable,
        socket: String,
        stateDatabase: String = DevContainerPathDefaults.stateDatabase,
        strictCompatibility: Bool = true
    ) {
        self.backend = backend
        self.composeProvider = composeProvider
        self.containerExecutable = containerExecutable
        self.socket = socket
        self.stateDatabase = stateDatabase
        self.strictCompatibility = strictCompatibility
    }
}

public enum DevContainerPathDefaults {
    public static var configuration: String {
        let root = ProcessInfo.processInfo.environment["XDG_CONFIG_HOME"]
            .map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config", isDirectory: true)
        return root
            .appendingPathComponent("devcontainer", isDirectory: true)
            .appendingPathComponent("config.toml")
            .path
    }

    public static var socket: String {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("devcontainer", isDirectory: true)
            .appendingPathComponent("docker.sock")
            .path
    }

    public static var stateDatabase: String {
        let applicationSupport = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first ?? FileManager.default.homeDirectoryForCurrentUser
        return applicationSupport
            .appendingPathComponent("devcontainer", isDirectory: true)
            .appendingPathComponent("state.sqlite")
            .path
    }

    public static var containerExecutable: String {
        for candidate in [
            "/usr/local/bin/container",
            "/opt/homebrew/bin/container",
            "/usr/bin/container"
        ] where FileManager.default.isExecutableFile(atPath: candidate) {
            return candidate
        }
        return "/usr/local/bin/container"
    }
}

public struct DevContainerRuntimeSelection: Equatable, Sendable {
    public var configuration: URL
    public var backend: BackendProvider
    public var composeProvider: ComposeProviderKind
    public var containerExecutable: String
    public var socket: String
    public var stateDatabase: String
    public var strictCompatibility: Bool
}

public enum DevContainerRuntimeSelectionResolver {
    public static func resolve(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        configuration: String? = nil,
        backend: String? = nil,
        composeProvider: String? = nil,
        containerExecutable: String? = nil,
        socket: String? = nil,
        stateDatabase: String? = nil
    ) throws -> DevContainerRuntimeSelection {
        let configurationURL = URL(
            fileURLWithPath: nonempty(configuration)
                ?? nonempty(environment["DEVCONTAINER_CONFIG"])
                ?? defaultConfiguration(environment: environment)
        )
        let stored = try DevContainerConfigurationStore.load(
            from: configurationURL,
            defaultSocket: DevContainerPathDefaults.socket
        )
        let selectedBackend = try selectBackend(
            backend,
            environment: environment,
            stored: stored.backend
        )
        let selectedCompose = try selectComposeProvider(
            composeProvider,
            environment: environment,
            stored: stored.composeProvider
        )
        let selectedSocket = try nonempty(socket)
            ?? socketFromEnvironment(environment)
            ?? stored.socket
        guard selectedSocket.hasPrefix("/") else {
            throw DevContainerError(
                .invalidRequest,
                message: "engine socket must be an absolute local path"
            )
        }
        let selectedContainer = try absolutePath(
            nonempty(containerExecutable)
                ?? nonempty(environment["DEVCONTAINER_CONTAINER_BIN"])
                ?? stored.containerExecutable,
            name: "runtime executable"
        )
        let selectedState = try absolutePath(
            nonempty(stateDatabase)
                ?? nonempty(environment["DEVCONTAINER_STATE"])
                ?? stored.stateDatabase,
            name: "state database"
        )
        return DevContainerRuntimeSelection(
            configuration: configurationURL,
            backend: selectedBackend,
            composeProvider: selectedCompose,
            containerExecutable: selectedContainer,
            socket: expandHome(selectedSocket),
            stateDatabase: selectedState,
            strictCompatibility: stored.strictCompatibility
        )
    }

    private static func selectBackend(
        _ explicit: String?,
        environment: [String: String],
        stored: BackendProvider
    ) throws -> BackendProvider {
        let value = nonempty(explicit)
            ?? nonempty(environment["DEVCONTAINER_BACKEND"])
            ?? stored.rawValue
        guard let result = BackendProvider(rawValue: value) else {
            throw DevContainerError(.invalidRequest, message: "invalid backend \(value)")
        }
        return result
    }

    private static func selectComposeProvider(
        _ explicit: String?,
        environment: [String: String],
        stored: ComposeProviderKind
    ) throws -> ComposeProviderKind {
        let value = nonempty(explicit)
            ?? nonempty(environment["DEVCONTAINER_COMPOSE_PROVIDER"])
            ?? stored.rawValue
        guard let result = ComposeProviderKind(rawValue: value) else {
            throw DevContainerError(
                .invalidRequest,
                message: "invalid Compose provider \(value)"
            )
        }
        return result
    }

    private static func absolutePath(_ value: String, name: String) throws -> String {
        let expanded = expandHome(value)
        guard expanded.hasPrefix("/") else {
            throw DevContainerError(
                .invalidRequest,
                message: "\(name) must be an absolute path"
            )
        }
        return expanded
    }

    private static func socketFromEnvironment(
        _ environment: [String: String]
    ) throws -> String? {
        if let socket = nonempty(environment["DEVCONTAINER_SOCKET"]) {
            return socket
        }
        guard let endpoint = nonempty(environment["DOCKER_HOST"]) else {
            return nil
        }
        guard
            endpoint.hasPrefix("unix://"),
            let socket = nonempty(String(endpoint.dropFirst("unix://".count)))
        else {
            throw DevContainerError(
                .invalidRequest,
                message: "DOCKER_HOST must select an absolute local Unix socket"
            )
        }
        return socket
    }

    private static func defaultConfiguration(
        environment: [String: String]
    ) -> String {
        guard let root = nonempty(environment["XDG_CONFIG_HOME"]) else {
            return DevContainerPathDefaults.configuration
        }
        return URL(fileURLWithPath: root, isDirectory: true)
            .appendingPathComponent("devcontainer", isDirectory: true)
            .appendingPathComponent("config.toml")
            .path
    }

    private static func nonempty(_ value: String?) -> String? {
        guard let value, !value.isEmpty else {
            return nil
        }
        return value
    }

    private static func expandHome(_ path: String) -> String {
        guard path == "~" || path.hasPrefix("~/") else {
            return path
        }
        return FileManager.default.homeDirectoryForCurrentUser.path
            + String(path.dropFirst())
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
        return try configuration(
            values: parse(text),
            defaultSocket: defaultSocket
        )
    }

    private static func parse(_ text: String) throws -> [String: String] {
        let allowedKeys = Set([
            "backend",
            "socket",
            "runtime.executable",
            "state.database",
            "compose.provider",
            "compatibility.strict"
        ])
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
            let qualifiedKey = section.isEmpty ? key : "\(section).\(key)"
            guard allowedKeys.contains(qualifiedKey) else {
                throw DevContainerError(
                    .invalidRequest,
                    message: "unknown configuration key \(qualifiedKey) at line \(lineNumber + 1)"
                )
            }
            guard values[qualifiedKey] == nil else {
                throw DevContainerError(
                    .invalidRequest,
                    message: "duplicate configuration key \(qualifiedKey) at line \(lineNumber + 1)"
                )
            }
            values[qualifiedKey] = value
        }
        return values
    }

    private static func configuration(
        values: [String: String],
        defaultSocket: String
    ) throws -> DevContainerConfiguration {
        let backendText = values["backend"] ?? BackendProvider.stock.rawValue
        guard let backend = BackendProvider(rawValue: backendText) else {
            throw DevContainerError(.invalidRequest, message: "invalid backend \(backendText)")
        }
        let composeText = values["compose.provider"] ?? ComposeProviderKind.containerCompose.rawValue
        guard let compose = ComposeProviderKind(rawValue: composeText) else {
            throw DevContainerError(
                .invalidRequest,
                message: "invalid Compose provider \(composeText)"
            )
        }
        let socket = expandHome(values["socket"] ?? defaultSocket)
        let containerExecutable = expandHome(
            values["runtime.executable"] ?? DevContainerPathDefaults.containerExecutable
        )
        let stateDatabase = expandHome(
            values["state.database"] ?? DevContainerPathDefaults.stateDatabase
        )
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
            containerExecutable: containerExecutable,
            socket: socket,
            stateDatabase: stateDatabase,
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

        [runtime]
        executable = "\(configuration.containerExecutable)"

        [state]
        database = "\(configuration.stateDatabase)"

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
