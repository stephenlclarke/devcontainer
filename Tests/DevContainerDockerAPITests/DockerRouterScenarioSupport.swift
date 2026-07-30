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

func createLifecycleContainer(_ router: DockerRouter) async throws -> String {
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
                    "8080/tcp": [["HostIp": "127.0.0.1", "HostPort": "18080"]]
                ]
            ]
        ]
    )
    let response = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/v1.53/containers/create?name=demo",
            body: body
        )
    )
    #expect(response.status == 201)
    let object = try JSONSerialization.jsonObject(with: bytes(response)) as? [String: Any]
    return try #require(object?["Id"] as? String)
}

func assertLifecycleInspectAndList(
    _ router: DockerRouter,
    id: String
) async throws {
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .post, target: "/containers/\(id)/start")
        ).status == 204
    )
    let inspect = await router.respond(
        to: DockerHTTPRequest(method: .get, target: "/containers/\(id)/json")
    )
    let object = try JSONSerialization.jsonObject(with: bytes(inspect)) as? [String: Any]
    #expect(object?["Name"] as? String == "/demo")
    #expect((object?["State"] as? [String: Any])?["Running"] as? Bool == true)
    #expect((object?["Config"] as? [String: Any])?["WorkingDir"] as? String == "/workspace")

    for target in [
        "/containers/json?all=true&filters="
            + "%7B%22label%22:%5B%22devcontainer.local_folder=/workspace%22%5D%7D",
        "/containers/json?all=true&filters="
            + "%7B%22label%22:%5B%22com.docker.compose.project=demo%22%5D%7D"
    ] {
        let response = await router.respond(
            to: DockerHTTPRequest(method: .get, target: target)
        )
        #expect(
            try (JSONSerialization.jsonObject(with: bytes(response)) as? [[String: Any]])?.count == 1
        )
    }

    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/containers/\(id)/rename?name=renamed-demo"
            )
        ).status == 204
    )
    let renamed = await router.respond(
        to: DockerHTTPRequest(method: .get, target: "/containers/renamed-demo/json")
    )
    let renamedObject =
        try JSONSerialization.jsonObject(with: bytes(renamed)) as? [String: Any]
    #expect(renamedObject?["Name"] as? String == "/renamed-demo")
}

func createMountSnapshot(
    runtime: InMemoryRuntime,
    router: DockerRouter
) async throws -> ContainerSnapshot {
    let body = try JSONSerialization.data(
        withJSONObject: [
            "Image": "mounts:latest",
            "Volumes": ["/declared": [:]],
            "HostConfig": [
                "Binds": ["/tmp/source:/bind:ro", "named-cache:/named"],
                "Mounts": [["Type": "volume", "Target": "/structured"]]
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
    return try await runtime.inspectContainer(
        id: identifier,
        context: RuntimeRequestContext()
    )
}

func assertMountTypes(_ snapshot: ContainerSnapshot) throws {
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
            == RuntimeMount(type: .volume, source: "named-cache", destination: "/named")
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

func assertImagePullInspectAndTag(_ router: DockerRouter) async throws {
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
    let object = try JSONSerialization.jsonObject(with: bytes(inspect)) as? [String: Any]
    #expect(object?["Architecture"] as? String == "arm64")
    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/images/alpine:3.22/tag?repo=example/alpine&tag=test"
            )
        ).status == 201
    )

    let digest = "sha256:" + String(repeating: "a", count: 64)
    let digestPull = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/images/create?fromImage=docker.io%2Flibrary%2Falpine"
                + "&tag=\(digest)"
        )
    )
    #expect(digestPull.status == 200)
    #expect(
        try await String(data: streamBytes(digestPull), encoding: .utf8)?
            .contains("\"status\"") == true
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .get,
                target: "/images/docker.io%2Flibrary%2Falpine%40\(digest)/json"
            )
        ).status == 200
    )
}

func assertImageBuildLoadAndDelete(_ router: DockerRouter) async throws {
    let build = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/build?dockerfile=Containerfile&t=example%2Fbuilt%3Alatest"
                + "&buildargs=%7B%22A%22%3A%22B%22%7D",
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
    #expect(
        try await String(data: streamBytes(load), encoding: .utf8)?
            .contains("Loaded image") == true
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .post, target: "/images/load")
        ).status == 400
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .delete, target: "/images/example%2Falpine:test")
        ).status == 200
    )
}

func assertNetworkEndpoints(_ router: DockerRouter) async throws {
    let body = try JSONSerialization.data(
        withJSONObject: [
            "Name": "demo-network",
            "Driver": "",
            "Labels": ["project": "demo"]
        ]
    )
    let created = await router.respond(
        to: DockerHTTPRequest(method: .post, target: "/networks/create", body: body)
    )
    let object = try JSONSerialization.jsonObject(with: bytes(created)) as? [String: Any]
    let identifier = try #require(object?["Id"] as? String)
    #expect(created.status == 201)
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .get, target: "/networks/\(identifier)")
        ).status == 200
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .get, target: "/networks")
        ).status == 200
    )
    let filtered = await router.respond(
        to: DockerHTTPRequest(
            method: .get,
            target: "/networks?filters="
                + "%7B%22label%22:%7B%22project=other%22:true%7D%7D"
        )
    )
    #expect(
        try (JSONSerialization.jsonObject(with: bytes(filtered)) as? [[String: Any]])?
            .isEmpty == true
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .delete, target: "/networks/\(identifier)")
        ).status == 204
    )
}

func assertVolumeEndpoints(_ router: DockerRouter) async throws {
    let body = try JSONSerialization.data(
        withJSONObject: [
            "Name": "demo-volume",
            "Driver": "local",
            "Labels": ["project": "demo"]
        ]
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .post, target: "/volumes/create", body: body)
        ).status == 201
    )
    for target in ["/volumes/demo-volume", "/volumes"] {
        #expect(
            await router.respond(
                to: DockerHTTPRequest(method: .get, target: target)
            ).status == 200
        )
    }
    let filtered = await router.respond(
        to: DockerHTTPRequest(
            method: .get,
            target: "/volumes?filters="
                + "%7B%22label%22:%7B%22project=other%22:true%7D%7D"
        )
    )
    let filteredObject =
        try JSONSerialization.jsonObject(with: bytes(filtered)) as? [String: Any]
    #expect((filteredObject?["Volumes"] as? [[String: Any]])?.isEmpty == true)
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .delete, target: "/volumes/demo-volume")
        ).status == 204
    )
}

func assertArchiveLogAndEventStreams(
    router: DockerRouter,
    container: ContainerSnapshot
) async throws {
    let id = container.dockerID.rawValue
    let archive = Data("tar-data".utf8)
    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .put,
                target: "/containers/\(id)/archive?path=%2Fworkspace",
                body: archive
            )
        ).status == 200
    )
    let download = await router.respond(
        to: DockerHTTPRequest(
            method: .get,
            target: "/containers/\(id)/archive?path=%2Fworkspace"
        )
    )
    #expect(try bytes(download) == archive)
    #expect(download.headers["X-Docker-Container-Path-Stat"] != nil)
    let logs = await router.respond(
        to: DockerHTTPRequest(
            method: .get,
            target: "/containers/\(id)/logs?stdout=1&stderr=1"
        )
    )
    #expect(try await !streamBytes(logs).isEmpty)
    let events = await router.respond(
        to: DockerHTTPRequest(
            method: .get,
            target: "/events?filters=%7B%22label%22:%7B%22project=demo%22:true%7D%7D"
        )
    )
    let eventText = try await String(
        data: streamBytes(events),
        encoding: .utf8
    )
    #expect(eventText?.contains("\"Action\":\"create\"") == true)
    #expect(
        eventText?.contains(
            "\"com.docker.compose.project\":\"workspace\""
        ) == true
    )
    #expect(
        eventText?.contains(
            "\"com.docker.compose.service\":\"app\""
        ) == true
    )
}

func advancedContainerBody() throws -> Data {
    try JSONSerialization.data(
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
                "PortBindings": ["3000/tcp": []]
            ]
        ]
    )
}

func createRunningAdvancedContainer(
    _ router: DockerRouter,
    body: Data
) async throws -> String {
    let created = await router.respond(
        to: DockerHTTPRequest(method: .post, target: "/containers/create", body: body)
    )
    let object = try JSONSerialization.jsonObject(with: bytes(created)) as? [String: Any]
    let identifier = try #require(object?["Id"] as? String)
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .delete, target: "/containers/\(identifier)")
        ).status == 204
    )
    let recreated = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/containers/create?name=advanced",
            body: body
        )
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
    return runningID
}

func assertAdvancedAttachLogsAndArchives(
    _ router: DockerRouter,
    containerID: String
) async throws {
    let attach = await router.respond(
        to: DockerHTTPRequest(method: .post, target: "/containers/\(containerID)/attach")
    )
    if case let .hijack(session, terminal) = attach.body {
        #expect(!terminal)
        #expect(try await session.wait() == 0)
    } else {
        Issue.record("attach did not return a hijacked session")
    }
    let logs = await router.respond(
        to: DockerHTTPRequest(
            method: .get,
            target: "/containers/\(containerID)/logs?follow=yes&stdout=0&stderr=1"
        )
    )
    #expect(try await streamBytes(logs).first == RuntimeIOChannel.standardError.rawValue)
    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .head,
                target: "/containers/\(containerID)/archive?path=%2Fworkspace"
            )
        ).headers["X-Docker-Container-Path-Stat"] != nil
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .get, target: "/containers/\(containerID)/archive")
        ).status == 400
    )
}

func createAdvancedExec(
    _ router: DockerRouter,
    containerID: String
) async throws -> String {
    let privileged = try JSONSerialization.data(
        withJSONObject: ["Cmd": ["true"], "Privileged": true]
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/containers/\(containerID)/exec",
                body: privileged
            )
        ).status == 501
    )
    let body = try JSONSerialization.data(
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
    let response = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/containers/\(containerID)/exec",
            body: body
        )
    )
    let object = try JSONSerialization.jsonObject(with: bytes(response)) as? [String: Any]
    return try #require(object?["Id"] as? String)
}

func assertAdvancedExecLifecycle(
    _ router: DockerRouter,
    execID: String
) async throws {
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
}

func assertAdvancedKillWaitAndRemove(
    _ router: DockerRouter,
    containerID: String
) async throws {
    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/containers/\(containerID)/kill?signal=SIGTERM"
            )
        ).status == 204
    )
    let wait = await router.respond(
        to: DockerHTTPRequest(method: .post, target: "/containers/\(containerID)/wait")
    )
    let object = try await JSONSerialization.jsonObject(with: streamBytes(wait)) as? [String: Any]
    #expect(object?["StatusCode"] as? Int == 0)
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .delete, target: "/containers/\(containerID)?force=1")
        ).status == 204
    )
}

private func neutralEndpointConfig(aliases: [String]) -> [String: Any] {
    [
        "Aliases": aliases,
        "DNSNames": [],
        "EndpointID": "",
        "Gateway": "",
        "GlobalIPv6Address": "",
        "GlobalIPv6PrefixLen": 0,
        "IPAddress": "",
        "IPPrefixLen": 0,
        "IPv6Gateway": "",
        "NetworkID": ""
    ]
}

func connectFixtureNetwork(
    router: DockerRouter,
    container: ContainerSnapshot
) async throws -> String {
    let body = try JSONSerialization.data(
        withJSONObject: ["Name": "connected", "Internal": true]
    )
    let created = await router.respond(
        to: DockerHTTPRequest(method: .post, target: "/networks/create", body: body)
    )
    let object = try JSONSerialization.jsonObject(with: bytes(created)) as? [String: Any]
    let networkID = try #require(object?["Id"] as? String)
    let connect = try JSONSerialization.data(
        withJSONObject: [
            "Container": container.dockerID.rawValue,
            "EndpointConfig": neutralEndpointConfig(aliases: ["app", "api"])
        ]
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/networks/\(networkID)/connect",
                body: connect
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
    let disconnect = try JSONSerialization.data(
        withJSONObject: ["Container": container.dockerID.rawValue, "Force": true]
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/networks/\(networkID)/disconnect",
                body: disconnect
            )
        ).status == 200
    )
    return networkID
}

func assertAnonymousVolumeRoutes(_ router: DockerRouter) async throws {
    let response = try await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/volumes/create",
            body: JSONSerialization.data(withJSONObject: ["Name": ""])
        )
    )
    let object = try JSONSerialization.jsonObject(with: bytes(response)) as? [String: Any]
    let name = try #require(object?["Name"] as? String)
    #expect(name.hasPrefix("devcontainer-"))
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .delete, target: "/volumes/\(name)?force=true")
        ).status == 204
    )
    let bad = try JSONSerialization.data(withJSONObject: ["Name": "bad", "Driver": "nfs"])
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .post, target: "/volumes/create", body: bad)
        ).status == 501
    )
}

func assertFilteredEventActions(_ router: DockerRouter) async throws {
    let response = await router.respond(
        to: DockerHTTPRequest(
            method: .get,
            target: "/events?since=0&until=2999-01-01T00%3A00%3A00Z"
                + "&filters=%7B%22event%22:%7B%22create%22:true,"
                + "%22start%22:false%7D,%22label%22:"
                + "%5B%22project=demo%22%5D%7D"
        )
    )
    let text =
        try await String(
            data: streamBytes(response),
            encoding: .utf8
        ) ?? "non-UTF-8 event stream"
    #expect(text.contains("\"Action\":\"create\""))
    #expect(!text.contains("\"Action\":\"start\""))
}

func assertMissingImageFailures(
    _ runtime: InMemoryRuntime,
    context: RuntimeRequestContext
) async {
    await #expect(throws: DevContainerError.self) {
        _ = try await runtime.inspectImage(reference: "missing", context: context)
    }
    await #expect(throws: DevContainerError.self) {
        try await runtime.tagImage(source: "missing", target: "tag", context: context)
    }
    await #expect(throws: DevContainerError.self) {
        try await runtime.removeImage(reference: "missing", force: false, context: context)
    }
}

func assertContainerNegativePaths(
    _ runtime: InMemoryRuntime,
    context: RuntimeRequestContext
) async throws {
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
        try await runtime.removeContainer(
            id: container.dockerID.rawValue, force: false, context: context
        )
    }
    #expect(
        await runtime.listContainers(
            all: false,
            labels: ["missing": ""],
            context: context
        ).isEmpty
    )
}

func assertProcessSessionControls() async throws {
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

func createHealthContainer(_ router: DockerRouter) async throws -> String {
    let response = try await router.respond(
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
    let object = try JSONSerialization.jsonObject(with: bytes(response)) as? [String: Any]
    let identifier = try #require(object?["Id"] as? String)
    #expect(
        await router.respond(
            to: DockerHTTPRequest(method: .post, target: "/containers/\(identifier)/start")
        ).status == 204
    )
    return identifier
}

func assertHealthInspect(
    _ router: DockerRouter,
    containerID: String
) async throws {
    let response = await router.respond(
        to: DockerHTTPRequest(method: .get, target: "/containers/\(containerID)/json")
    )
    let object = try JSONSerialization.jsonObject(with: bytes(response)) as? [String: Any]
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
