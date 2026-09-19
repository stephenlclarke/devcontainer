// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Darwin
@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerTestStorage
import Foundation
import Testing

struct ManagedNetworkHostsStoreTests {
    private func identity(_ operationID: UUID = UUID(), name: String = "service") -> ManagedNetworkHostsStore.Identity {
        .init(operationID: operationID, runtimeID: name, createdAt: Date(timeIntervalSince1970: 123))
    }

    private func withStore(_ body: (ManagedNetworkHostsStore) throws -> Void) throws {
        let root = TestStorage.temporaryDirectory.appendingPathComponent("network-hosts-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        try body(ManagedNetworkHostsStore(root: root))
    }

    @Test func `updates retain inode and unmanaged entries across store restart`() throws {
        try withStore { store in
            let owner = identity()
            let initial = "127.0.0.1 localhost\n# user entry\n"
            try store.create(identity: owner, contents: initial)
            let file = store.fileURL(for: owner)
            var before = stat()
            #expect(lstat(file.path, &before) == 0)
            let restarted = try ManagedNetworkHostsStore(root: store.root)
            try restarted.update(identity: owner) { $0 + "10.0.0.2 database\n" }
            var after = stat()
            #expect(lstat(file.path, &after) == 0)
            #expect(before.st_ino == after.st_ino)
            #expect(after.st_mode & 0o777 == 0o644)
            #expect(try String(contentsOf: file, encoding: .utf8) == initial + "10.0.0.2 database\n")
            #expect(try FileManager.default
                .contentsOfDirectory(atPath: file.deletingLastPathComponent().path) == ["hosts"])
            try restarted.update(identity: owner) { _ in initial }
            #expect(try String(contentsOf: file, encoding: .utf8) == initial)
            try restarted.update(identity: owner) { $0 }
            try restarted.remove(identity: owner)
            #expect(try FileManager.default.contentsOfDirectory(atPath: store.root.path).isEmpty)
        }
    }

    @Test func `allocation cannot overwrite an earlier operation`() throws {
        try withStore { store in
            let owner = identity()
            try store.create(identity: owner, contents: "original")
            #expect(throws: POSIXError.self) { try store.create(identity: owner, contents: "replacement") }
            try store.recoverAllocation(identity: owner)
            #expect(try String(contentsOf: store.fileURL(for: owner), encoding: .utf8) == "original")
        }
    }

    @Test func `mismatched creation identity cannot update or remove the backing file`() throws {
        try withStore { store in
            let owner = identity()
            try store.create(identity: owner, contents: "original")
            let wrong = identity(owner.operationID, name: "replacement")
            #expect(throws: DevContainerError.self) { try store.update(identity: wrong) { _ in "changed" } }
            #expect(throws: DevContainerError.self) { try store.remove(identity: wrong) }
            #expect(try String(contentsOf: store.fileURL(for: owner), encoding: .utf8) == "original")
        }
    }

    @Test(arguments: [false, true])
    func `symlink and hardlink substitutions never write an unrelated file`(hardlink: Bool) throws {
        try withStore { store in
            let owner = identity()
            try store.create(identity: owner, contents: "original")
            let file = store.fileURL(for: owner)
            let unrelated = store.root.appendingPathComponent("unrelated")
            try Data("untouched".utf8).write(to: unrelated)
            try FileManager.default.removeItem(at: file)
            if hardlink {
                try FileManager.default.linkItem(at: unrelated, to: file)
            } else {
                try FileManager.default.createSymbolicLink(at: file, withDestinationURL: unrelated)
            }
            #expect(throws: (any Error).self) { try store.update(identity: owner) { _ in "changed" } }
            #expect(throws: (any Error).self) { try store.remove(identity: owner) }
            #expect(try String(contentsOf: unrelated, encoding: .utf8) == "untouched")
        }
    }

    @Test func `unsafe directory permissions and invalid encoding fail closed`() throws {
        try withStore { store in
            let owner = identity()
            try store.create(identity: owner, contents: "original")
            let file = store.fileURL(for: owner)
            let share = file.deletingLastPathComponent()
            #expect(chmod(share.path, 0o777) == 0)
            #expect(throws: DevContainerError.self) { try store.update(identity: owner) { _ in "changed" } }
            #expect(chmod(share.path, 0o700) == 0)
            try Data([0xFF]).write(to: file)
            #expect(throws: DevContainerError.self) { try store.update(identity: owner) { _ in "changed" } }
        }
    }

    @Test func `oversized updates and transform failures preserve existing bytes`() throws {
        enum Injected: Error { case failure }
        try withStore { store in
            let owner = identity()
            try store.create(identity: owner, contents: "original")
            #expect(throws: DevContainerError.self) {
                try store.update(identity: owner) { _ in String(repeating: "x", count: 1024 * 1024 + 1) }
            }
            #expect(throws: Injected.failure) { try store.update(identity: owner) { _ in throw Injected.failure } }
            #expect(try String(contentsOf: store.fileURL(for: owner), encoding: .utf8) == "original")
        }
    }

    @Test func `replaced regular files and unexpected children are retained`() throws {
        try withStore { store in
            let owner = identity()
            try store.create(identity: owner, contents: "original")
            let file = store.fileURL(for: owner)
            let extra = file.deletingLastPathComponent().appendingPathComponent("unrelated")
            try Data("untouched".utf8).write(to: extra)
            #expect(throws: DevContainerError.self) { try store.remove(identity: owner) }
            #expect(try String(contentsOf: extra, encoding: .utf8) == "untouched")
            try FileManager.default.removeItem(at: extra)
            let replacement = file.deletingLastPathComponent().appendingPathComponent("replacement")
            try Data("replacement".utf8).write(to: replacement)
            #expect(rename(replacement.path, file.path) == 0)
            #expect(throws: DevContainerError.self) { try store.update(identity: owner) { _ in "changed" } }
            #expect(throws: DevContainerError.self) { try store.remove(identity: owner) }
            #expect(try String(contentsOf: file, encoding: .utf8) == "replacement")
        }
    }

    @Test(arguments: [0, 1, 2, 3])
    func `journal owned unpublished allocations recover at every construction stage`(stage: Int) throws {
        try withStore { store in
            let owner = identity()
            let slot = store.root.appendingPathComponent(owner.operationID.uuidString + ".alloc")
            try FileManager.default.createDirectory(
                at: slot,
                withIntermediateDirectories: false,
                attributes: [.posixPermissions: 0o700]
            )
            let share = slot.appendingPathComponent("share")
            if stage >= 1 {
                try FileManager.default.createDirectory(
                    at: share,
                    withIntermediateDirectories: false,
                    attributes: [.posixPermissions: 0o700]
                )
            }
            if stage >= 2 {
                try Data("partial".utf8).write(to: share.appendingPathComponent("hosts"))
            }
            if stage >= 3 {
                try Data("partial marker".utf8).write(to: slot.appendingPathComponent("owner.json"))
            }
            try store.recoverAllocation(identity: owner)
            #expect(try FileManager.default.contentsOfDirectory(atPath: store.root.path).isEmpty)
            try store.create(identity: owner, contents: "recovered")
            #expect(try String(contentsOf: store.fileURL(for: owner), encoding: .utf8) == "recovered")
        }
    }

    @Test func `oversized initial allocation leaves no directory`() throws {
        try withStore { store in
            #expect(throws: DevContainerError.self) {
                try store.create(identity: identity(), contents: String(repeating: "x", count: 1024 * 1024 + 1))
            }
            let contents = try FileManager.default.contentsOfDirectory(atPath: store.root.path)
            #expect(contents.isEmpty)
        }
    }

    @Test func `complete unpublished allocation authenticates identity and inodes`() throws {
        try withStore { store in
            let owner = identity()
            try store.create(identity: owner, contents: "live")
            #expect(throws: POSIXError.self) { try store.create(identity: owner, contents: "unpublished") }
            let wrong = identity(owner.operationID, name: "wrong")
            #expect(throws: DevContainerError.self) { try store.recoverAllocation(identity: wrong) }
            let share = store.root.appendingPathComponent(owner.operationID.uuidString + ".alloc/share")
            let hosts = share.appendingPathComponent("hosts")
            let replacement = share.appendingPathComponent("replacement")
            try Data("retain".utf8).write(to: replacement)
            #expect(rename(replacement.path, hosts.path) == 0)
            #expect(throws: DevContainerError.self) { try store.recoverAllocation(identity: owner) }
            #expect(try String(contentsOf: hosts, encoding: .utf8) == "retain")
            #expect(try String(contentsOf: store.fileURL(for: owner), encoding: .utf8) == "live")
        }
    }

    @Test(arguments: [0, 1, 2, 3])
    func `retired allocation cleanup resumes without touching a replacement`(stage: Int) throws {
        try withStore { store in
            let owner = identity()
            try store.create(identity: owner, contents: "original")
            let slot = store.root.appendingPathComponent(owner.operationID.uuidString)
            let retired = store.root.appendingPathComponent(owner.operationID.uuidString + ".retired")
            try FileManager.default.moveItem(at: slot, to: retired)
            let wrong = identity(owner.operationID, name: "wrong")
            #expect(throws: DevContainerError.self) { try store.recoverRemoval(identity: wrong) }
            if stage >= 1 {
                try FileManager.default.removeItem(at: retired.appendingPathComponent("share/hosts"))
            }
            if stage >= 2 {
                try FileManager.default.removeItem(at: retired.appendingPathComponent("share"))
            }
            if stage >= 3 {
                try FileManager.default.removeItem(at: retired.appendingPathComponent("owner.json"))
            }
            // A separate creation's UUID is never in the retired cleanup scope.
            let replacement = identity()
            try store.create(identity: replacement, contents: "replacement")
            try store.recoverRemoval(identity: owner)
            try store.recoverRemoval(identity: owner)
            #expect(try String(contentsOf: store.fileURL(for: replacement), encoding: .utf8) == "replacement")
        }
    }
}
