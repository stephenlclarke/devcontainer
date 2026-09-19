// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerTestSupport
import Foundation
import Testing

struct DockerStopTimeoutTests {
    @Test(arguments: [0, 7, Int(Int32.max)])
    func createInspectionPreservesTimeout(_ seconds: Int) async throws {
        let (router, _) = try await prepared(seconds: seconds)
        let response = await router.respond(to: .init(method: .get, target: "/containers/grace/json"))
        let object = try #require(JSONSerialization.jsonObject(with: bytes(response)) as? [String: Any])
        let config = try #require(object["Config"] as? [String: Any])
        #expect(config["StopTimeout"] as? Int == seconds)
    }

    @Test(arguments: ["stop", "restart"], [nil, 0, 7] as [Int?])
    func omittedOverrideUsesContainerPolicy(_ action: String, _ configured: Int?) async throws {
        let (router, runtime) = try await prepared(seconds: configured)
        let response = await router.respond(to: .init(method: .post, target: "/containers/grace/\(action)"))
        #expect(response.status == 204)
        #expect(await runtime.stopTimeouts == [.seconds(configured ?? 10)])
    }

    @Test(arguments: ["stop", "restart"], [0, 3])
    func explicitOverrideWins(_ action: String, _ seconds: Int) async throws {
        let (router, runtime) = try await prepared(seconds: 7)
        let response = await router.respond(to: .init(
            method: .post, target: "/containers/grace/\(action)?t=\(seconds)"
        ))
        #expect(response.status == 204)
        #expect(await runtime.stopTimeouts == [.seconds(seconds)])
    }

    @Test(arguments: ["stop", "restart"], ["abc", "", "9223372036854775808", "-1", "2147483648"])
    func invalidOverrideDoesNotSignal(_ action: String, _ value: String) async throws {
        let (router, runtime) = try await prepared(seconds: 7)
        let response = await router.respond(to: .init(
            method: .post, target: "/containers/grace/\(action)?t=\(value)"
        ))
        #expect(response.status == (Int64(value) == nil ? 400 : 501))
        #expect(await runtime.stopTimeouts.isEmpty)
        let inspected = await router.respond(to: .init(method: .get, target: "/containers/grace/json"))
        let object = try #require(JSONSerialization.jsonObject(with: bytes(inspected)) as? [String: Any])
        let state = try #require(object["State"] as? [String: Any])
        #expect(state["Running"] as? Bool == true)
    }

    @Test(arguments: [-1, Int(Int32.max) + 1])
    func unsupportedPolicyFailsBeforeAllocation(_ seconds: Int) async throws {
        let response = await DockerRouter(runtime: InMemoryRuntime()).respond(to: .init(
            method: .post, target: "/containers/create",
            body: try JSONSerialization.data(withJSONObject: ["Image": "missing", "StopTimeout": seconds])
        ))
        #expect(response.status == 501)
    }

    @Test func persistedPolicyAndLegacyMetadataDecode() throws {
        var spec = ContainerSpec(name: "grace", image: "test")
        let legacy = try JSONEncoder().encode(spec)
        #expect(try JSONDecoder().decode(ContainerSpec.self, from: legacy).stopTimeoutSeconds == nil)
        spec.stopTimeoutSeconds = 7
        #expect(try JSONDecoder().decode(ContainerSpec.self, from: JSONEncoder().encode(spec)) == spec)
    }

    private func prepared(seconds: Int?) async throws -> (DockerRouter, InMemoryRuntime) {
        let runtime = InMemoryRuntime()
        await runtime.seedImage(.init(id: "sha256:grace", references: ["grace:test"], createdAt: Date(), size: 1))
        let router = DockerRouter(runtime: runtime)
        var fields: [String: Any] = ["Image": "grace:test"]
        if let seconds { fields["StopTimeout"] = seconds }
        let response = await router.respond(to: .init(
            method: .post, target: "/containers/create?name=grace",
            body: try JSONSerialization.data(withJSONObject: fields)
        ))
        #expect(response.status == 201)
        let started = await router.respond(to: .init(method: .post, target: "/containers/grace/start"))
        #expect(started.status == 204)
        return (router, runtime)
    }
}
