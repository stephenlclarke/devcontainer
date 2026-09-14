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
@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerRuntimeSPI
import DevContainerTestSupport
import Foundation
import Testing

@Test
func `archive HEAD uses metadata without materialising an archive`() async throws {
    let runtime = InMemoryRuntime()
    await runtime.seedImage(
        ImageSnapshot(
            id: "sha256:head",
            references: ["head:test"],
            createdAt: Date(),
            size: 1
        )
    )
    let container = try await runtime.createContainer(
        spec: ContainerSpec(name: "archive-head", image: "head:test"),
        context: RuntimeRequestContext()
    )

    let response = await DockerRouter(runtime: runtime).respond(
        to: DockerHTTPRequest(
            method: .head,
            target: "/containers/\(container.dockerID.rawValue)/archive?path=%2Fworkspace"
        )
    )

    #expect(response.status == 200)
    #expect(response.headers["X-Docker-Container-Path-Stat"] != nil)
    #expect(await runtime.archiveCopyCount() == 0)
}

@Test
func `file backed archives stream in bounded chunks and remove their spool`() async throws {
    let root = FileManager.default.temporaryDirectory
        .appendingPathComponent("devcontainer-archive-stream-\(UUID().uuidString)")
    defer { try? FileManager.default.removeItem(at: root) }
    let file = try RuntimeArchiveFile(baseDirectory: root)
    #expect(
        try FileManager.default.attributesOfItem(atPath: file.url.path)[.posixPermissions]
            as? Int == 0o600
    )
    let payload = Data(repeating: 0x5A, count: (64 * 1024 * 2) + 17)
    let writer = try file.makeWritingHandle()
    try writer.write(contentsOf: payload)
    try writer.close()

    let runtime = InMemoryRuntime()
    await runtime.seedImage(
        ImageSnapshot(
            id: "sha256:archive",
            references: ["archive:test"],
            createdAt: Date(),
            size: 1
        )
    )
    let container = try await runtime.createContainer(
        spec: ContainerSpec(name: "archive-file", image: "archive:test"),
        context: RuntimeRequestContext()
    )
    try await runtime.seedArchiveFile(
        id: container.dockerID.rawValue,
        path: "/workspace/large.tar",
        file: file
    )

    let response = await DockerRouter(runtime: runtime).respond(
        to: DockerHTTPRequest(
            method: .get,
            target: "/containers/\(container.dockerID.rawValue)/archive?path=%2Fworkspace%2Flarge.tar"
        )
    )
    guard case let .managedStream(stream) = response.body else {
        Issue.record("file-backed archive should use a managed HTTP stream")
        return
    }
    var received = Data()
    var chunkSizes: [Int] = []
    while let chunk = try await stream.nextChunk() {
        chunkSizes.append(chunk.count)
        received.append(chunk)
    }
    #expect(received == payload)
    #expect(chunkSizes == [64 * 1024, 64 * 1024, 17])
    #expect(!FileManager.default.fileExists(atPath: file.url.path))
}

@Test
func `cancelling a file backed archive removes its spool`() async throws {
    let root = FileManager.default.temporaryDirectory
        .appendingPathComponent("devcontainer-archive-cancel-\(UUID().uuidString)")
    defer { try? FileManager.default.removeItem(at: root) }
    let file = try RuntimeArchiveFile(baseDirectory: root)
    let writer = try file.makeWritingHandle()
    try writer.write(contentsOf: Data(repeating: 0x5A, count: 65 * 1024))
    try writer.close()
    let stream = try DockerArchiveFileStream(archive: file)

    #expect(try await stream.nextChunk()?.count == 64 * 1024)
    await stream.cancel()
    #expect(try await stream.nextChunk() == nil)
    #expect(!FileManager.default.fileExists(atPath: file.url.path))
}

@Test
func `archive descriptors ignore pathname replacement and preserve foreign cleanup targets`() throws {
    let root = FileManager.default.temporaryDirectory
        .appendingPathComponent("devcontainer-archive-race-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
    defer { try? FileManager.default.removeItem(at: root) }
    let victim = root.appendingPathComponent("victim")
    try Data("safe".utf8).write(to: victim)
    let archive = try RuntimeArchiveFile(baseDirectory: root)
    let writer = try archive.makeWritingHandle()
    try FileManager.default.removeItem(at: archive.url)
    try FileManager.default.createSymbolicLink(
        at: archive.url,
        withDestinationURL: victim
    )

    try writer.write(contentsOf: Data("archive".utf8))
    try writer.close()
    let reader = try archive.makeReadingHandle()
    #expect(try reader.readToEnd() == Data("archive".utf8))
    try reader.close()
    #expect(try Data(contentsOf: victim) == Data("safe".utf8))

    archive.remove()
    var replacementStatus = Darwin.stat()
    #expect(lstat(archive.url.path, &replacementStatus) == 0)
    #expect(replacementStatus.st_mode & S_IFMT == S_IFLNK)
}
