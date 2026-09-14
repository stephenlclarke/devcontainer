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

@Suite("Docker CLI transfer regressions")
struct DockerCLITransferRegressionTests {
    @Test
    func `container removal preserves Docker force semantics`() throws {
        let transport = StubTransport([
            .init(status: 204),
            .init(status: 204)
        ])
        let application = DockerCLIApplication(transport: transport)

        #expect(try application.run(arguments: ["rm", "running"]).exitCode == 0)
        #expect(try application.run(arguments: ["rm", "--force", "forced"]).exitCode == 0)
        #expect(transport.requests.map(\.target) == [
            "/containers/running",
            "/containers/forced?force=true"
        ])
    }

    @Test
    func `copy to container terminates tar options before source basename`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-cp-option-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("wanted\n".utf8).write(to: root.appendingPathComponent("-T"))
        try Data("secret\n".utf8).write(to: root.appendingPathComponent("secret.txt"))
        let transport = StubTransport([.init(status: 200)])

        #expect(try DockerCLIApplication(transport: transport).run(arguments: [
            "cp", root.appendingPathComponent("-T").path, "box:/tmp"
        ]).exitCode == 0)
        let request = try #require(transport.requests.first)
        let entries = try archiveEntries(request.body)
        #expect(entries.contains("-T"))
        #expect(!entries.contains("secret.txt"))
    }

    @Test
    func `copy from container streams archives larger than default response limit`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-cp-large-\(UUID().uuidString)")
        let source = root.appendingPathComponent("source")
        let destination = root.appendingPathComponent("destination")
        try FileManager.default.createDirectory(at: source, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let payload = Data(repeating: 0x5A, count: 17 * 1024 * 1024)
        try payload.write(to: source.appendingPathComponent("large.bin"))
        let archive = try ProcessRunner.capturedSync(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: ["-cf", "-", "-C", source.path, "large.bin"],
            environment: ["PATH": "/usr/bin:/bin"]
        ).standardOutput
        let transport = try StubTransport([
            .init(
                status: 200,
                headers: [
                    "X-Docker-Container-Path-Stat": archiveStatHeader(
                        name: "large.bin",
                        mode: 0o644
                    )
                ],
                body: archive
            )
        ])

        #expect(try DockerCLIApplication(transport: transport).run(arguments: [
            "cp", "box:/tmp/large.bin", destination.path
        ]).exitCode == 0)
        #expect(try Data(contentsOf: destination.appendingPathComponent("large.bin")) == payload)
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

    private func archiveStatHeader(name: String, mode: UInt32) throws -> String {
        try JSONSerialization.data(
            withJSONObject: ["name": name, "mode": mode],
            options: [.sortedKeys]
        ).base64EncodedString()
    }
}
