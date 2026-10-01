// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerTestSupport
import Foundation
import Testing

struct DockerDNSTests {
    @Test func `create and inspect preserve resolver settings`() async throws {
        let runtime = InMemoryRuntime()
        await runtime.seedImage(.init(id: "sha256:dns", references: ["dns:test"], createdAt: Date(), size: 1))
        let router = DockerRouter(runtime: runtime)
        let host: [String: [String]] = [
            "Dns": ["192.0.2.53", "2001:db8::53"], "DnsSearch": ["example.test", "other.test"],
            "DnsOptions": ["ndots:2", "timeout:1"]
        ]
        let body = try JSONSerialization.data(withJSONObject: ["Image": "dns:test", "HostConfig": host])
        let created = await router.respond(to: .init(method: .post, target: "/containers/create?name=dns", body: body))
        #expect(created.status == 201)
        let inspected = await router.respond(to: .init(method: .get, target: "/containers/dns/json"))
        #expect(inspected.status == 200)
        let object = try #require(JSONSerialization.jsonObject(with: bytes(inspected)) as? [String: Any])
        let observed = try #require(object["HostConfig"] as? [String: Any])
        for (field, values) in host {
            #expect(observed[field] as? [String] == values)
        }
    }

    @Test(arguments: ["Dns", "DnsSearch", "DnsOptions"])
    func `rejects invalid settings before creation`(_ field: String) async throws {
        let router = DockerRouter(runtime: InMemoryRuntime())
        let body = try JSONSerialization.data(
            withJSONObject: ["Image": "missing", "HostConfig": [field: ["bad\nvalue"]]]
        )
        let response = await router.respond(to: .init(method: .post, target: "/containers/create", body: body))
        #expect(response.status == 400)
    }
}
