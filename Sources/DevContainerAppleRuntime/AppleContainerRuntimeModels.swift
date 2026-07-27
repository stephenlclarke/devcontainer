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

import Darwin
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

struct AppleEventPollRequest: Sendable {
    let initial: [String: ContainerSnapshot]
    let since: Date?
    let until: Date?
    let labels: [String: String]
    let context: RuntimeRequestContext
}

struct AppleContainerRecord {
    let id: String
    let dockerID: String
    let spec: ContainerSpec
    let state: String
    let createdAt: Date
    let startedAt: Date?
    let finishedAt: Date?
    let exitCode: Int32?
    let networkAddresses: [String: String]
}

struct AppleVersionRecord: Decodable {
    let appName: String
    let version: String
    let commit: String?
    let distribution: String?
}

final class TemporaryDirectory {
    let url: URL

    init(base: URL = FileManager.default.temporaryDirectory) throws {
        try FileManager.default.createDirectory(
            at: base,
            withIntermediateDirectories: true,
            attributes: nil
        )
        let root = base
            .appendingPathComponent("devcontainer-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700]
        )
        guard chmod(root.path, S_IRWXU) == 0 else {
            try? FileManager.default.removeItem(at: root)
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        var status = Darwin.stat()
        guard
            lstat(root.path, &status) == 0,
            status.st_uid == getuid(),
            status.st_mode & S_IFMT == S_IFDIR,
            status.st_mode & (S_IRWXG | S_IRWXO) == 0
        else {
            try? FileManager.default.removeItem(at: root)
            throw DevContainerError(
                .invalidRequest,
                message: "temporary directory must be private and owned by the current user"
            )
        }
        url = root
    }

    func remove() {
        try? FileManager.default.removeItem(at: url)
    }

    deinit {
        remove()
    }
}
