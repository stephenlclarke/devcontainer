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
    let body = try JSONSerialization.data(
        withJSONObject: [
            "Image": "alpine:3.22",
            "Cmd": ["sleep", "infinity"],
            "Env": ["A=B"],
            "Labels": [
                "devcontainer.local_folder": "/workspace",
                "com.apple.container.compose.project": "demo"
            ],
            "WorkingDir": "/workspace",
            "Tty": true,
            "HostConfig": [
                "Binds": ["/tmp/source:/workspace:ro"],
                "PortBindings": [
                    "8080/tcp": [
                        ["HostIp": "127.0.0.1", "HostPort": "18080"]
                    ]
                ]
            ]
        ]
    )
    let create = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/v1.53/containers/create?name=demo",
            body: body
        )
    )
    #expect(create.status == 201)
    let createObject = try JSONSerialization.jsonObject(with: bytes(create)) as? [String: Any]
    let id = try #require(createObject?["Id"] as? String)

    let start = await router.respond(
        to: DockerHTTPRequest(method: .post, target: "/containers/\(id)/start")
    )
    #expect(start.status == 204)

    let inspect = await router.respond(
        to: DockerHTTPRequest(method: .get, target: "/containers/\(id)/json")
    )
    #expect(inspect.status == 200)
    let inspected = try JSONSerialization.jsonObject(with: bytes(inspect)) as? [String: Any]
    #expect(inspected?["Name"] as? String == "/demo")
    let state = inspected?["State"] as? [String: Any]
    #expect(state?["Running"] as? Bool == true)
    let config = inspected?["Config"] as? [String: Any]
    #expect(config?["WorkingDir"] as? String == "/workspace")

    let list = await router.respond(
        to: DockerHTTPRequest(
            method: .get,
            target: "/containers/json?all=true&filters="
                + "%7B%22label%22:%5B%22devcontainer.local_folder="
                + "/workspace%22%5D%7D"
        )
    )
    #expect(list.status == 200)
    #expect(
        try (JSONSerialization.jsonObject(
            with: bytes(list)
        ) as? [[String: Any]])?.count == 1
    )
    let projectedList = await router.respond(
        to: DockerHTTPRequest(
            method: .get,
            target: "/containers/json?all=true&filters=%7B%22label%22:%5B%22com.docker.compose.project=demo%22%5D%7D"
        )
    )
    #expect(
        try (JSONSerialization.jsonObject(
            with: bytes(projectedList)
        ) as? [[String: Any]])?.count == 1
    )

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
    let body = try JSONSerialization.data(
        withJSONObject: [
            "Image": "mounts:latest",
            "Volumes": ["/declared": [:]],
            "HostConfig": [
                "Binds": [
                    "/tmp/source:/bind:ro",
                    "named-cache:/named"
                ],
                "Mounts": [
                    [
                        "Type": "volume",
                        "Target": "/structured"
                    ]
                ]
            ]
        ]
    )
    let response = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/containers/create?name=mounts",
            body: body
        )
    )
    #expect(response.status == 201)
    let object = try JSONSerialization.jsonObject(with: bytes(response)) as? [String: Any]
    let identifier = try #require(object?["Id"] as? String)
    let snapshot = try await runtime.inspectContainer(
        id: identifier,
        context: RuntimeRequestContext()
    )
    #expect(
        snapshot.spec.mounts.first { $0.destination == "/bind" }
            == RuntimeMount(
                type: .bind,
                source: "/tmp/source",
                destination: "/bind",
                readOnly: true
            )
    )
    #expect(
        snapshot.spec.mounts.first { $0.destination == "/named" }
            == RuntimeMount(
                type: .volume,
                source: "named-cache",
                destination: "/named"
            )
    )
    for destination in ["/structured", "/declared"] {
        let mount = try #require(
            snapshot.spec.mounts.first { $0.destination == destination }
        )
        #expect(mount.type == .volume)
        #expect(mount.source.hasPrefix("devcontainer-"))
        #expect(mount.anonymous == true)
    }
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

    let pull = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/images/create?fromImage=alpine&tag=3.22"
        )
    )
    #expect(pull.status == 200)
    #expect(
        try await String(data: streamBytes(pull), encoding: .utf8)?
            .contains("\"status\"") == true
    )

    let inspect = await router.respond(
        to: DockerHTTPRequest(method: .get, target: "/images/alpine:3.22/json")
    )
    #expect(inspect.status == 200)
    let inspected = try JSONSerialization.jsonObject(with: bytes(inspect)) as? [String: Any]
    #expect(inspected?["Architecture"] as? String == "arm64")

    let tag = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/images/alpine:3.22/tag?repo=example/alpine&tag=test"
        )
    )
    #expect(tag.status == 201)

    let build = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/build?dockerfile=Containerfile&t=example%2Fbuilt%3Alatest&buildargs=%7B%22A%22%3A%22B%22%7D",
            body: Data("context".utf8)
        )
    )
    #expect(build.status == 200)
    #expect(try await !streamBytes(build).isEmpty)

    let load = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/images/load?quiet=0",
            body: Data("image-archive".utf8)
        )
    )
    #expect(load.status == 200)
    #expect(
        try await String(data: streamBytes(load), encoding: .utf8)?
            .contains("Loaded image") == true
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .post, target: "/images/load")
        ).status == 400
    )

    let remove = await router.respond(
        to: DockerHTTPRequest(method: .delete, target: "/images/example%2Falpine:test")
    )
    #expect(remove.status == 200)
}

@Test
func `network and volume endpoints follow docker shapes`() async throws {
    let runtime = InMemoryRuntime()
    let router = DockerRouter(runtime: runtime)

    let networkBody = try JSONSerialization.data(
        withJSONObject: [
            "Name": "demo-network",
            "Driver": "",
            "Labels": ["project": "demo"]
        ]
    )
    let createNetwork = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/networks/create",
            body: networkBody
        )
    )
    #expect(createNetwork.status == 201)
    let networkObject = try JSONSerialization.jsonObject(
        with: bytes(createNetwork)
    ) as? [String: Any]
    let networkID = try #require(networkObject?["Id"] as? String)
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .get, target: "/networks/\(networkID)")
        ).status == 200
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .get, target: "/networks")
        ).status == 200
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .delete, target: "/networks/\(networkID)")
        ).status == 204
    )

    let volumeBody = try JSONSerialization.data(
        withJSONObject: [
            "Name": "demo-volume",
            "Driver": "local",
            "Labels": ["project": "demo"]
        ]
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/volumes/create",
                body: volumeBody
            )
        ).status == 201
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .get, target: "/volumes/demo-volume")
        ).status == 200
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .get, target: "/volumes")
        ).status == 200
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .delete, target: "/volumes/demo-volume")
        ).status == 204
    )
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
            labels: ["project": "demo"]
        ),
        context: RuntimeRequestContext()
    )
    let router = DockerRouter(runtime: runtime)
    let archive = Data("tar-data".utf8)
    let upload = await router.respond(
        to: DockerHTTPRequest(
            method: .put,
            target: "/containers/\(container.dockerID.rawValue)/archive?path=%2Fworkspace",
            body: archive
        )
    )
    #expect(upload.status == 200)
    let download = await router.respond(
        to: DockerHTTPRequest(
            method: .get,
            target: "/containers/\(container.dockerID.rawValue)/archive?path=%2Fworkspace"
        )
    )
    #expect(try bytes(download) == archive)
    #expect(download.headers["X-Docker-Container-Path-Stat"] != nil)

    let logs = await router.respond(
        to: DockerHTTPRequest(
            method: .get,
            target: "/containers/\(container.dockerID.rawValue)/logs?stdout=1&stderr=1"
        )
    )
    #expect(try await !streamBytes(logs).isEmpty)

    let events = await router.respond(
        to: DockerHTTPRequest(
            method: .get,
            target: "/events?filters=%7B%22label%22:%7B%22project=demo%22:true%7D%7D"
        )
    )
    let eventData = try await streamBytes(events)
    #expect(
        String(data: eventData, encoding: .utf8)?
            .contains("\"Action\":\"create\"") == true
    )
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
    let createBody = try JSONSerialization.data(
        withJSONObject: [
            "Image": "fixture:latest",
            "Entrypoint": "/bin/sh",
            "Cmd": ["-c", "sleep infinity"],
            "Env": ["EMPTY", "A=1"],
            "OpenStdin": true,
            "Hostname": "fixture-host",
            "User": "501:20",
            "HostConfig": [
                "Mounts": [
                    ["Type": "volume", "Source": "cache", "Target": "/cache", "ReadOnly": false],
                    ["Type": "tmpfs", "Target": "/run", "ReadOnly": false]
                ],
                "Init": true,
                "AutoRemove": true,
                "CapAdd": ["SYS_PTRACE"],
                "CapDrop": ["NET_RAW"],
                "SecurityOpt": ["seccomp:unconfined", "no-new-privileges=true"],
                "PortBindings": [
                    "3000/tcp": []
                ]
            ]
        ]
    )
    let create = await router.respond(
        to: DockerHTTPRequest(method: .post, target: "/containers/create", body: createBody)
    )
    #expect(create.status == 201)
    let createdObject = try JSONSerialization.jsonObject(with: bytes(create)) as? [String: Any]
    let id = try #require(createdObject?["Id"] as? String)

    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .delete, target: "/containers/\(id)")
        ).status == 204
    )
    let recreated = await router.respond(
        to: DockerHTTPRequest(method: .post, target: "/containers/create?name=advanced", body: createBody)
    )
    let recreatedObject = try JSONSerialization.jsonObject(with: bytes(recreated)) as? [String: Any]
    let runningID = try #require(recreatedObject?["Id"] as? String)
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .post, target: "/containers/\(runningID)/start")
        ).status == 204
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .delete, target: "/containers/\(runningID)")
        ).status == 409
    )

    let attach = await router.respond(
        to: DockerHTTPRequest(method: .post, target: "/containers/\(runningID)/attach")
    )
    #expect(attach.status == 101)
    if case let .hijack(session, terminal) = attach.body {
        #expect(!terminal)
        #expect(try await session.wait() == 0)
    } else {
        Issue.record("attach did not return a hijacked session")
    }

    let logResponse = await router.respond(
        to: DockerHTTPRequest(
            method: .get,
            target: "/containers/\(runningID)/logs?follow=yes&stdout=0&stderr=1"
        )
    )
    let logBytes = try await streamBytes(logResponse)
    #expect(logBytes.first == RuntimeIOChannel.standardError.rawValue)

    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .head, target: "/containers/\(runningID)/archive?path=%2Fworkspace")
        ).headers["X-Docker-Container-Path-Stat"] != nil
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .get, target: "/containers/\(runningID)/archive")
        ).status == 400
    )

    let privilegedExec = try JSONSerialization.data(
        withJSONObject: ["Cmd": ["true"], "Privileged": true]
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/containers/\(runningID)/exec",
                body: privilegedExec
            )
        ).status == 501
    )
    let execBody = try JSONSerialization.data(
        withJSONObject: [
            "Cmd": ["printf", "hello"],
            "Env": ["A=1"],
            "WorkingDir": "/workspace",
            "User": "501:20",
            "AttachStdin": true,
            "AttachStdout": true,
            "AttachStderr": false,
            "Tty": true
        ]
    )
    let execCreate = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/containers/\(runningID)/exec",
            body: execBody
        )
    )
    let execObject = try JSONSerialization.jsonObject(with: bytes(execCreate)) as? [String: Any]
    let execID = try #require(execObject?["Id"] as? String)
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .get, target: "/exec/\(execID)/json")
        ).status == 200
    )
    let startBody = try JSONSerialization.data(withJSONObject: ["Detach": false, "Tty": true])
    let started = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/exec/\(execID)/start",
            body: startBody
        )
    )
    if case let .hijack(session, terminal) = started.body {
        #expect(terminal)
        #expect(try await session.wait() == 0)
    } else {
        Issue.record("exec start did not return a hijacked session")
    }
    let detachBody = try JSONSerialization.data(withJSONObject: ["Detach": true, "Tty": false])
    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/exec/\(execID)/start",
                body: detachBody
            )
        ).status == 409
    )
    #expect(
        try await awaitExecResizeStatus(
            router: router,
            execID: ExecID(rawValue: execID),
            width: 120,
            height: 40
        ) == 409
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .post, target: "/exec/\(execID)/resize?w=wide&h=40")
        ).status == 400
    )

    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/containers/\(runningID)/kill?signal=SIGTERM"
            )
        ).status == 204
    )
    let wait = await router.respond(
        to: DockerHTTPRequest(method: .post, target: "/containers/\(runningID)/wait")
    )
    let waitObject = try await JSONSerialization.jsonObject(with: streamBytes(wait)) as? [String: Any]
    #expect(waitObject?["StatusCode"] as? Int == 0)
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .delete, target: "/containers/\(runningID)?force=1")
        ).status == 204
    )
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
    let networkBody = try JSONSerialization.data(
        withJSONObject: ["Name": "connected", "Internal": true]
    )
    let created = await router.respond(
        to: DockerHTTPRequest(method: .post, target: "/networks/create", body: networkBody)
    )
    let createdObject = try JSONSerialization.jsonObject(with: bytes(created)) as? [String: Any]
    let networkID = try #require(createdObject?["Id"] as? String)

    let connectBody = try JSONSerialization.data(
        withJSONObject: [
            "Container": container.dockerID.rawValue,
            "EndpointConfig": ["Aliases": ["app", "api"]]
        ]
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/networks/\(networkID)/connect",
                body: connectBody
            )
        ).status == 200
    )
    let inspect = await router.respond(
        to: DockerHTTPRequest(method: .get, target: "/networks/\(networkID)")
    )
    let inspected = try JSONSerialization.jsonObject(with: bytes(inspect)) as? [String: Any]
    #expect((inspected?["Containers"] as? [String: Any])?.count == 1)
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .delete, target: "/networks/\(networkID)")
        ).status == 409
    )
    let disconnectBody = try JSONSerialization.data(
        withJSONObject: ["Container": container.dockerID.rawValue, "Force": true]
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/networks/\(networkID)/disconnect",
                body: disconnectBody
            )
        ).status == 200
    )

    let anonymousVolume = try await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/volumes/create",
            body: JSONSerialization.data(withJSONObject: ["Name": ""])
        )
    )
    let volumeObject = try JSONSerialization.jsonObject(with: bytes(anonymousVolume)) as? [String: Any]
    let volumeName = try #require(volumeObject?["Name"] as? String)
    #expect(volumeName.hasPrefix("devcontainer-"))
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .delete, target: "/volumes/\(volumeName)?force=true")
        ).status == 204
    )
    let badVolume = try JSONSerialization.data(withJSONObject: ["Name": "bad", "Driver": "nfs"])
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .post, target: "/volumes/create", body: badVolume)
        ).status == 501
    )

    let events = await router.respond(
        to: DockerHTTPRequest(
            method: .get,
            target: "/events?since=0&until=2999-01-01T00%3A00%3A00Z"
                + "&filters=%7B%22event%22:%7B%22create%22:true,"
                + "%22start%22:false%7D,%22label%22:"
                + "%5B%22project=demo%22%5D%7D"
        )
    )
    let eventText = try await String(
        data: streamBytes(events),
        encoding: .utf8
    ) ?? "non-UTF-8 event stream"
    #expect(eventText.contains("\"Action\":\"create\""))
    #expect(!eventText.contains("\"Action\":\"start\""))
}

@Test
func `in memory runtime negative paths and process controls are deterministic`() async throws {
    let runtime = InMemoryRuntime()
    let context = RuntimeRequestContext()
    await #expect(throws: DevContainerError.self) {
        _ = try await runtime.inspectImage(reference: "missing", context: context)
    }
    await #expect(throws: DevContainerError.self) {
        try await runtime.tagImage(source: "missing", target: "tag", context: context)
    }
    await #expect(throws: DevContainerError.self) {
        try await runtime.removeImage(reference: "missing", force: false, context: context)
    }

    await runtime.seedImage(
        ImageSnapshot(
            id: "sha256:fixture",
            references: ["fixture:latest"],
            createdAt: Date(),
            size: 1
        )
    )
    let container = try await runtime.createContainer(
        spec: ContainerSpec(name: "fixture", image: "fixture:latest"),
        context: context
    )
    await #expect(throws: DevContainerError.self) {
        _ = try await runtime.createContainer(
            spec: ContainerSpec(name: "fixture", image: "fixture:latest"),
            context: context
        )
    }
    await #expect(throws: DevContainerError.self) {
        _ = try await runtime.createContainer(
            spec: ContainerSpec(name: "missing-image", image: "missing"),
            context: context
        )
    }
    await #expect(throws: DevContainerError.self) {
        _ = try await runtime.createExec(
            containerID: container.runtimeID.rawValue,
            spec: ExecSpec(command: ["true"]),
            context: context
        )
    }
    try await runtime.startContainer(id: container.spec.name, context: context)
    await #expect(throws: DevContainerError.self) {
        _ = try await runtime.waitContainer(id: container.dockerID.rawValue, context: context)
    }
    await #expect(throws: DevContainerError.self) {
        try await runtime.removeContainer(id: container.dockerID.rawValue, force: false, context: context)
    }
    #expect(
        await runtime.listContainers(
            all: false,
            labels: ["missing": ""],
            context: context
        ).isEmpty
    )

    let session = InMemoryProcessSession(
        frames: [RuntimeIOFrame(channel: .standardOutput, data: Data("output".utf8))],
        exitCode: 7
    )
    await session.write(Data("input".utf8))
    await session.closeStandardInput()
    await session.resize(width: 100, height: 50)
    #expect(try await session.wait() == 7)
    await session.cancel()
    await #expect(throws: CancellationError.self) {
        _ = try await session.wait()
    }
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
    let create = try await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/containers/create?name=health",
            body: JSONSerialization.data(
                withJSONObject: [
                    "Image": "health:latest",
                    "Healthcheck": [
                        "Test": ["CMD", "true"],
                        "Interval": 100_000_000,
                        "Timeout": 1_000_000_000,
                        "Retries": 2,
                        "StartPeriod": 0
                    ]
                ]
            )
        )
    )
    let createObject = try JSONSerialization.jsonObject(
        with: bytes(create)
    ) as? [String: Any]
    let identifier = try #require(createObject?["Id"] as? String)
    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/containers/\(identifier)/start"
            )
        ).status == 204
    )

    let inspect = await router.respond(
        to: DockerHTTPRequest(
            method: .get,
            target: "/containers/\(identifier)/json"
        )
    )
    let object = try JSONSerialization.jsonObject(
        with: bytes(inspect)
    ) as? [String: Any]
    let config = try #require(object?["Config"] as? [String: Any])
    let healthcheck = try #require(config["Healthcheck"] as? [String: Any])
    #expect(healthcheck["Retries"] as? Int == 2)
    #expect(healthcheck["Test"] as? [String] == ["CMD", "true"])
    let state = try #require(object?["State"] as? [String: Any])
    let health = try #require(state["Health"] as? [String: Any])
    #expect(health["Status"] as? String == "healthy")
    #expect(health["FailingStreak"] as? Int == 0)
    #expect((health["Log"] as? [[String: Any]])?.count == 1)
}
