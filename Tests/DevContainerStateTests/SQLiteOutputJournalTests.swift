// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import CSQLite
import DevContainerModel
import DevContainerRuntimeSPI
import DevContainerState
import Foundation
import Testing

@Suite(.serialized)
struct SQLiteOutputJournalTests {
    @Test func `history preserves binary bytes and source across database reopen`() async throws {
        try await withStore { store, snapshot in
            let frames = [
                RuntimeIOFrame(channel: .standardOutput, data: Data([0, 255, 13, 10])),
                RuntimeIOFrame(channel: .standardError, data: Data("stderr".utf8)),
                RuntimeIOFrame(channel: .standardOutput, data: Data(repeating: 42, count: 65536))
            ]
            let journal = try await store.beginContainerOutputCapture(snapshot: snapshot)
            for frame in frames {
                try journal.append(frame)
            }
            try journal.finish(complete: true)
            let reopened = try await SQLiteStateStore(path: store.path)
            let history = try await reopened.containerOutputHistory(snapshot: snapshot, context: .init())
            #expect(try await collect(history) == frames)
        }
    }

    @Test func `captured cutoff excludes later writes while new generation retains history`() async throws {
        try await withStore { store, snapshot in
            let first = try await store.beginContainerOutputCapture(snapshot: snapshot)
            try first.append(frame("first"))
            let prefix = try first.captureHistory(context: .init())
            try first.append(frame("second", channel: .standardError))
            try first.finish(complete: true)
            let next = try await store.beginContainerOutputCapture(snapshot: snapshot)
            try next.append(frame("third"))
            #expect(try await collect(prefix) == [frame("first")])
            try next.finish(complete: true)
            let history = try await store.containerOutputHistory(snapshot: snapshot, context: .init())
            #expect(try await collect(history) == [
                frame("first"),
                frame("second", channel: .standardError),
                frame("third")
            ])
        }
    }

    @Test func `active incomplete and abandoned captures never reopen as complete`() async throws {
        try await withStore { store, snapshot in
            var journal: (any RuntimeContainerOutputJournal)? = try await store
                .beginContainerOutputCapture(snapshot: snapshot)
            try journal?.append(frame("captured"))
            await #expect(throws: DevContainerError.self) {
                try await store.containerOutputHistory(snapshot: snapshot, context: .init())
            }
            await #expect(throws: DevContainerError.self) {
                try await store.beginContainerOutputCapture(snapshot: snapshot)
            }
            journal = nil
            let reopened = try await SQLiteStateStore(path: store.path)
            await #expect(throws: DevContainerError.self) {
                try await reopened.containerOutputHistory(snapshot: snapshot, context: .init())
            }
            await #expect(throws: DevContainerError.self) {
                try await reopened.beginContainerOutputCapture(snapshot: snapshot)
            }
        }
    }

    @Test func `removal cascades stored output and invalidates a captured cursor`() async throws {
        try await withStore { store, snapshot in
            let journal = try await store.beginContainerOutputCapture(snapshot: snapshot)
            try journal.append(frame("first"))
            try journal.append(frame("second"))
            try journal.finish(complete: true)
            var cursor = try await store.containerOutputHistory(snapshot: snapshot, context: .init())
                .makeAsyncIterator()
            #expect(try await cursor.next() == frame("first"))
            try await store.removeContainerMetadata(id: snapshot.dockerID.rawValue)
            await #expect(throws: DevContainerError.self) { try await cursor.next() }
            let count = try await scalar(store.path, "SELECT COUNT(*) FROM runtime_output_frames")
            #expect(count == 0)
        }
    }

    @Test func `reused runtime name has no access to old output`() async throws {
        try await withStore { store, snapshot in
            let journal = try await store.beginContainerOutputCapture(snapshot: snapshot)
            try journal.append(frame("private old bytes"))
            try journal.finish(complete: true)
            try await store.removeContainerMetadata(id: snapshot.dockerID.rawValue)
            var replacement = snapshot
            replacement.dockerID = DockerID(rawValue: String(repeating: "b", count: 64))
            replacement.createdAt.addTimeInterval(1)
            try await record(replacement, in: store)
            await #expect(throws: DevContainerError.self) {
                try await store.containerOutputHistory(snapshot: replacement, context: .init())
            }
            await #expect(throws: DevContainerError.self) {
                try await store.beginContainerOutputCapture(snapshot: snapshot)
            }
            let next = try await store.beginContainerOutputCapture(snapshot: replacement)
            try next.append(frame("new bytes"))
            try next.finish(complete: true)
            #expect(try await collect(store.containerOutputHistory(snapshot: replacement, context: .init())) ==
                [frame("new bytes")])
        }
    }

    @Test func `forged creation or native identity is rejected`() async throws {
        try await withStore { store, snapshot in
            let journal = try await store.beginContainerOutputCapture(snapshot: snapshot)
            try journal.finish(complete: true)
            var changed = snapshot
            changed.createdAt.addTimeInterval(0.001)
            await #expect(throws: DevContainerError.self) {
                try await store.beginContainerOutputCapture(snapshot: changed)
            }
            changed = snapshot
            changed.runtimeID = RuntimeID(rawValue: "another-native-name")
            await #expect(throws: DevContainerError.self) {
                try await store.containerOutputHistory(snapshot: changed, context: .init())
            }
        }
    }

    @Test(arguments: [RuntimeIOChannel.standardInput, .standardOutput])
    func `invalid source or oversized frame makes incompleteness durable`(channel: RuntimeIOChannel) async throws {
        try await withStore { store, snapshot in
            let journal = try await store.beginContainerOutputCapture(snapshot: snapshot)
            let length = channel == .standardInput ? 1 : 65537
            #expect(throws: DevContainerError.self) {
                try journal.append(RuntimeIOFrame(channel: channel, data: Data(repeating: 1, count: length)))
            }
            #expect(throws: DevContainerError.self) { try journal.captureHistory(context: .init()) }
            try journal.finish(complete: true)
            await #expect(throws: DevContainerError.self) {
                try await store.containerOutputHistory(snapshot: snapshot, context: .init())
            }
        }
    }

    @Test func `write failure rolls back sequence and cannot report successful history`() async throws {
        try await withStore { store, snapshot in
            let journal = try await store.beginContainerOutputCapture(snapshot: snapshot)
            try journal.append(frame("valid"))
            let beforeFailure = try journal.captureHistory(context: .init())
            try await execute(store.path, """
            CREATE TRIGGER reject_output BEFORE INSERT ON runtime_output_frames
            BEGIN SELECT RAISE(ABORT, 'injected full disk'); END;
            """)
            #expect(throws: DevContainerError.self) { try journal.append(frame("not committed")) }
            await #expect(throws: DevContainerError.self) { try await collect(beforeFailure) }
            #expect(try await scalar(store.path, "SELECT last_sequence FROM runtime_output_journals") == 1)
            #expect(try await scalar(store.path, "SELECT stored_bytes FROM runtime_output_journals") == 5)
            try journal.finish(complete: true)
            #expect(try await scalar(store.path, "SELECT complete FROM runtime_output_journals") == -1)
        }
    }

    @Test func `database lock bounds append and invalidates an existing reader even when failure cannot persist`(
    ) async throws {
        try await withStore { store, snapshot in
            let journal = try await store.beginContainerOutputCapture(snapshot: snapshot)
            try journal.append(frame("prefix"))
            let beforeFailure = try journal.captureHistory(context: .init())
            let blocker = try await open(store.path)
            defer { sqlite3_close(blocker) }
            try #require(sqlite3_exec(blocker, "BEGIN IMMEDIATE", nil, nil, nil) == SQLITE_OK)
            defer { sqlite3_exec(blocker, "ROLLBACK", nil, nil, nil) }
            let start = ContinuousClock.now
            #expect(throws: DevContainerError.self) { try journal.append(frame("blocked")) }
            #expect(start.duration(to: .now) < .seconds(4))
            await #expect(throws: DevContainerError.self) { try await collect(beforeFailure) }
        }
    }

    @Test func `missing record fails replay instead of returning silent EOF`() async throws {
        try await withStore { store, snapshot in
            let journal = try await store.beginContainerOutputCapture(snapshot: snapshot)
            try journal.append(frame("first"))
            try journal.append(frame("second"))
            try journal.finish(complete: true)
            try await execute(store.path, "DELETE FROM runtime_output_frames WHERE sequence = 1")
            let history = try await store.containerOutputHistory(snapshot: snapshot, context: .init())
            await #expect(throws: DevContainerError.self) { try await collect(history) }
        }
    }

    @Test func `completion publication failure invalidates already captured history`() async throws {
        try await withStore { store, snapshot in
            let journal = try await store.beginContainerOutputCapture(snapshot: snapshot)
            try journal.append(frame("prefix"))
            let beforeFailure = try journal.captureHistory(context: .init())
            try await execute(store.path, """
            CREATE TRIGGER reject_completion BEFORE UPDATE OF complete ON runtime_output_journals
            BEGIN SELECT RAISE(ABORT, 'injected completion failure'); END;
            """)
            #expect(throws: DevContainerError.self) { try journal.finish(complete: true) }
            await #expect(throws: DevContainerError.self) { try await collect(beforeFailure) }
            #expect(try await scalar(store.path, "SELECT complete FROM runtime_output_journals") == 0)
        }
    }

    @Test(arguments: ["stored_bytes = 1073741824", "last_sequence = 1048576"])
    func `payload and row budgets fail explicitly without inserting or truncating`(budget: String) async throws {
        try await withStore { store, snapshot in
            let journal = try await store.beginContainerOutputCapture(snapshot: snapshot)
            try journal.append(frame("kept"))
            try await execute(store.path, "UPDATE runtime_output_journals SET \(budget)")
            #expect(throws: DevContainerError.self) { try journal.append(frame("x")) }
            #expect(try await scalar(store.path, "SELECT COUNT(*) FROM runtime_output_frames") == 1)
            #expect(try await scalar(store.path, "SELECT complete FROM runtime_output_journals") == -1)
        }
    }

    @Test func `expired reader does not close writer or another reader`() async throws {
        try await withStore { store, snapshot in
            let journal = try await store.beginContainerOutputCapture(snapshot: snapshot)
            #expect(throws: DevContainerError.self) {
                try journal.captureHistory(context: .init(deadline: .distantPast))
            }
            try journal.append(frame("unaffected"))
            #expect(try await collect(journal.captureHistory(context: .init())) == [frame("unaffected")])
            try journal.finish(complete: true)
            try journal.finish(complete: true)
            #expect(throws: DevContainerError.self) { try journal.append(frame("closed")) }
        }
    }

    @Test func `empty capture is complete and version four migrates without inventing history`() async throws {
        try await withStore { store, snapshot in
            try await execute(store.path, """
            DROP TABLE runtime_output_frames;
            DROP TABLE runtime_output_journals;
            UPDATE schema_meta SET version = 4;
            """)
            let reopened = try await SQLiteStateStore(path: store.path)
            await #expect(throws: DevContainerError.self) {
                try await reopened.containerOutputHistory(snapshot: snapshot, context: .init())
            }
            #expect(try await scalar(store.path, "SELECT version FROM schema_meta") == 5)
            let journal = try await reopened.beginContainerOutputCapture(snapshot: snapshot)
            try journal.append(RuntimeIOFrame(channel: .standardOutput, data: Data()))
            try journal.finish(complete: true)
            #expect(try await collect(reopened.containerOutputHistory(snapshot: snapshot, context: .init())).isEmpty)
        }
    }

    private func frame(_ text: String, channel: RuntimeIOChannel = .standardOutput) -> RuntimeIOFrame {
        RuntimeIOFrame(channel: channel, data: Data(text.utf8))
    }

    private func collect(_ frames: AsyncThrowingStream<RuntimeIOFrame, any Error>) async throws -> [RuntimeIOFrame] {
        var result: [RuntimeIOFrame] = []
        for try await frame in frames {
            result.append(frame)
        }
        return result
    }

    private func withStore(_ body: (SQLiteStateStore, ContainerSnapshot) async throws -> Void) async throws {
        let environment = ProcessInfo.processInfo.environment
        let root = environment["TEST_TMPDIR"] ?? environment["TMPDIR"] ?? FileManager.default.temporaryDirectory.path
        if environment["BAZEL_TEST"] == "1" {
            try #require(root.hasPrefix("/Volumes/SSD/cf/bazel/"))
        }
        let directory = URL(fileURLWithPath: root).appendingPathComponent("output-journal-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = try SQLiteStateStore(path: directory.appendingPathComponent("state.sqlite"))
        let snapshot = ContainerSnapshot(
            runtimeID: RuntimeID(rawValue: "native-name"), dockerID: DockerID(rawValue: String(
                repeating: "a",
                count: 64
            )),
            spec: ContainerSpec(name: "native-name", image: "fixture"), state: .created,
            createdAt: Date(timeIntervalSinceReferenceDate: 1000)
        )
        try await record(snapshot, in: store)
        try await body(store, snapshot)
    }

    private func record(_ snapshot: ContainerSnapshot, in store: SQLiteStateStore) async throws {
        try await store.recordContainerMetadata(RuntimeContainerMetadata(
            runtimeID: snapshot.runtimeID, dockerID: snapshot.dockerID, spec: snapshot.spec,
            createdAt: snapshot.createdAt
        ))
    }

    private func execute(_ path: URL, _ sql: String) throws {
        let database = try open(path)
        defer { sqlite3_close(database) }
        try #require(sqlite3_exec(database, sql, nil, nil, nil) == SQLITE_OK)
    }

    private func scalar(_ path: URL, _ sql: String) throws -> Int64 {
        let database = try open(path)
        defer { sqlite3_close(database) }
        var statement: OpaquePointer?
        try #require(sqlite3_prepare_v2(database, sql, -1, &statement, nil) == SQLITE_OK)
        let prepared = try #require(statement)
        defer { sqlite3_finalize(prepared) }
        try #require(sqlite3_step(prepared) == SQLITE_ROW)
        return sqlite3_column_int64(prepared, 0)
    }

    private func open(_ path: URL) throws -> OpaquePointer {
        var database: OpaquePointer?
        try #require(sqlite3_open(path.path, &database) == SQLITE_OK)
        return try #require(database)
    }
}
