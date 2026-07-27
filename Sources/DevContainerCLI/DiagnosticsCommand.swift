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

import ArgumentParser
import CryptoKit
import Darwin
import DevContainerCore
import DevContainerModel
import DevContainerState
import Foundation

struct DiagnosticsCommand: AsyncParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "diagnostics",
        abstract: "Create a privacy-redacted support archive"
    )

    @Option(name: .long, help: "Apple container executable.")
    var container = CLIPaths.containerExecutable

    @Option(name: .long, help: "Optional container-compose executable.")
    var compose: String?

    @Option(name: .long, help: "Devcontainer configuration file.")
    var config = CLIPaths.configuration

    @Option(name: .long, help: "Engine state database.")
    var state = CLIPaths.stateDatabase

    @Option(name: .long, help: "Engine Unix socket.")
    var socket = CLIPaths.socket

    @Option(name: .long, help: "Additional log file; repeat at most eight times.")
    var log: [String] = []

    @Option(name: .long, help: "Maximum recent state events to include.")
    var eventLimit = 100

    @Option(name: .long, help: "Archive output path.")
    var output: String?

    mutating func run() async throws {
        let outputURL = try DiagnosticsPaths.outputURL(output)
        let requestedLogs = log.isEmpty
            ? DiagnosticsPaths.defaultLogs().filter {
                FileManager.default.isReadableFile(atPath: $0.path)
            }
            : log.map { URL(fileURLWithPath: $0) }
        let prepared = try await DiagnosticsBundleBuilder().prepare(
            DiagnosticsInputs(
                archiveName: outputURL.lastPathComponent,
                container: URL(fileURLWithPath: container),
                compose: compose.map { URL(fileURLWithPath: $0) },
                configuration: URL(fileURLWithPath: config),
                state: URL(fileURLWithPath: state),
                socket: URL(fileURLWithPath: socket),
                logs: requestedLogs,
                eventLimit: eventLimit
            )
        )
        defer {
            try? FileManager.default.removeItem(at: prepared.directory)
        }

        // The reviewable manifest is emitted before any archive is written.
        FileHandle.standardOutput.write(prepared.manifestData)
        FileHandle.standardOutput.write(Data("\n".utf8))
        try SystemTarArchiver.archive(
            directory: prepared.directory,
            output: outputURL
        )
        FileHandle.standardError.write(
            Data("wrote \(outputURL.path)\n".utf8)
        )
    }
}

enum DiagnosticsPaths {
    static func outputURL(_ requested: String?, now: Date = Date()) throws -> URL {
        if let requested {
            let url = URL(
                fileURLWithPath: requested,
                relativeTo: URL(
                    fileURLWithPath: FileManager.default.currentDirectoryPath,
                    isDirectory: true
                )
            ).standardizedFileURL
            guard !url.hasDirectoryPath else {
                throw DevContainerError(
                    .invalidRequest,
                    message: "diagnostics output must be a file path"
                )
            }
            return url
        }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyyMMdd'T'HHmmss'Z'"
        return URL(
            fileURLWithPath: FileManager.default.currentDirectoryPath,
            isDirectory: true
        ).appendingPathComponent(
            "devcontainer-diagnostics-\(formatter.string(from: now)).tar.gz"
        )
    }

    static func defaultLogs() -> [URL] {
        [
            "/opt/homebrew/var/log/devcontainer.log",
            "/opt/homebrew/var/log/devcontainer-error.log",
            "/usr/local/var/log/devcontainer.log",
            "/usr/local/var/log/devcontainer-error.log"
        ].map { URL(fileURLWithPath: $0) }
    }
}

struct PreparedDiagnostics {
    let directory: URL
    let manifest: DiagnosticsManifest
    let manifestData: Data
}

struct DiagnosticsInputs {
    var archiveName: String
    var container: URL
    var compose: URL?
    var configuration: URL
    var state: URL
    var socket: URL
    var logs: [URL]
    var eventLimit: Int
    var createdAt = Date()
}

struct DiagnosticsManifest: Codable, Equatable, Sendable {
    let schemaVersion: Int
    let createdAt: Date
    let archive: String
    let build: BuildInfo
    let files: [DiagnosticsFile]
    let warnings: [String]
    let privacy: String
}

struct DiagnosticsFile: Codable, Equatable, Sendable {
    let path: String
    let bytes: Int
    let sha256: String
}

struct DiagnosticsConfigurationSummary: Codable, Equatable, Sendable {
    let path: String
    let present: Bool
    let sha256: String?
    let configuration: DevContainerConfiguration?
    let error: String?
}

struct DiagnosticsRuntimeSummary: Codable, Equatable, Sendable {
    let executable: String
    let socket: DiagnosticsSocketSummary
    let probes: [DiagnosticsProbe]
}

struct DiagnosticsSocketSummary: Codable, Equatable, Sendable {
    let path: String
    let present: Bool
    let ownedByCurrentUser: Bool
    let socket: Bool
    let permissions: String?
}

struct DiagnosticsProbe: Codable, Equatable, Sendable {
    let name: String
    let arguments: [String]
    let exitCode: Int32?
    let standardOutput: String
    let standardError: String
    let error: String?
}

struct DiagnosticsStateSummary: Codable, Equatable, Sendable {
    let path: String
    let present: Bool
    let schemaVersion: Int?
    let projects: [ProjectRecord]
    let resources: [DiagnosticsResourceSummary]
    let unfinishedOperations: [OperationRecord]
    let recentEvents: [RuntimeEvent]
    let error: String?
}

struct DiagnosticsResourceSummary: Codable, Equatable, Sendable {
    let project: String
    let countsByKind: [String: Int]
}

struct DiagnosticsBundleBuilder {
    private static let maximumLogs = 8
    private static let maximumLogBytes = 256 * 1024
    private static let maximumProbeBytes = 256 * 1024

    func prepare(
        _ inputs: DiagnosticsInputs
    ) async throws -> PreparedDiagnostics {
        try validate(inputs)
        let directory = try makeStagingDirectory()
        do {
            let warnings = try await writePayload(inputs, to: directory)
            return try prepareManifest(
                inputs: inputs,
                warnings: warnings,
                directory: directory
            )
        } catch {
            try? FileManager.default.removeItem(at: directory)
            throw error
        }
    }

    private func validate(_ inputs: DiagnosticsInputs) throws {
        guard (1 ... 1000).contains(inputs.eventLimit) else {
            throw DevContainerError(
                .invalidRequest,
                message: "diagnostics event limit must be between 1 and 1000"
            )
        }
        guard inputs.logs.count <= Self.maximumLogs else {
            throw DevContainerError(
                .invalidRequest,
                message: "diagnostics accepts at most \(Self.maximumLogs) log files"
            )
        }
    }

    private func makeStagingDirectory() throws -> URL {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(
                "devcontainer-diagnostics-\(UUID().uuidString)",
                isDirectory: true
            )
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700]
        )
        return directory
    }

    private func writePayload(
        _ inputs: DiagnosticsInputs,
        to directory: URL
    ) async throws -> [String] {
        try write(
            configurationSummary(inputs.configuration),
            to: directory.appendingPathComponent("configuration.json")
        )
        try await write(
            runtimeSummary(
                container: inputs.container,
                compose: inputs.compose,
                socket: inputs.socket
            ),
            to: directory.appendingPathComponent("runtime.json")
        )
        let stateResult = await stateSummary(
            inputs.state,
            eventLimit: inputs.eventLimit
        )
        try write(
            stateResult.summary,
            to: directory.appendingPathComponent("state.json")
        )
        return try stateResult.warnings + copyLogs(inputs.logs, to: directory)
    }

    private func prepareManifest(
        inputs: DiagnosticsInputs,
        warnings: [String],
        directory: URL
    ) throws -> PreparedDiagnostics {
        let manifest = try DiagnosticsManifest(
            schemaVersion: 1,
            createdAt: inputs.createdAt,
            archive: inputs.archiveName,
            build: DevContainerProject.buildInfo,
            files: manifestFiles(in: directory),
            warnings: warnings.sorted(),
            privacy: "Paths beneath the current home directory and values named like credentials are redacted."
        )
        let manifestData = try encoder.encode(manifest)
        try write(
            manifestData,
            to: directory.appendingPathComponent("manifest.json")
        )
        return PreparedDiagnostics(
            directory: directory,
            manifest: manifest,
            manifestData: manifestData
        )
    }

    private var encoder: JSONEncoder {
        let encoder = JSONEncoder.pretty
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }

    private func configurationSummary(
        _ url: URL
    ) -> DiagnosticsConfigurationSummary {
        let path = DiagnosticsRedactor.redact(url.standardizedFileURL.path)
        guard FileManager.default.isReadableFile(atPath: url.path) else {
            return DiagnosticsConfigurationSummary(
                path: path,
                present: false,
                sha256: nil,
                configuration: nil,
                error: nil
            )
        }
        do {
            let data = try Data(contentsOf: url)
            var configuration = try DevContainerConfigurationStore.load(
                from: url,
                defaultSocket: CLIPaths.socket
            )
            configuration.socket = DiagnosticsRedactor.redact(
                configuration.socket
            )
            return DiagnosticsConfigurationSummary(
                path: path,
                present: true,
                sha256: Self.sha256(data),
                configuration: configuration,
                error: nil
            )
        } catch {
            return DiagnosticsConfigurationSummary(
                path: path,
                present: true,
                sha256: (try? Data(contentsOf: url)).map(Self.sha256),
                configuration: nil,
                error: DiagnosticsRedactor.redact(String(describing: error))
            )
        }
    }

    private func runtimeSummary(
        container: URL,
        compose: URL?,
        socket: URL
    ) async -> DiagnosticsRuntimeSummary {
        var probes: [DiagnosticsProbe] = []
        for command in probeRequests(container: container, compose: compose) {
            await probes.append(
                probe(
                    name: command.name,
                    executable: command.executable,
                    arguments: command.arguments
                )
            )
        }
        return DiagnosticsRuntimeSummary(
            executable: DiagnosticsRedactor.redact(
                container.standardizedFileURL.path
            ),
            socket: socketSummary(socket),
            probes: probes
        )
    }

    private func probeRequests(
        container: URL,
        compose: URL?
    ) -> [DiagnosticsProbeRequest] {
        var commands = [
            DiagnosticsProbeRequest(
                "container-version", container,
                ["system", "version", "--format", "json"]
            ),
            DiagnosticsProbeRequest(
                "container-status", container,
                ["system", "status", "--format", "json"]
            ),
            DiagnosticsProbeRequest(
                "container-capabilities", container, ["create", "--help"]
            ),
            DiagnosticsProbeRequest(
                "containers", container, ["list", "--all", "--format", "json"]
            ),
            DiagnosticsProbeRequest(
                "images", container, ["image", "list", "--format", "json"]
            ),
            DiagnosticsProbeRequest(
                "networks", container, ["network", "list", "--format", "json"]
            ),
            DiagnosticsProbeRequest(
                "volumes", container, ["volume", "list", "--format", "json"]
            )
        ]
        if let compose {
            commands.append(
                DiagnosticsProbeRequest(
                    "container-compose", compose, ["version", "--format", "json"]
                )
            )
        }
        return commands
    }

    private func probe(
        name: String,
        executable: URL,
        arguments: [String]
    ) async -> DiagnosticsProbe {
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            return DiagnosticsProbe(
                name: name,
                arguments: arguments,
                exitCode: nil,
                standardOutput: "",
                standardError: "",
                error: "not executable at \(DiagnosticsRedactor.redact(executable.path))"
            )
        }
        do {
            let result = try await runProcess(
                executable: executable,
                arguments: arguments
            )
            return DiagnosticsProbe(
                name: name,
                arguments: arguments,
                exitCode: result.status,
                standardOutput: DiagnosticsRedactor.redact(
                    Self.text(result.standardOutput, limit: Self.maximumProbeBytes)
                ),
                standardError: DiagnosticsRedactor.redact(
                    Self.text(result.standardError, limit: Self.maximumProbeBytes)
                ),
                error: nil
            )
        } catch {
            return DiagnosticsProbe(
                name: name,
                arguments: arguments,
                exitCode: nil,
                standardOutput: "",
                standardError: "",
                error: DiagnosticsRedactor.redact(String(describing: error))
            )
        }
    }

    private func socketSummary(_ url: URL) -> DiagnosticsSocketSummary {
        var status = stat()
        guard lstat(url.path, &status) == 0 else {
            return DiagnosticsSocketSummary(
                path: DiagnosticsRedactor.redact(url.path),
                present: false,
                ownedByCurrentUser: false,
                socket: false,
                permissions: nil
            )
        }
        return DiagnosticsSocketSummary(
            path: DiagnosticsRedactor.redact(url.path),
            present: true,
            ownedByCurrentUser: status.st_uid == getuid(),
            socket: status.st_mode & S_IFMT == S_IFSOCK,
            permissions: String(status.st_mode & 0o777, radix: 8)
        )
    }

    private func stateSummary(
        _ url: URL,
        eventLimit: Int
    ) async -> (summary: DiagnosticsStateSummary, warnings: [String]) {
        let path = DiagnosticsRedactor.redact(url.standardizedFileURL.path)
        guard FileManager.default.isReadableFile(atPath: url.path) else {
            return (emptyStateSummary(path: path, present: false), [])
        }
        do {
            return try await (
                populatedStateSummary(
                    url,
                    path: path,
                    eventLimit: eventLimit
                ),
                []
            )
        } catch {
            let message = DiagnosticsRedactor.redact(String(describing: error))
            return (emptyStateSummary(path: path, error: message), [
                "state database could not be summarized: \(message)"
            ])
        }
    }

    private func populatedStateSummary(
        _ url: URL,
        path: String,
        eventLimit: Int
    ) async throws -> DiagnosticsStateSummary {
        let store = try SQLiteStateStore(path: url)
        let projects = try await store.listProjects()
            .map(DiagnosticsRedactor.redact)
        return try await DiagnosticsStateSummary(
            path: path,
            present: true,
            schemaVersion: SQLiteStateStore.schemaVersion,
            projects: projects,
            resources: resourceSummaries(store, projects: projects),
            unfinishedOperations: store.unfinishedOperations()
                .map(DiagnosticsRedactor.redact),
            recentEvents: store.recentEvents(limit: eventLimit)
                .map(DiagnosticsRedactor.redact),
            error: nil
        )
    }

    private func resourceSummaries(
        _ store: SQLiteStateStore,
        projects: [ProjectRecord]
    ) async throws -> [DiagnosticsResourceSummary] {
        var summaries: [DiagnosticsResourceSummary] = []
        for project in projects {
            let records = try await store.resources(project: project.key)
            summaries.append(
                DiagnosticsResourceSummary(
                    project: project.key.rawValue,
                    countsByKind: Dictionary(
                        grouping: records,
                        by: \.runtimeKind
                    ).mapValues(\.count)
                )
            )
        }
        return summaries
    }

    private func emptyStateSummary(
        path: String,
        present: Bool = true,
        error: String? = nil
    ) -> DiagnosticsStateSummary {
        DiagnosticsStateSummary(
            path: path,
            present: present,
            schemaVersion: nil,
            projects: [],
            resources: [],
            unfinishedOperations: [],
            recentEvents: [],
            error: error
        )
    }

    private func copyLogs(
        _ logs: [URL],
        to directory: URL
    ) throws -> [String] {
        guard !logs.isEmpty else {
            return []
        }
        let destination = directory.appendingPathComponent(
            "logs",
            isDirectory: true
        )
        try FileManager.default.createDirectory(
            at: destination,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700]
        )
        var warnings: [String] = []
        for (index, source) in logs.enumerated() {
            guard FileManager.default.isReadableFile(atPath: source.path) else {
                warnings.append(
                    "log file is not readable: \(DiagnosticsRedactor.redact(source.path))"
                )
                continue
            }
            do {
                let data = try tail(source, maximumBytes: Self.maximumLogBytes)
                let name = String(format: "%02d-", index + 1)
                    + Self.safeName(source.lastPathComponent)
                try write(
                    Data(
                        DiagnosticsRedactor.redact(
                            Self.utf8(data)
                        ).utf8
                    ),
                    to: destination.appendingPathComponent(name)
                )
            } catch {
                warnings.append(
                    "log file could not be read: \(DiagnosticsRedactor.redact(source.path)): "
                        + DiagnosticsRedactor.redact(String(describing: error))
                )
            }
        }
        return warnings
    }

    private func tail(_ url: URL, maximumBytes: Int) throws -> Data {
        let handle = try FileHandle(forReadingFrom: url)
        defer {
            try? handle.close()
        }
        let length = try handle.seekToEnd()
        try handle.seek(toOffset: length > maximumBytes
            ? length - UInt64(maximumBytes)
            : 0)
        return try handle.readToEnd() ?? Data()
    }

    private func manifestFiles(in directory: URL) throws -> [DiagnosticsFile] {
        let keys: Set<URLResourceKey> = [.isRegularFileKey, .fileSizeKey]
        let resolvedRoot = directory.resolvingSymlinksInPath().path + "/"
        guard let enumerator = FileManager.default.enumerator(
            at: directory,
            includingPropertiesForKeys: Array(keys),
            options: [.skipsHiddenFiles]
        ) else {
            throw DevContainerError(
                .build,
                message: "cannot enumerate diagnostics staging directory"
            )
        }
        var files: [DiagnosticsFile] = []
        for case let url as URL in enumerator {
            let values = try url.resourceValues(forKeys: keys)
            guard values.isRegularFile == true else {
                continue
            }
            let data = try Data(contentsOf: url)
            let resolvedPath = url.resolvingSymlinksInPath().path
            guard resolvedPath.hasPrefix(resolvedRoot) else {
                throw DevContainerError(
                    .build,
                    message: "diagnostics file escaped the staging directory"
                )
            }
            let relative = String(resolvedPath.dropFirst(resolvedRoot.count))
            files.append(
                DiagnosticsFile(
                    path: relative,
                    bytes: values.fileSize ?? data.count,
                    sha256: Self.sha256(data)
                )
            )
        }
        return files.sorted { $0.path < $1.path }
    }

    private func write(_ value: some Encodable, to url: URL) throws {
        try write(encoder.encode(value), to: url)
    }

    private func write(_ data: Data, to url: URL) throws {
        try data.write(to: url, options: .atomic)
        guard chmod(url.path, S_IRUSR | S_IWUSR) == 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
    }

    private func runProcess(
        executable: URL,
        arguments: [String]
    ) async throws -> DiagnosticsProcessResult {
        let process = Process()
        let standardOutput = Pipe()
        let standardError = Pipe()
        process.executableURL = executable
        process.arguments = arguments
        process.environment = CLIPaths.safeEnvironment
        process.standardOutput = standardOutput
        process.standardError = standardError
        let (termination, continuation) = AsyncStream<Int32>.makeStream()
        process.terminationHandler = { process in
            continuation.yield(process.terminationStatus)
            continuation.finish()
        }
        try process.run()
        let outputTask = Task.detached {
            standardOutput.fileHandleForReading.readDataToEndOfFile()
        }
        let errorTask = Task.detached {
            standardError.fileHandleForReading.readDataToEndOfFile()
        }
        let status = await withTaskCancellationHandler {
            for await value in termination {
                return value
            }
            return 255
        } onCancel: {
            if process.isRunning {
                process.terminate()
            }
        }
        return await DiagnosticsProcessResult(
            standardOutput: outputTask.value,
            standardError: errorTask.value,
            status: status
        )
    }

    private static func sha256(_ data: Data) -> String {
        SHA256.hash(data: data)
            .map { String(format: "%02x", $0) }
            .joined()
    }

    private static func text(_ data: Data, limit: Int) -> String {
        let truncated = data.count > limit
        let text = utf8(Data(data.prefix(limit)))
        return truncated ? text + "\n<truncated>\n" : text
    }

    private static func utf8(_ data: Data) -> String {
        String(data: data, encoding: .utf8)
            ?? "<non-UTF-8 data omitted>"
    }

    private static func safeName(_ name: String) -> String {
        let scalars = name.unicodeScalars.map { scalar -> Character in
            CharacterSet.alphanumerics.contains(scalar)
                || scalar == "."
                || scalar == "-"
                || scalar == "_"
                ? Character(String(scalar))
                : "_"
        }
        let value = String(scalars)
        return value.isEmpty ? "log.txt" : value
    }
}

private struct DiagnosticsProcessResult: Sendable {
    let standardOutput: Data
    let standardError: Data
    let status: Int32
}

private struct DiagnosticsProbeRequest {
    let name: String
    let executable: URL
    let arguments: [String]

    init(_ name: String, _ executable: URL, _ arguments: [String]) {
        self.name = name
        self.executable = executable
        self.arguments = arguments
    }
}

enum DiagnosticsRedactor {
    private static let authorizationPattern = try? NSRegularExpression(
        pattern: #"(?i)(["']?authorization["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\r\n]+)"#
    )
    private static let sensitivePattern = try? NSRegularExpression(
        pattern:
        #"(?i)(["']?(?:authorization|token|password|secret|cookie|"#
            + #"credential)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)"#
    )

    static func redact(_ text: String) -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let pathRedacted = home == "/"
            ? text
            : text.replacingOccurrences(of: home, with: "$HOME")
        let authorizationRedacted = authorizationPattern?.stringByReplacingMatches(
            in: pathRedacted,
            range: NSRange(pathRedacted.startIndex..., in: pathRedacted),
            withTemplate: #"$1"<redacted>""#
        ) ?? pathRedacted
        guard let sensitivePattern else {
            return authorizationRedacted
        }
        return sensitivePattern.stringByReplacingMatches(
            in: authorizationRedacted,
            range: NSRange(
                authorizationRedacted.startIndex...,
                in: authorizationRedacted
            ),
            withTemplate: #"$1"<redacted>""#
        )
    }

    static func redact(_ project: ProjectRecord) -> ProjectRecord {
        var value = project
        value.projectDirectory = value.projectDirectory.map(redact)
        return value
    }

    static func redact(_ operation: OperationRecord) -> OperationRecord {
        var value = operation
        value.resourceKey = value.resourceKey.map(redact)
        value.errorCode = value.errorCode.map(redact)
        return value
    }

    static func redact(_ event: RuntimeEvent) -> RuntimeEvent {
        var value = event
        value.resourceID = redact(value.resourceID)
        value.attributes = Dictionary(
            uniqueKeysWithValues: value.attributes.map { key, item in
                let sensitive = [
                    "authorization",
                    "token",
                    "password",
                    "secret",
                    "cookie",
                    "credential"
                ].contains { key.localizedCaseInsensitiveContains($0) }
                return (key, sensitive ? "<redacted>" : redact(item))
            }
        )
        return value
    }
}

enum SystemTarArchiver {
    static func archive(directory: URL, output: URL) throws {
        try validate(directory: directory, output: output)
        do {
            try createArchive(directory: directory, output: output)
            guard chmod(output.path, S_IRUSR | S_IWUSR) == 0 else {
                throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
            }
        } catch {
            try? FileManager.default.removeItem(at: output)
            throw error
        }
    }

    private static func validate(directory: URL, output: URL) throws {
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(
            atPath: directory.path,
            isDirectory: &isDirectory
        ), isDirectory.boolValue else {
            throw DevContainerError(
                .invalidRequest,
                message: "diagnostics staging directory is missing"
            )
        }
        let parent = output.deletingLastPathComponent()
        guard FileManager.default.fileExists(
            atPath: parent.path,
            isDirectory: &isDirectory
        ), isDirectory.boolValue else {
            throw DevContainerError(
                .invalidRequest,
                message: "diagnostics output directory is missing"
            )
        }
        var outputStatus = stat()
        guard lstat(output.path, &outputStatus) != 0, errno == ENOENT else {
            throw DevContainerError(
                .conflict,
                message: "refusing to replace existing diagnostics archive at \(output.path)"
            )
        }
    }

    private static func createArchive(directory: URL, output: URL) throws {
        let process = Process()
        let standardError = Pipe()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/tar")
        process.arguments = [
            "--no-xattrs",
            "-czf",
            output.path,
            "-C",
            directory.path,
            "."
        ]
        var environment = CLIPaths.safeEnvironment
        environment["COPYFILE_DISABLE"] = "1"
        process.environment = environment
        process.standardOutput = FileHandle.nullDevice
        process.standardError = standardError
        try process.run()
        process.waitUntilExit()
        let error = standardError.fileHandleForReading.readDataToEndOfFile()
        guard process.terminationStatus == 0 else {
            let errorText = String(
                data: Data(error.prefix(4096)),
                encoding: .utf8
            ) ?? "<non-UTF-8 data omitted>"
            throw DevContainerError(
                .build,
                message: "tar exited \(process.terminationStatus): "
                    + DiagnosticsRedactor.redact(errorText)
            )
        }
    }
}
