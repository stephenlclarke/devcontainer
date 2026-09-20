// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerCore
@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerTestSupport
import Foundation
import Testing

struct DockerVolumeProjectionTests {
    private let nativeProject = "com.apple.container.compose.project"
    private let nativeVolume = "com.apple.container.compose.volume"
    private let dockerProject = "com.docker.compose.project"
    private let dockerVolume = "com.docker.compose.volume"

    @Test
    func `native volume inspection and filtering project compose labels`() async throws {
        let runtime = InMemoryRuntime()
        let labels = [nativeProject: "demo", nativeVolume: "cache", "user.label": "retained"]
        _ = await runtime.createVolume(
            spec: VolumeSpec(name: "demo_cache", labels: labels), context: RuntimeRequestContext()
        )
        let router = DockerRouter(runtime: runtime)
        let inspected = await router.respond(to: DockerHTTPRequest(method: .get, target: "/v1.53/volumes/demo_cache"))
        #expect(inspected.status == 200)
        let value = try #require(JSONSerialization.jsonObject(with: bytes(inspected)) as? [String: Any])
        let projected = try #require(value["Labels"] as? [String: String])
        #expect(projected == labels.merging([dockerProject: "demo", dockerVolume: "cache"]) { _, new in new })
        for (filter, expected) in [
            (dockerProject + "=demo", 1),
            (dockerVolume + "=cache", 1),
            (dockerVolume + "=missing", 0),
            (nativeVolume + "=cache", 1)
        ] {
            let filterData = try JSONSerialization.data(withJSONObject: ["label": [filter]])
            let query = try #require(String(data: filterData, encoding: .utf8)?
                .addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed))
            let listed = await router.respond(to: DockerHTTPRequest(method: .get, target: "/volumes?filters=" + query))
            #expect(listed.status == 200)
            let object = try #require(JSONSerialization.jsonObject(with: bytes(listed)) as? [String: Any])
            let volumes = try #require(object["Volumes"] as? [[String: Any]])
            #expect(volumes.count == expected)
            if let volume = volumes.first {
                #expect(volume["Labels"] as? [String: String] == projected)
            }
        }
        let native = try await runtime.inspectVolume(name: "demo_cache", context: RuntimeRequestContext())
        #expect(native.spec.labels == labels)
    }

    @Test
    func `conflicting native volume labels fail inspection and listing`() async {
        for (native, docker) in [(nativeProject, dockerProject), (nativeVolume, dockerVolume)] {
            let runtime = InMemoryRuntime()
            _ = await runtime.createVolume(
                spec: VolumeSpec(name: "conflict", labels: [native: "one", docker: "two"]),
                context: RuntimeRequestContext()
            )
            let router = DockerRouter(runtime: runtime)
            for path in ["/volumes/conflict", "/volumes"] {
                let response = await router.respond(to: DockerHTTPRequest(method: .get, target: path))
                #expect(response.status == 409)
            }
        }
    }

    @Test
    func `volume creation projects compatible labels and rejects conflicts before mutation`() async throws {
        for conflicting in [false, true] {
            let runtime = InMemoryRuntime()
            let router = DockerRouter(runtime: runtime)
            let labels = [nativeVolume: "cache", dockerVolume: conflicting ? "other" : "cache"]
            let response = try await router.respond(to: DockerHTTPRequest(
                method: .post, target: "/volumes/create",
                body: JSONSerialization.data(withJSONObject: ["Name": "demo_cache", "Labels": labels])
            ))
            #expect(response.status == (conflicting ? 409 : 201))
            let volumes = await runtime.listVolumes(context: RuntimeRequestContext())
            #expect(volumes.count == (conflicting ? 0 : 1))
            if !conflicting {
                let object = try #require(JSONSerialization.jsonObject(with: bytes(response)) as? [String: Any])
                #expect(object["Labels"] as? [String: String] == labels)
            }
        }
    }
}
