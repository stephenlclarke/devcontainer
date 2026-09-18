// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineWire
@testable import DevContainerDockerClient
import Foundation
import Testing

struct DockerFrontendTests {
    @Test(arguments: ["-v", "--version"])
    func `local version is honest and does not contact the engine`(flag: String) async throws {
        let transport = RecordingFrontendTransport("unused")
        let command = try DockerFrontendCommand.parse([flag])
        let frontend = DockerFrontend(version: "1.2.3")
        let output = try await frontend.execute(command, transport: transport)
        #expect(output == Data("devcontainer-docker version 1.2.3\n".utf8))
        #expect(await transport.requests.isEmpty)
    }

    @Test
    func `server version comes from selected engine`() async throws {
        let transport = RecordingFrontendTransport(#"{"Version":"1.4.1","ApiVersion":"1.53"}"#)
        let frontend = DockerFrontend(version: "1.2.3")
        let command = try DockerFrontendCommand.parse(["version", "--format", "{{.Server.Version}}"])
        #expect(try await frontend.execute(command, transport: transport) == Data("1.4.1\n".utf8))
        #expect(await transport.requests.map(\.target) == ["/version"])
        #expect(await transport.requests.map(\.method) == [.get])
        let text = try await frontend.execute(.version(format: nil), transport: transport)
        #expect(String(data: text, encoding: .utf8)?.contains("Server:\n Version: 1.4.1") == true)
        let json = try await frontend.execute(.version(format: "{{json .}}"), transport: transport)
        let result = try #require(JSONSerialization.jsonObject(with: json) as? [String: [String: String]])
        #expect(result["Server"]?["Version"] == "1.4.1")
        #expect(result["Client"]?["Name"] == "devcontainer-docker")
    }

    @Test(arguments: ["{}", "[]", #"{"Version":4}"#, #"{"Version":""}"#, #"{"Version":"one\ntwo"}"#])
    func `malformed version is not fabricated`(body: String) async throws {
        await #expect(throws: DockerFrontendError.self) {
            try await DockerFrontend(version: "1").execute(
                .version(format: nil), transport: RecordingFrontendTransport(body)
            )
        }
    }

    @Test(arguments: ["--format", "-f", "--format={{json .}}"])
    func `info JSON is preserved`(option: String) async throws {
        let arguments = ["info", option] + (option.contains("=") ? [] : ["{{json .}}"])
        let transport = RecordingFrontendTransport(#"{"ID":"stock","NCPU":12}"#)
        let output = try await DockerFrontend(version: "1").execute(.parse(arguments), transport: transport)
        #expect(output == Data("{\"ID\":\"stock\",\"NCPU\":12}\n".utf8))
        #expect(await transport.requests.map(\.target) == ["/info"])
    }

    @Test(arguments: ["image", "container"])
    func `inspect aliases use one escaped resource and preserve raw numeric data`(kind: String) async throws {
        let command = try DockerFrontendCommand.parse(["inspect", "--type", kind, "registry/x@sha256:a?/#"])
        #expect(try DockerFrontendCommand.parse([kind, "inspect", "registry/x@sha256:a?/#"]) == command)
        #expect(try DockerFrontendCommand.parse(["inspect", "--type=\(kind)", "--", "registry/x@sha256:a?/#"]) == command)
        let transport = RecordingFrontendTransport(#"{"Id":"abc","Created":9007199254740993}"#)
        let output = try await DockerFrontend(version: "1").execute(command, transport: transport)
        #expect(output == Data("[{\"Id\":\"abc\",\"Created\":9007199254740993}]\n".utf8))
        #expect(await transport.requests.map(\.target) == ["/\(kind)s/registry%2Fx%40sha256%3Aa%3F%2F%23/json"])
    }

    @Test
    func `quiet list projects IDs and combines repeated filters losslessly`() async throws {
        let command = try DockerFrontendCommand.parse([
            "ps", "-q", "-a", "--filter", "label=owner=a=b", "--filter=label=second", "-f", "name=x/y"
        ])
        let expectedFilters = ["label": ["owner=a=b", "second"], "name": ["x/y"]]
        #expect(command == .containers(all: true, truncate: true, filters: expectedFilters))
        let transport = RecordingFrontendTransport(#"[{"Id":"0123456789abcdef"},{"Id":"123"}]"#)
        let output = try await DockerFrontend(version: "1").execute(command, transport: transport)
        #expect(output == Data("0123456789ab\n123\n".utf8))
        let target = try #require(await transport.requests.first?.target)
        #expect(target.hasPrefix("/containers/json?all=1&filters="))
        let filters = try #require(target.components(separatedBy: "filters=").last?.removingPercentEncoding)
        let decoded = try JSONDecoder().decode([String: [String]].self, from: Data(filters.utf8))
        #expect(decoded == ["label": ["owner=a=b", "second"], "name": ["x/y"]])
    }

    @Test(arguments: ["-qa", "-aq"])
    func `quiet list grouped flags and no truncation`(flags: String) async throws {
        let command = try DockerFrontendCommand.parse(["ps", flags, "--no-trunc"])
        #expect(command == .containers(all: true, truncate: false, filters: [:]))
        let transport = RecordingFrontendTransport(#"[{"Id":"0123456789abcdef"}]"#)
        let output = try await DockerFrontend(version: "1").execute(command, transport: transport)
        #expect(output == Data("0123456789abcdef\n".utf8))
        let longFlags = try DockerFrontendCommand.parse(["ps", "--quiet", "--all"])
        #expect(longFlags == .containers(all: true, truncate: true, filters: [:]))
    }

    @Test
    func `empty quiet list stays empty`() async throws {
        let transport = RecordingFrontendTransport("[]")
        #expect(try await DockerFrontend(version: "1").execute(.parse(["ps", "-q"]), transport: transport).isEmpty)
        #expect(await transport.requests.first?.target == "/containers/json?all=0&filters=%7B%7D")
    }

    @Test(arguments: ["{}", "[null]", "[{}]", #"[{"Id":""}]"#, #"[{"Id":"bad\noutput"}]"#, #"[{"Id":4}]"#])
    func `malformed IDs cannot produce false discovery output`(body: String) async throws {
        await #expect(throws: DockerFrontendError.self) {
            try await DockerFrontend(version: "1").execute(
                .parse(["ps", "-q"]), transport: RecordingFrontendTransport(body)
            )
        }
    }

    @Test(arguments: ["[]", "null", "not-json"])
    func `malformed inspection and info fail`(body: String) async throws {
        for command in [DockerFrontendCommand.info, .inspect(kind: "image", name: "alpine")] {
            await #expect(throws: (any Error).self) {
                try await DockerFrontend(version: "1").execute(command, transport: RecordingFrontendTransport(body))
            }
        }
    }

    @Test(arguments: [
        [], ["buildx", "version"], ["compose", "version"], ["build"], ["-v", "extra"],
        ["version", "--format"], ["version", "--format="], ["version", "--format", "{{.Missing}}"],
        ["version", "--format", "{{json .}}", "extra"], ["version", "--other"], ["info"],
        ["image", "pull", "alpine"], ["container", "rm", "id"], ["inspect"],
        ["inspect", "--type", "volume", "id"], ["inspect", "--type", "image", ""],
        ["inspect", "--type", "image", "one", "two"], ["inspect", "--type", "image", "--size"],
        ["inspect", "--type", "image", "one", "--", "two"], ["inspect", "--type", "image", "--"],
        ["inspect", "--type", "image", "--", "one", "two"], ["image", "inspect", "--type", "image", "one"],
        ["ps"], ["ps", "-q", "--size"], ["ps", "-q", "--filter", "label"],
        ["ps", "-q", "--filter", "=value"], ["ps", "-q", "--filter", "label="]
    ])
    func `unsupported commands and flags fail before any transport exists`(arguments: [String]) {
        #expect(throws: DockerFrontendError.self) { try DockerFrontendCommand.parse(arguments) }
    }

    @Test
    func `path encoding cannot alter the request target`() {
        #expect(DockerFrontend.escaped("aZ09-._~ /?%\u{00e9}") == "aZ09-._~%20%2F%3F%25%C3%A9")
    }

    @Test
    func `engine errors propagate without successful output`() async {
        await #expect(throws: TestFailure.unavailable) {
            try await DockerFrontend(version: "1").execute(.info, transport: UnavailableFrontendTransport())
        }
    }
}

private actor RecordingFrontendTransport: DockerFrontendTransport {
    private let body: Data
    private(set) var requests: [DockerHTTPRequest] = []

    init(_ body: String) {
        self.body = Data(body.utf8)
    }

    func send(_ request: DockerHTTPRequest) async throws -> Data {
        requests.append(request)
        return body
    }
}

private enum TestFailure: Error { case unavailable }

private struct UnavailableFrontendTransport: DockerFrontendTransport {
    func send(_: DockerHTTPRequest) async throws -> Data {
        throw TestFailure.unavailable
    }
}
