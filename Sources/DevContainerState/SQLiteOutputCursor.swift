// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import CSQLite
import DevContainerModel
import Foundation

/// Replay is demand-driven. Each next() holds a read transaction for at most
/// one bounded frame, so an idle client cannot pin the WAL or block removal.
final class SQLiteOutputCursor: @unchecked Sendable {
    private let connection: SQLiteOutputConnection
    private let snapshot: ContainerSnapshot
    private let cutoff: Int64
    private let context: RuntimeRequestContext
    private let captureStatus: SQLiteOutputCaptureStatus?
    private var nextSequence: Int64 = 1

    private init(
        path: URL, snapshot: ContainerSnapshot, generation: String?, context: RuntimeRequestContext,
        captureStatus: SQLiteOutputCaptureStatus?
    ) throws {
        var boundedContext = context
        boundedContext.deadline = min(context.deadline ?? .distantFuture, Date().addingTimeInterval(300))
        try boundedContext.checkActive()
        connection = try SQLiteOutputConnection(path: path)
        self.snapshot = snapshot
        self.context = boundedContext
        self.captureStatus = captureStatus
        cutoff = try Self.boundary(connection, snapshot: snapshot, generation: generation)
    }

    static func history(
        path: URL, snapshot: ContainerSnapshot, generation: String?, context: RuntimeRequestContext,
        captureStatus: SQLiteOutputCaptureStatus? = nil
    ) throws -> AsyncThrowingStream<RuntimeIOFrame, any Error> {
        let cursor = try SQLiteOutputCursor(
            path: path, snapshot: snapshot, generation: generation, context: context, captureStatus: captureStatus
        )
        return AsyncThrowingStream(unfolding: { try cursor.next() })
    }

    private func next() throws -> RuntimeIOFrame? {
        try connection.synchronized {
            try context.checkActive()
            try captureStatus?.check()
            let frame = try connection.transaction(writing: false) {
                // Validate even at EOF: removal or a failed capture is not a
                // successfully exhausted historical stream.
                let current = try Self.boundary(connection, snapshot: snapshot, allowActive: true)
                guard current >= cutoff else { throw Self.failure("Output history was truncated") }
                guard nextSequence <= cutoff else { return nil as RuntimeIOFrame? }
                let frame = try readFrame()
                nextSequence += 1
                return frame
            }
            try captureStatus?.check()
            return frame
        }
    }

    private func readFrame() throws -> RuntimeIOFrame {
        try connection.statement("""
        SELECT channel, payload FROM runtime_output_frames WHERE docker_id = ? AND sequence = ?
        """) { statement in
            try connection.bind(snapshot.dockerID.rawValue, to: statement, at: 1)
            try connection.bind(nextSequence, to: statement, at: 2)
            guard sqlite3_step(statement) == SQLITE_ROW,
                  let channel = RuntimeIOChannel(rawValue: UInt8(clamping: sqlite3_column_int(statement, 0))),
                  channel != .standardInput,
                  sqlite3_column_type(statement, 1) == SQLITE_BLOB
            else { throw Self.failure("Output history contains a missing or invalid record") }
            let length = Int(sqlite3_column_bytes(statement, 1))
            guard length > 0, length <= 65536, let bytes = sqlite3_column_blob(statement, 1) else {
                throw Self.failure("Output history contains an invalid record length")
            }
            return RuntimeIOFrame(channel: channel, data: Data(bytes: bytes, count: length))
        }
    }

    static func requireIdentity(_ connection: SQLiteOutputConnection, snapshot: ContainerSnapshot) throws {
        try connection.statement("""
        SELECT runtime_id, created_at FROM runtime_containers WHERE docker_id = ?
        """) { statement in
            try connection.bind(snapshot.dockerID.rawValue, to: statement, at: 1)
            guard sqlite3_step(statement) == SQLITE_ROW,
                  text(statement, 0) == snapshot.runtimeID.rawValue,
                  sqlite3_column_double(statement, 1) == snapshot.createdAt.timeIntervalSinceReferenceDate
            else { throw Self.failure("Output container identity is no longer present") }
        }
    }

    static func boundary(
        _ connection: SQLiteOutputConnection, snapshot: ContainerSnapshot,
        generation: String? = nil, allowActive: Bool = false
    ) throws -> Int64 {
        try connection.statement("""
        SELECT c.runtime_id, c.created_at, j.created_at, j.generation, j.complete, j.last_sequence, j.stored_bytes
        FROM runtime_output_journals j JOIN runtime_containers c ON c.docker_id = j.docker_id
        WHERE j.docker_id = ?
        """) { statement in
            try connection.bind(snapshot.dockerID.rawValue, to: statement, at: 1)
            guard sqlite3_step(statement) == SQLITE_ROW,
                  text(statement, 0) == snapshot.runtimeID.rawValue,
                  sqlite3_column_double(statement, 1) == snapshot.createdAt.timeIntervalSinceReferenceDate,
                  sqlite3_column_double(statement, 2) == snapshot.createdAt.timeIntervalSinceReferenceDate
            else { throw Self.failure("Output history is unavailable for this container identity") }
            let complete = sqlite3_column_int(statement, 4)
            let matchesGeneration = generation.map { text(statement, 3) == $0 } ?? true
            let canReadActive = generation != nil || allowActive
            guard matchesGeneration, complete == 1 || (canReadActive && complete == 0) else {
                throw Self.failure("Output history is incomplete or still owned")
            }
            let sequence = sqlite3_column_int64(statement, 5)
            let bytes = sqlite3_column_int64(statement, 6)
            guard sequence >= 0, sequence <= SQLiteContainerOutputJournal.maximumStoredFrames, bytes >= 0,
                  bytes <= SQLiteContainerOutputJournal.maximumStoredBytes
            else { throw Self.failure("Output history bounds are invalid") }
            return sequence
        }
    }

    private static func text(_ statement: OpaquePointer, _ index: Int32) -> String? {
        sqlite3_column_text(statement, index).map { String(cString: $0) }
    }

    private static func failure(_ message: String) -> DevContainerError {
        SQLiteOutputConnection.failure(message)
    }
}
