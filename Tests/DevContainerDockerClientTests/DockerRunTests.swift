// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineWire
@testable import DevContainerDockerClient
import Foundation
import Testing

struct DockerRunTests {
    @Test(arguments: ["-u", "--user", "--user="])
    func `run preserves explicit non-root user in create request`(_ option: String) throws {
        let arguments = option.hasSuffix("=") ? [option + "vscode"] : [option, "vscode"]
        let spec = try command(["--sig-proxy=false"] + arguments + ["image"])
        let body = try #require(try JSONSerialization.jsonObject(with: spec.createBody()) as? [String: Any])
        #expect(body["User"] as? String == "vscode")
    }

    @Test
    func `run deadline still covers terminal wait after early output EOF`() async throws {
        let transport = RunTransport(waitDelay: .milliseconds(250))
        let start = ContinuousClock.now
        await #expect(throws: DockerFrontendError.self) {
            try await DockerFrontend(version: "test", executionTimeout: .milliseconds(40)).executeRun(
                command(["--sig-proxy=false", "image"]), transport: transport,
                output: { _ in /* Output ends immediately. */ }, warning: { _ in /* No-op fixture. */ }
            )
        }
        #expect(start.duration(to: .now) < .milliseconds(200))
        #expect(transport.connection.closed)
    }

    @Test
    func `run parses the recorded image configuration startup without losing settings`() throws {
        let spec = try command([
            "--sig-proxy=false", "-a", "STDOUT", "-a", "STDERR",
            "--mount", "source=/tmp/work folder,target=/workspaces/test,type=bind",
            "-l", "owner=d01", "--label=metadata={\"id\":1}", "-e", "VALUE=x=y",
            "--env=EMPTY=", "--entrypoint", "/bin/sh", "image:tag", "-c", "echo ready\nexec sleep 100"
        ])
        let body = try #require(try JSONSerialization.jsonObject(with: spec.createBody()) as? [String: Any])
        #expect(body["Image"] as? String == "image:tag")
        #expect(body["Cmd"] as? [String] == ["-c", "echo ready\nexec sleep 100"])
        #expect(body["Env"] as? [String] == ["VALUE=x=y", "EMPTY="])
        #expect(body["Entrypoint"] as? [String] == ["/bin/sh"])
        #expect(body["Labels"] as? [String: String] == ["owner": "d01", "metadata": #"{"id":1}"#])
        #expect(body["OpenStdin"] as? Bool == false)
        #expect(body["Tty"] as? Bool == false)
        #expect(spec.mounts == [.init(source: "/tmp/work folder", target: "/workspaces/test", readOnly: false)])
        let host = try #require(body["HostConfig"] as? [String: Any])
        let mount = try #require((host["Mounts"] as? [[String: Any]])?.first)
        #expect(mount["Source"] as? String == "/tmp/work folder")
        #expect(mount["Type"] as? String == "bind")
        #expect(mount["ReadOnly"] as? Bool == false)
        #expect(spec.standardOutput && spec.standardError)
    }

    @Test
    func `run aliases preserve empty command arguments and explicit attachment selection`() throws {
        let spec = try command([
            "--sig-proxy", "false", "--attach=stderr", "--entrypoint=/bin/echo",
            "--mount=type=bind,src=/src,dst=/dst,ro", "--label=x=1", "-l", "x=2", "--", "image", "", "-n"
        ])
        #expect(!spec.standardOutput && spec.standardError)
        #expect(spec.mounts.first?.readOnly == true)
        #expect(spec.labels == ["x": "2"])
        #expect(spec.command == ["", "-n"])
        let defaults = try command(["--sig-proxy=false", "image"])
        let body = try #require(try JSONSerialization.jsonObject(with: defaults.createBody()) as? [String: Any])
        #expect(defaults.standardOutput && defaults.standardError)
        #expect(body["Cmd"] == nil && body["Entrypoint"] == nil)
        #expect(body["User"] == nil)
        #expect(try DockerRunMount.parse("type=bind,source=/s,destination=/d,readonly=false").readOnly == false)
    }

    @Test(arguments: [
        [String](), ["image"], ["--sig-proxy=true", "image"], ["--sig-proxy=false"],
        ["--sig-proxy=false", "--", ""], ["--sig-proxy=false", "image", "bad\0"],
        ["--sig-proxy=false", "-a", "stdin", "image"], ["--sig-proxy=false", "-t", "image"],
        ["--sig-proxy=false", "-d", "image"], ["--sig-proxy=false", "-e", "FROM_HOST", "image"],
        ["--sig-proxy=false", "-e", "=bad", "image"], ["--sig-proxy=false", "-l", "=bad", "image"],
        ["--sig-proxy=false", "-l", "a=bad\0", "image"], ["--sig-proxy=false", "--entrypoint=bad\0", "image"],
        ["--sig-proxy=false", "--entrypoint=a", "--entrypoint=b", "image"],
        ["--sig-proxy=false", "--user"], ["--sig-proxy=false", "--user=", "image"],
        ["--sig-proxy=false", "-u", "bad\0", "image"],
        ["--sig-proxy=false", "-u", "vscode", "--user=root", "image"]
    ])
    func `unsupported or invalid run options fail before mutation`(_ arguments: [String]) {
        #expect(throws: DockerFrontendError.self) { try command(arguments) }
    }

    @Test(arguments: [
        "", "type=bind,", "type=volume,source=x,target=/d", "type=bind,source=s,target=/d",
        "type=bind,source=/s,target=d", "type=bind,source=/s,target=/d,readonly=invalid",
        "type=bind,source=/s,target=/d,src=/other", "type=bind,source=/s,target=/d,unknown=x",
        "type=bind,source=\"/s\",target=/d", "type=bind,source=/s\0,target=/d"
    ])
    func `mount parser rejects ambiguous unsupported and unsafe fields`(_ value: String) {
        #expect(throws: DockerFrontendError.self) { try DockerRunMount.parse(value) }
    }

    @Test
    func `run attaches before starting and reports warnings output and exact exit`() async throws {
        let transport = RunTransport()
        let output = RunOutput()
        let status = try await DockerFrontend(version: "test").executeRun(
            command(["--sig-proxy=false", "image"]), transport: transport,
            output: { await output.append($0) }, warning: { await output.warn($0) }
        )
        #expect(status == 9)
        #expect(await transport.targets == [
            "/containers/create", "/containers/run-id/attach?stream=1&stdin=0&stdout=1&stderr=1",
            "/containers/run-id/start", "/containers/run-id/wait?condition=not-running"
        ])
        #expect(await output.warnings == ["fixture warning"])
        #expect(await output.frames.map(\.data) == [Data("out".utf8), Data("err".utf8)])
        #expect(transport.connection.closed && transport.connection.inputFinished)
    }

    @Test
    func `run projects selected output without forwarding unwanted channels`() async throws {
        let output = RunOutput()
        _ = try await DockerFrontend(version: "test").executeRun(
            command(["--sig-proxy=false", "-a", "stderr", "image"]), transport: RunTransport(),
            output: { await output.append($0) }, warning: { _ in /* This test checks stream selection. */ }
        )
        #expect(await output.frames.map(\.data) == [Data("err".utf8)])
    }

    @Test(arguments: ["create", "open", "start", "wait", "bad-id", "bad-exit", "wait-error", "bad-body"])
    func `startup errors preserve failure and close any established attachment`(_ failure: String) async throws {
        let transport = RunTransport(failure: failure)
        await #expect(throws: (any Error).self) {
            try await DockerFrontend(version: "test").executeRun(
                command(["--sig-proxy=false", "image"]), transport: transport,
                output: { _ in /* Error-path fixture. */ }, warning: { _ in /* Ignore fixture warning. */ }
            )
        }
        #expect(await transport.targets.allSatisfy { !$0.contains("remove") })
        if ["start", "wait", "bad-exit", "wait-error"].contains(failure) {
            #expect(transport.connection.closed)
        }
    }

    @Test
    func `blocked run output is cancelled and attachment joined at deadline`() async throws {
        let transport = RunTransport()
        let start = ContinuousClock.now
        await #expect(throws: DockerFrontendError.self) {
            try await DockerFrontend(version: "test", executionTimeout: .milliseconds(40)).executeRun(
                command(["--sig-proxy=false", "image"]), transport: transport,
                output: { _ in try await Task.sleep(for: .seconds(30)) }, warning: { _ in /* No-op fixture. */ }
            )
        }
        #expect(start.duration(to: .now) < .seconds(2))
        #expect(transport.connection.closed)
        #expect(await transport.targets.last == "/containers/run-id/start")
    }

    private func command(_ args: [String]) throws -> DockerRunCommand {
        guard case let .run(spec) = try DockerFrontendCommand.parse(["run"] + args) else {
            throw DockerFrontendError.usage("not run")
        }
        return spec
    }
}

private actor RunOutput {
    var frames: [DockerStreamFrame] = []
    var warnings: [String] = []
    func append(_ frame: DockerStreamFrame) {
        frames.append(frame)
    }

    func warn(_ message: String) {
        warnings.append(message)
    }
}

private actor RunTransport: DockerFrontendRunTransport {
    nonisolated let connection = RunConnection()
    let failure: String
    let waitDelay: Duration
    var targets: [String] = []

    init(failure: String = "", waitDelay: Duration = .zero) {
        self.failure = failure
        self.waitDelay = waitDelay
    }

    func send(_ request: DockerHTTPRequest) async throws -> Data {
        targets.append(request.target)
        if request.target == "/containers/create" {
            if failure == "create" {
                throw POSIXError(.EIO)
            }
            if failure == "bad-body" {
                return Data("bad".utf8)
            }
            let id = failure == "bad-id" ? "bad/id" : "run-id"
            return Data("{\"Id\":\"\(id)\",\"Warnings\":[\"fixture warning\"]}".utf8)
        }
        if request.target.hasSuffix("/start") {
            #expect(connection.inputFinished)
            if failure == "start" {
                throw POSIXError(.EIO)
            }
            return Data()
        }
        if failure == "wait" {
            throw POSIXError(.EIO)
        }
        try await Task.sleep(for: waitDelay)
        if failure == "bad-exit" {
            return Data(#"{"StatusCode":999}"#.utf8)
        }
        if failure == "wait-error" {
            return Data(#"{"StatusCode":0,"Error":{"Message":"failed"}}"#.utf8)
        }
        return Data(#"{"StatusCode":9,"Error":{"Message":""}}"#.utf8)
    }

    func open(_ request: DockerHTTPRequest) throws -> any DockerFrontendConnection {
        targets.append(request.target)
        if failure == "open" {
            throw POSIXError(.EIO)
        }
        return connection
    }

    func wait(_ request: DockerHTTPRequest) async throws -> Data {
        try await send(request)
    }
}

private final class RunConnection: DockerFrontendConnection, @unchecked Sendable {
    private let lock = NSLock()
    private var finished = false
    private var isClosed = false
    private var sent = false
    var inputFinished: Bool {
        lock.withLock { finished }
    }

    var closed: Bool {
        lock.withLock { isClosed }
    }

    func finishInput() {
        lock.withLock { finished = true }
    }

    func close() {
        lock.withLock { isClosed = true }
    }

    func write(_: Data) throws {
        throw POSIXError(.ENOTSUP)
    }

    func read() -> Data? {
        lock.withLock {
            guard !sent else { return nil }
            sent = true
            return Data([1, 0, 0, 0, 0, 0, 0, 3]) + Data("out".utf8)
                + Data([2, 0, 0, 0, 0, 0, 0, 3]) + Data("err".utf8)
        }
    }
}
