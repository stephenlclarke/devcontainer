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
@testable import DevContainerService
import Foundation
import Testing

@Test
func `default paths are user scoped and executable selection is absolute`() {
    #expect(DefaultPaths.socket.hasSuffix("/devcontainer/docker.sock"))
    #expect(DefaultPaths.stateDatabase.hasSuffix("/devcontainer/state.sqlite"))
    #expect(DefaultPaths.containerExecutable.hasPrefix("/"))
    if FileManager.default.isExecutableFile(atPath: "/usr/local/bin/container") {
        #expect(DefaultPaths.containerExecutable == "/usr/local/bin/container")
    } else if FileManager.default.isExecutableFile(
        atPath: "/opt/homebrew/bin/container"
    ) {
        #expect(DefaultPaths.containerExecutable == "/opt/homebrew/bin/container")
    } else {
        #expect(DefaultPaths.containerExecutable == "/usr/local/bin/container")
    }
}

@Test
func `private provider socket is deterministic and bounded`() {
    let longPublicSocket = "/" + String(repeating: "a", count: 102)
    let providerSocket = DefaultPaths.providerSocket(
        publicSocket: longPublicSocket
    )

    #expect(providerSocket == DefaultPaths.providerSocket(publicSocket: longPublicSocket))
    #expect(
        providerSocket
            != DefaultPaths.providerSocket(publicSocket: longPublicSocket + "b")
    )
    #expect(providerSocket.hasPrefix("/tmp/devcontainer-engine-\(getuid())-"))
    #expect(providerSocket.hasSuffix("/provider.sock"))
    #expect(providerSocket.utf8.count < 104)
}

@Test
func `private provider socket cleanup removes expected artifacts`() throws {
    let directory = providerArtifactDirectory()
    defer { try? FileManager.default.removeItem(at: directory) }
    let socket = directory.appendingPathComponent("provider.sock")
    let lock = URL(fileURLWithPath: socket.path + ".lock")
    try createPrivateDirectory(directory)
    #expect(FileManager.default.createFile(
        atPath: lock.path,
        contents: Data(),
        attributes: [.posixPermissions: 0o600]
    ))

    try DefaultPaths.removeProviderSocketArtifacts(socketPath: socket.path)

    #expect(!FileManager.default.fileExists(atPath: lock.path))
    #expect(!FileManager.default.fileExists(atPath: directory.path))
    try DefaultPaths.removeProviderSocketArtifacts(socketPath: socket.path)
}

@Test
func `private provider socket cleanup preserves unexpected artifacts`() throws {
    let directory = providerArtifactDirectory()
    defer { try? FileManager.default.removeItem(at: directory) }
    let socket = directory.appendingPathComponent("provider.sock")
    let lock = URL(fileURLWithPath: socket.path + ".lock")
    let unexpected = directory.appendingPathComponent("unexpected")
    try createPrivateDirectory(directory)
    #expect(FileManager.default.createFile(
        atPath: lock.path,
        contents: Data(),
        attributes: [.posixPermissions: 0o600]
    ))
    #expect(FileManager.default.createFile(
        atPath: unexpected.path,
        contents: Data()
    ))

    #expect(throws: (any Error).self) {
        try DefaultPaths.removeProviderSocketArtifacts(socketPath: socket.path)
    }
    #expect(FileManager.default.fileExists(atPath: lock.path))
    #expect(FileManager.default.fileExists(atPath: unexpected.path))
}

@Test
func `private provider socket cleanup rejects live sockets and unsafe locks`() throws {
    let directory = providerArtifactDirectory()
    defer { try? FileManager.default.removeItem(at: directory) }
    let socket = directory.appendingPathComponent("provider.sock")
    let lock = URL(fileURLWithPath: socket.path + ".lock")
    try createPrivateDirectory(directory)
    #expect(FileManager.default.createFile(atPath: socket.path, contents: Data()))
    #expect(FileManager.default.createFile(
        atPath: lock.path,
        contents: Data(),
        attributes: [.posixPermissions: 0o600]
    ))
    #expect(throws: (any Error).self) {
        try DefaultPaths.removeProviderSocketArtifacts(socketPath: socket.path)
    }
    try FileManager.default.removeItem(at: socket)
    try FileManager.default.setAttributes(
        [.posixPermissions: 0o644],
        ofItemAtPath: lock.path
    )

    #expect(throws: (any Error).self) {
        try DefaultPaths.removeProviderSocketArtifacts(socketPath: socket.path)
    }
    #expect(FileManager.default.fileExists(atPath: lock.path))
}

private func providerArtifactDirectory() -> URL {
    FileManager.default.temporaryDirectory
        .appendingPathComponent("devcontainer-provider-test-\(UUID().uuidString)")
}

private func createPrivateDirectory(_ directory: URL) throws {
    try FileManager.default.createDirectory(
        at: directory,
        withIntermediateDirectories: false,
        attributes: [.posixPermissions: 0o700]
    )
}
