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

@testable import DevContainerDockerCLI
import DevContainerProcess
import Foundation
import Testing

@Suite("Docker build context traversal")
struct DockerBuildContextTraversalTests {
    @Test
    func `excluded directory pruning skips its descendants`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-prune-\(UUID().uuidString)")
        let directory = root.appendingPathComponent("ignored")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("ignored\n".utf8).write(to: directory.appendingPathComponent("child.txt"))
        let enumerator = try #require(FileManager.default.enumerator(
            at: root,
            includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey]
        ))

        let first = try #require(enumerator.nextObject() as? URL)
        #expect(first.lastPathComponent == "ignored")
        DockerBuildContextTraversal.pruneExcludedDirectory(
            first,
            path: "ignored",
            preserving: nil,
            in: enumerator
        )

        #expect(enumerator.nextObject() == nil)
    }

    @Test
    func `build archive prunes an entirely ignored package directory`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-ignore-package-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("FROM scratch\n".utf8).write(to: root.appendingPathComponent("Dockerfile"))
        let bundle = root.appendingPathComponent("Generated.bundle")
        try FileManager.default.createDirectory(at: bundle, withIntermediateDirectories: false)
        for index in 0 ..< 100 {
            try Data("generated\n".utf8).write(
                to: bundle.appendingPathComponent("entry-\(index).txt")
            )
        }
        try Data("Generated.bundle\n".utf8).write(
            to: root.appendingPathComponent(".dockerignore")
        )

        let entries = try archiveEntries(
            DockerBuildOptions(arguments: [root.path]).archive()
        )

        #expect(!entries.contains(where: { $0.hasPrefix("Generated.bundle") }))
    }

    @Test
    func `build archive preserves a requested Dockerfile inside an ignored directory`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-ignore-required-file-\(UUID().uuidString)")
        let ignored = root.appendingPathComponent("ignored")
        try FileManager.default.createDirectory(at: ignored, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("FROM scratch\n".utf8).write(to: ignored.appendingPathComponent("Dockerfile"))
        try Data("secret\n".utf8).write(to: ignored.appendingPathComponent("secret.txt"))
        try Data("ignored\n".utf8).write(to: root.appendingPathComponent(".dockerignore"))

        let entries = try archiveEntries(
            DockerBuildOptions(
                arguments: ["--file", ignored.appendingPathComponent("Dockerfile").path, root.path]
            ).archive()
        )

        #expect(entries.contains("ignored/Dockerfile"))
        #expect(!entries.contains("ignored/secret.txt"))
    }

    private func archiveEntries(_ archive: Data) throws -> Set<String> {
        let result = try ProcessRunner.capturedSync(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: ["-tf", "-"],
            environment: ["PATH": "/usr/bin:/bin"],
            input: archive
        )
        #expect(result.exitCode == 0)
        let output = try #require(String(data: result.standardOutput, encoding: .utf8))
        return Set(output.split(whereSeparator: \.isNewline).map(String.init))
    }
}
