// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerDockerAPI
import DevContainerTestSupport
import Foundation
import Testing

struct DockerEnvironmentOverridesTests {
    @Test func inspectionRetainsBareRemovalKeys() async throws {
        let runtime = InMemoryRuntime()
        await runtime.seedImage(.init(id: "sha256:env", references: ["env:test"], createdAt: Date(), size: 1))
        let router = DockerRouter(runtime: runtime)
        let created = await router.respond(to: .init(
            method: .post, target: "/containers/create?name=env",
            body: Data(#"{"Image":"env:test","Env":["REMOVE","EMPTY="]}"#.utf8)
        ))
        #expect(created.status == 201)
        let inspected = await router.respond(to: .init(method: .get, target: "/containers/env/json"))
        let object = try #require(JSONSerialization.jsonObject(with: bytes(inspected)) as? [String: Any])
        let env = try #require((object["Config"] as? [String: Any])?["Env"] as? [String])
        #expect(Set(env) == ["REMOVE", "EMPTY="])
    }

    @Test func createPreservesRemovalSeparatelyFromEmpty() throws {
        let request = try JSONDecoder().decode(DockerCreateContainerRequest.self, from: Data(
            #"{"Image":"fixture","Env":["REMOVE","EMPTY=","VALUE=a=b"]}"#.utf8
        ))
        let spec = try DockerRouter(runtime: InMemoryRuntime()).containerSpec(from: request, requestedName: "fixture")
        #expect(spec.environment == ["EMPTY": "", "VALUE": "a=b"])
        #expect(spec.removedEnvironmentKeys == ["REMOVE"])
    }
}
