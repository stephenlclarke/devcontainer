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

@testable import DevContainerCLI
import DevContainerCore
import DevContainerModel
import DevContainerState
import Foundation
import Testing

struct DiagnosticsCommandTests {
    @Test
    func `root command exposes diagnostics`() {
        #expect(
            DevContainerCommand.configuredSubcommands().contains {
                ObjectIdentifier($0) == ObjectIdentifier(DiagnosticsCommand.self)
            }
        )
    }

    @Test
    func `redactor removes home paths and credential-like values`() {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let value = DiagnosticsRedactor.redact(
            """
            path=\(home)/private
            Authorization: Bearer fixture-authorization
            "token":"fixture-token"
            password=fixture-password
            """
        )
        #expect(value.contains("$HOME/private"))
        #expect(!value.contains(home))
        #expect(!value.contains("fixture-authorization"))
        #expect(!value.contains("fixture-token"))
        #expect(!value.contains("fixture-password"))
        #expect(value.contains("<redacted>"))

        let event = DiagnosticsRedactor.redact(
            RuntimeEvent(
                sequence: 1,
                timestamp: Date(timeIntervalSince1970: 1),
                resourceID: "\(home)/container",
                action: .start,
                attributes: [
                    "tokenValue": "fixture-event-token",
                    "path": "\(home)/workspace"
                ]
            )
        )
        #expect(event.resourceID == "$HOME/container")
        #expect(event.attributes["tokenValue"] == "<redacted>")
        #expect(event.attributes["path"] == "$HOME/workspace")
    }

    @Test
    func `bundle contains redacted runtime state configuration and logs`() async throws {
        let fixture = try DiagnosticsFixture()
        let now = Date(timeIntervalSince1970: 1_700_000_000)
        let configuration = try await fixture.populateState(now: now)
        try Data("not-a-socket".utf8).write(to: fixture.socket)
        try fixture.writeSensitiveLog()

        let prepared = try await DiagnosticsBundleBuilder().prepare(
            fixture.inputs(createdAt: now)
        )
        defer {
            try? FileManager.default.removeItem(at: prepared.directory)
        }

        assertManifest(prepared, now: now)
        try assertConfiguration(prepared, expected: configuration)
        try assertRuntime(prepared)
        try assertState(prepared)
        try assertPayloadRedaction(prepared)
        try assertArchive(prepared, fixture: fixture)
    }

    private func assertManifest(
        _ prepared: PreparedDiagnostics,
        now: Date
    ) {
        #expect(prepared.manifest.schemaVersion == 1)
        #expect(prepared.manifest.createdAt == now)
        #expect(prepared.manifest.archive == "diagnostics.tar.gz")
        #expect(prepared.manifest.warnings.isEmpty)
        #expect(prepared.manifest.files.map(\.path) == [
            "configuration.json",
            "logs/01-engine.log",
            "runtime.json",
            "state.json"
        ])
        #expect(prepared.manifest.files.allSatisfy { $0.sha256.count == 64 })
    }

    private func assertConfiguration(
        _ prepared: PreparedDiagnostics,
        expected: DevContainerConfiguration
    ) throws {
        let summary: DiagnosticsConfigurationSummary = try decode(
            "configuration.json",
            from: prepared
        )
        #expect(summary.present)
        #expect(summary.sha256?.count == 64)
        var expectedConfiguration = expected
        expectedConfiguration.socket = "$HOME/diagnostics.sock"
        #expect(summary.configuration == expectedConfiguration)
        #expect(summary.error == nil)
    }

    private func assertRuntime(
        _ prepared: PreparedDiagnostics
    ) throws {
        let runtime: DiagnosticsRuntimeSummary = try decode(
            "runtime.json",
            from: prepared
        )
        #expect(runtime.probes.count == 8)
        #expect(runtime.probes.allSatisfy { $0.exitCode == 0 })
        #expect(runtime.socket.present)
        #expect(!runtime.socket.socket)
    }

    private func assertState(
        _ prepared: PreparedDiagnostics
    ) throws {
        let state: DiagnosticsStateSummary = try decode(
            "state.json",
            from: prepared
        )
        #expect(state.present)
        #expect(state.schemaVersion == SQLiteStateStore.schemaVersion)
        #expect(state.projects.count == 1)
        #expect(state.projects[0].projectDirectory == "$HOME/private-workspace")
        #expect(state.resources == [
            DiagnosticsResourceSummary(
                project: "fixture",
                countsByKind: ["container": 1]
            )
        ])
        #expect(state.unfinishedOperations.count == 1)
        #expect(state.recentEvents.count == 1)
        #expect(state.recentEvents[0].attributes["accessToken"] == "<redacted>")
    }

    private func assertPayloadRedaction(
        _ prepared: PreparedDiagnostics
    ) throws {
        var allPayload = ""
        for file in prepared.manifest.files {
            let data = try Data(
                contentsOf: prepared.directory.appendingPathComponent(file.path)
            )
            allPayload += try #require(
                String(data: data, encoding: .utf8)
            )
        }
        #expect(!allPayload.contains(
            FileManager.default.homeDirectoryForCurrentUser.path
        ))
        #expect(!allPayload.contains("fixture-log-secret"))
        #expect(!allPayload.contains("fixture-event-secret"))
        #expect(allPayload.contains("<redacted>"))
    }

    private func assertArchive(
        _ prepared: PreparedDiagnostics,
        fixture: DiagnosticsFixture
    ) throws {
        let output = fixture.root.appendingPathComponent("diagnostics.tar.gz")
        try SystemTarArchiver.archive(
            directory: prepared.directory,
            output: output
        )
        let attributes = try FileManager.default.attributesOfItem(
            atPath: output.path
        )
        let permissions = try #require(
            attributes[.posixPermissions] as? NSNumber
        )
        #expect(permissions.intValue == 0o600)
        let members = try tarMembers(output)
        #expect(members.contains("./manifest.json"))
        #expect(members.contains("./runtime.json"))
        #expect(members.contains("./logs/01-engine.log"))
    }

    @Test
    func `bundle records unavailable and malformed inputs`() async throws {
        let fixture = try DiagnosticsFixture()
        try Data("not valid configuration\n".utf8)
            .write(to: fixture.configuration)
        try Data("not a sqlite database".utf8).write(to: fixture.state)
        let missingLog = fixture.root.appendingPathComponent("missing.log")
        var inputs = fixture.inputs(eventLimit: 1)
        inputs.container = fixture.root.appendingPathComponent("missing-container")
        inputs.compose = nil
        inputs.socket = fixture.root.appendingPathComponent("missing.sock")
        inputs.logs = [missingLog]
        let prepared = try await DiagnosticsBundleBuilder().prepare(
            inputs
        )
        defer {
            try? FileManager.default.removeItem(at: prepared.directory)
        }

        try assertUnavailableInputs(prepared)
    }

    private func assertUnavailableInputs(
        _ prepared: PreparedDiagnostics
    ) throws {
        let configuration: DiagnosticsConfigurationSummary = try decode(
            "configuration.json",
            from: prepared
        )
        #expect(configuration.present)
        #expect(configuration.sha256?.count == 64)
        #expect(configuration.configuration == nil)
        #expect(configuration.error != nil)

        let runtime: DiagnosticsRuntimeSummary = try decode(
            "runtime.json",
            from: prepared
        )
        #expect(runtime.probes.count == 7)
        #expect(runtime.probes.allSatisfy {
            $0.error?.contains("not executable") == true
        })
        #expect(!runtime.socket.present)

        let state: DiagnosticsStateSummary = try decode(
            "state.json",
            from: prepared
        )
        #expect(state.present)
        #expect(state.schemaVersion == nil)
        #expect(state.error != nil)
        #expect(prepared.manifest.warnings.count == 2)
        #expect(
            prepared.manifest.warnings.contains {
                $0.contains("state database could not be summarized")
            }
        )
        #expect(
            prepared.manifest.warnings.contains {
                $0.contains("log file is not readable")
            }
        )
    }

    @Test
    func `bundle bounds events and logs`() async throws {
        let fixture = try DiagnosticsFixture()
        let builder = DiagnosticsBundleBuilder()
        var invalidEvents = fixture.inputs()
        invalidEvents.eventLimit = 0
        await #expect(throws: DevContainerError.self) {
            try await builder.prepare(invalidEvents)
        }
        var excessiveLogs = fixture.inputs(eventLimit: 1)
        excessiveLogs.logs = Array(repeating: fixture.log, count: 9)
        await #expect(throws: DevContainerError.self) {
            try await builder.prepare(excessiveLogs)
        }
    }

    @Test
    func `archive refuses missing roots existing files and dangling links`() throws {
        let fixture = try DiagnosticsFixture()
        let missingDirectory = fixture.root.appendingPathComponent("missing")
        #expect(throws: DevContainerError.self) {
            try SystemTarArchiver.archive(
                directory: missingDirectory,
                output: fixture.root.appendingPathComponent("output.tar.gz")
            )
        }
        #expect(throws: DevContainerError.self) {
            try SystemTarArchiver.archive(
                directory: fixture.root,
                output: missingDirectory.appendingPathComponent("output.tar.gz")
            )
        }

        let existing = fixture.root.appendingPathComponent("existing.tar.gz")
        try Data().write(to: existing)
        #expect(throws: DevContainerError.self) {
            try SystemTarArchiver.archive(
                directory: fixture.root,
                output: existing
            )
        }

        let dangling = fixture.root.appendingPathComponent("dangling.tar.gz")
        try FileManager.default.createSymbolicLink(
            at: dangling,
            withDestinationURL: fixture.root.appendingPathComponent("absent")
        )
        #expect(throws: DevContainerError.self) {
            try SystemTarArchiver.archive(
                directory: fixture.root,
                output: dangling
            )
        }
    }

    @Test
    func `diagnostics paths resolve explicit and timestamped archives`() throws {
        let explicit = try DiagnosticsPaths.outputURL("result.tar.gz")
        #expect(
            explicit.path == URL(
                fileURLWithPath: FileManager.default.currentDirectoryPath,
                isDirectory: true
            ).appendingPathComponent("result.tar.gz").path
        )
        let timestamped = try DiagnosticsPaths.outputURL(
            nil,
            now: Date(timeIntervalSince1970: 0)
        )
        #expect(
            timestamped.lastPathComponent
                == "devcontainer-diagnostics-19700101T000000Z.tar.gz"
        )
        #expect(throws: DevContainerError.self) {
            _ = try DiagnosticsPaths.outputURL("directory/")
        }
        #expect(DiagnosticsPaths.defaultLogs().count == 4)
    }

    private func decode<Value: Decodable>(
        _ name: String,
        from prepared: PreparedDiagnostics
    ) throws -> Value {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(
            Value.self,
            from: Data(
                contentsOf: prepared.directory.appendingPathComponent(name)
            )
        )
    }

    private func tarMembers(_ archive: URL) throws -> [String] {
        let process = Process()
        let output = Pipe()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/tar")
        process.arguments = ["-tzf", archive.path]
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        try process.run()
        process.waitUntilExit()
        #expect(process.terminationStatus == 0)
        let data = output.fileHandleForReading.readDataToEndOfFile()
        let text = try #require(String(data: data, encoding: .utf8))
        return text.split(separator: "\n").map(String.init)
    }
}

private final class DiagnosticsFixture {
    let root: URL
    let configuration: URL
    let state: URL
    let socket: URL
    let log: URL
    let container: URL
    let compose: URL

    init() throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent(
                "devcontainer-diagnostics-tests-\(UUID().uuidString)",
                isDirectory: true
            )
        configuration = root.appendingPathComponent("config/config.toml")
        state = root.appendingPathComponent("state/state.sqlite")
        socket = root.appendingPathComponent("engine.sock")
        log = root.appendingPathComponent("engine.log")
        container = root.appendingPathComponent("container")
        compose = root.appendingPathComponent("container-compose")
        try Self.createDirectory(root)
        for directory in [
            configuration.deletingLastPathComponent(),
            state.deletingLastPathComponent()
        ] {
            try Self.createDirectory(directory)
        }
        try writeExecutable(Self.containerScript, to: container)
        try writeExecutable(Self.composeScript, to: compose)
    }

    deinit {
        try? FileManager.default.removeItem(at: root)
    }

    func inputs(
        eventLimit: Int = 10,
        createdAt: Date = Date()
    ) -> DiagnosticsInputs {
        DiagnosticsInputs(
            archiveName: "diagnostics.tar.gz",
            container: container,
            compose: compose,
            configuration: configuration,
            state: state,
            socket: socket,
            logs: [log],
            eventLimit: eventLimit,
            createdAt: createdAt
        )
    }

    func writeSensitiveLog() throws {
        var data = Data(repeating: 0x78, count: 300_000)
        data.append(
            Data(
                "\npassword=fixture-log-secret\n"
                    .appending(
                        FileManager.default.homeDirectoryForCurrentUser.path
                    ).utf8
            )
        )
        try data.write(to: log)
    }

    func populateState(
        now: Date
    ) async throws -> DevContainerConfiguration {
        let configuration = Self.configurationValue()
        try DevContainerConfigurationStore.save(
            configuration,
            to: self.configuration
        )
        let store = try SQLiteStateStore(path: state)
        let project = ProjectKey(rawValue: "fixture")
        _ = try await store.claimProject(
            key: project,
            provider: .stock,
            composeProject: nil,
            projectDirectory: FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("private-workspace").path,
            configurationHash: "configuration-hash"
        )
        try await store.recordResource(Self.resource(project: project, now: now))
        try await store.beginOperation(Self.operation(project: project, now: now))
        try await store.appendEvent(Self.event(now: now))
        return configuration
    }

    private static func configurationValue() -> DevContainerConfiguration {
        DevContainerConfiguration(
            backend: .stock,
            composeProvider: .docker,
            socket: FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("diagnostics.sock").path,
            strictCompatibility: true
        )
    }

    private static func resource(
        project: ProjectKey,
        now: Date
    ) -> ResourceRecord {
        ResourceRecord(
            runtimeKind: "container",
            runtimeID: RuntimeID(rawValue: "runtime-fixture"),
            dockerID: DockerID(rawValue: "docker-fixture"),
            project: project,
            logicalName: "app",
            role: "service",
            provider: .stock,
            specificationHash: "specification-hash",
            generation: 1,
            observedState: "running",
            labelsHash: "labels-hash",
            createdAt: now,
            updatedAt: now
        )
    }

    private static func operation(
        project: ProjectKey,
        now: Date
    ) -> OperationRecord {
        OperationRecord(
            id: OperationID(rawValue: "operation-fixture"),
            project: project,
            resourceKey: "resource",
            requestKind: "create",
            requestHash: "request-hash",
            createdAt: now,
            updatedAt: now
        )
    }

    private static func event(now: Date) -> RuntimeEvent {
        RuntimeEvent(
            sequence: 1,
            timestamp: now,
            resourceID: "runtime-fixture",
            action: .start,
            attributes: ["accessToken": "fixture-event-secret"]
        )
    }

    private static func createDirectory(_ url: URL) throws {
        try FileManager.default.createDirectory(
            at: url,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700]
        )
    }

    private func writeExecutable(_ text: String, to url: URL) throws {
        try Data((text + "\n").utf8).write(to: url)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o755],
            ofItemAtPath: url.path
        )
    }

    private static let containerScript = """
    #!/bin/sh
    set -eu
    case "$*" in
      "system version --format json")
        printf '{"version":"1.1.0","token":"fixture-runtime-secret","home":"%s"}\\n' "$HOME"
        ;;
      "system status --format json")
        printf '{"status":"running"}\\n'
        ;;
      "create --help")
        printf '%s\\n' 'Usage: container create'
        ;;
      *)
        printf '{"result":"ok"}\\n'
        ;;
    esac
    """

    private static let composeScript = """
    #!/bin/sh
    printf '{"version":"fixture"}\\n'
    """
}
