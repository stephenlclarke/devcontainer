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
        let transport = try StubTransport([
            .init(
                status: 200,
                headers: [
                    "X-Docker-Container-Path-Stat": archiveStatHeader(
                        name: "tmp",
                        mode: (1 << 31) | 0o755
                    )
                ]
            ),
            .init(status: 200)
        ])

        #expect(try DockerCLIApplication(transport: transport).run(arguments: [
            "cp", root.appendingPathComponent("-T").path, "box:/tmp"
        ]).exitCode == 0)
        let request = try #require(transport.requests.last)
        let entries = try archiveEntries(request.body)
        #expect(entries.contains("-T"))
        #expect(!entries.contains("secret.txt"))
    }

    @Test
    func `copy to container rebases a file onto a missing destination`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-cp-rebase-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let source = root.appendingPathComponent("source.txt")
        try Data("wanted\n".utf8).write(to: source)
        let transport = try StubTransport([
            .init(status: 404),
            .init(
                status: 200,
                headers: [
                    "X-Docker-Container-Path-Stat": archiveStatHeader(
                        name: "tmp",
                        mode: (1 << 31) | 0o755
                    )
                ]
            ),
            .init(status: 200)
        ])

        #expect(try DockerCLIApplication(transport: transport).run(arguments: [
            "cp", source.path, "box:/tmp/renamed.txt"
        ]).exitCode == 0)

        #expect(transport.requests.map(\.method) == ["HEAD", "HEAD", "PUT"])
        #expect(transport.requests.map(\.target) == [
            "/containers/box/archive?path=%2Ftmp%2Frenamed.txt",
            "/containers/box/archive?path=%2Ftmp",
            "/containers/box/archive?path=%2Ftmp"
        ])
        let upload = try #require(transport.requests.last)
        let entries = try archiveEntries(upload.body)
        #expect(entries.contains("renamed.txt"))
        #expect(!entries.contains("source.txt"))
    }

    @Test
    func `copy to container rebases a file over an existing file`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-cp-replace-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let source = root.appendingPathComponent("source.txt")
        try Data("wanted\n".utf8).write(to: source)
        let transport = try StubTransport([
            .init(
                status: 200,
                headers: [
                    "X-Docker-Container-Path-Stat": archiveStatHeader(
                        name: "renamed.txt",
                        mode: 0o644
                    )
                ]
            ),
            .init(status: 200)
        ])

        #expect(try DockerCLIApplication(transport: transport).run(arguments: [
            "cp", source.path, "box:/tmp/renamed.txt"
        ]).exitCode == 0)

        #expect(transport.requests.map(\.target) == [
            "/containers/box/archive?path=%2Ftmp%2Frenamed.txt",
            "/containers/box/archive?path=%2Ftmp"
        ])
        let upload = try #require(transport.requests.last)
        #expect(try archiveEntries(upload.body).contains("renamed.txt"))
    }

    @Test
    func `copy to container rebases a directory onto a missing destination`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-cp-directory-\(UUID().uuidString)")
        let source = root.appendingPathComponent("source")
        try FileManager.default.createDirectory(at: source, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("wanted\n".utf8).write(to: source.appendingPathComponent("value.txt"))
        let transport = try StubTransport([
            .init(status: 404),
            .init(
                status: 200,
                headers: [
                    "X-Docker-Container-Path-Stat": archiveStatHeader(
                        name: "tmp",
                        mode: (1 << 31) | 0o755
                    )
                ]
            ),
            .init(status: 200)
        ])

        #expect(try DockerCLIApplication(transport: transport).run(arguments: [
            "cp", source.path, "box:/tmp/renamed"
        ]).exitCode == 0)

        let upload = try #require(transport.requests.last)
        let entries = try archiveEntries(upload.body)
        #expect(entries.contains("renamed/"))
        #expect(entries.contains("renamed/value.txt"))
        #expect(!entries.contains("source/value.txt"))
    }

    @Test
    func `copy to container safely rebases option shaped destination names`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-cp-destination-option-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let source = root.appendingPathComponent("source.txt")
        try Data("wanted\n".utf8).write(to: source)
        let transport = try StubTransport([
            .init(status: 404),
            .init(
                status: 200,
                headers: [
                    "X-Docker-Container-Path-Stat": archiveStatHeader(
                        name: "tmp",
                        mode: (1 << 31) | 0o755
                    )
                ]
            ),
            .init(status: 200)
        ])

        #expect(try DockerCLIApplication(transport: transport).run(arguments: [
            "cp", source.path, "box:/tmp/-T|a&b\\c"
        ]).exitCode == 0)

        let upload = try #require(transport.requests.last)
        let extracted = root.appendingPathComponent("extracted")
        try FileManager.default.createDirectory(
            at: extracted,
            withIntermediateDirectories: false
        )
        let extraction = try ProcessRunner.capturedSync(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: ["-xf", "-", "-C", extracted.path],
            environment: ["PATH": "/usr/bin:/bin"],
            input: upload.body
        )
        #expect(extraction.exitCode == 0)
        #expect(FileManager.default.fileExists(
            atPath: extracted.appendingPathComponent("-T|a&b\\c").path
        ))
        #expect(!FileManager.default.fileExists(
            atPath: extracted.appendingPathComponent("source.txt").path
        ))
    }

    @Test
    func `copy to container requires a directory for a trailing slash`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-cp-trailing-slash-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let source = root.appendingPathComponent("source.txt")
        try Data("wanted\n".utf8).write(to: source)
        let transport = try StubTransport([
            .init(
                status: 200,
                headers: [
                    "X-Docker-Container-Path-Stat": archiveStatHeader(
                        name: "target",
                        mode: 0o644
                    )
                ]
            )
        ])

        #expect(throws: DockerCLIError.self) {
            _ = try DockerCLIApplication(transport: transport).run(arguments: [
                "cp", source.path, "box:/tmp/target/"
            ])
        }
        #expect(transport.requests.map(\.method) == ["HEAD"])
    }

    @Test
    func `copy to container rejects a directory over an existing file`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-cp-directory-file-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let transport = try StubTransport([
            .init(
                status: 200,
                headers: [
                    "X-Docker-Container-Path-Stat": archiveStatHeader(
                        name: "target",
                        mode: 0o644
                    )
                ]
            )
        ])

        #expect(throws: DockerCLIError.self) {
            _ = try DockerCLIApplication(transport: transport).run(arguments: [
                "cp", root.path, "box:/tmp/target"
            ])
        }
        #expect(transport.requests.map(\.method) == ["HEAD"])
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
