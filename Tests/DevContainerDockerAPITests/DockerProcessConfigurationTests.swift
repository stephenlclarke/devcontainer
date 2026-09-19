// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerTestSupport
import Foundation
import Testing

struct DockerProcessConfigurationTests {
    @Test(arguments: [
        "{}",
        #"{"Entrypoint":null}"#,
        #"{"Entrypoint":[]}"#,
        #"{"Entrypoint":""}"#,
        #"{"Entrypoint":[""]}"#,
        #"{"Entrypoint":["/custom"]}"#
    ])
    func `preserve entrypoint presence`(_ input: String) throws {
        var object = try #require(JSONSerialization.jsonObject(with: Data(input.utf8)) as? [String: Any])
        object["Image"] = "fixture"
        let request = try JSONDecoder().decode(
            DockerCreateContainerRequest.self, from: JSONSerialization.data(withJSONObject: object)
        )
        let spec = try DockerRouter(runtime: InMemoryRuntime()).containerSpec(from: request, requestedName: "test")
        #expect(spec.inheritImageEntrypoint == (request.entrypoint == nil))
        #expect(spec.entrypoint == request.entrypoint?.values ?? [])
    }
}
