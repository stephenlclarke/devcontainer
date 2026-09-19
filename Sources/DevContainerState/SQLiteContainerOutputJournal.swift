// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import CSQLite
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

extension SQLiteStateStore: RuntimeContainerOutputStore {
    public func beginContainerOutputCapture(snapshot: ContainerSnapshot) throws -> any RuntimeContainerOutputJournal {
        try SQLiteContainerOutputJournal(path: path, snapshot: snapshot)
    }

    public func containerOutputHistory(
        snapshot: ContainerSnapshot, context: RuntimeRequestContext
    ) throws -> AsyncThrowingStream<RuntimeIOFrame, any Error> {
        try SQLiteOutputCursor.history(path: path, snapshot: snapshot, generation: nil, context: context)
    }
}

/// A retained journal never discards bytes to stay below its storage bound. A
/// full/failed/interrupted capture becomes an explicit incomplete-history error.
final class SQLiteContainerOutputJournal: RuntimeContainerOutputJournal, @unchecked Sendable {
    static let maximumStoredBytes: Int64 = 1024 * 1024 * 1024
    // Bound index/row overhead independently of the payload-byte budget.
    static let maximumStoredFrames: Int64 = 1024 * 1024
    let connection: SQLiteOutputConnection
    let snapshot: ContainerSnapshot
    let generation = UUID().uuidString
    let path: URL
    private var finished = false
    private var failed = false
    private let captureStatus = SQLiteOutputCaptureStatus()

    init(path: URL, snapshot: ContainerSnapshot) throws {
        self.path = path
        self.snapshot = snapshot
        connection = try SQLiteOutputConnection(path: path)
        try connection.transaction {
            try SQLiteOutputCursor.requireIdentity(connection, snapshot: snapshot)
            try createIfNeeded()
            try connection.statement("""
            UPDATE runtime_output_journals SET generation = ?, complete = 0
            WHERE docker_id = ? AND complete = 1
            """) { statement in
                try connection.bind(generation, to: statement, at: 1)
                try connection.bind(snapshot.dockerID.rawValue, to: statement, at: 2)
                try connection.done(statement)
                guard sqlite3_changes(connection.database) == 1 else {
                    throw SQLiteOutputConnection.failure("Previous output capture is incomplete or still owned")
                }
            }
        }
    }

    deinit {
        // A crash leaves complete=0 durably. Normal abandoned ownership records
        // failure too; neither path may be reopened as a complete history.
        if !finished {
            try? finish(complete: false)
        }
    }

    func append(_ frame: RuntimeIOFrame) throws {
        try connection.synchronized {
            guard !finished, !failed else { throw SQLiteOutputConnection.failure("Output capture is closed") }
            guard !frame.data.isEmpty else { return }
            do {
                try connection.transaction {
                    try reserve(frame)
                    try connection.statement("""
                    INSERT INTO runtime_output_frames(docker_id, sequence, channel, payload)
                    SELECT docker_id, last_sequence, ?, ? FROM runtime_output_journals
                    WHERE docker_id = ? AND generation = ?
                    """) { statement in
                        try connection.bind(frame.channel == .standardOutput ? 1 : 2, to: statement, at: 1)
                        try connection.bind(frame.data, to: statement, at: 2)
                        try bindIdentity(statement, startingAt: 3)
                        try connection.done(statement)
                    }
                }
            } catch {
                failed = true
                captureStatus.fail()
                // Disk/lock failure can also prevent this durable update. The
                // shared status still invalidates already-open local cursors.
                try? setCompletion(-1)
                throw error
            }
        }
    }

    func captureHistory(context: RuntimeRequestContext) throws -> AsyncThrowingStream<RuntimeIOFrame, any Error> {
        try connection.synchronized {
            guard !failed else { throw SQLiteOutputConnection.failure("Output capture is incomplete") }
            return try SQLiteOutputCursor.history(
                path: path, snapshot: snapshot, generation: generation, context: context, captureStatus: captureStatus
            )
        }
    }

    func finish(complete: Bool) throws {
        try connection.synchronized {
            guard !finished else { return }
            if !complete || failed {
                captureStatus.fail()
            }
            do {
                try setCompletion(complete && !failed ? 1 : -1)
            } catch {
                failed = true
                captureStatus.fail()
                try? setCompletion(-1)
                throw error
            }
            finished = true
        }
    }

    private func setCompletion(_ value: Int64) throws {
        try connection.statement("""
        UPDATE runtime_output_journals SET complete = ? WHERE docker_id = ? AND generation = ?
        """) { statement in
            try connection.bind(value, to: statement, at: 1)
            try bindIdentity(statement, startingAt: 2)
            try connection.done(statement)
            guard sqlite3_changes(connection.database) == 1 else {
                throw SQLiteOutputConnection.failure("Output capture identity changed before completion")
            }
        }
    }

    private func createIfNeeded() throws {
        try connection.statement("""
        INSERT INTO runtime_output_journals(docker_id, created_at, generation, complete, last_sequence, stored_bytes)
        VALUES (?, ?, '', 1, 0, 0) ON CONFLICT(docker_id) DO NOTHING
        """) { statement in
            try connection.bind(snapshot.dockerID.rawValue, to: statement, at: 1)
            guard sqlite3_bind_double(statement, 2, snapshot.createdAt.timeIntervalSinceReferenceDate) == SQLITE_OK
            else {
                throw SQLiteOutputConnection.failure("Cannot bind output creation identity")
            }
            try connection.done(statement)
        }
        _ = try SQLiteOutputCursor.boundary(connection, snapshot: snapshot, generation: nil)
    }

    private func reserve(_ frame: RuntimeIOFrame) throws {
        guard frame.data.count <= 65536, frame.channel != .standardInput else {
            throw SQLiteOutputConnection.failure("Output record has an invalid source or length")
        }
        try SQLiteOutputCursor.requireIdentity(connection, snapshot: snapshot)
        try connection.statement("""
        UPDATE runtime_output_journals SET last_sequence = last_sequence + 1, stored_bytes = stored_bytes + ?
        WHERE docker_id = ? AND generation = ? AND complete = 0
        AND stored_bytes <= ? AND last_sequence < ?
        """) { statement in
            try connection.bind(Int64(frame.data.count), to: statement, at: 1)
            try bindIdentity(statement, startingAt: 2)
            try connection.bind(Self.maximumStoredBytes - Int64(frame.data.count), to: statement, at: 4)
            try connection.bind(Self.maximumStoredFrames, to: statement, at: 5)
            try connection.done(statement)
            guard sqlite3_changes(connection.database) == 1 else {
                throw SQLiteOutputConnection.failure("Output capture changed or its storage bound was exceeded")
            }
        }
    }

    private func bindIdentity(_ statement: OpaquePointer, startingAt index: Int32) throws {
        try connection.bind(snapshot.dockerID.rawValue, to: statement, at: index)
        try connection.bind(generation, to: statement, at: index + 1)
    }
}

/// Failure survives the writer's lifetime without retaining its SQLite handle.
final class SQLiteOutputCaptureStatus: @unchecked Sendable {
    private let lock = NSLock()
    private var failed = false

    func fail() {
        lock.withLock { failed = true }
    }

    func check() throws {
        guard !lock.withLock({ failed }) else {
            throw SQLiteOutputConnection.failure("Output capture is incomplete")
        }
    }
}
