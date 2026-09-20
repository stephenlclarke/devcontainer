// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
import DevContainerState
import Foundation
import Testing

extension SQLiteOutputJournalTests {
    @Test func `log snapshot does not flush a partial line or gain a later source EOF`() async throws {
        try await withStore { store, snapshot in
            let journal = try await store.beginContainerOutputCapture(snapshot: snapshot)
            try journal.append(frame("line\npartial"))
            let beforeEOF = try journal.captureLogHistory(context: .init())
            try journal.endSource(.standardOutput)
            let afterEOF = try journal.captureLogHistory(context: .init())
            try journal.endSource(.standardError)
            try journal.finish(complete: true)
            #expect(try await collect(beforeEOF) == [frame("line\n")])
            #expect(try await collect(afterEOF) == [frame("line\n"), frame("partial")])
            #expect(try await scalar(store.path, "SELECT ended FROM runtime_output_generations") == 3)
        }
    }

    @Test func `log projection preserves raw evidence and independent restart generations`() async throws {
        try await withStore { store, snapshot in
            let first = try await store.beginContainerOutputCapture(snapshot: snapshot)
            try first.append(.init(channel: .standardOutput, data: Data([0xE2, 0x82])))
            try first.append(frame("err1", channel: .standardError))
            try finish(first)
            let cutoff = try first.captureLogHistory(context: .init())
            let second = try await store.beginContainerOutputCapture(snapshot: snapshot)
            try second.append(.init(channel: .standardOutput, data: Data([0xAC])))
            try second.append(frame("err2", channel: .standardError))
            try finish(second)
            let reopened = try await SQLiteStateStore(path: store.path)
            let replacement = Data([0xEF, 0xBF, 0xBD])
            let expected = [
                RuntimeIOFrame(channel: .standardOutput, data: replacement + replacement),
                frame("err1", channel: .standardError),
                RuntimeIOFrame(channel: .standardOutput, data: replacement),
                frame("err2", channel: .standardError)
            ]
            #expect(try await collect(cutoff) == Array(expected.prefix(2)))
            #expect(try await collect(reopened.containerLogHistory(snapshot: snapshot, context: .init())) == expected)
            let raw = try await collect(reopened.containerOutputHistory(snapshot: snapshot, context: .init()))
            #expect(raw[0].data == Data([0xE2, 0x82]))
            #expect(raw[2].data == Data([0xAC]))
            let generations = try await scalar(
                store.path, "SELECT COUNT(*) FROM runtime_output_generations WHERE ended = sources"
            )
            #expect(generations == 2)
        }
    }

    @Test func `a missing natural EOF fails completion and invalidates captured log readers`() async throws {
        try await withStore { store, snapshot in
            let journal = try await store.beginContainerOutputCapture(snapshot: snapshot)
            try journal.append(frame("valid\n"))
            let reader = try journal.captureLogHistory(context: .init())
            try journal.endSource(.standardOutput)
            #expect(throws: DevContainerError.self) { try journal.finish(complete: true) }
            await #expect(throws: DevContainerError.self) { try await collect(reader) }
            #expect(try await scalar(store.path, "SELECT complete FROM runtime_output_journals") == -1)
        }
    }

    @Test(arguments: ["runtime_output_logs", "runtime_output_generations"])
    func `EOF record and marker failures roll back and invalidate both histories`(_ table: String) async throws {
        try await withStore { store, snapshot in
            let journal = try await store.beginContainerOutputCapture(snapshot: snapshot)
            try journal.append(frame("partial"))
            let raw = try journal.captureHistory(context: .init())
            let logs = try journal.captureLogHistory(context: .init())
            let event = table == "runtime_output_logs" ? "INSERT" : "UPDATE"
            try await execute(store.path, """
            CREATE TRIGGER reject_log BEFORE \(event) ON \(table)
            BEGIN SELECT RAISE(ABORT, 'injected log failure'); END;
            """)
            #expect(throws: DevContainerError.self) { try journal.endSource(.standardOutput) }
            await #expect(throws: DevContainerError.self) { try await collect(raw) }
            await #expect(throws: DevContainerError.self) { try await collect(logs) }
            #expect(try await scalar(store.path, "SELECT log_sequence FROM runtime_output_journals") == 0)
            #expect(try await scalar(store.path, "SELECT ended FROM runtime_output_generations") == 0)
        }
    }

    @Test(arguments: ["log_bytes = 1073741824", "log_sequence = 1048576"])
    func `derived storage bound rolls back raw write as well`(_ budget: String) async throws {
        try await withStore { store, snapshot in
            let journal = try await store.beginContainerOutputCapture(snapshot: snapshot)
            try await execute(store.path, "UPDATE runtime_output_journals SET \(budget)")
            #expect(throws: DevContainerError.self) { try journal.append(frame("line\n")) }
            #expect(try await scalar(store.path, "SELECT COUNT(*) FROM runtime_output_frames") == 0)
            #expect(try await scalar(store.path, "SELECT complete FROM runtime_output_journals") == -1)
        }
    }

    @Test func `TTY requires stdout EOF only and cannot capture a separate stderr`() async throws {
        try await withStore { store, snapshot in
            var tty = snapshot
            tty.spec.terminal = true
            try await record(tty, in: store)
            let journal = try await store.beginContainerOutputCapture(snapshot: tty)
            try journal.append(frame("terminal\r\n"))
            try journal.endSource(.standardOutput)
            try journal.finish(complete: true)
            let history = try await collect(store.containerLogHistory(snapshot: tty, context: .init()))
            #expect(history == [frame("terminal\r\n")])
            let next = try await store.beginContainerOutputCapture(snapshot: tty)
            #expect(throws: DevContainerError.self) { try next.append(frame("invalid", channel: .standardError)) }
        }
    }

    @Test func `legacy version five raw history remains readable without invented log projection`() async throws {
        try await withStore { store, snapshot in
            let journal = try await store.beginContainerOutputCapture(snapshot: snapshot)
            try journal.append(frame("old raw"))
            try finish(journal)
            try await execute(store.path, """
            DROP TABLE runtime_output_logs;
            DROP TABLE runtime_output_generations;
            ALTER TABLE runtime_output_journals DROP COLUMN log_version;
            ALTER TABLE runtime_output_journals DROP COLUMN log_sequence;
            ALTER TABLE runtime_output_journals DROP COLUMN log_bytes;
            ALTER TABLE runtime_output_journals DROP COLUMN generation_count;
            UPDATE schema_meta SET version = 5;
            """)
            let reopened = try await SQLiteStateStore(path: store.path)
            let raw = try await collect(reopened.containerOutputHistory(snapshot: snapshot, context: .init()))
            #expect(raw == [frame("old raw")])
            await #expect(throws: DevContainerError.self) {
                try await reopened.containerLogHistory(snapshot: snapshot, context: .init())
            }
            await #expect(throws: DevContainerError.self) {
                try await reopened.beginContainerOutputCapture(snapshot: snapshot)
            }
            #expect(try await scalar(store.path, "SELECT log_version FROM runtime_output_journals") == 0)
        }
    }
}
