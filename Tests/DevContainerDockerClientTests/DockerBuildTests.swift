// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineWire
import Darwin
@testable import DevContainerDockerClient
import DevContainerProcess
import DevContainerTestStorage
import Foundation
import Testing

struct DockerBuildTests {
    @Test
    func `captured upstream build command preserves explicit arguments and target`() throws {
        let spec = try build([
            "-f",
            "/external/Dockerfile-with-features",
            "-t",
            "vsc-test",
            "--target",
            "dev_containers_target_stage",
            "--build-arg",
            "PARITY_BUILD_ARG=from-devcontainer",
            "--build-arg",
            "_DEV_CONTAINERS_BASE_IMAGE=development",
            "/workspace"
        ])
        #expect(spec.context == "/workspace")
        #expect(spec.dockerfile == "/external/Dockerfile-with-features")
        #expect(spec.tags == ["vsc-test"])
        #expect(spec.target == "dev_containers_target_stage")
        #expect(spec.arguments == [
            "PARITY_BUILD_ARG": "from-devcontainer",
            "_DEV_CONTAINERS_BASE_IMAGE": "development"
        ])
        let options = try build([
            "--file=a",
            "--tag=x",
            "-t",
            "y",
            "--target=dev",
            "--build-arg=X=a=b&c",
            "--build-arg=EMPTY=",
            "--build-arg=X=last",
            "--",
            "context"
        ])
        #expect(options.arguments == ["X": "last", "EMPTY": ""])
        let request = try options.request(archive: .init(data: Data([1, 2]), dockerfile: "d/f?"))
        #expect(request.method == .post)
        #expect(request.body == Data([1, 2]))
        #expect(try request.headers.uniqueValue(for: "Content-Type") == "application/x-tar")
        #expect(request
            .target ==
            "/build?dockerfile=d%2Ff%3F&buildargs=%7B%22EMPTY%22%3A%22%22%2C%22X%22%3A%22last%22%7D&t=x&t=y&target=dev")
        #expect(try build(["."]).dockerfile == nil)
    }

    @Test(arguments: [
        [String](), ["-"], ["https://example.org/repo"], ["one", "two"], ["--", "one", "two"],
        ["--"], ["-f"], ["-f", "a", "--file=b", "."], ["--target=a", "--target=b", "."],
        ["--pull", "."], ["--build-arg", "FROM_ENV", "."], ["--build-arg==x", "."],
        ["--build-arg=X=a\0", "."], ["--file=a\0", "."], ["--tag=", "."], [""]
    ])
    func `unsupported build inputs fail before archiving or connecting`(_ args: [String]) {
        #expect(throws: DockerFrontendError.self) { try build(args) }
    }

    @Test
    func `real archive includes external Dockerfile and preserves context file bytes`() async throws {
        let root = try scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        let context = root.appendingPathComponent("context")
        try FileManager.default.createDirectory(at: context, withIntermediateDirectories: false)
        try Data("context\n".utf8).write(to: context.appendingPathComponent("payload"))
        let external = root.appendingPathComponent("generated.Dockerfile")
        let dockerfile = Data("FROM scratch\nCOPY payload /payload\n".utf8)
        try dockerfile.write(to: external)
        let archive = try await DockerBuildArchive.prepare(build(["-f", external.path, context.path]))
        #expect(archive.dockerfile.hasPrefix(".devcontainer-build-"))
        #expect(try await tar(archive.data, ["-xOf", "-", archive.dockerfile]) == dockerfile)
        #expect(try await tar(archive.data, ["-xOf", "-", ".dockerignore"]) ==
            Data((archive.dockerfile + "\n.dockerignore\n").utf8))
        #expect(try await tar(archive.data, ["-xOf", "-", "./payload"]) == Data("context\n".utf8))
        #expect(try FileManager.default.contentsOfDirectory(atPath: root.path).sorted() == [
            "context",
            "generated.Dockerfile"
        ])
        #expect(!FileManager.default
            .fileExists(atPath: TestStorage.temporaryDirectory.appendingPathComponent(archive.dockerfile).path))
        await #expect(throws: DockerFrontendError.self) {
            try await DockerBuildArchive.create(build(["-f", external.path, context.path]), maximumArchiveBytes: 512)
        }
    }

    @Test
    func `default Dockerfile and ignore files do not silently change semantics`() async throws {
        let root = try scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        let dockerfile = root.appendingPathComponent("Dockerfile")
        try Data("FROM scratch\n".utf8).write(to: dockerfile)
        let archive = try await DockerBuildArchive.prepare(build([root.path]))
        #expect(archive.dockerfile == "Dockerfile")
        #expect(try await tar(archive.data, ["-tf", "-"]) == Data("./\n./Dockerfile\n".utf8))
        for name in [".dockerignore", "Dockerfile.dockerignore"] {
            let ignore = root.appendingPathComponent(name)
            try Data("secret\n".utf8).write(to: ignore)
            await #expect(throws: DockerFrontendError.self) {
                try await DockerBuildArchive.prepare(build([root.path]))
            }
            try FileManager.default.removeItem(at: ignore)
        }
        try FileManager.default.removeItem(at: dockerfile)
        #expect(mkfifo(dockerfile.path, 0o600) == 0)
        await #expect(throws: DockerFrontendError.self) { try await DockerBuildArchive.prepare(build([root.path])) }
        try FileManager.default.removeItem(at: dockerfile)
        try Data().write(to: dockerfile)
        await #expect(throws: DockerFrontendError.self) { try await DockerBuildArchive.prepare(build([root.path])) }
        await #expect(throws: DockerFrontendError.self) {
            try await DockerBuildArchive.prepare(build([dockerfile.path]))
        }
        try Data(repeating: 32, count: 1024 * 1024 + 1).write(to: dockerfile)
        await #expect(throws: DockerFrontendError.self) { try await DockerBuildArchive.prepare(build([root.path])) }
    }

    @Test
    func `staging containment resolves aliases and root ancestry`() throws {
        let root = try scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        let alias = root.appendingPathComponent("alias")
        try FileManager.default.createSymbolicLink(at: alias, withDestinationURL: root)
        for context in [root, alias, URL(fileURLWithPath: "/")] {
            #expect(throws: DockerFrontendError.self) {
                try DockerBuildArchive.requireSeparateStage(alias.appendingPathComponent("stage"), context: context)
            }
        }
        try DockerBuildArchive.requireSeparateStage(
            root.appendingPathComponent("stage"),
            context: root.appendingPathComponent("context")
        )
    }

    @Test
    func `interrupting executable archiving joins its child and removes staged Dockerfile`() async throws {
        let root = try scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        let files = FileManager.default
        let fixture = try interruptFixture(root)
        let temporary = fixture.temporary
        let process = ProcessCommand(
            FrontendExecutable.executable.path,
            arguments: ["build", "-f", fixture.dockerfile.path, fixture.context.path],
            environment: [
                "PATH=/no-docker",
                "TMPDIR=" + temporary.path,
                "DEVCONTAINER_CONFIG=/no-config"
            ],
            directory: nil
        )
        process.attributes.setProcessGroup = true
        try process.start()
        let termination = OwnedProcessTermination()
        termination.didLaunch(processGroup: process.pid)
        let timeout = Task {
            do { try await Task.sleep(for: .seconds(5)); termination.cancel() } catch {
            /* Normal completion cancels watchdog. */ }
        }
        let end = ContinuousClock.now.advanced(by: .seconds(3))
        var sawStage = false
        while ContinuousClock.now < end {
            if (try? files.contentsOfDirectory(atPath: temporary.path).isEmpty) == false {
                sawStage = true
                break
            }
            try? await Task.sleep(for: .milliseconds(1))
        }
        #expect(kill(process.pid, SIGTERM) == 0)
        // Keep the PID unreaped until the watchdog has relinquished ownership.
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            DispatchQueue.global().async {
                _ = try? process.waitUntilExit()
                continuation.resume()
            }
        }
        timeout.cancel()
        termination.didExit()
        let status = try process.wait()
        #expect(status == 1)
        #expect(sawStage)
        let residue = try files.contentsOfDirectory(atPath: temporary.path)
        #expect(residue.isEmpty)
    }

    @Test
    func `build progress preserves content across every byte split`() throws {
        let input = Data(
            ("{\"stream\":\"Step 1\\n\"}\n"
                + "{\"status\":\"building\",\"id\":\"one\",\"progress\":\"50%\"}\n"
                + "{\"aux\":{\"ID\":\"sha256:x\"}}").utf8
        )
        for offset in 0 ... input.count {
            let lines = DockerBuildLines()
            var output = Data()
            try lines.consume(Data(input.prefix(offset))) { output.append($0) }
            try lines.consume(Data(input.dropFirst(offset))) { output.append($0) }
            if let tail = try lines.finish() {
                output.append(tail)
            }
            #expect(output == Data("Step 1\none: building 50%\n".utf8))
        }
    }

    @Test(arguments: [
        "",
        "[]\n",
        "{\"stream\":4}\n",
        "{}\n",
        "{\"error\":\"failed\"}\n",
        "{\"errorDetail\":{\"message\":\"failed\"}}",
        "{\"errorDetail\":false}"
    ])
    func `HTTP success never hides invalid or failed build records`(_ input: String) {
        let lines = DockerBuildLines()
        #expect(throws: DockerFrontendError.self) {
            try lines.consume(Data(input.utf8)) { _ in Issue.record("failed build emitted as success") }
            _ = try lines.finish()
        }
    }

    @Test
    func `build deadline cancels and joins a blocked progress writer`() async throws {
        let pipe = Pipe()
        defer { try? pipe.fileHandleForWriting.close(); try? pipe.fileHandleForReading.close() }
        let writer = try DockerFrontendOutput(descriptor: pipe.fileHandleForWriting.fileDescriptor)
        let start = ContinuousClock.now
        await #expect(throws: DockerFrontendError.self) {
            try await DockerFrontend(version: "test", executionTimeout: .milliseconds(60)).executeBuild(
                build(["."]), archive: .init(data: Data([1]), dockerfile: "Dockerfile"),
                transport: BuildTestTransport(), output: writer
            )
        }
        #expect(start.duration(to: .now) < .seconds(2))
    }

    private func build(_ arguments: [String]) throws -> DockerBuildCommand {
        guard case let .build(spec) = try DockerFrontendCommand.parse(["build"] + arguments) else {
            throw DockerFrontendError.usage("not build")
        }
        return spec
    }

    private func scratch() throws -> URL {
        let root = TestStorage.temporaryDirectory.appendingPathComponent("db-\(UUID().uuidString.prefix(8))")
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700]
        )
        return root
    }

    private struct InterruptFixture {
        let temporary: URL
        let context: URL
        let dockerfile: URL
    }

    private func interruptFixture(_ root: URL) throws -> InterruptFixture {
        let temporary = root.appendingPathComponent("temporary")
        let context = root.appendingPathComponent("context")
        for directory in [temporary, context] {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
        }
        let dockerfile = root.appendingPathComponent("external.Dockerfile")
        try Data("FROM scratch\n".utf8).write(to: dockerfile)
        let payload = context.appendingPathComponent("sparse-payload")
        try Data().write(to: payload)
        let handle = try FileHandle(forWritingTo: payload)
        try handle.truncate(atOffset: 1024 * 1024 * 1024)
        try handle.close()
        return InterruptFixture(temporary: temporary, context: context, dockerfile: dockerfile)
    }

    private func tar(_ data: Data, _ arguments: [String]) async throws -> Data {
        let result = try await ProcessRunner.captured(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: arguments,
            environment: ["PATH": "/usr/bin:/bin"],
            input: data,
            maximumOutputBytes: 65536
        )
        #expect(result.exitCode == 0)
        return result.standardOutput
    }
}

private struct BuildTestTransport: DockerFrontendBuildTransport {
    func build(_ request: DockerHTTPRequest, onBody: @escaping @Sendable (Data) throws -> Void) async throws {
        #expect(request.method == .post)
        let chunk = Data(("{\"stream\":\"" + String(repeating: "x", count: 60000) + "\"}\n").utf8)
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, any Error>) in
            DispatchQueue.global().async {
                continuation.resume(with: Result { for _ in 0 ..< 20 {
                    try onBody(chunk)
                } })
            }
        }
    }
}
