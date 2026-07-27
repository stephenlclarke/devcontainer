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
import Darwin
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

public actor SQLiteStateStore: ProjectStateStore, RuntimeMetadataStore {
    public static let schemaVersion = 2

    private let handle: SQLiteHandle
    private var database: OpaquePointer {
        handle.pointer
    }

    public let path: URL

    public init(path: URL) throws {
        self.path = path.standardizedFileURL
        try Self.prepareParentDirectory(for: self.path)

        var handle: OpaquePointer?
        let flags = SQLITE_OPEN_CREATE | SQLITE_OPEN_READWRITE | SQLITE_OPEN_FULLMUTEX
        let status = sqlite3_open_v2(self.path.path, &handle, flags, nil)
        guard status == SQLITE_OK, let handle else {
            let message = handle.map { String(cString: sqlite3_errmsg($0)) } ?? "unknown SQLite error"
            if let handle {
                sqlite3_close(handle)
            }
            throw DevContainerError(.stateCorruption, message: "cannot open state database: \(message)")
        }
        self.handle = SQLiteHandle(pointer: handle)

        do {
            try Self.execute(handle, sql: "PRAGMA foreign_keys = ON")
            try Self.execute(handle, sql: "PRAGMA journal_mode = WAL")
            try Self.execute(handle, sql: "PRAGMA synchronous = FULL")
            try Self.migrate(handle)
            _ = chmod(path.path, S_IRUSR | S_IWUSR)
        } catch {
            throw error
        }
    }

    public func claimProject(
        key: ProjectKey,
        provider: BackendProvider,
        composeProject: String? = nil,
        projectDirectory: String? = nil,
        configurationHash: String? = nil
    ) throws -> ProjectRecord {
        try transaction {
            if let existing = try project(key: key) {
                guard existing.provider == provider else {
                    throw DevContainerError(
                        .conflict,
                        message: "project \(key) is owned by \(existing.provider.rawValue); drain and reset it before selecting \(provider.rawValue)"
                    )
                }
                return existing
            }

            let now = Date()
            let record = ProjectRecord(
                key: key,
                provider: provider,
                composeProject: composeProject,
                projectDirectory: projectDirectory,
                configurationHash: configurationHash,
                createdAt: now,
                updatedAt: now
            )
            let sql = """
            INSERT INTO projects (
                project_key, provider, compose_project, project_directory,
                config_hash, desired_generation, desired_state,
                reconciliation_state, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            try withStatement(sql) { statement in
                try bind(key.rawValue, at: 1, to: statement)
                try bind(provider.rawValue, at: 2, to: statement)
                try bind(composeProject, at: 3, to: statement)
                try bind(projectDirectory, at: 4, to: statement)
                try bind(configurationHash, at: 5, to: statement)
                try bind(record.desiredGeneration, at: 6, to: statement)
                try bind(record.desiredState.rawValue, at: 7, to: statement)
                try bind(record.reconciliationState.rawValue, at: 8, to: statement)
                try bind(now.timeIntervalSinceReferenceDate, at: 9, to: statement)
                try bind(now.timeIntervalSinceReferenceDate, at: 10, to: statement)
                try stepDone(statement)
            }
            return record
        }
    }

    public func project(key: ProjectKey) throws -> ProjectRecord? {
        let sql = """
        SELECT project_key, provider, compose_project, project_directory,
               config_hash, desired_generation, desired_state,
               reconciliation_state, created_at, updated_at
        FROM projects WHERE project_key = ?
        """
        return try withStatement(sql) { statement in
            try bind(key.rawValue, at: 1, to: statement)
            guard sqlite3_step(statement) == SQLITE_ROW else {
                return nil
            }
            return try decodeProject(statement)
        }
    }

    public func listProjects() throws -> [ProjectRecord] {
        let sql = """
        SELECT project_key, provider, compose_project, project_directory,
               config_hash, desired_generation, desired_state,
               reconciliation_state, created_at, updated_at
        FROM projects ORDER BY project_key
        """
        return try withStatement(sql) { statement in
            var records: [ProjectRecord] = []
            while sqlite3_step(statement) == SQLITE_ROW {
                try records.append(decodeProject(statement))
            }
            return records
        }
    }

    public func setProjectState(
        key: ProjectKey,
        desiredState: DesiredProjectState,
        reconciliationState: ReconciliationState,
        generation: Int64
    ) throws {
        let sql = """
        UPDATE projects
        SET desired_state = ?, reconciliation_state = ?,
            desired_generation = ?, updated_at = ?
        WHERE project_key = ?
        """
        try withStatement(sql) { statement in
            try bind(desiredState.rawValue, at: 1, to: statement)
            try bind(reconciliationState.rawValue, at: 2, to: statement)
            try bind(generation, at: 3, to: statement)
            try bind(Date().timeIntervalSinceReferenceDate, at: 4, to: statement)
            try bind(key.rawValue, at: 5, to: statement)
            try stepDone(statement)
            guard sqlite3_changes(database) == 1 else {
                throw DevContainerError(.notFound, message: "project \(key) is not claimed")
            }
        }
    }

    public func releaseProject(key: ProjectKey) throws {
        try transaction {
            let resourceCount = try scalarInt64(
                "SELECT COUNT(*) FROM resources WHERE project_key = ?",
                text: key.rawValue
            )
            guard resourceCount == 0 else {
                throw DevContainerError(
                    .conflict,
                    message: "project \(key) still owns \(resourceCount) runtime resources"
                )
            }
            try withStatement("DELETE FROM operations WHERE project_key = ?") { statement in
                try bind(key.rawValue, at: 1, to: statement)
                try stepDone(statement)
            }
            try withStatement("DELETE FROM projects WHERE project_key = ?") { statement in
                try bind(key.rawValue, at: 1, to: statement)
                try stepDone(statement)
            }
        }
    }

    public func recordResource(_ resource: ResourceRecord) throws {
        let sql = """
        INSERT INTO resources (
            runtime_kind, runtime_id, docker_id, project_key, logical_name,
            role, provider, spec_hash, generation, observed_state,
            labels_hash, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(runtime_kind, runtime_id) DO UPDATE SET
            docker_id = excluded.docker_id,
            project_key = excluded.project_key,
            logical_name = excluded.logical_name,
            role = excluded.role,
            provider = excluded.provider,
            spec_hash = excluded.spec_hash,
            generation = excluded.generation,
            observed_state = excluded.observed_state,
            labels_hash = excluded.labels_hash,
            updated_at = excluded.updated_at
        """
        try withStatement(sql) { statement in
            try bind(resource.runtimeKind, at: 1, to: statement)
            try bind(resource.runtimeID.rawValue, at: 2, to: statement)
            try bind(resource.dockerID.rawValue, at: 3, to: statement)
            try bind(resource.project.rawValue, at: 4, to: statement)
            try bind(resource.logicalName, at: 5, to: statement)
            try bind(resource.role, at: 6, to: statement)
            try bind(resource.provider.rawValue, at: 7, to: statement)
            try bind(resource.specificationHash, at: 8, to: statement)
            try bind(resource.generation, at: 9, to: statement)
            try bind(resource.observedState, at: 10, to: statement)
            try bind(resource.labelsHash, at: 11, to: statement)
            try bind(resource.createdAt.timeIntervalSinceReferenceDate, at: 12, to: statement)
            try bind(resource.updatedAt.timeIntervalSinceReferenceDate, at: 13, to: statement)
            try stepDone(statement)
        }
    }

    public func removeResource(runtimeID: RuntimeID) throws {
        try withStatement("DELETE FROM resources WHERE runtime_id = ?") { statement in
            try bind(runtimeID.rawValue, at: 1, to: statement)
            try stepDone(statement)
        }
    }

    public func resources(project: ProjectKey) throws -> [ResourceRecord] {
        let sql = """
        SELECT runtime_kind, runtime_id, docker_id, project_key, logical_name,
               role, provider, spec_hash, generation, observed_state,
               labels_hash, created_at, updated_at
        FROM resources WHERE project_key = ? ORDER BY runtime_kind, logical_name
        """
        return try withStatement(sql) { statement in
            try bind(project.rawValue, at: 1, to: statement)
            var records: [ResourceRecord] = []
            while sqlite3_step(statement) == SQLITE_ROW {
                guard
                    let provider = BackendProvider(rawValue: text(statement, 6))
                else {
                    throw DevContainerError(.stateCorruption, message: "invalid resource provider")
                }
                records.append(
                    ResourceRecord(
                        runtimeKind: text(statement, 0),
                        runtimeID: RuntimeID(rawValue: text(statement, 1)),
                        dockerID: DockerID(rawValue: text(statement, 2)),
                        project: ProjectKey(rawValue: text(statement, 3)),
                        logicalName: text(statement, 4),
                        role: text(statement, 5),
                        provider: provider,
                        specificationHash: text(statement, 7),
                        generation: sqlite3_column_int64(statement, 8),
                        observedState: text(statement, 9),
                        labelsHash: text(statement, 10),
                        createdAt: Date(timeIntervalSinceReferenceDate: sqlite3_column_double(statement, 11)),
                        updatedAt: Date(timeIntervalSinceReferenceDate: sqlite3_column_double(statement, 12))
                    )
                )
            }
            return records
        }
    }

    public func beginOperation(_ operation: OperationRecord) throws {
        let sql = """
        INSERT INTO operations (
            operation_id, project_key, resource_key, request_kind, request_hash,
            phase, retry_class, error_code, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        try withStatement(sql) { statement in
            try bind(operation.id.rawValue, at: 1, to: statement)
            try bind(operation.project.rawValue, at: 2, to: statement)
            try bind(operation.resourceKey, at: 3, to: statement)
            try bind(operation.requestKind, at: 4, to: statement)
            try bind(operation.requestHash, at: 5, to: statement)
            try bind(operation.phase.rawValue, at: 6, to: statement)
            try bind(operation.retryClass, at: 7, to: statement)
            try bind(operation.errorCode, at: 8, to: statement)
            try bind(operation.createdAt.timeIntervalSinceReferenceDate, at: 9, to: statement)
            try bind(operation.updatedAt.timeIntervalSinceReferenceDate, at: 10, to: statement)
            try stepDone(statement)
        }
    }

    public func updateOperation(
        id: OperationID,
        phase: OperationPhase,
        errorCode: String? = nil
    ) throws {
        let sql = """
        UPDATE operations SET phase = ?, error_code = ?, updated_at = ?
        WHERE operation_id = ?
        """
        try withStatement(sql) { statement in
            try bind(phase.rawValue, at: 1, to: statement)
            try bind(errorCode, at: 2, to: statement)
            try bind(Date().timeIntervalSinceReferenceDate, at: 3, to: statement)
            try bind(id.rawValue, at: 4, to: statement)
            try stepDone(statement)
            guard sqlite3_changes(database) == 1 else {
                throw DevContainerError(.notFound, message: "operation \(id) was not found")
            }
        }
    }

    public func unfinishedOperations() throws -> [OperationRecord] {
        let sql = """
        SELECT operation_id, project_key, resource_key, request_kind,
               request_hash, phase, retry_class, error_code, created_at, updated_at
        FROM operations WHERE phase NOT IN ('committed', 'failed')
        ORDER BY created_at, operation_id
        """
        return try withStatement(sql) { statement in
            var records: [OperationRecord] = []
            while sqlite3_step(statement) == SQLITE_ROW {
                guard let phase = OperationPhase(rawValue: text(statement, 5)) else {
                    throw DevContainerError(.stateCorruption, message: "invalid operation phase")
                }
                records.append(
                    OperationRecord(
                        id: OperationID(rawValue: text(statement, 0)),
                        project: ProjectKey(rawValue: text(statement, 1)),
                        resourceKey: optionalText(statement, 2),
                        requestKind: text(statement, 3),
                        requestHash: text(statement, 4),
                        phase: phase,
                        retryClass: text(statement, 6),
                        errorCode: optionalText(statement, 7),
                        createdAt: Date(timeIntervalSinceReferenceDate: sqlite3_column_double(statement, 8)),
                        updatedAt: Date(timeIntervalSinceReferenceDate: sqlite3_column_double(statement, 9))
                    )
                )
            }
            return records
        }
    }

    public func appendEvent(_ event: RuntimeEvent) throws {
        let attributes = try JSONEncoder().encode(event.attributes)
        let sql = """
        INSERT INTO events (
            seq, time, resource, resource_type, action, attributes_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """
        try withStatement(sql) { statement in
            try bind(event.sequence, at: 1, to: statement)
            try bind(event.timestamp.timeIntervalSinceReferenceDate, at: 2, to: statement)
            try bind(event.resourceID, at: 3, to: statement)
            try bind(event.resourceType, at: 4, to: statement)
            try bind(event.action.rawValue, at: 5, to: statement)
            try bind(attributes, at: 6, to: statement)
            try stepDone(statement)
        }
    }

    public func events(after sequence: Int64, limit: Int) throws -> [RuntimeEvent] {
        guard (1 ... 10000).contains(limit) else {
            throw DevContainerError(.invalidRequest, message: "event limit must be between 1 and 10000")
        }
        let sql = """
        SELECT seq, time, resource, resource_type, action, attributes_json
        FROM events WHERE seq > ? ORDER BY seq LIMIT ?
        """
        return try withStatement(sql) { statement in
            try bind(sequence, at: 1, to: statement)
            try bind(Int64(limit), at: 2, to: statement)
            var events: [RuntimeEvent] = []
            while sqlite3_step(statement) == SQLITE_ROW {
                try events.append(decodeEvent(statement))
            }
            return events
        }
    }

    public func recentEvents(limit: Int) throws -> [RuntimeEvent] {
        guard (1 ... 10000).contains(limit) else {
            throw DevContainerError(.invalidRequest, message: "event limit must be between 1 and 10000")
        }
        let sql = """
        SELECT seq, time, resource, resource_type, action, attributes_json
        FROM events ORDER BY seq DESC LIMIT ?
        """
        return try withStatement(sql) { statement in
            try bind(Int64(limit), at: 1, to: statement)
            var events: [RuntimeEvent] = []
            while sqlite3_step(statement) == SQLITE_ROW {
                try events.append(decodeEvent(statement))
            }
            return events.reversed()
        }
    }

    private func decodeEvent(_ statement: OpaquePointer) throws -> RuntimeEvent {
        guard
            let action = RuntimeEventAction(rawValue: text(statement, 4)),
            let attributesData = blob(statement, 5)
        else {
            throw DevContainerError(.stateCorruption, message: "invalid event record")
        }
        let attributes = try JSONDecoder().decode(
            [String: String].self,
            from: attributesData
        )
        return RuntimeEvent(
            sequence: sqlite3_column_int64(statement, 0),
            timestamp: Date(
                timeIntervalSinceReferenceDate: sqlite3_column_double(
                    statement,
                    1
                )
            ),
            resourceID: text(statement, 2),
            resourceType: text(statement, 3),
            action: action,
            attributes: attributes
        )
    }

    public func recordContainerMetadata(
        _ metadata: RuntimeContainerMetadata
    ) throws {
        let specification = try JSONEncoder().encode(metadata.spec)
        let sql = """
        INSERT INTO runtime_containers (
            runtime_id, docker_id, specification_json, created_at, started_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(runtime_id) DO UPDATE SET
            docker_id = excluded.docker_id,
            specification_json = excluded.specification_json,
            created_at = excluded.created_at,
            started_at = excluded.started_at
        """
        try withStatement(sql) { statement in
            try bind(metadata.runtimeID.rawValue, at: 1, to: statement)
            try bind(metadata.dockerID.rawValue, at: 2, to: statement)
            try bind(specification, at: 3, to: statement)
            try bind(metadata.createdAt.timeIntervalSinceReferenceDate, at: 4, to: statement)
            try bind(
                metadata.startedAt?.timeIntervalSinceReferenceDate,
                at: 5,
                to: statement
            )
            try stepDone(statement)
        }
    }

    public func containerMetadata(id: String) throws -> RuntimeContainerMetadata? {
        let sql = """
        SELECT runtime_id, docker_id, specification_json, created_at, started_at
        FROM runtime_containers
        WHERE runtime_id = ? OR docker_id = ?
        """
        return try withStatement(sql) { statement in
            try bind(id, at: 1, to: statement)
            try bind(id, at: 2, to: statement)
            guard sqlite3_step(statement) == SQLITE_ROW else {
                return nil
            }
            guard let specification = blob(statement, 2) else {
                throw DevContainerError(
                    .stateCorruption,
                    message: "runtime container specification is missing"
                )
            }
            return try RuntimeContainerMetadata(
                runtimeID: RuntimeID(rawValue: text(statement, 0)),
                dockerID: DockerID(rawValue: text(statement, 1)),
                spec: JSONDecoder().decode(ContainerSpec.self, from: specification),
                createdAt: Date(
                    timeIntervalSinceReferenceDate: sqlite3_column_double(statement, 3)
                ),
                startedAt: sqlite3_column_type(statement, 4) == SQLITE_NULL
                    ? nil
                    : Date(
                        timeIntervalSinceReferenceDate: sqlite3_column_double(statement, 4)
                    )
            )
        }
    }

    public func listContainerMetadata() throws -> [RuntimeContainerMetadata] {
        let sql = """
        SELECT runtime_id, docker_id, specification_json, created_at, started_at
        FROM runtime_containers
        ORDER BY runtime_id
        """
        return try withStatement(sql) { statement in
            var values: [RuntimeContainerMetadata] = []
            while sqlite3_step(statement) == SQLITE_ROW {
                guard let specification = blob(statement, 2) else {
                    throw DevContainerError(
                        .stateCorruption,
                        message: "runtime container specification is missing"
                    )
                }
                try values.append(
                    RuntimeContainerMetadata(
                        runtimeID: RuntimeID(rawValue: text(statement, 0)),
                        dockerID: DockerID(rawValue: text(statement, 1)),
                        spec: JSONDecoder().decode(
                            ContainerSpec.self,
                            from: specification
                        ),
                        createdAt: Date(
                            timeIntervalSinceReferenceDate: sqlite3_column_double(
                                statement,
                                3
                            )
                        ),
                        startedAt: sqlite3_column_type(statement, 4) == SQLITE_NULL
                            ? nil
                            : Date(
                                timeIntervalSinceReferenceDate: sqlite3_column_double(
                                    statement,
                                    4
                                )
                            )
                    )
                )
            }
            return values
        }
    }

    public func markContainerStarted(id: String, at date: Date) throws {
        let sql = """
        UPDATE runtime_containers SET started_at = ?
        WHERE runtime_id = ? OR docker_id = ?
        """
        try withStatement(sql) { statement in
            try bind(date.timeIntervalSinceReferenceDate, at: 1, to: statement)
            try bind(id, at: 2, to: statement)
            try bind(id, at: 3, to: statement)
            try stepDone(statement)
            guard sqlite3_changes(database) == 1 else {
                throw DevContainerError(
                    .notFound,
                    message: "runtime container metadata \(id) was not found"
                )
            }
        }
    }

    public func removeContainerMetadata(id: String) throws {
        let sql = """
        DELETE FROM runtime_containers WHERE runtime_id = ? OR docker_id = ?
        """
        try withStatement(sql) { statement in
            try bind(id, at: 1, to: statement)
            try bind(id, at: 2, to: statement)
            try stepDone(statement)
        }
    }
}

extension SQLiteStateStore {
    private static func prepareParentDirectory(for path: URL) throws {
        let directory = path.deletingLastPathComponent()
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        var status = stat()
        guard
            lstat(directory.path, &status) == 0,
            status.st_uid == getuid(),
            status.st_mode & S_IFMT == S_IFDIR,
            status.st_mode & (S_IWGRP | S_IWOTH) == 0
        else {
            throw DevContainerError(
                .invalidRequest,
                message: "state directory must be owned by the current user and not group/world writable"
            )
        }
        guard chmod(directory.path, S_IRWXU) == 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
    }

    private static func migrate(_ database: OpaquePointer) throws {
        try execute(database, sql: "BEGIN IMMEDIATE")
        do {
            try execute(database, sql: schemaSQL)
            let count = try scalarInt64(database, "SELECT COUNT(*) FROM schema_meta")
            if count == 0 {
                let info = BuildInfo.current
                try execute(
                    database,
                    sql: "INSERT INTO schema_meta(version, bridge_version) VALUES (\(schemaVersion), '\(info.version)')"
                )
            } else {
                let version = try scalarInt64(
                    database,
                    "SELECT version FROM schema_meta LIMIT 1"
                )
                guard version <= Int64(schemaVersion) else {
                    throw DevContainerError(
                        .stateCorruption,
                        message: "state schema \(version) is newer than supported schema \(schemaVersion)"
                    )
                }
                try execute(
                    database,
                    sql: "UPDATE schema_meta SET version = \(schemaVersion)"
                )
            }
            try execute(database, sql: "COMMIT")
        } catch {
            try? execute(database, sql: "ROLLBACK")
            throw error
        }
    }

    private static let schemaSQL = """
    CREATE TABLE IF NOT EXISTS schema_meta (
        version INTEGER NOT NULL,
        bridge_version TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS projects (
        project_key TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        compose_project TEXT,
        project_directory TEXT,
        config_hash TEXT,
        desired_generation INTEGER NOT NULL,
        desired_state TEXT NOT NULL,
        reconciliation_state TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS resources (
        runtime_kind TEXT NOT NULL,
        runtime_id TEXT NOT NULL,
        docker_id TEXT NOT NULL UNIQUE,
        project_key TEXT NOT NULL REFERENCES projects(project_key) ON DELETE RESTRICT,
        logical_name TEXT NOT NULL,
        role TEXT NOT NULL,
        provider TEXT NOT NULL,
        spec_hash TEXT NOT NULL,
        generation INTEGER NOT NULL,
        observed_state TEXT NOT NULL,
        labels_hash TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        PRIMARY KEY(runtime_kind, runtime_id),
        UNIQUE(project_key, runtime_kind, logical_name)
    );
    CREATE TABLE IF NOT EXISTS operations (
        operation_id TEXT PRIMARY KEY,
        project_key TEXT NOT NULL REFERENCES projects(project_key) ON DELETE RESTRICT,
        resource_key TEXT,
        request_kind TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        phase TEXT NOT NULL,
        retry_class TEXT NOT NULL,
        error_code TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS events (
        seq INTEGER PRIMARY KEY,
        time REAL NOT NULL,
        resource TEXT NOT NULL,
        resource_type TEXT NOT NULL,
        action TEXT NOT NULL,
        attributes_json BLOB NOT NULL
    );
    CREATE TABLE IF NOT EXISTS runtime_containers (
        runtime_id TEXT PRIMARY KEY,
        docker_id TEXT NOT NULL UNIQUE,
        specification_json BLOB NOT NULL,
        created_at REAL NOT NULL,
        started_at REAL
    );
    CREATE INDEX IF NOT EXISTS resources_project_idx
        ON resources(project_key);
    CREATE INDEX IF NOT EXISTS operations_phase_idx
        ON operations(phase, created_at);
    """

    private func transaction<T>(_ body: () throws -> T) throws -> T {
        try Self.execute(database, sql: "BEGIN IMMEDIATE")
        do {
            let result = try body()
            try Self.execute(database, sql: "COMMIT")
            return result
        } catch {
            try? Self.execute(database, sql: "ROLLBACK")
            throw error
        }
    }

    private func decodeProject(_ statement: OpaquePointer) throws -> ProjectRecord {
        guard
            let provider = BackendProvider(rawValue: text(statement, 1)),
            let desiredState = DesiredProjectState(rawValue: text(statement, 6)),
            let reconciliationState = ReconciliationState(rawValue: text(statement, 7))
        else {
            throw DevContainerError(.stateCorruption, message: "invalid project record")
        }
        return ProjectRecord(
            key: ProjectKey(rawValue: text(statement, 0)),
            provider: provider,
            composeProject: optionalText(statement, 2),
            projectDirectory: optionalText(statement, 3),
            configurationHash: optionalText(statement, 4),
            desiredGeneration: sqlite3_column_int64(statement, 5),
            desiredState: desiredState,
            reconciliationState: reconciliationState,
            createdAt: Date(timeIntervalSinceReferenceDate: sqlite3_column_double(statement, 8)),
            updatedAt: Date(timeIntervalSinceReferenceDate: sqlite3_column_double(statement, 9))
        )
    }

    private func scalarInt64(_ sql: String, text value: String) throws -> Int64 {
        try withStatement(sql) { statement in
            try bind(value, at: 1, to: statement)
            guard sqlite3_step(statement) == SQLITE_ROW else {
                throw DevContainerError(.stateCorruption, message: "SQLite scalar query returned no row")
            }
            return sqlite3_column_int64(statement, 0)
        }
    }

    private static func scalarInt64(_ database: OpaquePointer, _ sql: String) throws -> Int64 {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(database, sql, -1, &statement, nil) == SQLITE_OK, let statement else {
            throw sqliteError(database, prefix: "cannot prepare scalar query")
        }
        defer { sqlite3_finalize(statement) }
        guard sqlite3_step(statement) == SQLITE_ROW else {
            throw sqliteError(database, prefix: "scalar query returned no row")
        }
        return sqlite3_column_int64(statement, 0)
    }

    private func withStatement<T>(
        _ sql: String,
        _ body: (OpaquePointer) throws -> T
    ) throws -> T {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(database, sql, -1, &statement, nil) == SQLITE_OK, let statement else {
            throw Self.sqliteError(database, prefix: "cannot prepare state query")
        }
        defer { sqlite3_finalize(statement) }
        return try body(statement)
    }

    private func bind(_ value: String?, at index: Int32, to statement: OpaquePointer) throws {
        let status: Int32 = if let value {
            sqlite3_bind_text(statement, index, value, -1, Self.transientDestructor)
        } else {
            sqlite3_bind_null(statement, index)
        }
        guard status == SQLITE_OK else {
            throw Self.sqliteError(database, prefix: "cannot bind text")
        }
    }

    private func bind(_ value: Data, at index: Int32, to statement: OpaquePointer) throws {
        let status = value.withUnsafeBytes { bytes in
            sqlite3_bind_blob(statement, index, bytes.baseAddress, Int32(bytes.count), Self.transientDestructor)
        }
        guard status == SQLITE_OK else {
            throw Self.sqliteError(database, prefix: "cannot bind data")
        }
    }

    private func bind(_ value: Int64, at index: Int32, to statement: OpaquePointer) throws {
        guard sqlite3_bind_int64(statement, index, value) == SQLITE_OK else {
            throw Self.sqliteError(database, prefix: "cannot bind integer")
        }
    }

    private func bind(_ value: Double, at index: Int32, to statement: OpaquePointer) throws {
        guard sqlite3_bind_double(statement, index, value) == SQLITE_OK else {
            throw Self.sqliteError(database, prefix: "cannot bind double")
        }
    }

    private func bind(_ value: Double?, at index: Int32, to statement: OpaquePointer) throws {
        if let value {
            try bind(value, at: index, to: statement)
        } else if sqlite3_bind_null(statement, index) != SQLITE_OK {
            throw Self.sqliteError(database, prefix: "cannot bind optional double")
        }
    }

    private func stepDone(_ statement: OpaquePointer) throws {
        guard sqlite3_step(statement) == SQLITE_DONE else {
            throw Self.sqliteError(database, prefix: "state query failed")
        }
    }

    private func text(_ statement: OpaquePointer, _ column: Int32) -> String {
        guard let pointer = sqlite3_column_text(statement, column) else {
            return ""
        }
        return String(cString: pointer)
    }

    private func optionalText(_ statement: OpaquePointer, _ column: Int32) -> String? {
        guard sqlite3_column_type(statement, column) != SQLITE_NULL else {
            return nil
        }
        return text(statement, column)
    }

    private func blob(_ statement: OpaquePointer, _ column: Int32) -> Data? {
        guard
            sqlite3_column_type(statement, column) != SQLITE_NULL,
            let pointer = sqlite3_column_blob(statement, column)
        else {
            return nil
        }
        return Data(bytes: pointer, count: Int(sqlite3_column_bytes(statement, column)))
    }

    private static func execute(_ database: OpaquePointer, sql: String) throws {
        var message: UnsafeMutablePointer<CChar>?
        guard sqlite3_exec(database, sql, nil, nil, &message) == SQLITE_OK else {
            let detail = message.map { String(cString: $0) } ?? String(cString: sqlite3_errmsg(database))
            sqlite3_free(message)
            throw DevContainerError(.stateCorruption, message: "state database error: \(detail)")
        }
    }

    private static func sqliteError(
        _ database: OpaquePointer,
        prefix: String
    ) -> DevContainerError {
        DevContainerError(
            .stateCorruption,
            message: "\(prefix): \(String(cString: sqlite3_errmsg(database)))"
        )
    }

    private static let transientDestructor = unsafeBitCast(
        -1,
        to: sqlite3_destructor_type.self
    )
}

private final class SQLiteHandle: @unchecked Sendable {
    let pointer: OpaquePointer

    init(pointer: OpaquePointer) {
        self.pointer = pointer
    }

    deinit {
        sqlite3_close(pointer)
    }
}
