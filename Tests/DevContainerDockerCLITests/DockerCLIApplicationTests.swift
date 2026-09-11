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

@Suite("Docker CLI compatibility application")
// swiftlint:disable:next type_body_length
struct DockerCLIApplicationTests {
    @Test
    func `build archive excludes macOS extended attributes`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-build-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let dockerfile = root.appendingPathComponent("Dockerfile")
        try Data("FROM scratch\n".utf8).write(to: dockerfile)
        let attribute = Data([0x01, 0x02])
        let result = dockerfile.path.withCString { path in
            "com.apple.provenance".withCString { name in
                attribute.withUnsafeBytes { bytes in
                    setxattr(path, name, bytes.baseAddress, bytes.count, 0, 0)
                }
            }
        }
        #expect(result == 0)

        let archive = try DockerBuildOptions(
            arguments: ["--file", dockerfile.path, root.path]
        ).archive()

        #expect(!archive.contains(Data("com.apple.provenance".utf8)))
    }

    @Test
    func `build archive honors Docker ignore patterns and negation`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-ignore-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("FROM scratch\n".utf8).write(
            to: root.appendingPathComponent("Dockerfile")
        )
        try Data("secret\n".utf8).write(
            to: root.appendingPathComponent("credentials.pem")
        )
        try Data("keep\n".utf8).write(
            to: root.appendingPathComponent("public.pem")
        )
        try Data("*.pem\n!public.pem\n".utf8).write(
            to: root.appendingPathComponent(".dockerignore")
        )

        let archive = try DockerBuildOptions(arguments: [root.path]).archive()
        let entries = try archiveEntries(archive)

        #expect(entries.contains("Dockerfile"))
        #expect(entries.contains("public.pem"))
        #expect(!entries.contains("credentials.pem"))
        #expect(!entries.contains(".dockerignore"))
    }

    @Test
    func `build archive does not restore an ignored descendant through its directory`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-ignore-descendant-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("FROM scratch\n".utf8).write(to: root.appendingPathComponent("Dockerfile"))
        let subdirectory = root.appendingPathComponent("subdir")
        try FileManager.default.createDirectory(
            at: subdirectory,
            withIntermediateDirectories: false
        )
        try Data("secret\n".utf8).write(
            to: subdirectory.appendingPathComponent("secret.pem")
        )
        try Data("keep\n".utf8).write(
            to: subdirectory.appendingPathComponent("public.txt")
        )
        try Data("secret.pem\n".utf8).write(to: root.appendingPathComponent(".dockerignore"))

        let entries = try archiveEntries(
            DockerBuildOptions(arguments: [root.path]).archive()
        )

        #expect(entries.contains("subdir/"))
        #expect(entries.contains("subdir/public.txt"))
        #expect(!entries.contains("subdir/secret.pem"))
    }

    @Test
    func `build archive cannot reinclude a child of an excluded directory`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-ignore-parent-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("FROM scratch\n".utf8).write(to: root.appendingPathComponent("Dockerfile"))
        let privateDirectory = root.appendingPathComponent("private")
        try FileManager.default.createDirectory(
            at: privateDirectory,
            withIntermediateDirectories: false
        )
        try Data("secret\n".utf8).write(
            to: privateDirectory.appendingPathComponent("keep.txt")
        )
        try Data("private\n!private/keep.txt\n".utf8).write(
            to: root.appendingPathComponent(".dockerignore")
        )

        let entries = try archiveEntries(
            DockerBuildOptions(arguments: [root.path]).archive()
        )

        #expect(!entries.contains("private"))
        #expect(!entries.contains("private/keep.txt"))
    }

    @Test
    func `build archive preserves symlinks without archiving their targets`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-symlink-\(UUID().uuidString)")
        let outside = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-symlink-target-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer {
            try? FileManager.default.removeItem(at: root)
            try? FileManager.default.removeItem(at: outside)
        }
        try Data("FROM scratch\n".utf8).write(to: root.appendingPathComponent("Dockerfile"))
        try Data("outside\n".utf8).write(to: outside)
        let link = root.appendingPathComponent("outside-link")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: outside)

        let archive = try DockerBuildOptions(arguments: [root.path]).archive()
        let entries = try archiveEntries(archive)
        let extracted = root.appendingPathComponent("extracted")
        try FileManager.default.createDirectory(at: extracted, withIntermediateDirectories: false)
        _ = try ProcessRunner.capturedSync(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: ["-xf", "-", "-C", extracted.path],
            environment: ["PATH": "/usr/bin:/bin"],
            input: archive
        )

        #expect(entries.contains("outside-link"))
        #expect(
            try FileManager.default.destinationOfSymbolicLink(
                atPath: extracted.appendingPathComponent("outside-link").path
            ) == outside.path
        )
    }

    @Test
    func `build archive includes descendants of macOS package directories`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-package-\(UUID().uuidString)")
        let bundle = root.appendingPathComponent("Fixture.bundle/Contents", isDirectory: true)
        try FileManager.default.createDirectory(at: bundle, withIntermediateDirectories: true)
        try Data("payload\n".utf8).write(to: bundle.appendingPathComponent("value.txt"))
        try Data("FROM scratch\n".utf8).write(to: root.appendingPathComponent("Dockerfile"))
        defer { try? FileManager.default.removeItem(at: root) }

        let entries = try archiveEntries(DockerBuildOptions(arguments: [root.path]).archive())
        #expect(entries.contains("Fixture.bundle/Contents/value.txt"))
    }

    @Test
    func `dockerfile specific ignore file takes precedence`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("dockerfile-ignore-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let dockerfile = root.appendingPathComponent("Containerfile")
        try Data("FROM scratch\n".utf8).write(to: dockerfile)
        try Data("root-only\n".utf8).write(
            to: root.appendingPathComponent("root.txt")
        )
        try Data("specific-only\n".utf8).write(
            to: root.appendingPathComponent("specific.txt")
        )
        try Data("root.txt\n".utf8).write(
            to: root.appendingPathComponent(".dockerignore")
        )
        try Data("specific.txt\n".utf8).write(
            to: root.appendingPathComponent("Containerfile.dockerignore")
        )

        let archive = try DockerBuildOptions(
            arguments: ["--file", dockerfile.path, root.path]
        ).archive()
        let entries = try archiveEntries(archive)

        #expect(entries.contains("Containerfile"))
        #expect(entries.contains("root.txt"))
        #expect(!entries.contains("specific.txt"))
        #expect(!entries.contains("Containerfile.dockerignore"))
    }

    @Test
    func `build archive honors Docker ignore character classes`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-ignore-class-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("FROM scratch\n".utf8).write(to: root.appendingPathComponent("Dockerfile"))
        for name in ["secret1.txt", "secretA.txt", "public1.txt", "publicA.txt"] {
            try Data(name.utf8).write(to: root.appendingPathComponent(name))
        }
        try Data("secret[0-9].txt\npublic*.txt\n!public[!A-Z].txt\n".utf8).write(
            to: root.appendingPathComponent(".dockerignore")
        )

        let entries = try archiveEntries(
            DockerBuildOptions(arguments: [root.path]).archive()
        )

        #expect(!entries.contains("secret1.txt"))
        #expect(entries.contains("secretA.txt"))
        #expect(entries.contains("public1.txt"))
        #expect(!entries.contains("publicA.txt"))
    }

    @Test
    func `reports a Docker-shaped version without contacting the engine`() throws {
        let transport = StubTransport([])
        let result = try DockerCLIApplication(transport: transport).run(arguments: ["-v"])
        let output = try #require(String(data: result.standardOutput, encoding: .utf8))

        #expect(output.hasPrefix("Docker version 26.1.0"))
        #expect(transport.requests.isEmpty)
    }

    @Test
    func `detects interactive exec after supported global options`() throws {
        #expect(
            try DockerCLIApplication.requiresInteractiveInput(
                arguments: ["--host", "unix:///tmp/example.sock", "exec", "-i", "box", "cat"]
            )
        )
        #expect(
            try !DockerCLIApplication.requiresInteractiveInput(
                arguments: ["--context=example", "exec", "box", "cat"]
            )
        )
    }

    @Test
    func `formats the server version expected by the upstream CLI`() throws {
        let transport = StubTransport([.json(["Version": "1.4.1"])])
        let result = try DockerCLIApplication(transport: transport).run(
            arguments: ["version", "--format", "{{.Server.Version}}"]
        )

        #expect(result.standardOutput == Data("1.4.1\n".utf8))
        #expect(transport.requests.map(\.target) == ["/version"])
    }

    @Test
    func `rejects formatter semantics it does not implement`() {
        #expect(throws: DockerCLIError.self) {
            try DockerCLIApplication(transport: StubTransport([])).run(
                arguments: ["version", "--format", "{{json .}}"]
            )
        }
    }

    @Test
    func `aggregates typed inspect responses into a Docker array`() throws {
        let transport = StubTransport([
            .json(["Id": "first"]),
            .json(["Id": "second"])
        ])
        let result = try DockerCLIApplication(transport: transport).run(
            arguments: ["inspect", "--type", "container", "first", "second"]
        )
        let objects = try #require(
            JSONSerialization.jsonObject(with: result.standardOutput) as? [[String: String]]
        )

        #expect(objects == [["Id": "first"], ["Id": "second"]])
        #expect(
            transport.requests.map(\.target) == [
                "/containers/first/json",
                "/containers/second/json"
            ]
        )
    }

    @Test
    func `encodes label filters and prints only matching IDs`() throws {
        let transport = StubTransport([
            .json([["Id": "one"], ["Id": "two"]])
        ])
        let result = try DockerCLIApplication(transport: transport).run(
            arguments: ["ps", "-a", "-q", "--filter", "label=devcontainer.local_folder=/work"]
        )

        #expect(result.standardOutput == Data("one\ntwo\n".utf8))
        let target = try #require(transport.requests.first?.target)
        #expect(target.hasPrefix("/containers/json?"))
        #expect(target.contains("all=true"))
        #expect(target.contains("filters="))
    }

    @Test
    func `accepts combined all and quiet ps flags`() throws {
        let transport = StubTransport([.json([["Id": "one"]])])

        let result = try DockerCLIApplication(transport: transport).run(
            arguments: ["ps", "-aq"]
        )

        #expect(result.standardOutput == Data("one\n".utf8))
        #expect(try #require(transport.requests.first?.target).contains("all=true"))
    }

    @Test
    func `prints a Docker shaped container table when quiet is omitted`() throws {
        let transport = StubTransport([
            .json([
                [
                    "Id": "1234567890abcdef",
                    "Image": "example:latest",
                    "Command": "/bin/sh",
                    "Created": 123,
                    "Status": "Up 1 minute",
                    "Ports": [["PrivatePort": 8080, "Type": "tcp"]],
                    "Names": ["/workspace"]
                ]
            ])
        ])

        let result = try DockerCLIApplication(transport: transport).run(
            arguments: ["ps", "--all"]
        )
        let output = try #require(String(data: result.standardOutput, encoding: .utf8))

        #expect(output.hasPrefix("CONTAINER ID\tIMAGE\tCOMMAND\tCREATED\tSTATUS\tPORTS\tNAMES\n"))
        #expect(
            output.contains(
                "1234567890ab\texample:latest\t/bin/sh\t123\tUp 1 minute\t8080/tcp\tworkspace"
            )
        )
    }

    @Test
    func `rejects unsupported list filters instead of broadening the result`() {
        #expect(throws: DockerCLIError.self) {
            try DockerCLIApplication(transport: StubTransport([])).run(
                arguments: ["ps", "-q", "--filter", "status=running"]
            )
        }
    }

    @Test
    func `rejects build cache sources that Apple container cannot honor`() {
        #expect(throws: DockerCLIError.unsupported("build --cache-from")) {
            try DockerCLIApplication(transport: StubTransport([])).run(
                arguments: ["build", "--cache-from", "example/cache", "."]
            )
        }
    }

    @Test
    func `exec demultiplexes output and returns the remote exit code`() throws {
        let outputFrame = frame(channel: 1, text: "out")
        let errorFrame = frame(channel: 2, text: "err")
        let transport = StubTransport([
            .json(["Id": "exec-1"], status: 201),
            .init(status: 101, body: outputFrame + errorFrame),
            .json(["ExitCode": 7])
        ])
        let result = try DockerCLIApplication(transport: transport).run(
            arguments: ["exec", "-i", "-u", "vscode", "-e", "A=B", "box", "/bin/false"],
            standardInput: Data("input\n".utf8)
        )

        #expect(result.standardOutput == Data("out".utf8))
        #expect(result.standardError == Data("err".utf8))
        #expect(result.exitCode == 7)
        #expect(transport.hijackInput == Data("input\n".utf8))
        #expect(
            transport.requests.map(\.target) == [
                "/containers/box/exec",
                "/exec/exec-1/start",
                "/exec/exec-1/json"
            ]
        )
    }

    @Test
    func `run maps mounts, ports, labels, environment, and entrypoint`() throws {
        let transport = StubTransport([
            .json(["Id": "container-1"], status: 201),
            .init(status: 204)
        ])
        let result = try DockerCLIApplication(transport: transport).run(arguments: [
            "run", "--detach", "--name", "sample", "-e", "A=B", "-l", "x=y",
            "-p", "127.0.0.1:8000:80", "--mount", "type=bind,source=/host,target=/work",
            "--entrypoint", "/bin/sh", "image:tag", "-c", "true"
        ])
        let request = try #require(transport.requests.first)
        let body = try #require(JSONSerialization.jsonObject(with: request.body) as? [String: Any])

        #expect(result.standardOutput == Data("container-1\n".utf8))
        #expect(request.target == "/containers/create?name=sample")
        #expect(body["Image"] as? String == "image:tag")
        #expect(body["Cmd"] as? [String] == ["-c", "true"])
        #expect(transport.requests.last?.target == "/containers/container-1/start")
    }

    @Test
    func `supports the upstream discovery and lifecycle command shapes`() throws {
        let transport = StubTransport([
            .json(["Version": "1.4.1"]),
            .json(["Driver": "apple-container"]),
            .json(["Name": "cache"]),
            .json([]),
            .init(status: 204),
            .init(status: 204),
            .init(status: 204),
            .init(status: 204),
            .init(status: 200, body: Data("{\"status\":\"done\"}\n".utf8)),
            .init(status: 201),
            .init(status: 200, body: Data("{\"Type\":\"container\"}\n".utf8))
        ])
        let application = DockerCLIApplication(transport: transport)

        #expect(try application.run(arguments: ["--host=unix:///ignored", "version"])
            .standardOutput.contains(Data("1.4.1".utf8)))
        #expect(try application.run(arguments: ["-H", "unix:///ignored", "info"])
            .standardOutput.contains(Data("apple-container".utf8)))
        #expect(try application.run(arguments: ["inspect", "--type", "volume", "cache"])
            .standardOutput.contains(Data("cache".utf8)))
        #expect(try application.run(arguments: ["ps", "--format", "{{.ID}}"])
            .standardOutput.isEmpty)
        #expect(try application.run(arguments: ["rm", "-f", "first", "second"])
            .standardOutput == Data("first\nsecond\n".utf8))
        #expect(try application.run(arguments: ["start", "first"]).exitCode == 0)
        #expect(try application.run(arguments: ["stop", "--time", "3", "first"]).exitCode == 0)
        #expect(try application.run(arguments: ["pull", "registry.example/a/b@sha256:abc"])
            .standardOutput.contains(Data("done".utf8)))
        #expect(try application.run(arguments: ["tag", "source", "registry.example:5000/a"])
            .exitCode == 0)
        #expect(try application.run(
            arguments: ["events", "--format", "{{json .}}", "--filter", "type=container"]
        ).standardOutput.contains(Data("container".utf8)))

        let buildx = try application.run(arguments: ["buildx", "version"])
        #expect(buildx.exitCode == 1)
        #expect(buildx.standardError.contains(Data("classic build path".utf8)))
    }

    @Test
    func `supports volume inspect and removal used by parity cleanup`() throws {
        let transport = StubTransport([
            .json(["Name": "cache"]),
            .init(status: 204),
            .init(status: 204)
        ])
        let application = DockerCLIApplication(transport: transport)

        #expect(try application.run(arguments: ["volume", "inspect", "cache"])
            .standardOutput.contains(Data("cache".utf8)))
        #expect(try application.run(arguments: ["volume", "rm", "cache"])
            .standardOutput == Data("cache\n".utf8))
        #expect(try application.run(arguments: ["volume", "remove", "--force", "other"])
            .standardOutput == Data("other\n".utf8))
        #expect(transport.requests.map(\.target) == [
            "/volumes/cache",
            "/volumes/cache",
            "/volumes/other?force=true"
        ])
    }

    @Test
    func `supports direct engine lifecycle image and network probes`() throws {
        let transport = StubTransport([
            .json(["Id": "container-1"], status: 201),
            .json(["State": ["Status": "created", "ExitCode": 7]]),
            .init(status: 204),
            .init(status: 204),
            .json(["StatusCode": 7]),
            .json(["Config": ["Labels": ["devcontainer.parity": "true"]]]),
            .json([["Deleted": "image:latest"]]),
            .json(["Id": "network-1"], status: 201),
            .json(["Name": "parity-network"]),
            .init(status: 204),
            .json(["Name": "parity-volume"], status: 201)
        ])
        let application = DockerCLIApplication(transport: transport)

        #expect(try application.run(arguments: ["create", "image", "true"])
            .standardOutput == Data("container-1\n".utf8))
        #expect(try application.run(arguments: ["inspect", "-f", "{{.State.Status}}", "container-1"])
            .standardOutput == Data("created\n".utf8))
        #expect(try application.run(arguments: ["restart", "container-1"]).exitCode == 0)
        #expect(try application.run(arguments: ["kill", "--signal", "TERM", "container-1"])
            .exitCode == 0)
        #expect(try application.run(arguments: ["wait", "container-1"])
            .standardOutput == Data("7\n".utf8))
        #expect(try application.run(arguments: [
            "image", "inspect", "-f", "{{index .Config.Labels \"devcontainer.parity\"}}",
            "image:latest"
        ]).standardOutput == Data("true\n".utf8))
        #expect(try application.run(arguments: ["image", "rm", "-f", "image:latest"])
            .standardOutput == Data("image:latest\n".utf8))
        #expect(try application.run(arguments: ["network", "create", "parity-network"])
            .standardOutput == Data("network-1\n".utf8))
        #expect(try application.run(arguments: ["network", "inspect", "parity-network"])
            .standardOutput.contains(Data("parity-network".utf8)))
        #expect(try application.run(arguments: ["network", "rm", "parity-network"])
            .standardOutput == Data("parity-network\n".utf8))
        #expect(try application.run(arguments: ["volume", "create", "parity-volume"])
            .standardOutput == Data("parity-volume\n".utf8))

        #expect(transport.requests.map(\.target) == [
            "/containers/create", "/containers/container-1/json",
            "/containers/container-1/restart", "/containers/container-1/kill?signal=TERM",
            "/containers/container-1/wait", "/images/image:latest/json",
            "/images/image:latest?force=true", "/networks/create",
            "/networks/parity-network", "/networks/parity-network", "/volumes/create"
        ])
    }

    @Test
    func `copies archives through the engine without Docker`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-cp-\(UUID().uuidString)")
        let source = root.appendingPathComponent("source")
        let destination = root.appendingPathComponent("destination")
        try FileManager.default.createDirectory(at: source, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("archive-content\n".utf8).write(to: source.appendingPathComponent("value.txt"))
        let archive = try ProcessRunner.capturedSync(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: ["-cf", "-", "-C", source.path, "."],
            environment: ["PATH": "/usr/bin:/bin"]
        ).standardOutput
        let transport = StubTransport([
            .init(status: 200),
            .init(status: 200, body: archive)
        ])
        let application = DockerCLIApplication(transport: transport)

        #expect(try application.run(arguments: ["cp", source.path + "/.", "box:/archive"])
            .exitCode == 0)
        #expect(try application.run(arguments: ["cp", "box:/archive", destination.path])
            .exitCode == 0)
        #expect(try String(
            contentsOf: destination.appendingPathComponent("value.txt"),
            encoding: .utf8
        ) == "archive-content\n")
        #expect(transport.requests[0].target == "/containers/box/archive?path=%2Farchive")
        #expect(!transport.requests[0].body.isEmpty)
        #expect(transport.requests[1].target == "/containers/box/archive?path=%2Farchive")
    }

    @Test
    func `maps the complete supported build and run option sets`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-options-\(UUID().uuidString)")
        let external = root.deletingLastPathComponent()
            .appendingPathComponent("External-\(UUID().uuidString).Dockerfile")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        try Data("FROM scratch\n".utf8).write(to: external)
        defer {
            try? FileManager.default.removeItem(at: root)
            try? FileManager.default.removeItem(at: external)
        }

        let build = try DockerBuildOptions(arguments: [
            "--file", external.path, "--tag", "one:latest", "-t", "two:latest",
            "--target", "development", "--build-arg", "A=B", "--label", "x=y",
            "--platform", "linux/arm64", "--progress", "plain", "--no-cache", "--pull",
            root.path
        ])
        #expect(build.tags == ["one:latest", "two:latest"])
        #expect(build.target == "development")
        #expect(build.buildArguments == ["A": "B"])
        #expect(build.labels == ["x": "y"])
        #expect(build.platform == "linux/arm64")
        let archive = try build.archive()
        #expect(!archive.isEmpty)
        let query = try build.query()
        #expect(query.contains { $0 == ("dockerfile", external.lastPathComponent) })

        let inline = try DockerBuildOptions(arguments: [
            "--build-arg=C=D", "--label=z=w", "--platform=linux/amd64",
            "--progress=plain", root.path
        ])
        #expect(inline.buildArguments == ["C": "D"])
        #expect(inline.labels == ["z": "w"])

        let run = try DockerRunOptions(arguments: [
            "-d", "--init", "--privileged", "--name", "box", "--attach", "stdout",
            "--env", "A=B", "--label", "x=y", "--user", "1000", "--publish", "8080:80",
            "--mount", "type=volume,src=cache,dst=/cache,ro,consistency=cached",
            "--cap-add", "SYS_PTRACE", "--security-opt", "seccomp=unconfined",
            "--sig-proxy=false", "image", "command"
        ])
        let request = run.createRequest
        #expect(request["User"] as? String == "1000")
        #expect((request["HostConfig"] as? [String: Any])?["Privileged"] as? Bool == true)

        let exec = try DockerExecOptions(arguments: [
            "--interactive", "--tty", "--user", "vscode", "--workdir", "/work",
            "--env", "A=B", "box", "sh", "-lc", "true"
        ])
        #expect(exec.createRequest["WorkingDir"] as? String == "/work")
        #expect(exec.createRequest["Tty"] as? Bool == true)
    }

    @Test
    func `maps network aliases and automatic removal`() throws {
        let request = try DockerRunOptions(arguments: [
            "--rm", "--network", "parity", "--network-alias", "app", "image"
        ]).createRequest

        #expect((request["HostConfig"] as? [String: Any])?["AutoRemove"] as? Bool == true)
        let endpoints = (request["NetworkingConfig"] as? [String: Any])?["EndpointsConfig"]
            as? [String: [String: [String]]]
        #expect(endpoints?["parity"]?["Aliases"] == ["app"])
    }

    @Test
    func `preserves published UDP protocol and defaults to TCP`() throws {
        let request = try DockerRunOptions(arguments: [
            "--publish", "8080:53/udp", "--publish", "127.0.0.1:8443:443", "image"
        ]).createRequest
        let bindings = (request["HostConfig"] as? [String: Any])?["PortBindings"]
            as? [String: [[String: String]]]

        #expect(bindings?["53/udp"] == [["HostIp": "0.0.0.0", "HostPort": "8080"]])
        #expect(bindings?["443/tcp"] == [["HostIp": "127.0.0.1", "HostPort": "8443"]])
    }

    @Test
    func `maps native Compose creation options onto the Engine request`() throws {
        let transport = StubTransport([.json(["Id": "compose-container"], status: 201)])
        let application = DockerCLIApplication(transport: transport)

        _ = try application.run(arguments: [
            "create", "--name", "compose-app", "--platform", "linux/arm64",
            "--workdir", "/workspace", "--hostname", "app", "--domainname", "example.test",
            "--interactive", "--tty", "--stop-signal", "SIGTERM", "--stop-timeout", "12",
            "--health-cmd", "test -f /tmp/ready", "--health-interval", "1.5s",
            "--health-timeout", "750ms", "--health-start-period", "2s",
            "--health-retries", "20", "alpine:3.22"
        ])

        let request = try #require(transport.requests.first)
        #expect(request.target == "/containers/create?name=compose-app&platform=linux%2Farm64")
        let body = try #require(
            JSONSerialization.jsonObject(with: request.body) as? [String: Any]
        )
        #expect(body["WorkingDir"] as? String == "/workspace")
        #expect(body["Hostname"] as? String == "app")
        #expect(body["Domainname"] as? String == "example.test")
        #expect(body["OpenStdin"] as? Bool == true)
        #expect(body["Tty"] as? Bool == true)
        #expect(body["StopSignal"] as? String == "SIGTERM")
        #expect(body["StopTimeout"] as? Int == 12)
        let health = try #require(body["Healthcheck"] as? [String: Any])
        #expect(health["Test"] as? [String] == ["CMD-SHELL", "test -f /tmp/ready"])
        #expect(health["Interval"] as? Int == 1_500_000_000)
        #expect(health["Timeout"] as? Int == 750_000_000)
        #expect(health["StartPeriod"] as? Int == 2_000_000_000)
        #expect(health["Retries"] as? Int == 20)
    }

    @Test
    func `streams foreground run build and terminal exec output`() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("docker-stream-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        try Data("FROM scratch\n".utf8).write(to: root.appendingPathComponent("Dockerfile"))
        defer { try? FileManager.default.removeItem(at: root) }

        let transport = StubTransport([
            .json(["Id": "foreground"], status: 201),
            .init(status: 204),
            .init(status: 200, body: frame(channel: 1, text: "logs")),
            .json(["StatusCode": 17]),
            .init(status: 200, body: Data("build-output".utf8)),
            .json(["Id": "terminal-exec"], status: 201),
            .init(status: 101, body: Data("terminal-output".utf8)),
            .json(["ExitCode": 0])
        ])
        let application = DockerCLIApplication(transport: transport)
        let foreground = try application.run(arguments: ["run", "image", "true"])
        #expect(foreground.standardOutput == Data("logs".utf8))
        #expect(foreground.exitCode == 17)

        var streamed = Data()
        let build = try application.run(arguments: ["build", root.path]) { data, _ in
            streamed.append(data)
        }
        #expect(build.standardOutput.isEmpty)
        #expect(streamed == Data("build-output".utf8))

        let terminal = try application.run(arguments: ["exec", "-t", "box", "printf", "ok"])
        #expect(terminal.standardOutput == Data("terminal-output".utf8))
    }

    @Test
    // swiftlint:disable:next function_body_length
    func `rejects incomplete and unsupported adapter options`() {
        let application = DockerCLIApplication(transport: StubTransport([]))
        let invalid: [[String]] = [
            [], ["--host"], ["version", "extra"], ["info", "--format", "{{json .}}"],
            ["inspect", "--type", "network", "n"], ["ps", "--filter"],
            ["ps", "--format"], ["ps", "--help"], ["rm", "-f"], ["start"],
            ["stop", "--time"], ["start", "--unknown", "box"], ["pull"],
            ["tag", "only-one"], ["events", "--format"], ["events", "--filter"],
            ["events", "--filter", "invalid"], ["events", "--unknown"],
            ["buildx", "build"], ["volume"], ["volume", "ls"],
            ["volume", "rm"], ["volume", "rm", "--unknown", "cache"]
        ]
        for arguments in invalid {
            #expect(throws: (any Error).self) {
                try application.run(arguments: arguments)
            }
        }

        let invalidBuildOptions = [
            ["--file"], ["--build-arg", "invalid", "."], ["--label=invalid", "."],
            ["--progress", "tty", "."], ["--progress=tty", "."], ["--security-opt=x", "."],
            ["--unknown", "."], [".", "second"]
        ]
        for arguments in invalidBuildOptions {
            #expect(throws: (any Error).self) { try DockerBuildOptions(arguments: arguments) }
        }
        #expect(throws: (any Error).self) {
            try DockerBuildOptions(arguments: ["/definitely/missing"]).archive()
        }
        #expect(throws: (any Error).self) { try DockerRunOptions(arguments: []) }
        #expect(throws: (any Error).self) { try DockerRunOptions(arguments: ["--mount", "type=bind", "image"]) }
        #expect(throws: (any Error).self) { try DockerRunOptions(arguments: ["-p", "1:2:3:4", "image"]) }
        #expect(throws: (any Error).self) {
            try DockerRunOptions(arguments: ["-p", "8080:53/sctp", "image"])
        }
        #expect(throws: (any Error).self) {
            try DockerRunOptions(arguments: ["--stop-timeout", "-1", "image"])
        }
        #expect(throws: (any Error).self) {
            try DockerRunOptions(arguments: ["--health-interval", "", "image"])
        }
        #expect(throws: (any Error).self) {
            try DockerRunOptions(arguments: ["--health-timeout", "one-second", "image"])
        }
        #expect(throws: (any Error).self) {
            try DockerRunOptions(arguments: ["--health-retries", "0", "image"])
        }
        #expect(throws: DockerCLIError.unsupported("run --health-start-interval")) {
            try DockerRunOptions(arguments: ["--health-start-interval", "1s", "image"])
        }
        #expect(throws: (any Error).self) { try DockerRunOptions(arguments: ["--unknown", "image"]) }
        #expect(throws: (any Error).self) { try DockerExecOptions(arguments: ["--unknown"]) }
        #expect(throws: (any Error).self) { try DockerExecOptions(arguments: ["box"]) }
    }

    @Test
    func `unsupported commands fail closed`() {
        #expect(throws: DockerCLIError.unsupported("system")) {
            try DockerCLIApplication(transport: StubTransport([])).run(arguments: ["system", "prune"])
        }
    }

    private func frame(channel: UInt8, text: String) -> Data {
        let payload = Data(text.utf8)
        var result = Data([channel, 0, 0, 0])
        var length = UInt32(payload.count).bigEndian
        withUnsafeBytes(of: &length) { result.append(contentsOf: $0) }
        result.append(payload)
        return result
    }

    private func archiveEntries(_ archive: Data) throws -> Set<String> {
        let result = try ProcessRunner.capturedSync(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: ["-tf", "-"],
            environment: ["PATH": "/usr/bin:/bin"],
            input: archive
        )
        #expect(result.exitCode == 0)
        let output = try #require(
            String(data: result.standardOutput, encoding: .utf8)
        )
        return Set(output.split(whereSeparator: \.isNewline).map(String.init))
    }
}

private final class StubTransport: DockerEngineHijackTransport, @unchecked Sendable {
    struct StubResponse {
        var status: Int
        var headers: [String: String]
        var body: Data

        init(status: Int, headers: [String: String] = [:], body: Data = Data()) {
            self.status = status
            self.headers = headers
            self.body = body
        }

        static func json(_ object: Any, status: Int = 200) -> StubResponse {
            guard
                let body = try? JSONSerialization.data(
                    withJSONObject: object,
                    options: [.sortedKeys]
                )
            else {
                fatalError("Stub JSON must be encodable")
            }
            return StubResponse(
                status: status,
                headers: ["content-type": "application/json"],
                body: body
            )
        }
    }

    private let lock = NSLock()
    private var responses: [StubResponse]
    private var recordedRequests: [DockerHTTPRequest] = []
    private var recordedHijackInput: Data?

    init(_ responses: [StubResponse]) {
        self.responses = responses
    }

    var requests: [DockerHTTPRequest] {
        lock.withLock { recordedRequests }
    }

    var hijackInput: Data? {
        lock.withLock { recordedHijackInput }
    }

    func send(
        _ request: DockerHTTPRequest,
        maximumBodyBytes _: Int?,
        onBody: @escaping (Data) throws -> Void
    ) throws -> DockerHTTPResponse {
        try respond(to: request, onBody: onBody)
    }

    func hijack(
        _ request: DockerHTTPRequest,
        input: Data?,
        inputFileDescriptor _: Int32?,
        maximumBodyBytes _: Int?,
        onBody: @escaping (Data) throws -> Void
    ) throws -> DockerHTTPResponse {
        lock.withLock { recordedHijackInput = input }
        return try respond(to: request, onBody: onBody)
    }

    private func respond(
        to request: DockerHTTPRequest,
        onBody: (Data) throws -> Void
    ) throws -> DockerHTTPResponse {
        let response = try lock.withLock { () throws -> StubResponse in
            recordedRequests.append(request)
            guard !responses.isEmpty else {
                throw DockerHTTPClientError.invalidResponse("no stub response")
            }
            return responses.removeFirst()
        }
        try onBody(response.body)
        return DockerHTTPResponse(status: response.status, headers: response.headers, body: Data())
    }
}
