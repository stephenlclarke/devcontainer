// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import CSQLite
import Darwin
import DevContainerModel
import Foundation

/// Each journal/cursor owns a separate connection; a writer never enters the
/// state actor or holds a database transaction while waiting on an async client.
final class SQLiteOutputConnection: @unchecked Sendable {
    private let handle: SQLiteOutputHandle
    var database: OpaquePointer {
        handle.pointer
    }

    private let lock = NSLock()
    private static let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

    init(path: URL) throws {
        var info = stat()
        guard lstat(path.path, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
              info.st_uid == getuid(), info.st_nlink == 1, info.st_mode & 0o077 == 0
        else { throw Self.failure("Output database ownership is invalid") }
        var pointer: OpaquePointer?
        let flags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_FULLMUTEX | SQLITE_OPEN_NOFOLLOW
        guard sqlite3_open_v2(path.path, &pointer, flags, nil) == SQLITE_OK, let pointer else {
            if let pointer {
                sqlite3_close(pointer)
            }
            throw Self.failure("Cannot open output database")
        }
        handle = SQLiteOutputHandle(pointer: pointer)
        guard sqlite3_busy_handler(pointer, { context, count in
            guard let context else { return 0 }
            let budget = Unmanaged<SQLiteOutputBusyBudget>.fromOpaque(context).takeUnretainedValue()
            return budget.wait(count: count)
        }, Unmanaged.passUnretained(handle.busyBudget).toOpaque()) == SQLITE_OK else {
            throw Self.failure("Cannot bound output database contention")
        }
        try execute("PRAGMA foreign_keys = ON")
        try execute("PRAGMA synchronous = FULL")
    }

    func synchronized<T>(_ body: () throws -> T) rethrows -> T {
        try lock.withLock(body)
    }

    func transaction<T>(writing: Bool = true, _ body: () throws -> T) throws -> T {
        try execute(writing ? "BEGIN IMMEDIATE" : "BEGIN")
        do {
            let result = try body()
            try execute("COMMIT")
            return result
        } catch {
            try? execute("ROLLBACK")
            throw error
        }
    }

    func execute(_ sql: String) throws {
        guard sqlite3_exec(database, sql, nil, nil, nil) == SQLITE_OK else {
            throw Self.failure("Output database operation failed")
        }
    }

    func statement<T>(_ sql: String, _ body: (OpaquePointer) throws -> T) throws -> T {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(database, sql, -1, &statement, nil) == SQLITE_OK, let statement else {
            throw Self.failure("Cannot prepare output database operation")
        }
        defer { sqlite3_finalize(statement) }
        return try body(statement)
    }

    func bind(_ value: String, to statement: OpaquePointer, at index: Int32) throws {
        guard sqlite3_bind_text(statement, index, value, -1, Self.transient) == SQLITE_OK else {
            throw Self.failure("Cannot bind output identity")
        }
    }

    func bind(_ value: Int64, to statement: OpaquePointer, at index: Int32) throws {
        guard sqlite3_bind_int64(statement, index, value) == SQLITE_OK else {
            throw Self.failure("Cannot bind output sequence")
        }
    }

    func bind(_ value: Data, to statement: OpaquePointer, at index: Int32) throws {
        guard value.count <= 65536 else { throw Self.failure("Output record exceeds 64 KiB") }
        let result = value.withUnsafeBytes {
            sqlite3_bind_blob(statement, index, $0.baseAddress, Int32($0.count), Self.transient)
        }
        guard result == SQLITE_OK else { throw Self.failure("Cannot bind output record") }
    }

    func done(_ statement: OpaquePointer) throws {
        guard sqlite3_step(statement) == SQLITE_DONE else {
            throw Self.failure("Output database mutation failed")
        }
    }

    static func failure(_ message: String) -> DevContainerError {
        DevContainerError(.stateCorruption, message: message)
    }
}

private final class SQLiteOutputHandle {
    let pointer: OpaquePointer
    /// SQLite invokes this only while using this connection. Published journal
    /// and cursor operations already hold SQLiteOutputConnection.lock; setup is
    /// single-owner before the connection escapes its initializer.
    let busyBudget = SQLiteOutputBusyBudget()

    init(pointer: OpaquePointer) {
        self.pointer = pointer
    }

    deinit {
        sqlite3_busy_handler(pointer, nil, nil)
        sqlite3_close(pointer)
    }
}

private final class SQLiteOutputBusyBudget {
    private var deadline: ContinuousClock.Instant?

    func wait(count: Int32) -> Int32 {
        // SQLite starts count at zero for each new busy sequence. Separate
        // statements therefore each retain the original one-second budget.
        if count == 0 {
            deadline = ContinuousClock.now.advanced(by: .seconds(1))
        }
        guard count >= 0, let deadline else { return 0 }
        let remaining = ContinuousClock.now.duration(to: deadline)
        guard remaining > .zero else { return 0 }
        let microseconds = remaining.components.seconds * 1_000_000 +
            remaining.components.attoseconds / 1_000_000_000_000
        _ = usleep(useconds_t(min(10000, max(1, microseconds))))
        // A delayed wake must not grant SQLite another retry past the bound.
        return ContinuousClock.now < deadline ? 1 : 0
    }
}
