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

import CSQLite
import DevContainerModel
import DevContainerRuntimeSPI
import DevContainerState
import Foundation
import Testing

@Suite(.serialized)
struct SQLiteStateStoreTests {
    @Test
    func `claims are durable and provider immutable`() async throws {
        try await withStore { store in
            let key = ProjectKey(rawValue: "501:demo")
            let claimed = try await store.claimProject(
                key: key,
                provider: .stock,
                composeProject: "demo",
                projectDirectory: "/workspace",
                configurationHash: "config"
            )
            #expect(claimed.provider == .stock)
            #expect(try await store.project(key: key) == claimed)
            #expect(try await store.listProjects().map(\.key) == [key])

            await #expect(throws: DevContainerError.self) {
                try await store.claimProject(
                    key: key,
                    provider: .containerCompose,
                    composeProject: nil,
                    projectDirectory: nil,
                    configurationHash: nil
                )
            }
        }
    }

    @Test
    func `resources block provider reset`() async throws {
        try await withStore { store in
            let key = ProjectKey(rawValue: "501:demo")
            _ = try await store.claimProject(
                key: key,
                provider: .stock,
                composeProject: nil,
                projectDirectory: nil,
                configurationHash: "hash"
            )
            let now = Date()
            let resource = ResourceRecord(
                runtimeKind: "container",
                runtimeID: RuntimeID(rawValue: "runtime"),
                dockerID: DockerID(rawValue: "docker"),
                project: key,
                logicalName: "app",
                role: "primary",
                provider: .stock,
                specificationHash: "spec",
                generation: 1,
                observedState: "running",
                labelsHash: "labels",
                createdAt: now,
                updatedAt: now
            )
            try await store.recordResource(resource)
            #expect(try await store.resources(project: key) == [resource])
            await #expect(throws: DevContainerError.self) {
                try await store.releaseProject(key: key)
            }

            try await store.removeResource(runtimeID: resource.runtimeID)
            try await store.releaseProject(key: key)
            #expect(try await store.project(key: key) == nil)
        }
    }

    @Test
    func `operations and events support recovery`() async throws {
        try await withStore { store in
            let key = ProjectKey(rawValue: "501:demo")
            _ = try await store.claimProject(
                key: key,
                provider: .stock,
                composeProject: nil,
                projectDirectory: nil,
                configurationHash: nil
            )
            let now = Date()
            let operation = OperationRecord(
                id: OperationID(rawValue: "operation"),
                project: key,
                requestKind: "create",
                requestHash: "request",
                createdAt: now,
                updatedAt: now
            )
            try await store.beginOperation(operation)
            #expect(try await store.unfinishedOperations() == [operation])
            try await store.updateOperation(id: operation.id, phase: .committed, errorCode: nil)
            #expect(try await store.unfinishedOperations().isEmpty)

            let event = RuntimeEvent(
                sequence: 1,
                timestamp: now,
                resourceID: "docker",
                action: .create,
                attributes: ["project": "demo"]
            )
            try await store.appendEvent(event)
            let secondEvent = RuntimeEvent(
                sequence: 2,
                timestamp: now,
                resourceID: "docker-2",
                action: .start
            )
            try await store.appendEvent(secondEvent)
            #expect(
                try await store.events(after: 0, limit: 10)
                    == [event, secondEvent]
            )
            #expect(try await store.recentEvents(limit: 1) == [secondEvent])
            await #expect(throws: DevContainerError.self) {
                try await store.events(after: 0, limit: 0)
            }
            await #expect(throws: DevContainerError.self) {
                try await store.recentEvents(limit: 0)
            }
        }
    }

    @Test
    func `runtime container metadata survives lifecycle transitions`() async throws {
        try await withStore { store in
            let createdAt = Date(timeIntervalSinceReferenceDate: 1000)
            let metadata = RuntimeContainerMetadata(
                runtimeID: RuntimeID(rawValue: "runtime-container"),
                dockerID: DockerID(rawValue: "docker-container"),
                imageID: "sha256:immutable",
                spec: ContainerSpec(
                    name: "workspace",
                    image: "alpine:latest",
                    user: "501:20",
                    autoRemove: true
                ),
                createdAt: createdAt
            )
            try await store.recordContainerMetadata(metadata)
            #expect(
                try await store.containerMetadata(id: metadata.runtimeID.rawValue)
                    == metadata
            )
            #expect(
                try await store.containerMetadata(id: metadata.dockerID.rawValue)
                    == metadata
            )
            #expect(try await store.listContainerMetadata() == [metadata])

            let startedAt = Date(timeIntervalSinceReferenceDate: 2000)
            try await store.markContainerStarted(
                id: metadata.dockerID.rawValue,
                at: startedAt
            )
            #expect(
                try await store.containerMetadata(id: metadata.runtimeID.rawValue)?
                    .startedAt == startedAt
            )
            await #expect(throws: DevContainerError.self) {
                try await store.markContainerStarted(id: "missing", at: startedAt)
            }

            try await store.removeContainerMetadata(id: metadata.runtimeID.rawValue)
            #expect(try await store.containerMetadata(id: metadata.dockerID.rawValue) == nil)
            #expect(try await store.listContainerMetadata().isEmpty)
        }
    }

    @Test
    func `version two state migrates image identity transactionally`() throws {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let path = directory.appendingPathComponent("state.sqlite")
        _ = try SQLiteStateStore(path: path)
        try executeSQL(
            path: path,
            sql: """
            ALTER TABLE runtime_containers DROP COLUMN image_id;
            UPDATE schema_meta SET version = 2;
            """
        )

        _ = try SQLiteStateStore(path: path)
        #expect(
            try scalar(path: path, sql: "SELECT version FROM schema_meta")
                == SQLiteStateStore.schemaVersion
        )
        #expect(try tableColumns(path: path, table: "runtime_containers").contains("image_id"))
    }

    @Test
    func `future state schema is rejected without mutation`() throws {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let path = directory.appendingPathComponent("state.sqlite")
        _ = try SQLiteStateStore(path: path)
        try executeSQL(path: path, sql: "UPDATE schema_meta SET version = 999")

        #expect(throws: DevContainerError.self) {
            _ = try SQLiteStateStore(path: path)
        }
        #expect(try scalar(path: path, sql: "SELECT version FROM schema_meta") == 999)
    }

    @Test
    // swiftlint:disable:next function_body_length
    func `retention bounds history without deleting unfinished recovery work`() async throws {
        try await withStore { store in
            let project = ProjectKey(rawValue: "501:retention")
            _ = try await store.claimProject(
                key: project,
                provider: .stock,
                composeProject: nil,
                projectDirectory: nil,
                configurationHash: nil
            )
            let now = Date(timeIntervalSinceReferenceDate: 10000)
            let old = now.addingTimeInterval(-1000)
            try await store.beginOperation(
                OperationRecord(
                    id: OperationID(rawValue: "old-complete"),
                    project: project,
                    requestKind: "old",
                    requestHash: "old",
                    phase: .committed,
                    createdAt: old,
                    updatedAt: old
                )
            )
            try await store.beginOperation(
                OperationRecord(
                    id: OperationID(rawValue: "unfinished"),
                    project: project,
                    requestKind: "recover",
                    requestHash: "recover",
                    createdAt: old,
                    updatedAt: old
                )
            )
            try await store.beginOperation(
                OperationRecord(
                    id: OperationID(rawValue: "recent-complete"),
                    project: project,
                    requestKind: "recent",
                    requestHash: "recent",
                    phase: .committed,
                    createdAt: now,
                    updatedAt: now
                )
            )
            try await store.appendEvent(
                RuntimeEvent(
                    sequence: 1,
                    timestamp: old,
                    resourceID: "old",
                    action: .create
                )
            )
            try await store.appendEvent(
                RuntimeEvent(
                    sequence: 2,
                    timestamp: now,
                    resourceID: "recent",
                    action: .start
                )
            )

            let result = try await store.pruneRetainedState(
                policy: StateRetentionPolicy(
                    maximumEventCount: 1,
                    maximumCompletedOperationCount: 1,
                    maximumAge: 100
                ),
                now: now
            )
            #expect(result.deletedEvents == 1)
            #expect(result.deletedOperations == 1)
            #expect(result.retainedEvents == 1)
            #expect(result.retainedOperations == 2)
            #expect(
                try await store.unfinishedOperations().map(\.id)
                    == [OperationID(rawValue: "unfinished")]
            )
            #expect(
                try await store.events(after: 0, limit: 10).map(\.resourceID)
                    == ["recent"]
            )
        }
    }

    private func withStore(
        _ body: (SQLiteStateStore) async throws -> Void
    ) async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("devcontainer-state-test-\(UUID().uuidString)", isDirectory: true)
        defer {
            try? FileManager.default.removeItem(at: directory)
        }
        let store = try SQLiteStateStore(path: directory.appendingPathComponent("state.sqlite"))
        try await body(store)
    }

    private func temporaryDirectory() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent(
                "devcontainer-state-migration-test-\(UUID().uuidString)",
                isDirectory: true
            )
    }

    private func executeSQL(path: URL, sql: String) throws {
        let database = try openDatabase(path)
        defer { sqlite3_close(database) }
        guard sqlite3_exec(database, sql, nil, nil, nil) == SQLITE_OK else {
            throw DevContainerError(
                .stateCorruption,
                message: "test database update failed: \(String(cString: sqlite3_errmsg(database)))"
            )
        }
    }

    private func scalar(path: URL, sql: String) throws -> Int64 {
        let database = try openDatabase(path)
        defer { sqlite3_close(database) }
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(database, sql, -1, &statement, nil) == SQLITE_OK,
              let statement
        else {
            throw DevContainerError(.stateCorruption, message: "cannot prepare test scalar")
        }
        defer { sqlite3_finalize(statement) }
        guard sqlite3_step(statement) == SQLITE_ROW else {
            throw DevContainerError(.stateCorruption, message: "test scalar returned no row")
        }
        return sqlite3_column_int64(statement, 0)
    }

    private func tableColumns(path: URL, table: String) throws -> Set<String> {
        let database = try openDatabase(path)
        defer { sqlite3_close(database) }
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(
            database,
            "PRAGMA table_info(\(table))",
            -1,
            &statement,
            nil
        ) == SQLITE_OK, let statement
        else {
            throw DevContainerError(.stateCorruption, message: "cannot inspect test table")
        }
        defer { sqlite3_finalize(statement) }
        var result = Set<String>()
        while sqlite3_step(statement) == SQLITE_ROW {
            if let text = sqlite3_column_text(statement, 1) {
                result.insert(String(cString: text))
            }
        }
        return result
    }

    private func openDatabase(_ path: URL) throws -> OpaquePointer {
        var database: OpaquePointer?
        guard sqlite3_open(path.path, &database) == SQLITE_OK, let database else {
            throw DevContainerError(.stateCorruption, message: "cannot open test database")
        }
        return database
    }
}
