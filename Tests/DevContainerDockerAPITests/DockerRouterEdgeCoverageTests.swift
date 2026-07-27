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

@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerTestSupport
import Foundation
import Testing

private struct EdgeFixture {
    let runtime: InMemoryRuntime
    let router: DockerRouter
    let context: RuntimeRequestContext
    let identifier: String
}

private func makeEdgeFixture(name: String = "edge") async throws -> EdgeFixture {
    let runtime = InMemoryRuntime()
    let context = RuntimeRequestContext()
    await runtime.seedImage(
        ImageSnapshot(
            id: "sha256:edge",
            references: ["edge:latest"],
            createdAt: Date(),
            size: 1
        )
    )
    let container = try await runtime.createContainer(
        spec: ContainerSpec(name: name, image: "edge:latest"),
        context: context
    )
    return EdgeFixture(
        runtime: runtime,
        router: DockerRouter(runtime: runtime),
        context: context,
        identifier: container.dockerID.rawValue
    )
}

@Test
func `request and lifecycle edge routes retain Docker semantics`() async throws {
    let fixture = try await makeEdgeFixture()
    #expect(
        DockerHTTPRequest(
            method: .get,
            target: "/_ping",
            headers: ["x-fixture": "present"]
        ).header("X-Fixture") == "present"
    )
    #expect(
        await fixture.router.respond(
            to: DockerHTTPRequest(
                method: .get,
                target: "/containers/json?all=1&filters=%7B%22label%22:%5B%22missing%3Dvalue%22%5D%7D"
            )
        ).status == 200
    )
    #expect(
        await fixture.router.respond(
            to: DockerHTTPRequest(method: .post, target: "/containers/\(fixture.identifier)/start")
        ).status == 204
    )
    #expect(
        await fixture.router.respond(
            to: DockerHTTPRequest(method: .post, target: "/containers/\(fixture.identifier)/restart?t=1")
        ).status == 204
    )
    #expect(
        await fixture.router.respond(
            to: DockerHTTPRequest(method: .get, target: "/containers/\(fixture.identifier)/unknown")
        ).status == 404
    )
}

@Test
func `archive and exec edge routes reject incomplete requests`() async throws {
    let fixture = try await makeEdgeFixture()
    for request in [
        DockerHTTPRequest(method: .head, target: "/containers/\(fixture.identifier)/archive"),
        DockerHTTPRequest(method: .put, target: "/containers/\(fixture.identifier)/archive")
    ] {
        #expect(await fixture.router.respond(to: request).status == 400)
    }
    #expect(
        await fixture.router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/exec/missing/resize?w=80&h=24"
            )
        ).status == 409
    )
}

@Test
func `network routes create and delete local networks`() async throws {
    let fixture = try await makeEdgeFixture()
    let body = try JSONSerialization.data(withJSONObject: ["Name": "edge-network"])
    let created = await fixture.router.respond(
        to: DockerHTTPRequest(method: .post, target: "/networks/create", body: body)
    )
    let object = try JSONSerialization.jsonObject(with: responseBytes(created)) as? [String: Any]
    let identifier = try #require(object?["Id"] as? String)
    #expect(
        await fixture.router.respond(
            to: DockerHTTPRequest(method: .delete, target: "/networks/\(identifier)")
        ).status == 204
    )
}

@Test
func `container creation rejects invalid bind port and mount forms`() async throws {
    let fixture = try await makeEdgeFixture()
    let invalidBind = try JSONSerialization.data(
        withJSONObject: ["Image": "edge:latest", "HostConfig": ["Binds": ["missing-colon"]]]
    )
    let invalidPort = try JSONSerialization.data(
        withJSONObject: ["Image": "edge:latest", "HostConfig": ["PortBindings": ["invalid/tcp": []]]]
    )
    let invalidMount = try JSONSerialization.data(
        withJSONObject: [
            "Image": "edge:latest",
            "Mounts": [["Type": "unknown", "Target": "/workspace"]]
        ]
    )
    for (body, expectedStatus) in [(invalidBind, 400), (invalidPort, 400), (invalidMount, 501)] {
        let response = await fixture.router.respond(
            to: DockerHTTPRequest(method: .post, target: "/containers/create", body: body)
        )
        #expect(response.status == expectedStatus)
    }
}

@Test
func `container inspect accepts empty port bindings and sorted aliases`() async throws {
    let fixture = try await makeEdgeFixture()
    let body = try JSONSerialization.data(
        withJSONObject: [
            "Image": "edge:latest",
            "HostConfig": ["PortBindings": ["8080/tcp": []]],
            "NetworkingConfig": [
                "EndpointsConfig": ["edge-network": ["Aliases": ["zeta", "alpha"]]]
            ]
        ]
    )
    let created = await fixture.router.respond(
        to: DockerHTTPRequest(method: .post, target: "/containers/create?name=configured", body: body)
    )
    let object = try JSONSerialization.jsonObject(with: responseBytes(created)) as? [String: Any]
    let identifier = try #require(object?["Id"] as? String)
    #expect(
        await fixture.router.respond(
            to: DockerHTTPRequest(method: .get, target: "/containers/\(identifier)/json")
        ).status == 200
    )
}

@Test
func `uncommon filter build and image identifiers are handled`() async throws {
    let fixture = try await makeEdgeFixture()
    let requests = [
        DockerHTTPRequest(
            method: .get,
            target: "/events?filters=%7B%22event%22:%7B%22create%22:1,%22start%22:0%7D%7D"
        ),
        DockerHTTPRequest(method: .post, target: "/build?buildargs=%5B%5D"),
        DockerHTTPRequest(method: .post, target: "/images/edge/not-tag?repo=fixture"),
        DockerHTTPRequest(method: .delete, target: "/images/edge/extra"),
        DockerHTTPRequest(method: .delete, target: "/images/")
    ]
    for request in requests {
        let response = await fixture.router.respond(to: request)
        if request.target.hasPrefix("/events") {
            #expect(response.status == 200)
            _ = try await responseStreamBytes(response)
        } else {
            #expect([400, 404].contains(response.status))
        }
    }
}

@Test
func `wait until removed completes after runtime removal`() async throws {
    let fixture = try await makeEdgeFixture()
    try await fixture.runtime.startContainer(id: fixture.identifier, context: fixture.context)
    try await fixture.runtime.stopContainer(
        id: fixture.identifier,
        timeout: nil,
        context: fixture.context
    )
    let wait = await fixture.router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/containers/\(fixture.identifier)/wait?condition=removed"
        )
    )
    let removal = Task {
        try await Task.sleep(for: .milliseconds(50))
        try await fixture.runtime.removeContainer(
            id: fixture.identifier,
            force: true,
            context: fixture.context
        )
    }
    _ = try await responseStreamBytes(wait)
    try await removal.value
}

@Test
func `in memory runtime secondary negative paths are deterministic`() async throws {
    let fixture = try await makeEdgeFixture()
    let missing = ExecID(rawValue: "missing")
    await #expect(throws: DevContainerError.self) {
        _ = try await fixture.runtime.startExec(id: missing, context: fixture.context)
    }
    await #expect(throws: DevContainerError.self) {
        _ = try await fixture.runtime.inspectExec(id: missing, context: fixture.context)
    }
    await #expect(throws: DevContainerError.self) {
        _ = try await fixture.runtime.inspectVolume(name: "missing", context: fixture.context)
    }
    await #expect(throws: DevContainerError.self) {
        try await fixture.runtime.removeVolume(name: "missing", force: false, context: fixture.context)
    }
}

@Test
func `in memory runtime reuses volumes and resolves container names`() async throws {
    let fixture = try await makeEdgeFixture()
    let volume = await fixture.runtime.createVolume(
        spec: VolumeSpec(name: "fixture"),
        context: fixture.context
    )
    #expect(
        await fixture.runtime.createVolume(
            spec: VolumeSpec(name: "fixture"),
            context: fixture.context
        ) == volume
    )
    #expect(
        try await fixture.runtime.inspectContainer(
            id: "edge",
            context: fixture.context
        ).dockerID.rawValue == fixture.identifier
    )
}

@Test
func `health registry accepts a missing container start time`() async {
    let registry = ContainerHealthRegistry()
    let now = Date(timeIntervalSince1970: 1000)
    let check = ContainerHealthcheck(test: ["CMD", "true"])
    guard case .check = await registry.decision(
        id: "nil-start",
        startedAt: nil,
        healthcheck: check,
        now: now
    ) else {
        Issue.record("a missing start time must permit the first check")
        return
    }
    let health = await registry.record(
        id: "nil-start",
        startedAt: nil,
        healthcheck: check,
        exitCode: 0,
        started: now,
        ended: now
    )
    #expect(health.status == "healthy")
}

@Test
func `container health inspect reuses a fresh cached observation`() async throws {
    let fixture = try await makeEdgeFixture()
    let body = try JSONSerialization.data(
        withJSONObject: [
            "Image": "edge:latest",
            "Healthcheck": [
                "Test": ["CMD", "true"],
                "Interval": 10_000_000_000
            ]
        ]
    )
    let created = await fixture.router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/containers/create?name=health-cache",
            body: body
        )
    )
    let object = try JSONSerialization.jsonObject(with: responseBytes(created)) as? [String: Any]
    let identifier = try #require(object?["Id"] as? String)
    #expect(
        await fixture.router.respond(
            to: DockerHTTPRequest(method: .post, target: "/containers/\(identifier)/start")
        ).status == 204
    )
    for _ in 0 ..< 2 {
        #expect(
            await fixture.router.respond(
                to: DockerHTTPRequest(method: .get, target: "/containers/\(identifier)/json")
            ).status == 200
        )
    }
}

private func responseBytes(_ response: DockerHTTPResponse) throws -> Data {
    guard case let .bytes(data) = response.body else {
        throw DevContainerError(.invalidRequest, message: "expected byte response")
    }
    return data
}

private func responseStreamBytes(_ response: DockerHTTPResponse) async throws -> Data {
    guard case let .stream(stream) = response.body else {
        throw DevContainerError(.invalidRequest, message: "expected stream response")
    }
    var result = Data()
    for try await data in stream {
        result.append(data)
    }
    return result
}
