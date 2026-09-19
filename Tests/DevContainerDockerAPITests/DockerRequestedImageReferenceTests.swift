// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerTestSupport
import Foundation
import Testing

struct DockerRequestedImageReferenceTests {
    private let imageID = "sha256:" + String(repeating: "a", count: 64)

    @Test(arguments: ["fixture:mutable", "fixture:tag@sha256:" + String(repeating: "b", count: 64)])
    func displaySpellingNeverBecomesLaunchIdentity(_ reference: String) async throws {
        let runtime = InMemoryRuntime()
        // The declared spelling is intentionally absent from the image store.
        await runtime.seedImage(.init(id: imageID, references: [], createdAt: Date(), size: 1))
        let router = DockerRouter(runtime: runtime)
        let created = await router.respond(to: .init(
            method: .post, target: "/containers/create?name=alias",
            body: try JSONSerialization.data(withJSONObject: ["Image": imageID, "ContainerImageReference": reference])
        ))
        #expect(created.status == 201)
        let native = try await runtime.inspectContainer(id: "alias", context: .init())
        #expect(native.spec.image == imageID)
        #expect(native.spec.requestedImageReference == reference)
        let data = try JSONEncoder().encode(native.spec)
        #expect(try JSONDecoder().decode(ContainerSpec.self, from: data) == native.spec)
        let response = await router.respond(to: .init(method: .get, target: "/containers/alias/json"))
        let inspected = try #require(JSONSerialization.jsonObject(with: bytes(response)) as? [String: Any])
        let config = try #require(inspected["Config"] as? [String: Any])
        #expect(config["Image"] as? String == reference)
        #expect(inspected["Image"] as? String == imageID)
        let listed = await router.respond(to: .init(method: .get, target: "/containers/json?all=1"))
        let values = try #require(JSONSerialization.jsonObject(with: bytes(listed)) as? [[String: Any]])
        #expect(values.first?["Image"] as? String == reference)
    }

    @Test(arguments: ["", "bad\nreference", String(repeating: "a", count: 1025)])
    func invalidDisplayRejectedBeforeLookup(_ reference: String) async throws {
        let response = await DockerRouter(runtime: InMemoryRuntime()).respond(to: .init(
            method: .post, target: "/containers/create",
            body: try JSONSerialization.data(withJSONObject: ["Image": imageID, "ContainerImageReference": reference])
        ))
        #expect(response.status == 400)
    }

    @Test func mutableLaunchIdentityCannotUseDisplayExtension() async throws {
        let response = await DockerRouter(runtime: InMemoryRuntime()).respond(to: .init(
            method: .post, target: "/containers/create",
            body: Data(#"{"Image":"fixture:mutable","ContainerImageReference":"other:tag"}"#.utf8)
        ))
        #expect(response.status == 400)
    }

    @Test func providerCannotSubstituteTheSelectedImage() async throws {
        let runtime = InMemoryRuntime()
        await runtime.seedImage(.init(
            id: "sha256:" + String(repeating: "b", count: 64), references: [imageID], createdAt: Date(), size: 1
        ))
        let response = await DockerRouter(runtime: runtime).respond(to: .init(
            method: .post, target: "/containers/create",
            body: try JSONSerialization.data(withJSONObject: [
                "Image": imageID, "ContainerImageReference": "fixture:mutable"
            ])
        ))
        #expect(response.status >= 400)
        #expect(await runtime.listContainers(all: true, labels: [:], context: .init()).isEmpty)
    }
}
