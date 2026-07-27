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

@testable import DevContainerDockerAPI
import Foundation
import Testing

@Test
func `docker server container and exec response defaults encode`() throws {
    let encoder = JSONEncoder()
    let version = DockerVersionResponse(
        platform: DockerVersionPlatform(
            name: "devcontainer Apple runtime bridge"
        ),
        components: [],
        version: "fixture",
        apiVersion: "1.53",
        minAPIVersion: "1.41",
        gitCommit: "fixture",
        operatingSystem: "linux",
        buildTime: "2026-01-01T00:00:00Z"
    )
    #expect(version.platform.name == "devcontainer Apple runtime bridge")
    let responses: [Data] = try [
        encoder.encode(version),
        encoder.encode(
            DockerInfoResponse(
                containers: 2,
                containersRunning: 1,
                containersStopped: 1,
                images: 3,
                serverVersion: "fixture"
            )
        ),
        encoder.encode(
            DockerContainerState(
                status: "running",
                running: true,
                pid: 42,
                exitCode: 0,
                startedAt: "2026-01-01T00:00:00Z",
                finishedAt: "",
                health: nil
            )
        ),
        encoder.encode(DockerInspectHostConfig(binds: [])),
        encoder.encode(
            DockerExecProcessConfig(
                tty: false,
                entrypoint: "printf",
                arguments: ["fixture"],
                user: "developer"
            )
        )
    ]
    #expect(responses.allSatisfy { !$0.isEmpty })
}

@Test
func `docker image response defaults encode`() throws {
    let encoder = JSONEncoder()
    let responses: [Data] = try [
        encoder.encode(
            DockerImageSummary(
                created: 1,
                id: "sha256:fixture",
                repoDigests: [],
                repoTags: ["fixture:latest"],
                size: 1,
                virtualSize: 1
            )
        ),
        encoder.encode(
            DockerImageConfig(
                user: "developer",
                environment: [],
                entrypoint: nil,
                command: nil,
                labels: [:]
            )
        )
    ]
    #expect(responses.allSatisfy { !$0.isEmpty })
}

@Test
func `docker network and volume response defaults encode`() throws {
    let encoder = JSONEncoder()
    let responses: [Data] = try [
        encoder.encode(
            DockerNetworkInspect(
                name: "fixture",
                id: "network-fixture",
                created: "2026-01-01T00:00:00Z",
                driver: "bridge",
                internalNetwork: false,
                containers: [:],
                labels: [:]
            )
        ),
        encoder.encode(
            DockerNetworkContainer(
                name: "fixture",
                ipv4Address: "192.0.2.2/24"
            )
        ),
        encoder.encode(
            DockerVolumeInspect(
                createdAt: "2026-01-01T00:00:00Z",
                driver: "local",
                labels: [:],
                mountpoint: "/volumes/fixture",
                name: "fixture"
            )
        )
    ]
    #expect(responses.allSatisfy { !$0.isEmpty })
}

@Test
func `string or array decodes both Docker entrypoint forms`() throws {
    let scalar = try JSONDecoder().decode(
        StringOrArray.self,
        from: Data(#""entrypoint""#.utf8)
    )
    let array = try JSONDecoder().decode(
        StringOrArray.self,
        from: Data(#"["entrypoint", "--flag"]"#.utf8)
    )
    #expect(scalar.values == ["entrypoint"])
    #expect(array.values == ["entrypoint", "--flag"])
}
