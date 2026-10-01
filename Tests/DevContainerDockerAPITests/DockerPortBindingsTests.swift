// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerTestSupport
import Foundation
import Testing

@Test
func `inspect retains explicit loopback publication and omits expose-only ports from host bindings`() async throws {
    let runtime = InMemoryRuntime()
    await runtime.seedImage(ImageSnapshot(
        id: "sha256:fixture", references: ["fixture:latest"], createdAt: Date(timeIntervalSince1970: 1), size: 1
    ))
    let router = DockerRouter(runtime: runtime)
    let expected = ["8123/tcp": [["HostIp": "127.0.0.1", "HostPort": "49277"]]]
    let body = try JSONSerialization.data(withJSONObject: [
        "Image": "fixture:latest", "ExposedPorts": ["8123/tcp": [:], "9090/tcp": [:]],
        "HostConfig": ["PortBindings": expected]
    ])
    let created = await router.respond(to: DockerHTTPRequest(
        method: .post, target: "/containers/create?name=ports", body: body
    ))
    #expect(created.status == 201)
    let value = try #require(JSONSerialization.jsonObject(with: bytes(created)) as? [String: Any])
    let identifier = try #require(value["Id"] as? String)
    let response = await router.respond(to: DockerHTTPRequest(method: .get, target: "/containers/\(identifier)/json"))
    #expect(response.status == 200)
    let inspection = try #require(JSONSerialization.jsonObject(with: bytes(response)) as? [String: Any])
    let host = try #require(inspection["HostConfig"] as? [String: Any])
    #expect(host["PortBindings"] as? [String: [[String: String]]] == expected)
    let config = try #require(inspection["Config"] as? [String: Any])
    #expect(Set((config["ExposedPorts"] as? [String: Any] ?? [:]).keys) == ["8123/tcp", "9090/tcp"])
}
