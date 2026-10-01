// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerTestSupport
import Foundation
import Testing

@Test
func `actual image endpoint preserves native ancestry and does not invent missing layers`() async throws {
    let runtime = InMemoryRuntime()
    let router = DockerRouter(runtime: runtime)
    let expected = ["sha256:" + String(repeating: "a", count: 64), "sha256:" + String(repeating: "b", count: 64)]
    for layers: [String]? in [expected, [], nil] {
        await runtime.seedImage(.init(
            id: "sha256:fixture",
            references: ["fixture:latest"],
            createdAt: .distantPast,
            size: 0,
            rootFSLayers: layers
        ))
        let response = await router.respond(to: .init(method: .get, target: "/images/fixture:latest/json"))
        #expect(response.status == 200)
        let object = try #require(JSONSerialization.jsonObject(with: bytes(response)) as? [String: Any])
        let root = object["RootFS"] as? [String: Any]
        if let layers {
            #expect(root?["Type"] as? String == "layers")
            #expect(root?["Layers"] as? [String] == layers)
        } else {
            #expect(root == nil)
        }
    }
}
