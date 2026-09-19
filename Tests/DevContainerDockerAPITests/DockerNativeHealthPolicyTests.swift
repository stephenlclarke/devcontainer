// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerTestSupport
import Foundation
import Testing

@Test(arguments: [CapabilityStatus.emulated, .native, .unsupported])
func `native health policy requires explicit provider capability`(status: CapabilityStatus) async throws {
    let router = DockerRouter(runtime: InMemoryRuntime(capabilities: [.composeHealthPolicy: status]))
    let response = await router.respond(to: DockerHTTPRequest(method: .get, target: "/v1.53/version"))
    #expect(response.status == 200)
    let value = try #require(try JSONSerialization.jsonObject(with: bytes(response)) as? [String: Any])
    let components = try #require(value["Components"] as? [[String: Any]])
    let details = try #require(components.first?["Details"] as? [String: String])
    #expect(details["NativeComposeHealthPolicy"] == (status == .emulated ? "1" : "0"))
}
