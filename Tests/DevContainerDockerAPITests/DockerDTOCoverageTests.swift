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

@Test
func `strict request decoders survive a deterministic malformed corpus`() {
    let seeds = [
        Data(#"{"Image":"alpine:test","Labels":{"fixture":"a=b"}}"#.utf8),
        Data(
            #"{"Image":"alpine:test","HostConfig":{"Mounts":[{"Type":"bind","Source":"/tmp","Target":"/work"}]}}"#
                .utf8
        ),
        Data(
            #"{"Name":"fixture","Driver":"bridge","Labels":{"fixture":"value"}}"#
                .utf8
        ),
        Data(#"{"Name":"fixture","Driver":"local"}"#.utf8),
        Data(#"{"Cmd":["printf","fixture"],"AttachStdout":true}"#.utf8)
    ]
    var generator = DeterministicGenerator(seed: 0xD3C0_17A1_5EED)
    var attempts = 0

    for index in 0 ..< 1024 {
        let data = malformedMutation(
            of: seeds[index % seeds.count],
            generator: &generator
        )
        decodeMalformedRequest(data, kind: index % seeds.count)
        attempts += 1
    }

    #expect(attempts == 1024)
}

@Test
func `generated unknown request fields always fail closed`() throws {
    var generator = DeterministicGenerator(seed: 0xBAD5_C0DE)

    for _ in 0 ..< 128 {
        let field = "FutureField\(String(generator.next(), radix: 16))"
        let data = try JSONSerialization.data(
            withJSONObject: [
                "Image": "alpine:test",
                "HostConfig": [field: generator.next() & 1 == 0]
            ]
        )
        do {
            _ = try DockerJSON.decode(
                DockerCreateContainerRequest.self,
                from: data,
                schema: .createContainer
            )
            Issue.record("strict decoding accepted \(field)")
        } catch {
            #expect(String(describing: error).contains(field))
        }
    }
}

private struct DeterministicGenerator {
    private var state: UInt64

    init(seed: UInt64) {
        state = seed
    }

    mutating func next() -> UInt64 {
        state = state &* 6_364_136_223_846_793_005 &+ 1_442_695_040_888_963_407
        return state
    }
}

private func malformedMutation(
    of seed: Data,
    generator: inout DeterministicGenerator
) -> Data {
    let mutationBytes: [UInt8] = [
        0x00, 0x09, 0x0A, 0x22, 0x2C, 0x3A, 0x5B, 0x5D, 0x7B, 0x7D, 0x80, 0xFF
    ]
    var bytes = Array(seed)
    switch generator.next() % 4 {
    case 0:
        let limit = Int(generator.next() % UInt64(bytes.count + 1))
        bytes = Array(bytes.prefix(limit))
    case 1:
        let offset = Int(generator.next() % UInt64(bytes.count))
        bytes[offset] = mutationBytes[
            Int(generator.next() % UInt64(mutationBytes.count))
        ]
    case 2:
        let offset = Int(generator.next() % UInt64(bytes.count + 1))
        bytes.insert(
            mutationBytes[Int(generator.next() % UInt64(mutationBytes.count))],
            at: offset
        )
    default:
        bytes.append(
            contentsOf: mutationBytes.prefix(
                Int(generator.next() % UInt64(mutationBytes.count + 1))
            )
        )
    }
    return Data(bytes)
}

private func decodeMalformedRequest(_ data: Data, kind: Int) {
    switch kind {
    case 0, 1:
        _ = try? DockerJSON.decode(
            DockerCreateContainerRequest.self,
            from: data,
            schema: .createContainer
        )
    case 2:
        _ = try? DockerJSON.decode(
            DockerNetworkCreateRequest.self,
            from: data,
            schema: .createNetwork
        )
    case 3:
        _ = try? DockerJSON.decode(
            DockerVolumeCreateRequest.self,
            from: data,
            schema: .createVolume
        )
    default:
        _ = try? DockerJSON.decode(
            DockerCreateExecRequest.self,
            from: data,
            schema: .createExec
        )
    }
}
