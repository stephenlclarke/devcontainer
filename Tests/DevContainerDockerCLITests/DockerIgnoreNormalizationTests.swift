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

@Suite("Docker ignore normalization")
struct DockerIgnoreNormalizationTests {
    @Test
    func `build archive cleans Docker ignore paths before matching`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-ignore-clean-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("FROM scratch\n".utf8).write(to: root.appendingPathComponent("Dockerfile"))
        try Data("local-secret\n".utf8).write(to: root.appendingPathComponent(".env"))
        try Data("decoy/../.env\n".utf8).write(to: root.appendingPathComponent(".dockerignore"))

        let archive = try DockerBuildOptions(arguments: [root.path]).archive()
        let result = try ProcessRunner.capturedSync(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: ["-tf", "-"],
            environment: ["PATH": "/usr/bin:/bin"],
            input: archive
        )
        #expect(result.exitCode == 0)
        let output = try #require(String(data: result.standardOutput, encoding: .utf8))
        let entries = Set(output.split(whereSeparator: \.isNewline).map(String.init))

        #expect(entries.contains("Dockerfile"))
        #expect(entries.contains(".dockerignore"))
        #expect(!entries.contains(".env"))
    }
}
