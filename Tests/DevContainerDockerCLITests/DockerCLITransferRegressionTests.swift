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
            "cp", source.path, "box:/tmp/-T|a&b\\c~d"
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
            atPath: extracted.appendingPathComponent("-T|a&b\\c~d").path
        ))
        #expect(!FileManager.default.fileExists(
            atPath: extracted.appendingPathComponent("source.txt").path
        ))
    }

    @Test
    func `copy to container rebases hard link targets`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-cp-hard-link-\(UUID().uuidString)")
        let source = root.appendingPathComponent("source")
        try FileManager.default.createDirectory(at: source, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let first = source.appendingPathComponent("first.txt")
        let second = source.appendingPathComponent("second.txt")
        try Data("linked\n".utf8).write(to: first)
        try FileManager.default.linkItem(at: first, to: second)
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
        let extracted = root.appendingPathComponent("extracted")
        try FileManager.default.createDirectory(at: extracted, withIntermediateDirectories: false)
        let extraction = try ProcessRunner.capturedSync(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: ["-xf", "-", "-C", extracted.path],
            environment: ["PATH": "/usr/bin:/bin"],
            input: upload.body
        )
        #expect(extraction.exitCode == 0)
        let firstAttributes = try FileManager.default.attributesOfItem(
            atPath: extracted.appendingPathComponent("renamed/first.txt").path
        )
        let secondAttributes = try FileManager.default.attributesOfItem(
            atPath: extracted.appendingPathComponent("renamed/second.txt").path
        )
        #expect(firstAttributes[.systemFileNumber] as? UInt64 == secondAttributes[.systemFileNumber] as? UInt64)
    }

    @Test
    func `copy to container preserves dangling local symbolic links`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-cp-local-symlink-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let source = root.appendingPathComponent("dangling")
        try FileManager.default.createSymbolicLink(atPath: source.path, withDestinationPath: "missing")
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
            "cp", source.path, "box:/tmp"
        ]).exitCode == 0)
        let upload = try #require(transport.requests.last)
        let extracted = root.appendingPathComponent("extracted")
        try FileManager.default.createDirectory(at: extracted, withIntermediateDirectories: false)
        _ = try ProcessRunner.capturedSync(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: ["-xf", "-", "-C", extracted.path],
            environment: ["PATH": "/usr/bin:/bin"],
            input: upload.body
        )
        var status = Darwin.stat()
        #expect(lstat(extracted.appendingPathComponent("dangling").path, &status) == 0)
        #expect(status.st_mode & S_IFMT == S_IFLNK)
    }

    @Test
    func `copy to container does not classify a directory symbolic link as a directory`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-cp-local-directory-link-\(UUID().uuidString)")
        let directory = root.appendingPathComponent("directory")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let source = root.appendingPathComponent("link")
        try FileManager.default.createSymbolicLink(
            atPath: source.path,
            withDestinationPath: directory.lastPathComponent
        )
        let transport = try StubTransport([
            .init(
                status: 200,
                headers: [
                    "X-Docker-Container-Path-Stat": archiveStatHeader(
                        name: "target",
                        mode: 0o644
                    )
                ]
            ),
            .init(status: 200)
        ])

        #expect(try DockerCLIApplication(transport: transport).run(arguments: [
            "cp", source.path, "box:/tmp/target"
        ]).exitCode == 0)
        let upload = try #require(transport.requests.last)
        let extracted = root.appendingPathComponent("extracted-link")
        try FileManager.default.createDirectory(at: extracted, withIntermediateDirectories: false)
        _ = try ProcessRunner.capturedSync(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: ["-xf", "-", "-C", extracted.path],
            environment: ["PATH": "/usr/bin:/bin"],
            input: upload.body
        )
        var status = Darwin.stat()
        #expect(lstat(extracted.appendingPathComponent("target").path, &status) == 0)
        #expect(status.st_mode & S_IFMT == S_IFLNK)
    }

    @Test
    func `copy to container resolves existing destination directory symbolic links`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-cp-remote-directory-link-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let source = root.appendingPathComponent("source.txt")
        try Data("wanted\n".utf8).write(to: source)
        let transport = try StubTransport([
            .init(
                status: 200,
                headers: [
                    "X-Docker-Container-Path-Stat": archiveStatHeader(
                        name: "alias",
                        mode: (1 << 27) | 0o777,
                        linkTarget: "/workspace"
                    )
                ]
            ),
            .init(
                status: 200,
                headers: [
                    "X-Docker-Container-Path-Stat": archiveStatHeader(
                        name: "workspace",
                        mode: (1 << 31) | 0o755
                    )
                ]
            ),
            .init(status: 200)
        ])

        #expect(try DockerCLIApplication(transport: transport).run(arguments: [
            "cp", source.path, "box:/alias"
        ]).exitCode == 0)
        #expect(transport.requests.map(\.target) == [
            "/containers/box/archive?path=%2Falias",
            "/containers/box/archive?path=%2Fworkspace",
            "/containers/box/archive?path=%2Fworkspace"
        ])
    }

    @Test
    func `copy to container resolves existing destination file symbolic links`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-cp-remote-file-link-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let source = root.appendingPathComponent("source.txt")
        try Data("wanted\n".utf8).write(to: source)
        let transport = try StubTransport([
            .init(
                status: 200,
                headers: [
                    "X-Docker-Container-Path-Stat": archiveStatHeader(
                        name: "alias.txt",
                        mode: (1 << 27) | 0o777,
                        linkTarget: "real.txt"
                    )
                ]
            ),
            .init(
                status: 200,
                headers: [
                    "X-Docker-Container-Path-Stat": archiveStatHeader(
                        name: "real.txt",
                        mode: 0o644
                    )
                ]
            ),
            .init(status: 200)
        ])

        #expect(try DockerCLIApplication(transport: transport).run(arguments: [
            "cp", source.path, "box:/tmp/alias.txt"
        ]).exitCode == 0)
        let upload = try #require(transport.requests.last)
        #expect(transport.requests.map(\.target).last == "/containers/box/archive?path=%2Ftmp")
        #expect(try archiveEntries(upload.body).contains("real.txt"))
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

    @Test
    func `copy from container rejects a missing destination parent`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-cp-missing-parent-\(UUID().uuidString)")
        let source = root.appendingPathComponent("source")
        let destination = root.appendingPathComponent("missing/renamed.txt")
        try FileManager.default.createDirectory(at: source, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("wanted\n".utf8).write(to: source.appendingPathComponent("value.txt"))
        let archive = try ProcessRunner.capturedSync(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: ["-cf", "-", "-C", source.path, "value.txt"],
            environment: ["PATH": "/usr/bin:/bin"]
        ).standardOutput
        let transport = try StubTransport([
            .init(
                status: 200,
                headers: [
                    "X-Docker-Container-Path-Stat": archiveStatHeader(
                        name: "value.txt",
                        mode: 0o644
                    )
                ],
                body: archive
            )
        ])

        #expect(throws: DockerCLIError.self) {
            _ = try DockerCLIApplication(transport: transport).run(arguments: [
                "cp", "box:/tmp/value.txt", destination.path
            ])
        }
        #expect(!FileManager.default.fileExists(atPath: destination.deletingLastPathComponent().path))
    }

    @Test
    func `copy from container preserves a renamed dangling symbolic link`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-cp-remote-symlink-\(UUID().uuidString)")
        let source = root.appendingPathComponent("source")
        let destination = root.appendingPathComponent("renamed")
        try FileManager.default.createDirectory(at: source, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.createSymbolicLink(
            atPath: source.appendingPathComponent("dangling").path,
            withDestinationPath: "missing"
        )
        let archive = try ProcessRunner.capturedSync(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: ["-cf", "-", "-C", source.path, "dangling"],
            environment: ["PATH": "/usr/bin:/bin"]
        ).standardOutput
        let transport = try StubTransport([
            .init(
                status: 200,
                headers: [
                    "X-Docker-Container-Path-Stat": archiveStatHeader(
                        name: "dangling",
                        mode: (1 << 27) | 0o777,
                        linkTarget: "missing"
                    )
                ],
                body: archive
            )
        ])

        #expect(try DockerCLIApplication(transport: transport).run(arguments: [
            "cp", "box:/tmp/dangling", destination.path
        ]).exitCode == 0)
        var status = Darwin.stat()
        #expect(lstat(destination.path, &status) == 0)
        #expect(status.st_mode & S_IFMT == S_IFLNK)
        #expect(try FileManager.default.destinationOfSymbolicLink(atPath: destination.path) == "missing")
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

    private func archiveStatHeader(
        name: String,
        mode: UInt32,
        linkTarget: String? = nil
    ) throws -> String {
        var object: [String: Any] = ["name": name, "mode": mode]
        if let linkTarget {
            object["linkTarget"] = linkTarget
        }
        return try JSONSerialization.data(
            withJSONObject: object,
            options: [.sortedKeys]
        ).base64EncodedString()
    }
}
