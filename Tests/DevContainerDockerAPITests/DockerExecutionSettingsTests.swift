// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerTestSupport
import Foundation
import Testing

struct DockerExecutionSettingsTests {
    @Test func `create and inspect execution settings`() async throws {
        let runtime = InMemoryRuntime()
        await runtime.seedImage(.init(id: "sha256:settings", references: ["settings:test"], createdAt: Date(), size: 1))
        let router = DockerRouter(runtime: runtime)
        let host: [String: Any] = [
            "Memory": 268_435_456, "ShmSize": 33_554_432, "ReadonlyRootfs": true,
            "Sysctls": ["net.ipv4.ip_forward": "1"]
        ]
        let body = try JSONSerialization.data(
            withJSONObject: ["Image": "settings:test", "HostConfig": host, "StopSignal": "SIGUSR1"]
        )
        let created = await router.respond(to: .init(
            method: .post, target: "/containers/create?name=settings", body: body
        ))
        #expect(created.status == 201)
        let inspected = await router.respond(to: .init(method: .get, target: "/containers/settings/json"))
        let object = try #require(JSONSerialization.jsonObject(with: bytes(inspected)) as? [String: Any])
        let actual = try #require(object["HostConfig"] as? NSDictionary)
        for (key, value) in host {
            #expect(NSDictionary(dictionary: [key: actual[key] as Any]) == [key: value] as NSDictionary)
        }
        #expect((object["Config"] as? [String: Any])?["StopSignal"] as? String == "SIGUSR1")
    }

    @Test(arguments: ["Memory", "ShmSize"], [-1, -9_223_372_036_854_775_807])
    func `rejects negative bytes`(_ field: String, _ value: Int64) async throws {
        let body = try JSONSerialization.data(withJSONObject: ["Image": "missing", "HostConfig": [field: value]])
        let response = await DockerRouter(runtime: InMemoryRuntime()).respond(
            to: .init(method: .post, target: "/containers/create", body: body)
        )
        #expect(response.status == 400)
    }

    @Test func `zero and missing values retain defaults`() throws {
        let request = try JSONDecoder().decode(DockerCreateContainerRequest.self, from: Data(
            #"{"Image":"image","StopSignal":"","HostConfig":{"Memory":0,"ShmSize":0}}"#.utf8
        ))
        let settings = try DockerRouter(runtime: InMemoryRuntime()).executionSettings(request)
        #expect(settings == .init())
    }
}
