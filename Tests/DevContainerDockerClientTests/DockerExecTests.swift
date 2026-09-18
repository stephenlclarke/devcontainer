// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineWire
import Darwin
@testable import DevContainerDockerClient
import Foundation
import Testing

struct DockerExecTests {
    @Test
    func `terminal inspection waits for observed exit publication without restarting exec`() async throws {
        let transport = ExecTestTransport(connection: ExecTestConnection(), runningResponses: 2)
        let spec = try command(["box", "true"])
        let status = try await DockerFrontend(version: "test").executeExec(
            spec, transport: transport, input: { nil }, output: { _ in /* Lifecycle-only case. */ }
        )
        #expect(status == 7)
        #expect(await transport.requests.map(\.target) == [
            "/containers/box/exec", "/exec/exec1/start", "/exec/exec1/json", "/exec/exec1/json", "/exec/exec1/json"
        ])
    }

    @Test
    func `blocked output cancels and joins without changing inherited pipe flags`() async throws {
        let pipe = Pipe()
        defer { try? pipe.fileHandleForWriting.close(); try? pipe.fileHandleForReading.close() }
        let descriptor = pipe.fileHandleForWriting.fileDescriptor
        // XNU sets FWASWRITTEN on the first write; establish that kernel state
        // before comparing all flags, without masking any caller-owned flags.
        // https://github.com/apple/darwin-xnu/blob/main/bsd/sys/fcntl.h
        var byte: UInt8 = 0
        #expect(Darwin.write(descriptor, &byte, 1) == 1)
        #expect(Darwin.read(pipe.fileHandleForReading.fileDescriptor, &byte, 1) == 1)
        let flags = fcntl(descriptor, F_GETFL)
        let signalFlags = fcntl(descriptor, F_GETNOSIGPIPE)
        let writer = try DockerFrontendOutput(descriptor: descriptor)
        let connection = ExecTestConnection(immediate: true)
        let transport = ExecTestTransport(connection: connection)
        let spec = try command(["-i", "box", "sh"])
        let start = ContinuousClock.now
        await #expect(throws: DockerFrontendError.self) {
            try await DockerFrontend(version: "test", executionTimeout: .milliseconds(100)).executeExec(
                spec, transport: transport,
                input: { try await Task.sleep(for: .seconds(30)); return nil },
                output: { _ in try await writer.write(Data(repeating: 97, count: 1024 * 1024)) }
            )
        }
        #expect(start.duration(to: .now) < .seconds(2))
        #expect(connection.closed)
        #expect(fcntl(descriptor, F_GETFL) == flags)
        #expect(fcntl(descriptor, F_GETNOSIGPIPE) == signalFlags)
    }

    @Test
    func `output writes bytes and reports closed pipes without SIGPIPE`() async throws {
        let pipe = Pipe()
        defer { try? pipe.fileHandleForWriting.close() }
        let writer = try DockerFrontendOutput(descriptor: pipe.fileHandleForWriting.fileDescriptor)
        try await writer.write(Data())
        try await writer.write(Data("hello".utf8))
        #expect(try pipe.fileHandleForReading.read(upToCount: 5) == Data("hello".utf8))
        try pipe.fileHandleForReading.close()
        await #expect(throws: POSIXError.self) { try await writer.write(Data("late".utf8)) }
        #expect(throws: POSIXError.self) { try DockerFrontendOutput(descriptor: -1) }
    }

    @Test
    func `exec parses the pinned upstream CLI forms without interpreting command arguments`() throws {
        let spec = try command([
            "-i",
            "-u",
            "root",
            "-e",
            "A=x=y",
            "--env=B=",
            "-w",
            "/work",
            "box",
            "sh",
            "-c",
            "echo hi"
        ])
        #expect(spec.container == "box")
        #expect(spec.command == ["sh", "-c", "echo hi"])
        #expect(spec.interactive)
        #expect(spec.user == "root")
        #expect(spec.environment == ["A=x=y", "B="])
        #expect(spec.workingDirectory == "/work")
        let aliases = try command([
            "--interactive",
            "--user=root",
            "--env",
            "A=x=y",
            "-e",
            "B=",
            "--workdir=/work",
            "--",
            "box",
            "sh",
            "-c",
            "echo hi"
        ])
        #expect(aliases == spec)
        #expect(try command(["box", "sh", ""]).command == ["sh", ""])
    }

    @Test(arguments: [
        [], ["box"], ["box", ""], ["", "sh"], ["box", "sh\0"], ["-t", "box", "sh"],
        ["-i=true", "box", "sh"], ["-e", "NAME", "box", "sh"], ["-e", "=bad", "box", "sh"],
        ["-u", "root", "-u", "user", "box", "sh"], ["-w", "/", "-w", "/tmp", "box", "sh"],
        ["--"], ["-u"], ["-e", "A=nul\0", "box", "sh"], ["--detach", "box", "sh"]
    ])
    func `unsupported or malformed exec arguments fail before creation`(arguments: [String]) {
        #expect(throws: DockerFrontendError.self) { try command(arguments) }
    }

    @Test
    func `exec sends exact requests and preserves separate output and exit status`() async throws {
        let connection = ExecTestConnection()
        let transport = ExecTestTransport(connection: connection)
        let input = ExecTestInput()
        let output = ExecTestOutput()
        let spec = try command(["-i", "-u", "root", "-e", "X=1", "-w", "/work", "box/name", "sh"])
        let status = try await DockerFrontend(version: "test").executeExec(
            spec, transport: transport, input: { await input.next() }, output: { await output.append($0) }
        )
        #expect(status == 7)
        #expect(connection.written == Data("command\n".utf8))
        #expect(connection.closed)
        #expect(await output.stdout == Data("out".utf8))
        #expect(await output.stderr == Data("err".utf8))
        let requests = await transport.requests
        #expect(requests.map(\.target) == ["/containers/box%2Fname/exec", "/exec/exec1/start", "/exec/exec1/json"])
        #expect(requests.map(\.method) == [.post, .post, .get])
        let body = try #require(JSONSerialization.jsonObject(with: requests[0].body) as? [String: Any])
        #expect(body["User"] as? String == "root")
        #expect(body["WorkingDir"] as? String == "/work")
        #expect(body["Env"] as? [String] == ["X=1"])
        #expect(body["Cmd"] as? [String] == ["sh"])
        #expect(body["AttachStdin"] as? Bool == true)
        #expect(body["Tty"] as? Bool == false)
        #expect(requests[1].body == Data(#"{"Detach":false,"Tty":false}"#.utf8))
    }

    @Test
    func `noninteractive exec closes input without reading caller stdin`() async throws {
        let connection = ExecTestConnection()
        let transport = ExecTestTransport(connection: connection)
        let spec = try command(["box", "true"])
        let status = try await DockerFrontend(version: "test").executeExec(spec, transport: transport, input: {
            Issue.record("noninteractive execution must not read stdin")
            return nil
        }, output: { _ in /* Output is tested separately. */ })
        #expect(status == 7)
        #expect(connection.written.isEmpty)
        await #expect(throws: DockerFrontendError.self) {
            try await DockerFrontend(version: "test").execute(.exec(spec), transport: transport)
        }
    }

    @Test(arguments: [
        #"{"ID":"different","Running":false,"ExitCode":0}"#,
        #"{"ID":"exec1","Running":true,"ExitCode":0}"#,
        #"{"ID":"exec1","Running":false,"ExitCode":256}"#,
        #"{"ID":"exec1","Running":false,"ExitCode":-1}"#,
        #"{"ID":"exec1","Running":false,"ExitCode":true}"#
    ])
    func `malformed or nonterminal exec status is not success`(state: String) async throws {
        let connection = ExecTestConnection()
        let transport = ExecTestTransport(connection: connection, state: state)
        let spec = try command(["box", "true"])
        await #expect(throws: (any Error).self) {
            try await DockerFrontend(version: "test").executeExec(
                spec, transport: transport, input: { nil }, output: { _ in /* Status-only case. */ }
            )
        }
        #expect(connection.closed)
    }

    @Test(arguments: ["", "bad/path", "bad\n", "n\0"])
    func `invalid created identifier never reaches exec start`(identifier: String) async throws {
        let transport = ExecTestTransport(connection: ExecTestConnection(), identifier: identifier)
        let spec = try command(["box", "true"])
        await #expect(throws: DockerFrontendError.self) {
            try await DockerFrontend(version: "test").executeExec(
                spec, transport: transport, input: { nil }, output: { _ in /* No stream can open. */ }
            )
        }
        #expect(await transport.requests.count == 1)
    }

    @Test
    func `output failure closes connection and cancels pending input`() async throws {
        let connection = ExecTestConnection(immediate: true)
        let transport = ExecTestTransport(connection: connection)
        let spec = try command(["-i", "box", "sh"])
        await #expect(throws: ExecTestFailure.self) {
            try await DockerFrontend(version: "test").executeExec(spec, transport: transport, input: {
                try await Task.sleep(for: .seconds(30))
                return nil
            }, output: { _ in throw ExecTestFailure.failed })
        }
        #expect(connection.closed)
        #expect(await transport.requests.count == 2)
    }

    @Test
    func `remote EOF cancels open stdin and still returns inspected status`() async throws {
        let connection = ExecTestConnection(immediate: true)
        let transport = ExecTestTransport(connection: connection)
        let spec = try command(["-i", "box", "sh"])
        let status = try await DockerFrontend(version: "test").executeExec(spec, transport: transport, input: {
            try await Task.sleep(for: .seconds(30))
            return nil
        }, output: { _ in /* EOF and input cancellation are under test. */ })
        #expect(status == 7)
        #expect(connection.closed)
    }

    @Test
    func `stdin wrapper reads bytes EOF and cancels an idle pipe`() async throws {
        let pipe = Pipe()
        let input = try DockerFrontendInput(descriptor: pipe.fileHandleForReading.fileDescriptor)
        try pipe.fileHandleForWriting.write(contentsOf: Data("hello".utf8))
        #expect(try await input.read() == Data("hello".utf8))
        try pipe.fileHandleForWriting.close()
        #expect(try await input.read() == nil)
        let idle = Pipe()
        defer { try? idle.fileHandleForWriting.close(); try? idle.fileHandleForReading.close() }
        let waiting = try DockerFrontendInput(descriptor: idle.fileHandleForReading.fileDescriptor)
        let task = Task { try await waiting.read() }
        try await Task.sleep(for: .milliseconds(20))
        task.cancel()
        await #expect(throws: CancellationError.self) { try await task.value }
        #expect(throws: POSIXError.self) { try DockerFrontendInput(descriptor: -1) }
    }

    private func command(_ arguments: [String]) throws -> DockerExecCommand {
        guard case let .exec(spec) = try DockerFrontendCommand.parse(["exec"] + arguments) else {
            throw ExecTestFailure.failed
        }
        return spec
    }
}

private enum ExecTestFailure: Error { case failed }

private actor ExecTestInput {
    var sent = false
    func next() -> Data? {
        defer { sent = true }
        return sent ? nil : Data("command\n".utf8)
    }
}

private actor ExecTestOutput {
    var stdout = Data()
    var stderr = Data()
    func append(_ frame: DockerStreamFrame) {
        if frame.channel == .standardOutput {
            stdout.append(frame.data)
        } else {
            stderr.append(frame.data)
        }
    }
}

private actor ExecTestTransport: DockerFrontendExecTransport {
    let connection: ExecTestConnection
    let identifier: String
    let state: String
    var requests: [DockerHTTPRequest] = []
    var runningResponses: Int

    init(
        connection: ExecTestConnection,
        identifier: String = "exec1",
        state: String = #"{"ID":"exec1","Running":false,"ExitCode":7}"#,
        runningResponses: Int = 0
    ) {
        self.connection = connection
        self.identifier = identifier
        self.state = state
        self.runningResponses = runningResponses
    }

    func send(_ request: DockerHTTPRequest) throws -> Data {
        requests.append(request)
        if request.method == .get, runningResponses > 0 {
            runningResponses -= 1
            return try JSONSerialization.data(withJSONObject: ["ID": identifier, "Running": true, "ExitCode": 0])
        }
        return request.method == .post
            ? try JSONSerialization.data(withJSONObject: ["Id": identifier]) : Data(state.utf8)
    }

    func open(_ request: DockerHTTPRequest) -> any DockerFrontendConnection {
        requests.append(request)
        return connection
    }
}

private final class ExecTestConnection: DockerFrontendConnection, @unchecked Sendable {
    private let lock = NSLock()
    private var input = Data()
    private var inputFinished: Bool
    private var didClose = false
    private var didRead = false

    init(immediate: Bool = false) {
        inputFinished = immediate
    }

    var closed: Bool {
        lock.withLock { didClose }
    }

    var written: Data {
        lock.withLock { input }
    }

    func read() async throws -> Data? {
        while !lock.withLock({ inputFinished }) {
            try await Task.sleep(for: .milliseconds(1))
        }
        return try lock.withLock {
            guard !didRead else { return nil }
            didRead = true
            return try DockerStreamFraming.encode(
                .init(channel: .standardOutput, data: Data("out".utf8)),
                terminal: false
            )
                + DockerStreamFraming.encode(.init(channel: .standardError, data: Data("err".utf8)), terminal: false)
        }
    }

    func write(_ data: Data) {
        lock.withLock { input.append(data) }
    }

    func finishInput() {
        lock.withLock { inputFinished = true }
    }

    func close() {
        lock.withLock { didClose = true }
    }
}
