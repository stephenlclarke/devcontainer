//===----------------------------------------------------------------------===//
//
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
//
//===----------------------------------------------------------------------===//

import Darwin
import DevContainerModel
import Foundation

/// Implements Docker's shareable `local` volume contract with user-owned host
/// directories. Apple container's native volumes are block devices and cannot
/// be attached to multiple one-VM-per-container guests at the same time.
struct ManagedVolumeStore {
    private struct Metadata: Codable {
        let spec: VolumeSpec
        let createdAt: Date
    }

    let root: URL
    private let setMetadataMode: @Sendable (String, mode_t) -> Int32

    init(
        root: URL,
        setMetadataMode: @escaping @Sendable (String, mode_t) -> Int32 = {
            chmod($0, $1)
        }
    ) throws {
        self.root = root.standardizedFileURL
        self.setMetadataMode = setMetadataMode
        try Self.createPrivateDirectoryIfNeeded(at: self.root)
    }

    func list() throws -> [VolumeSnapshot] {
        try FileManager.default.contentsOfDirectory(
            at: root,
            includingPropertiesForKeys: nil,
            options: [.skipsHiddenFiles]
        )
        .map(\.lastPathComponent)
        .map(inspect)
        .sorted { $0.name < $1.name }
    }

    func inspect(name: String) throws -> VolumeSnapshot {
        let directory = try volumeDirectory(name: name)
        try Self.requireOwnedDirectory(directory, volumeName: name)
        let dataURL = directory.appendingPathComponent("_data", isDirectory: true)
        try Self.requireOwnedDirectory(dataURL, volumeName: name)

        let metadata: Metadata
        do {
            metadata = try JSONDecoder().decode(
                Metadata.self,
                from: Data(
                    contentsOf: directory.appendingPathComponent("metadata.json")
                )
            )
        } catch let error as DevContainerError {
            throw error
        } catch {
            throw DevContainerError(
                .stateCorruption,
                message: "managed volume \(name) metadata is invalid: \(error)"
            )
        }
        guard metadata.spec.name == name, metadata.spec.driver == "local" else {
            throw DevContainerError(
                .stateCorruption,
                message: "managed volume \(name) metadata does not match its directory"
            )
        }
        return VolumeSnapshot(
            name: name,
            spec: metadata.spec,
            mountpoint: dataURL.path,
            createdAt: metadata.createdAt
        )
    }

    @discardableResult
    func create(spec: VolumeSpec) throws -> VolumeSnapshot {
        guard spec.driver == "local" else {
            throw DevContainerError(
                .unsupportedCapability,
                message: "volume driver \(spec.driver) is not supported"
            )
        }
        let directory = try volumeDirectory(name: spec.name)
        var status = stat()
        if lstat(directory.path, &status) == 0 {
            return try inspect(name: spec.name)
        }
        guard errno == ENOENT else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }

        do {
            try FileManager.default.createDirectory(
                at: directory,
                withIntermediateDirectories: false,
                attributes: [.posixPermissions: 0o700]
            )
            try FileManager.default.createDirectory(
                at: directory.appendingPathComponent("_data", isDirectory: true),
                withIntermediateDirectories: false,
                attributes: [.posixPermissions: 0o755]
            )
            let metadata = Metadata(spec: spec, createdAt: Date())
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.sortedKeys]
            let metadataURL = directory.appendingPathComponent("metadata.json")
            try encoder.encode(metadata).write(
                to: metadataURL,
                options: .atomic
            )
            guard setMetadataMode(metadataURL.path, S_IRUSR | S_IWUSR) == 0 else {
                throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
            }
            return try inspect(name: spec.name)
        } catch {
            try? FileManager.default.removeItem(at: directory)
            throw error
        }
    }

    func remove(name: String) throws {
        let directory = try volumeDirectory(name: name)
        _ = try inspect(name: name)
        try FileManager.default.removeItem(at: directory)
    }

    static func validate(name: String) throws {
        let scalars = name.unicodeScalars
        guard
            !scalars.isEmpty,
            scalars.count <= 255,
            scalars.first.map(isAlphaNumeric) == true,
            scalars.allSatisfy({
                isAlphaNumeric($0) || $0 == "." || $0 == "_" || $0 == "-"
            })
        else {
            throw DevContainerError(
                .invalidRequest,
                message: "invalid Docker volume name \(name)"
            )
        }
    }

    private func volumeDirectory(name: String) throws -> URL {
        try Self.validate(name: name)
        return root.appendingPathComponent(name, isDirectory: true)
    }

    private static func isAlphaNumeric(_ scalar: Unicode.Scalar) -> Bool {
        switch scalar.value {
        case 48 ... 57, 65 ... 90, 97 ... 122:
            true
        default:
            false
        }
    }

    private static func createPrivateDirectoryIfNeeded(at url: URL) throws {
        try FileManager.default.createDirectory(
            at: url,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        try requireOwnedDirectory(url, volumeName: nil)
        guard chmod(url.path, S_IRWXU) == 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
    }

    private static func requireOwnedDirectory(
        _ url: URL,
        volumeName: String?
    ) throws {
        var status = stat()
        guard
            lstat(url.path, &status) == 0,
            status.st_uid == getuid(),
            status.st_mode & S_IFMT == S_IFDIR,
            status.st_mode & (S_IWGRP | S_IWOTH) == 0
        else {
            if errno == ENOENT, let volumeName {
                throw DevContainerError(
                    .notFound,
                    message: "managed volume \(volumeName) was not found"
                )
            }
            throw DevContainerError(
                .stateCorruption,
                message: "managed volume path is unsafe: \(url.path)"
            )
        }
    }
}
