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
    func `pathological stars have deterministic bounded matching work`() throws {
        var matcher = try DockerIgnoreMatcher(
            contents: "a*a*a*a*a*a*a*a*a*b\n"
        )
        let clock = ContinuousClock()
        let started = clock.now

        #expect(try matcher.includes(String(repeating: "a", count: 40)))
        #expect(started.duration(to: clock.now) < .seconds(1))
    }

    @Test
    func `long literal rules use bounded linear matching work`() throws {
        var matcher = try DockerIgnoreMatcher(
            contents: String(repeating: "a", count: 65000) + "\n"
        )
        let clock = ContinuousClock()
        let started = clock.now

        for index in 0 ..< 100 {
            #expect(try matcher.includes("path-\(index)-" + String(repeating: "b", count: 240)))
        }
        #expect(started.duration(to: clock.now) < .seconds(1))
    }

    @Test
    func `global work budget rejects adversarial wildcard rules`() throws {
        var matcher = try DockerIgnoreMatcher(
            contents: String(repeating: "a", count: 65000) + "*\n",
            matchingWorkLimit: 1
        )
        let path = String(repeating: "a", count: 250)
        let clock = ContinuousClock()
        let started = clock.now
        var rejected = false

        for _ in 0 ..< 10 {
            do {
                _ = try matcher.includes(path)
            } catch let DockerCLIError.invalidArguments(message) {
                #expect(message.contains("matching work exceeds"))
                rejected = true
                break
            }
        }
        #expect(rejected)
        #expect(started.duration(to: clock.now) < .seconds(2))
    }

    @Test
    func `wildcards consume one Unicode scalar like Docker`() throws {
        var matcher = try DockerIgnoreMatcher(contents: "*\n!?\n")

        #expect(try matcher.includes("é"))
        #expect(try !matcher.includes("e\u{301}"))
    }

    @Test
    func `double stars and escaped character classes preserve ignore semantics`() throws {
        var matcher = try DockerIgnoreMatcher(contents: """
        build/**/secret[0-9].txt
        literal\\[name\\].txt
        """)

        #expect(try !matcher.includes("build/secret1.txt"))
        #expect(try !matcher.includes("build/a/b/secret9.txt"))
        #expect(try matcher.includes("build/a/b/secretA.txt"))
        #expect(try matcher.includes("build/xsecret1.txt"))
        #expect(try !matcher.includes("nested/literal[name].txt"))
    }

    @Test
    func `escaped class hyphens remain literals`() throws {
        var matcher = try DockerIgnoreMatcher(contents: "[z\\-.]env\n")

        #expect(try !matcher.includes("zenv"))
        #expect(try !matcher.includes("-env"))
        #expect(try !matcher.includes(".env"))
        #expect(try matcher.includes("aenv"))
    }

    @Test
    func `malformed patterns are rejected like Go path match`() {
        for pattern in [
            "[-.]env\n", "[.-]env\n", "[a--.]env\n", "[a-b-c]env\n",
            "[z-a]env\n", "secret\\\n", "!\n"
        ] {
            #expect(throws: DockerCLIError.self) {
                _ = try DockerIgnoreMatcher(contents: pattern)
            }
        }
    }

    @Test
    func `unescaped leading closing brackets are rejected like Go path match`() {
        #expect(throws: DockerCLIError.self) {
            _ = try DockerIgnoreMatcher(contents: "*\n![^]a]env\n")
        }
        #expect(throws: Never.self) {
            _ = try DockerIgnoreMatcher(contents: "[\\]a]env\n")
        }
    }

    @Test
    func `escaped class hyphens exclude matching secrets from build archives`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-ignore-hyphen-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("FROM scratch\n".utf8).write(to: root.appendingPathComponent("Dockerfile"))
        try Data("secret\n".utf8).write(to: root.appendingPathComponent(".env"))
        try Data("public\n".utf8).write(to: root.appendingPathComponent("aenv"))
        try Data("[z\\-.]env\n".utf8).write(to: root.appendingPathComponent(".dockerignore"))

        let entries = try archiveEntries(
            DockerBuildOptions(arguments: [root.path]).archive()
        )

        #expect(!entries.contains(".env"))
        #expect(entries.contains("aenv"))
    }

    @Test
    func `invalid UTF-8 Docker ignore files fail closed`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-ignore-encoding-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("FROM scratch\n".utf8).write(to: root.appendingPathComponent("Dockerfile"))
        try Data("secret\n".utf8).write(to: root.appendingPathComponent(".env"))
        try (Data(".env\n".utf8) + Data([0xFF])).write(
            to: root.appendingPathComponent(".dockerignore")
        )

        #expect(throws: DockerCLIError.self) {
            _ = try DockerBuildOptions(arguments: [root.path]).archive()
        }
    }

    @Test
    func `UTF-8 byte order mark does not disable the first ignore rule`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-ignore-bom-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("FROM scratch\n".utf8).write(to: root.appendingPathComponent("Dockerfile"))
        try Data("secret\n".utf8).write(to: root.appendingPathComponent(".env"))
        try (Data([0xEF, 0xBB, 0xBF]) + Data(".env\n".utf8)).write(
            to: root.appendingPathComponent(".dockerignore")
        )

        let entries = try archiveEntries(
            DockerBuildOptions(arguments: [root.path]).archive()
        )

        #expect(!entries.contains(".env"))
    }

    @Test
    func `oversized Docker ignore lines fail closed`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-ignore-line-limit-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("FROM scratch\n".utf8).write(to: root.appendingPathComponent("Dockerfile"))
        try (Data(repeating: 0x61, count: 64 * 1024) + Data([0x0A])).write(
            to: root.appendingPathComponent(".dockerignore")
        )

        #expect(throws: DockerCLIError.self) {
            _ = try DockerBuildOptions(arguments: [root.path]).archive()
        }

        try Data(repeating: 0x61, count: 64 * 1024).write(
            to: root.appendingPathComponent(".dockerignore")
        )
        #expect(throws: DockerCLIError.self) {
            _ = try DockerBuildOptions(arguments: [root.path]).archive()
        }
    }

    @Test
    func `character classes can match path separators like Go`() throws {
        var matcher = try DockerIgnoreMatcher(contents: "secrets[/].env\n")

        #expect(try !matcher.includes("secrets/.env"))
        #expect(try matcher.includes("secrets/a.env"))
    }

    @Test
    func `build archive honors Go compatible Docker ignore character classes`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-ignore-class-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("FROM scratch\n".utf8).write(to: root.appendingPathComponent("Dockerfile"))
        for name in [
            "secret1.txt", "secretA.txt", "public1.txt", "publicA.txt", ".env", "!env"
        ] {
            try Data(name.utf8).write(to: root.appendingPathComponent(name))
        }
        try Data("secret[0-9].txt\npublic*.txt\n!public[^A-Z].txt\n[!.]env\n".utf8).write(
            to: root.appendingPathComponent(".dockerignore")
        )

        let entries = try archiveEntries(
            DockerBuildOptions(arguments: [root.path]).archive()
        )

        #expect(!entries.contains("secret1.txt"))
        #expect(entries.contains("secretA.txt"))
        #expect(entries.contains("public1.txt"))
        #expect(!entries.contains("publicA.txt"))
        #expect(!entries.contains(".env"))
        #expect(!entries.contains("!env"))
    }

    @Test
    func `build archive cleans Docker ignore paths before matching`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-ignore-clean-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("FROM scratch\n".utf8).write(to: root.appendingPathComponent("Dockerfile"))
        try Data("local-secret\n".utf8).write(to: root.appendingPathComponent(".env"))
        try Data("decoy/../.env\n".utf8).write(to: root.appendingPathComponent(".dockerignore"))

        let entries = try archiveEntries(
            DockerBuildOptions(arguments: [root.path]).archive()
        )

        #expect(entries.contains("Dockerfile"))
        #expect(entries.contains(".dockerignore"))
        #expect(!entries.contains(".env"))
    }

    @Test
    func `build archive cleans before interpreting ignore negation`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-ignore-negation-clean-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("FROM scratch\n".utf8).write(to: root.appendingPathComponent("Dockerfile"))
        try Data("local-secret\n".utf8).write(to: root.appendingPathComponent(".env"))
        try Data("!decoy/../.env\n".utf8).write(
            to: root.appendingPathComponent(".dockerignore")
        )

        let entries = try archiveEntries(
            DockerBuildOptions(arguments: [root.path]).archive()
        )

        #expect(entries.contains("Dockerfile"))
        #expect(entries.contains(".dockerignore"))
        #expect(!entries.contains(".env"))
    }

    @Test
    func `external Dockerfile basename cannot inject tar options`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-external-file-\(UUID().uuidString)")
        let context = root.appendingPathComponent("context")
        try FileManager.default.createDirectory(at: context, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let dockerfile = root.appendingPathComponent("-Tlist")
        try Data("FROM scratch\n".utf8).write(to: dockerfile)
        try Data("local-secret\n".utf8).write(to: root.appendingPathComponent(".env"))
        try Data("./-Tlist\n.env\n".utf8).write(to: root.appendingPathComponent("list"))

        let entries = try archiveEntries(
            DockerBuildOptions(
                arguments: ["--file", dockerfile.path, context.path]
            ).archive()
        )

        #expect(entries.contains("-Tlist"))
        #expect(!entries.contains(".env"))
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
