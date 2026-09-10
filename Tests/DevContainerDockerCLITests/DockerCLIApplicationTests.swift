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
import Foundation
import Testing

@Suite("Docker CLI compatibility application")
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
    func `reports a Docker-shaped version without contacting the engine`() throws {
        let transport = StubTransport([])
        let result = try DockerCLIApplication(transport: transport).run(arguments: ["-v"])
        let output = try #require(String(data: result.standardOutput, encoding: .utf8))

        #expect(output.hasPrefix("Docker version 26.1.0"))
        #expect(transport.requests.isEmpty)
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
