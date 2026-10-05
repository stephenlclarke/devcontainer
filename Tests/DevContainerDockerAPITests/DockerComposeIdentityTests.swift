//===----------------------------------------------------------------------===//
// Copyright 2026 devcontainer project authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//===----------------------------------------------------------------------===//

import DevContainerCore
@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerState
import DevContainerTestStorage
import DevContainerTestSupport
import Foundation
import Testing

@Test
func `docker create imports complete Compose identity before runtime bootstrap`() async throws {
    let runtime = InMemoryRuntime()
    await runtime.seedImage(ImageSnapshot(
        id: "sha256:compose", references: ["fixture:latest"], createdAt: Date(), size: 1
    ))
    let router = DockerRouter(runtime: runtime)
    let docker = "com.docker.compose."
    let labels = [docker + "project": "example", docker + "service": "database", docker + "oneoff": "False"]
    let body = try JSONSerialization.data(withJSONObject: ["Image": "fixture:latest", "Labels": labels])
    let response = await router.respond(to: DockerHTTPRequest(
        method: .post, target: "/containers/create?name=database", body: body
    ))
    #expect(response.status == 201)
    let snapshot = try #require(await runtime.listContainers(
        all: true, labels: [:], context: RuntimeRequestContext()
    ).first)
    #expect(snapshot.spec.labels["com.apple.container.compose.version"] == "1")
    #expect(snapshot.spec.labels["com.apple.container.compose.service"] == "database")
    #expect(snapshot.spec.labels["com.apple.container.compose.oneoff"] == "false")
    #expect(snapshot.spec.labels[docker + "oneoff"] == "False")
}
