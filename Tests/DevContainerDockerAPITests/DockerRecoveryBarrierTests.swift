// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerTestSupport
import Foundation
import Testing

struct DockerRecoveryBarrierTests {
    private let owner = String(repeating: "a", count: 64)

    @Test func inFlightAndUncertainWorkCannotGrantRecovery() async throws {
        let barrier = DockerRecoveryBarrier()
        let token = try await barrier.begin(.post)
        await #expect(throws: DevContainerError.self) {
            try await barrier.freeze(epoch: barrier.epoch, owner: owner)
        }
        await barrier.finish(token, response: .init(status: 500))
        await #expect(throws: DevContainerError.self) { try await barrier.requireIdle() }
    }

    @Test func preflightRejectionFreezesAllocationsButAllowsExactDeletion() async throws {
        let runtime = InMemoryRuntime()
        let router = DockerRouter(runtime: runtime)
        let session = await router.respond(to: .init(method: .get, target: DockerRecoveryBarrier.route))
        let value = try #require(JSONSerialization.jsonObject(with: bytes(session)) as? [String: String])
        let rejected = await router.respond(to: .init(
            method: .post, target: "/containers/create",
            body: Data(#"{"Image":"unused","HostConfig":{"PidMode":"host"}}"#.utf8)
        ))
        #expect(rejected.status == 501)
        let epoch = try #require(value["epoch"])
        let freeze = await router.respond(to: .init(
            method: .post, target: DockerRecoveryBarrier.route,
            body: try JSONEncoder().encode(["epoch": epoch, "owner": owner])
        ))
        #expect(freeze.status == 200)
        let later = await router.respond(to: .init(method: .post, target: "/containers/create", body: Data("{}".utf8)))
        #expect(later.status == 409)
        let deleted = await router.respond(to: .init(method: .delete, target: "/containers/absent"))
        #expect(deleted.status == 404)
    }

    @Test func epochOwnerAndStreamingUncertaintyFailClosed() async throws {
        let barrier = DockerRecoveryBarrier()
        await #expect(throws: DevContainerError.self) { try await barrier.freeze(epoch: "old", owner: owner) }
        await #expect(throws: DevContainerError.self) { try await barrier.freeze(epoch: barrier.epoch, owner: "bad") }
        let token = try await barrier.begin(.post)
        await barrier.finish(token, response: .init(status: 200, body: .stream(.init { $0.finish() })))
        await #expect(throws: DevContainerError.self) { try await barrier.freeze(epoch: barrier.epoch, owner: owner) }
    }
}
