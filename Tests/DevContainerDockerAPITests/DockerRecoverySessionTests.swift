// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerTestSupport
import Foundation
import Testing

struct DockerRecoverySessionTests {
    private struct Fixture {
        let router: DockerRouter
        let id: String
        let session: RecoveryProcessSession
    }

    private func fixture(health: Bool = false, healthFailure: Bool = false) async throws -> Fixture {
        let session = RecoveryProcessSession(failAfterRelease: healthFailure)
        let runtime = InMemoryRuntime(execSession: session)
        await runtime.seedImage(.init(id: "sha256:recovery", references: ["recovery:test"], createdAt: Date(), size: 1))
        let spec = ContainerSpec(
            name: "recovery", image: "recovery:test",
            healthcheck: health ? ContainerHealthcheck(test: ["CMD", "true"], intervalNanoseconds: 1) : nil
        )
        let container = try await runtime.createContainer(spec: spec, context: .init())
        try await runtime.startContainer(id: container.dockerID.rawValue, context: .init())
        return Fixture(router: DockerRouter(runtime: runtime), id: container.dockerID.rawValue, session: session)
    }

    private func freezeRequest(_ router: DockerRouter) async throws -> DockerHTTPRequest {
        let response = await router.respond(to: .init(method: .get, target: DockerRecoveryBarrier.route))
        let value = try #require(JSONSerialization.jsonObject(with: bytes(response)) as? [String: String])
        let epoch = try #require(value["epoch"])
        return try .init(
            method: .post, target: DockerRecoveryBarrier.route,
            body: JSONEncoder().encode(["epoch": epoch, "owner": String(repeating: "a", count: 64)])
        )
    }

    @Test func inFlightHealthPreventsFreezeAndFrozenInspectionDoesNotProbe() async throws {
        let fixture = try await fixture(health: true)
        let router = fixture.router, id = fixture.id, session = fixture.session
        let freeze = try await freezeRequest(router)
        let inspection = Task { await router.respond(to: .init(method: .get, target: "/containers/\(id)/json")) }
        await session.waitForStart()
        #expect(await router.respond(to: freeze).status == 409)
        await session.release()
        #expect(await inspection.value.status == 200)
        #expect(await router.respond(to: freeze).status == 200)
        // One nanosecond interval means a normal subsequent inspect is due.
        let cached = await router.respond(to: .init(method: .get, target: "/containers/\(id)/json"))
        #expect(cached.status == 200)
        let object = try #require(JSONSerialization.jsonObject(with: bytes(cached)) as? [String: Any])
        let state = try #require(object["State"] as? [String: Any])
        #expect((state["Health"] as? [String: Any])?["Status"] as? String == "healthy")
        #expect(await session.waitCount == 1)
        #expect(await router.respond(to: freeze).status == 200)
    }

    @Test func freezeBeforeFirstHealthInspectionDoesNotLaunchProbe() async throws {
        let fixture = try await fixture(health: true)
        let router = fixture.router, id = fixture.id, session = fixture.session
        let freeze = try await freezeRequest(router)
        #expect(await router.respond(to: freeze).status == 200)
        #expect(await router.respond(to: .init(method: .get, target: "/containers/\(id)/json")).status == 200)
        #expect(await session.waitCount == 0)
        #expect(await router.respond(to: freeze).status == 200)
    }

    @Test func failedHealthCompletionCannotGrantQuiescence() async throws {
        let fixture = try await fixture(health: true, healthFailure: true)
        let router = fixture.router, id = fixture.id, session = fixture.session
        let freeze = try await freezeRequest(router)
        let inspection = Task { await router.respond(to: .init(method: .get, target: "/containers/\(id)/json")) }
        await session.waitForStart()
        await session.release()
        #expect(await inspection.value.status == 200)
        #expect(await router.respond(to: freeze).status == 409)
    }

    @Test(arguments: [false, true])
    func attachSessionsPreventRecovery(webSocket: Bool) async throws {
        let fixture = try await fixture()
        let router = fixture.router, id = fixture.id
        let freeze = try await freezeRequest(router)
        let attach = await router.respond(to: .init(
            method: webSocket ? .get : .post,
            target: "/containers/\(id)/attach" + (webSocket ? "/ws" : "")
        ))
        #expect(attach.status == 101)
        #expect(await router.respond(to: freeze).status == 409)
        // A failed freeze still closes admission for new writable sessions.
        #expect(await router.respond(to: .init(method: .get, target: "/v1.47/containers/\(id)/attach/ws")).status == 409)
    }

    @Test(arguments: [false, true])
    func attachedAndDetachedExecPreventRecovery(detached: Bool) async throws {
        let fixture = try await fixture()
        let router = fixture.router, id = fixture.id, session = fixture.session
        let freeze = try await freezeRequest(router)
        let created = await router.respond(to: .init(
            method: .post, target: "/containers/\(id)/exec", body: Data(#"{"Cmd":["sleep","10"]}"#.utf8)
        ))
        let object = try #require(JSONSerialization.jsonObject(with: bytes(created)) as? [String: String])
        let execID = try #require(object["Id"])
        let started = await router.respond(to: .init(
            method: .post, target: "/exec/\(execID)/start",
            body: try JSONEncoder().encode(["Detach": detached])
        ))
        #expect(started.status == (detached ? 200 : 101))
        await session.waitForStart()
        #expect(await router.respond(to: freeze).status == 409)
        await session.release()
        // No lifetime receipt yet: observed completion cannot erase uncertainty.
        #expect(await router.respond(to: freeze).status == 409)
    }
}
