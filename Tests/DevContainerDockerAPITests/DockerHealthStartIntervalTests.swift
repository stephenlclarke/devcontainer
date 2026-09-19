// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerTestSupport
import Foundation
import Testing

struct DockerHealthStartIntervalTests {
    @Test(arguments: [Int64(0), 1_000_000, 750_000_000])
    func createInspectionPreservesStartupInterval(_ interval: Int64) async throws {
        let runtime = InMemoryRuntime()
        await runtime.seedImage(.init(id: "sha256:health", references: ["health:test"], createdAt: Date(), size: 1))
        let router = DockerRouter(runtime: runtime)
        let body = try JSONSerialization.data(withJSONObject: [
            "Image": "health:test", "Healthcheck": ["Test": ["CMD", "true"], "StartInterval": interval]
        ])
        let created = await router.respond(to: .init(
            method: .post, target: "/containers/create?name=health", body: body
        ))
        #expect(created.status == 201)
        let inspected = await router.respond(to: .init(method: .get, target: "/containers/health/json"))
        let object = try #require(JSONSerialization.jsonObject(with: bytes(inspected)) as? [String: Any])
        let config = try #require(object["Config"] as? [String: Any])
        let health = try #require(config["Healthcheck"] as? [String: Any])
        #expect(health["StartInterval"] as? Int64 == interval)
    }

    @Test(arguments: [Int64(-1), 1, 999_999])
    func invalidIntervalsFailBeforeImageLookup(_ interval: Int64) async throws {
        let body = try JSONSerialization.data(withJSONObject: [
            "Image": "missing", "Healthcheck": ["Test": ["CMD", "true"], "StartInterval": interval]
        ])
        let response = await DockerRouter(runtime: InMemoryRuntime()).respond(
            to: .init(method: .post, target: "/containers/create", body: body)
        )
        #expect(response.status == 400)
    }

    @Test func legacyHealthPolicyDecodesWithoutStartupInterval() throws {
        let json = #"{"test":["CMD","true"],"intervalNanoseconds":1000000000,"timeoutNanoseconds":1000000000,"#
            + #""retries":3,"startPeriodNanoseconds":0}"#
        let data = Data(json.utf8)
        let value = try JSONDecoder().decode(ContainerHealthcheck.self, from: data)
        #expect(value.startIntervalNanoseconds == nil)
        #expect(try JSONDecoder().decode(ContainerHealthcheck.self, from: JSONEncoder().encode(value)) == value)
    }
}
