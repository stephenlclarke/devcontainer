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
import DevContainerModel
import DevContainerTestSupport
import Foundation
import Testing

@Test
func `negotiation works with versioned and unversioned paths`() async throws {
    let router = DockerRouter(runtime: InMemoryRuntime())
    let ping = await router.respond(to: DockerHTTPRequest(method: .get, target: "/_ping"))
    #expect(ping.status == 200)
    #expect(try bytes(ping) == Data("OK".utf8))
    #expect(ping.headers["API-Version"] == "1.53")

    let version = await router.respond(
        to: DockerHTTPRequest(method: .get, target: "/v1.53/version")
    )
    #expect(version.status == 200)
    let object = try JSONSerialization.jsonObject(with: bytes(version)) as? [String: Any]
    #expect(object?["ApiVersion"] as? String == "1.53")
    #expect(object?["Os"] as? String == "linux")
}

@Test
func `network addresses separate cidr prefixes for docker clients`() {
    let ipv4 = DockerRouter.networkAddress("192.0.2.10/24")
    #expect(ipv4.address == "192.0.2.10")
    #expect(ipv4.prefixLength == 24)

    let ipv6 = DockerRouter.networkAddress("2001:db8::10/64")
    #expect(ipv6.address == "2001:db8::10")
    #expect(ipv6.prefixLength == 64)

    let unqualified = DockerRouter.networkAddress("192.0.2.10")
    #expect(unqualified.address == "192.0.2.10")
    #expect(unqualified.prefixLength == 0)
}

@Test
func `container lifecycle and inspection use docker shapes`() async throws {
    let runtime = InMemoryRuntime()
    await runtime.seedImage(
        ImageSnapshot(
            id: "sha256:image",
            references: ["alpine:3.22"],
            createdAt: Date(timeIntervalSince1970: 1),
            size: 100
        )
    )
    let router = DockerRouter(runtime: runtime)
    let id = try await createLifecycleContainer(router)
    try await assertLifecycleInspectAndList(router, id: id)

    let stop = await router.respond(
        to: DockerHTTPRequest(method: .post, target: "/containers/\(id)/stop?t=1")
    )
    #expect(stop.status == 204)
    let remove = await router.respond(
        to: DockerHTTPRequest(method: .delete, target: "/containers/\(id)?force=true")
    )
    #expect(remove.status == 204)
}

@Test
func `container create distinguishes bind named and anonymous volumes`() async throws {
    let runtime = InMemoryRuntime()
    await runtime.seedImage(
        ImageSnapshot(
            id: "sha256:mounts",
            references: ["mounts:latest"],
            createdAt: Date(),
            size: 1
        )
    )
    let router = DockerRouter(runtime: runtime)
    try await assertMountTypes(
        createMountSnapshot(runtime: runtime, router: router)
    )
}

@Test
func `exec creation and framing are docker compatible`() async throws {
    let runtime = InMemoryRuntime()
    await runtime.seedImage(
        ImageSnapshot(
            id: "sha256:image",
            references: ["alpine:3.22"],
            createdAt: Date(),
            size: 1
        )
    )
    let created = try await runtime.createContainer(
        spec: ContainerSpec(name: "demo", image: "alpine:3.22"),
        context: RuntimeRequestContext()
    )
    try await runtime.startContainer(id: created.dockerID.rawValue, context: RuntimeRequestContext())
    let router = DockerRouter(runtime: runtime)
    let execBody = try JSONSerialization.data(
        withJSONObject: [
            "Cmd": ["printf", "hello"],
            "AttachStdout": true,
            "AttachStderr": true,
            "Tty": false
        ]
    )
    let response = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/containers/\(created.dockerID.rawValue)/exec",
            body: execBody
        )
    )
    #expect(response.status == 201)

    let framed = DockerStreamFraming.encode(
        RuntimeIOFrame(channel: .standardError, data: Data("error".utf8)),
        terminal: false
    )
    #expect(Array(framed.prefix(4)) == [2, 0, 0, 0])
    #expect(Array(framed[4 ..< 8]) == [0, 0, 0, 5])
    #expect(Data(framed.dropFirst(8)) == Data("error".utf8))
    #expect(
        DockerStreamFraming.encode(
            RuntimeIOFrame(channel: .standardOutput, data: Data("raw".utf8)),
            terminal: true
        ) == Data("raw".utf8)
    )
}

@Test
func `unknown routes return docker not found envelope`() async throws {
    let router = DockerRouter(runtime: InMemoryRuntime())
    let response = await router.respond(
        to: DockerHTTPRequest(method: .post, target: "/swarm/init")
    )
    #expect(response.status == 404)
    let object = try JSONSerialization.jsonObject(with: bytes(response)) as? [String: Any]
    #expect(object?["message"] as? String == "page not found")
}

@Test
func `image endpoints cover pull inspect tag build and delete`() async throws {
    let runtime = InMemoryRuntime()
    let router = DockerRouter(runtime: runtime)

    try await assertImagePullInspectAndTag(router)
    try await assertImageBuildLoadAndDelete(router)
}

@Test
func `network and volume endpoints follow docker shapes`() async throws {
    let runtime = InMemoryRuntime()
    let router = DockerRouter(runtime: runtime)

    try await assertNetworkEndpoints(router)
    try await assertVolumeEndpoints(router)
}

@Test
func `archives logs events and docker filter maps are streamed`() async throws {
    let runtime = InMemoryRuntime()
    await runtime.seedImage(
        ImageSnapshot(
            id: "sha256:image",
            references: ["alpine:test"],
            createdAt: Date(),
            size: 1
        )
    )
    let container = try await runtime.createContainer(
        spec: ContainerSpec(
            name: "archive",
            image: "alpine:test",
            labels: [
                "project": "demo",
                "com.apple.container.compose.project": "workspace",
                "com.apple.container.compose.service": "app"
            ]
        ),
        context: RuntimeRequestContext()
    )
    let router = DockerRouter(runtime: runtime)
    try await assertArchiveLogAndEventStreams(router: router, container: container)
}

@Test
func `create rejects unknown security and resource drivers`() async throws {
    let runtime = InMemoryRuntime()
    await runtime.seedImage(
        ImageSnapshot(
            id: "sha256:image",
            references: ["alpine:test"],
            createdAt: Date(),
            size: 1
        )
    )
    let router = DockerRouter(runtime: runtime)
    let body = try JSONSerialization.data(
        withJSONObject: [
            "Image": "alpine:test",
            "HostConfig": ["SecurityOpt": ["label=disable"]]
        ]
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .post, target: "/containers/create", body: body)
        ).status == 501
    )
    let network = try JSONSerialization.data(
        withJSONObject: ["Name": "bad", "Driver": "overlay"]
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .post, target: "/networks/create", body: network)
        ).status == 501
    )
}

@Test
func `daemon information head requests and invalid inputs use Docker semantics`() async throws {
    let runtime = InMemoryRuntime(version: "1.1.0", commit: "fixture", distribution: "apple")
    await runtime.seedImage(
        ImageSnapshot(
            id: "sha256:fixture",
            references: ["fixture:latest"],
            createdAt: Date(),
            size: 42
        )
    )
    let router = DockerRouter(runtime: runtime)

    let head = await router.respond(to: DockerHTTPRequest(method: .head, target: "/_ping"))
    #expect(head.status == 200)
    #expect(try bytes(head).isEmpty)
    let info = await router.respond(to: DockerHTTPRequest(method: .get, target: "/info"))
    let infoObject = try JSONSerialization.jsonObject(with: bytes(info)) as? [String: Any]
    #expect(infoObject?["Images"] as? Int == 1)
    #expect(infoObject?["ServerVersion"] as? String == "1.1.0")
    let images = await router.respond(to: DockerHTTPRequest(method: .get, target: "/images/json"))
    #expect(try (JSONSerialization.jsonObject(with: bytes(images)) as? [[String: Any]])?.count == 1)

    for request in [
        DockerHTTPRequest(method: .get, target: ""),
        DockerHTTPRequest(method: .post, target: "/containers/create", body: Data("{".utf8)),
        DockerHTTPRequest(method: .post, target: "/images/create"),
        DockerHTTPRequest(method: .post, target: "/images/fixture:latest/tag"),
        DockerHTTPRequest(method: .get, target: "/containers/json?filters=%5B%5D"),
        DockerHTTPRequest(method: .get, target: "/containers/json?filters=%7B%22label%22:%221%22%7D"),
        DockerHTTPRequest(
            method: .get,
            target: "/containers/json?filters=%7B%22label%22:%5B%22a=1%22,%22a=2%22%5D%7D"
        ),
        DockerHTTPRequest(method: .get, target: "/events?since=not-a-date")
    ] {
        #expect(await router.respond(to: request).status == 400)
    }
}

@Test
func `advanced container exec archive and stream routes are compatible`() async throws {
    let runtime = InMemoryRuntime()
    await runtime.seedImage(
        ImageSnapshot(
            id: "sha256:fixture",
            references: ["fixture:latest"],
            createdAt: Date(),
            size: 42
        )
    )
    let router = DockerRouter(runtime: runtime)
    let containerID = try await createRunningAdvancedContainer(
        router,
        body: advancedContainerBody()
    )
    try await assertAdvancedAttachLogsAndArchives(router, containerID: containerID)
    let execID = try await createAdvancedExec(router, containerID: containerID)
    try await assertAdvancedExecLifecycle(router, execID: execID)
    try await assertAdvancedKillWaitAndRemove(router, containerID: containerID)
}

@Test
func `network connections anonymous volumes filters and event actions are exercised`() async throws {
    let runtime = InMemoryRuntime()
    await runtime.seedImage(
        ImageSnapshot(
            id: "sha256:fixture",
            references: ["fixture:latest"],
            createdAt: Date(),
            size: 42
        )
    )
    let container = try await runtime.createContainer(
        spec: ContainerSpec(name: "fixture", image: "fixture:latest", labels: ["project": "demo"]),
        context: RuntimeRequestContext()
    )
    let router = DockerRouter(runtime: runtime)

    _ = try await connectFixtureNetwork(router: router, container: container)
    try await assertAnonymousVolumeRoutes(router)
    try await assertFilteredEventActions(router)
}

@Test
func `in memory runtime negative paths and process controls are deterministic`() async throws {
    let runtime = InMemoryRuntime()
    let context = RuntimeRequestContext()

    await assertMissingImageFailures(runtime, context: context)
    try await assertContainerNegativePaths(runtime, context: context)
    try await assertProcessSessionControls()
}

@Test
func `health checks are decoded executed and projected through inspect`() async throws {
    let runtime = InMemoryRuntime()
    await runtime.seedImage(
        ImageSnapshot(
            id: "sha256:health",
            references: ["health:latest"],
            createdAt: Date(),
            size: 1
        )
    )
    let router = DockerRouter(runtime: runtime)
    let identifier = try await createHealthContainer(router)
    try await assertHealthInspect(router, containerID: identifier)
}
