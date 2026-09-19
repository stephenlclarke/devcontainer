// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Darwin
import DevContainerModel
import Foundation

/// Backing files for native single-file mounts. The creation journal remains
/// authoritative; the marker is only an incarnation/provenance check.
struct ManagedNetworkHostsStore: Sendable {
    struct Identity: Codable, Equatable, Sendable {
        let operationID: UUID
        let runtimeID: String
        let createdAt: Date
    }

    private struct Node: Codable, Equatable {
        let device: dev_t
        let inode: ino_t

        init(_ descriptor: Int32) throws {
            var info = stat()
            guard fstat(descriptor, &info) == 0 else { throw failure() }
            device = info.st_dev
            inode = info.st_ino
        }
    }

    private struct Record: Codable {
        let identity: Identity
        let share: Node
        let hosts: Node
    }

    let root: URL

    init(root: URL) throws {
        self.root = root.standardizedFileURL
        try FileManager.default.createDirectory(
            at: self.root, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700]
        )
        let descriptor = try Self.openDirectory(self.root.path)
        defer { close(descriptor) }
    }

    func fileURL(for identity: Identity) -> URL {
        directory(for: identity).appendingPathComponent("share/hosts")
    }

    /// Invoke only after the creation intent is durable, before submitting the
    /// native create. The shared parent contains no metadata or credentials.
    func create(identity: Identity, contents: String) throws {
        let bytes = Data(contents.utf8)
        guard bytes.count <= Self.maximumFileSize else { throw Self.unsafePath() }
        let parent = try Self.openDirectory(root.path)
        defer { close(parent) }
        let name = identity.operationID.uuidString
        let staging = name + ".alloc"
        guard mkdirat(parent, staging, 0o700) == 0 else { throw Self.failure() }
        let slot = try Self.openDirectory(staging, relativeTo: parent)
        defer { close(slot) }
        // Staging is never a native mount source and is recoverable even if
        // interrupted before a complete provenance marker exists.
        guard mkdirat(slot, "share", 0o700) == 0 else { throw Self.failure() }
        let share = try Self.openDirectory("share", relativeTo: slot)
        defer { close(share) }
        let hosts = try Self.createFile("hosts", in: share, bytes: bytes, mode: 0o644)
        let record = try Record(identity: identity, share: Node(share), hosts: hosts)
        _ = try Self.createFile("owner.json", in: slot, bytes: JSONEncoder().encode(record), mode: 0o600)
        guard fsync(share) == 0, fsync(slot) == 0 else { throw Self.failure() }
        guard renameatx_np(parent, staging, parent, name, UInt32(RENAME_EXCL)) == 0 else { throw Self.failure() }
        guard fsync(parent) == 0 else { throw Self.failure() }
    }

    /// The caller supplies the operation identity from the creation journal.
    /// The .alloc directory is never referenced by a native container.
    func recoverAllocation(identity: Identity) throws {
        let parent = try Self.openDirectory(root.path)
        defer { close(parent) }
        try Self.removeMembers(
            identity.operationID.uuidString + ".alloc", parent: parent, expected: identity,
            incompleteMarkerAllowed: true
        )
    }

    /// Updates must preserve the bound inode. Replacing the host path atomically
    /// would leave an already-running guest attached to the previous file.
    func update(identity: Identity, transform: (String) throws -> String) throws {
        try withVerifiedFile(identity: identity) { _, descriptor in
            let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: false)
            let data = try handle.readToEnd() ?? Data()
            guard let current = String(data: data, encoding: .utf8) else { throw Self.unsafePath() }
            let updated = try transform(current)
            guard updated != current else { return }
            let bytes = Data(updated.utf8)
            guard bytes.count <= Self.maximumFileSize else { throw Self.unsafePath() }
            try handle.seek(toOffset: 0)
            try handle.write(contentsOf: bytes)
            try handle.truncate(atOffset: UInt64(bytes.count))
            try handle.synchronize()
        }
    }

    /// Call only after native removal has been proved for this exact incarnation.
    func remove(identity: Identity) throws {
        let name = identity.operationID.uuidString
        try withVerifiedFile(identity: identity) { parent, _ in
            guard renameatx_np(parent, name, parent, name + ".retired", UInt32(RENAME_EXCL)) == 0 else {
                throw Self.failure()
            }
            guard fsync(parent) == 0 else { throw Self.failure() }
        }
        try recoverRemoval(identity: identity)
    }

    /// A retired directory was verified and detached only after removal proof.
    func recoverRemoval(identity: Identity) throws {
        let parent = try Self.openDirectory(root.path)
        defer { close(parent) }
        try Self.removeMembers(identity.operationID.uuidString + ".retired", parent: parent, expected: identity)
    }

    private func directory(for identity: Identity) -> URL {
        root.appendingPathComponent(identity.operationID.uuidString, isDirectory: true)
    }

    private func withVerifiedFile<T>(identity: Identity, body: (Int32, Int32) throws -> T) throws -> T {
        let parent = try Self.openDirectory(root.path)
        defer { close(parent) }
        let slot = try Self.openDirectory(identity.operationID.uuidString, relativeTo: parent)
        defer { close(slot) }
        guard try Self.members(slot) == ["owner.json", "share"] else { throw Self.unsafePath() }
        let marker = try Self.openFile("owner.json", in: slot, flags: O_RDONLY)
        defer { close(marker) }
        let bytes = try FileHandle(fileDescriptor: marker, closeOnDealloc: false).readToEnd() ?? Data()
        let record = try JSONDecoder().decode(Record.self, from: bytes)
        guard record.identity == identity else { throw Self.unsafePath() }
        let share = try Self.openDirectory("share", relativeTo: slot)
        defer { close(share) }
        guard try Self.members(share) == ["hosts"], try Node(share) == record.share else { throw Self.unsafePath() }
        let hosts = try Self.openFile("hosts", in: share, flags: O_RDWR)
        defer { close(hosts) }
        guard try Node(hosts) == record.hosts else { throw Self.unsafePath() }
        return try body(parent, hosts)
    }

    /// Remove only fixed members of an unpublished/retired allocation. Validate
    /// all remaining members before mutation; interrupted deletion is retryable.
    private static func removeMembers(
        _ name: String, parent: Int32, expected: Identity, incompleteMarkerAllowed: Bool = false
    ) throws {
        let slot: Int32
        do {
            slot = try openDirectory(name, relativeTo: parent)
        } catch let error as POSIXError where error.code == .ENOENT {
            return
        }
        defer { close(slot) }
        let children = try members(slot)
        guard children.isSubset(of: ["owner.json", "share"]) else { throw unsafePath() }
        let record = try recoveryRecord(
            slot: slot, children: children, expected: expected, incompleteMarkerAllowed: incompleteMarkerAllowed
        )
        let share = try recoveryShare(slot: slot, children: children, record: record)
        defer {
            if share >= 0 {
                close(share)
            }
        }
        let hasHosts = try recoveryHosts(share: share, record: record)
        if children.contains("owner.json") {
            try close(openFile("owner.json", in: slot, flags: O_RDONLY))
        }
        if hasHosts, unlinkat(share, "hosts", 0) != 0 {
            throw failure()
        }
        if share >= 0, unlinkat(slot, "share", AT_REMOVEDIR) != 0 {
            throw failure()
        }
        if children.contains("owner.json"), unlinkat(slot, "owner.json", 0) != 0 {
            throw failure()
        }
        guard unlinkat(parent, name, AT_REMOVEDIR) == 0, fsync(parent) == 0 else { throw failure() }
    }

    private static func recoveryShare(slot: Int32, children: Set<String>, record: Record?) throws -> Int32 {
        guard children.contains("share") else { return -1 }
        let share = try openDirectory("share", relativeTo: slot)
        do {
            guard try members(share).isSubset(of: ["hosts"]) else { throw unsafePath() }
            if let record, try Node(share) != record.share {
                throw unsafePath()
            }
            return share
        } catch {
            close(share)
            throw error
        }
    }

    private static func recoveryHosts(share: Int32, record: Record?) throws -> Bool {
        guard try share >= 0 && members(share).contains("hosts") else { return false }
        let file = try openFile("hosts", in: share, flags: O_RDONLY)
        defer { close(file) }
        if let record, try Node(file) != record.hosts {
            throw unsafePath()
        }
        return true
    }

    private static func recoveryRecord(
        slot: Int32, children: Set<String>, expected: Identity, incompleteMarkerAllowed: Bool
    ) throws -> Record? {
        guard children.contains("owner.json") else {
            guard incompleteMarkerAllowed || children.isEmpty else { throw unsafePath() }
            return nil
        }
        let marker = try openFile("owner.json", in: slot, flags: O_RDONLY)
        defer { close(marker) }
        let bytes = try FileHandle(fileDescriptor: marker, closeOnDealloc: false).readToEnd() ?? Data()
        let record: Record
        do {
            record = try JSONDecoder().decode(Record.self, from: bytes)
        } catch is DecodingError where incompleteMarkerAllowed {
            return nil
        }
        guard record.identity == expected else { throw unsafePath() }
        return record
    }

    private static func members(_ descriptor: Int32) throws -> Set<String> {
        let copied = dup(descriptor)
        guard copied >= 0 else { throw failure() }
        guard let directory = fdopendir(copied) else { close(copied); throw failure() }
        defer { closedir(directory) }
        rewinddir(directory)
        var names: Set<String> = []
        errno = 0
        while let entry = readdir(directory) {
            var name = entry.pointee.d_name
            let capacity = MemoryLayout.size(ofValue: name)
            let value = withUnsafePointer(to: &name) {
                $0.withMemoryRebound(to: CChar.self, capacity: capacity) { String(cString: $0) }
            }
            if value != ".", value != ".." {
                names.insert(value)
            }
            errno = 0
        }
        guard errno == 0 else { throw failure() }
        return names
    }

    private static let maximumFileSize = 1024 * 1024

    private static func openDirectory(_ path: String, relativeTo parent: Int32 = AT_FDCWD) throws -> Int32 {
        let descriptor = openat(parent, path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard descriptor >= 0 else { throw failure() }
        var info = stat()
        guard fstat(descriptor, &info) == 0, info.st_uid == getuid(), info.st_mode & 0o077 == 0 else {
            close(descriptor)
            throw unsafePath()
        }
        return descriptor
    }

    private static func openFile(_ path: String, in parent: Int32, flags: Int32) throws -> Int32 {
        let descriptor = openat(parent, path, flags | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK)
        guard descriptor >= 0 else { throw failure() }
        var info = stat()
        guard fstat(descriptor, &info) == 0, info.st_uid == getuid(), info.st_mode & S_IFMT == S_IFREG,
              info.st_nlink == 1, info.st_mode & 0o022 == 0,
              info.st_size >= 0, info.st_size <= maximumFileSize
        else {
            close(descriptor)
            throw unsafePath()
        }
        return descriptor
    }

    private static func createFile(_ path: String, in parent: Int32, bytes: Data, mode: mode_t) throws -> Node {
        guard bytes.count <= maximumFileSize else { throw unsafePath() }
        let descriptor = openat(parent, path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o600)
        guard descriptor >= 0 else { throw failure() }
        defer { close(descriptor) }
        guard fchmod(descriptor, mode) == 0 else { throw failure() }
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: false)
        try handle.write(contentsOf: bytes)
        try handle.synchronize()
        return try Node(descriptor)
    }

    private static func unsafePath() -> DevContainerError {
        DevContainerError(.stateCorruption, message: "Managed network hosts ownership or file identity is invalid")
    }

    private static func failure() -> POSIXError {
        POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
    }
}
