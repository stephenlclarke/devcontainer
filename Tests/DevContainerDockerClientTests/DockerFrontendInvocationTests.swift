// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerDockerClient
import Testing

struct DockerFrontendInvocationTests {
    @Test(arguments: ["--host", "-H", "--host=unix:///private/test.sock"])
    func `explicit endpoint wins without reading Docker contexts`(flag: String) throws {
        let arguments = [flag] + (flag.contains("=") ? [] : ["unix:///private/test.sock"]) + ["version"]
        let invocation = try DockerFrontendInvocation(arguments: arguments, environment: [
            "DOCKER_HOST": "tcp://unwanted:2375", "DOCKER_CONTEXT": "unwanted"
        ])
        #expect(invocation.socketPath == "/private/test.sock")
    }

    @Test
    func `environment endpoint remains literal and default is deferred`() throws {
        let invocation = try DockerFrontendInvocation(arguments: ["ps", "-q"], environment: [
            "DOCKER_HOST": "unix:///private/space and %literal.sock"
        ])
        #expect(invocation.socketPath == "/private/space and %literal.sock")
        #expect(try DockerFrontendInvocation(arguments: ["version"], environment: [:]).socketPath == nil)
    }

    @Test
    func `VS Code neutral default context preserves its explicit local socket`() throws {
        let invocation = try DockerFrontendInvocation(arguments: ["version", "--format", "{{json .}}"], environment: [
            "DOCKER_CONTEXT": "default", "DOCKER_HOST": "unix:///private/test.sock"
        ])
        #expect(invocation.socketPath == "/private/test.sock")
        #expect(invocation.command == .version(format: "{{json .}}"))
    }

    @Test(arguments: [
        ["DOCKER_CONTEXT": "default"],
        ["DOCKER_CONTEXT": "other", "DOCKER_HOST": "unix:///private/test.sock"],
        ["DOCKER_CONTEXT": "default", "DOCKER_HOST": "tcp://localhost:2375"],
        ["DOCKER_CONTEXT": "default", "DOCKER_HOST": ""]
    ])
    func `neutral context does not discover contexts or permit remote endpoints`(environment: [String: String]) {
        #expect(throws: DockerFrontendError.self) {
            try DockerFrontendInvocation(arguments: ["version"], environment: environment)
        }
    }

    @Test
    func `local version ignores unavailable runtime configuration`() throws {
        let invocation = try DockerFrontendInvocation(arguments: ["-v"], environment: [
            "DOCKER_HOST": "invalid", "DOCKER_CONTEXT": "unavailable"
        ])
        #expect(invocation.socketPath == nil)
        #expect(invocation.command == .clientVersion)
    }

    @Test(arguments: [
        "tcp://localhost:2375",
        "ssh://host",
        "unix://relative",
        "unix:///",
        "",
        "unix:///a/../b",
        "unix:///a/./b",
        "unix:///a\0b",
        "unix:///" + String(repeating: "a", count: 104)
    ])
    func `remote ambiguous and unsafe endpoints are rejected`(host: String) {
        #expect(throws: DockerFrontendError.self) {
            try DockerFrontendInvocation(arguments: ["version"], environment: ["DOCKER_HOST": host])
        }
    }

    @Test
    func `context selection and duplicate hosts fail rather than selecting another runtime`() {
        #expect(throws: DockerFrontendError.self) {
            try DockerFrontendInvocation(arguments: ["version"], environment: ["DOCKER_CONTEXT": "other"])
        }
        #expect(throws: DockerFrontendError.self) {
            try DockerFrontendInvocation(
                arguments: ["-H", "unix:///one", "-H", "unix:///two", "version"], environment: [:]
            )
        }
        #expect(throws: DockerFrontendError.self) {
            try DockerFrontendInvocation(arguments: ["--host"], environment: [:])
        }
    }

    @Test
    func `shared transport rejects malformed socket paths`() {
        #expect(throws: (any Error).self) { try UnixDockerFrontendTransport(socketPath: "relative") }
    }
}
