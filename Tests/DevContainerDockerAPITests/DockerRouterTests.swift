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
// swiftlint:disable:next function_body_length
func `unknown request members fail before runtime side effects`() async throws {
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
    let requests = [
        (
            "/containers/create",
            #"{"Image":"alpine:test","UnknownRoot":true}"#,
            "UnknownRoot"
        ),
        (
            "/containers/create",
            #"{"Image":"alpine:test","HostConfig":{"FutureResource":1048576}}"#,
            "HostConfig.FutureResource"
        ),
        (
            "/containers/create",
            #"""
            {
              "Image":"alpine:test",
              "Mounts":[{
                "Type":"bind",
                "Source":"/tmp",
                "Target":"/work",
                "BindOptions":{"FuturePropagation":"rprivate"}
              }]
            }
            """#,
            "Mounts.[0].BindOptions.FuturePropagation"
        ),
        (
            "/networks/create",
            #"{"Name":"fixture","FutureIPAM":{"Driver":"default"}}"#,
            "FutureIPAM"
        ),
        (
            "/volumes/create",
            #"{"Name":"fixture","FutureDriverOpts":{"type":"none"}}"#,
            "FutureDriverOpts"
        )
    ]

    for (target, body, field) in requests {
        let response = await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: target,
                body: Data(body.utf8)
            )
        )
        #expect(response.status == 400)
        let envelope = try JSONSerialization.jsonObject(
            with: bytes(response)
        ) as? [String: String]
        #expect(envelope?["message"]?.contains(field) == true)
    }

    #expect(await runtime.listContainers(
        all: true,
        labels: [:],
        context: RuntimeRequestContext()
    ).isEmpty)
    #expect(await runtime.listNetworks(context: RuntimeRequestContext()).isEmpty)
    #expect(await runtime.listVolumes(context: RuntimeRequestContext()).isEmpty)
}

@Test
// swiftlint:disable:next function_body_length
func `known unsupported create fields fail before runtime side effects`() async throws {
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
    let containerRequests = [
        (#"{"Image":"alpine:test","ArgsEscaped":true}"#, "ArgsEscaped"),
        (#"{"Image":"alpine:test","MacAddress":"02:42:ac:11:00:02"}"#, "MacAddress"),
        (#"{"Image":"alpine:test","NetworkDisabled":true}"#, "NetworkDisabled"),
        (#"{"Image":"alpine:test","OnBuild":["RUN true"]}"#, "OnBuild"),
        (#"{"Image":"alpine:test","Shell":["/bin/sh","-c"]}"#, "Shell"),
        (#"{"Image":"alpine:test","StdinOnce":true}"#, "StdinOnce"),
        (#"{"Image":"alpine:test","StopSignal":"SIGUSR1"}"#, "StopSignal"),
        (
            #"{"Image":"alpine:test","HostConfig":{"PublishAllPorts":true}}"#,
            "HostConfig.PublishAllPorts"
        ),
        (
            #"{"Image":"alpine:test","HostConfig":{"LogConfig":{"Type":"json-file","Config":{}}}}"#,
            "HostConfig.LogConfig"
        ),
        (
            #"{"Image":"alpine:test","HostConfig":{"MemorySwappiness":0}}"#,
            "HostConfig.MemorySwappiness"
        ),
        (
            #"{"Image":"alpine:test","HostConfig":{"ConsoleSize":[24,80]}}"#,
            "HostConfig.ConsoleSize"
        ),
        (#"{"Image":"alpine:test","HostConfig":{"Memory":1048576}}"#, "HostConfig.Memory"),
        (
            #"{"Image":"alpine:test","HostConfig":{"DeviceRequests":[{"Count":-1,"Capabilities":[["gpu"]]}]}}"#,
            "HostConfig.DeviceRequests"
        ),
        (
            #"{"Image":"alpine:test","HostConfig":{"Dns":["1.1.1.1"]}}"#,
            "HostConfig.Dns"
        ),
        (
            #"{"Image":"alpine:test","HostConfig":{"PidMode":"host"}}"#,
            "HostConfig.PidMode"
        ),
        (
            #"{"Image":"alpine:test","HostConfig":{"RestartPolicy":{"Name":"always"}}}"#,
            "HostConfig.RestartPolicy"
        ),
        (
            #"""
            {
              "Image":"alpine:test",
              "Mounts":[{
                "Type":"bind",
                "Source":"/tmp",
                "Target":"/work",
                "BindOptions":{"Propagation":"rshared"}
              }]
            }
            """#,
            "Mounts.[0].BindOptions"
        ),
        (
            #"""
            {
              "Image":"alpine:test",
              "NetworkingConfig":{
                "EndpointsConfig":{
                  "bridge":{"IPAMConfig":{"IPv4Address":"172.20.0.10"}}
                }
              }
            }
            """#,
            "NetworkingConfig.EndpointsConfig.bridge.IPAMConfig"
        ),
        (
            #"""
            {
              "Image":"alpine:test",
              "NetworkingConfig":{
                "EndpointsConfig":{
                  "bridge":{"Gateway":"172.20.0.1"}
                }
              }
            }
            """#,
            "NetworkingConfig.EndpointsConfig.bridge.Gateway"
        )
    ]
    for (body, field) in containerRequests {
        let response = await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/containers/create",
                body: Data(body.utf8)
            )
        )
        #expect(response.status == 501, Comment(rawValue: field))
        let envelope = try JSONSerialization.jsonObject(
            with: bytes(response)
        ) as? [String: String]
        #expect(
            envelope?["message"]?.contains(field) == true,
            Comment(rawValue: field)
        )
    }

    let network = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/networks/create",
            body: Data(
                #"{"Name":"fixture","IPAM":{"Config":[{"Subnet":"172.20.0.0/16"}]}}"#.utf8
            )
        )
    )
    #expect(network.status == 501)
    let volume = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/volumes/create",
            body: Data(#"{"Name":"fixture","DriverOpts":{"type":"none"}}"#.utf8)
        )
    )
    #expect(volume.status == 501)

    #expect(await runtime.listContainers(
        all: true,
        labels: [:],
        context: RuntimeRequestContext()
    ).isEmpty)
    #expect(await runtime.listNetworks(context: RuntimeRequestContext()).isEmpty)
    #expect(await runtime.listVolumes(context: RuntimeRequestContext()).isEmpty)
}

@Test
func `neutral Docker client metadata is accepted`() async {
    let runtime = InMemoryRuntime()
    await runtime.seedImage(
        ImageSnapshot(
            id: "sha256:image",
            references: ["alpine:test"],
            createdAt: Date(),
            size: 1
        )
    )
    let response = await DockerRouter(runtime: runtime).respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/containers/create?name=neutral",
            body: neutralDockerClientMetadata
        )
    )

    #expect(response.status == 201)
    #expect(await runtime.listContainers(
        all: true,
        labels: [:],
        context: RuntimeRequestContext()
    ).count == 1)
}

private let neutralDockerClientMetadata = Data(
    #"""
    {
      "Image":"alpine:test",
      "ArgsEscaped":false,
      "ExposedPorts":{},
      "MacAddress":"",
      "NetworkDisabled":false,
      "OnBuild":[],
      "Shell":[],
      "StdinOnce":false,
      "StopSignal":"SIGTERM",
      "HostConfig":{
        "Annotations":{},
        "BlkioDeviceReadBps":[],
        "BlkioDeviceReadIOps":[],
        "BlkioDeviceWriteBps":[],
        "BlkioDeviceWriteIOps":[],
        "BlkioWeight":0,
        "BlkioWeightDevice":[],
        "Cgroup":"",
        "CgroupParent":"",
        "ConsoleSize":[0,0],
        "ContainerIDFile":"",
        "CpuCount":0,
        "CpuPercent":0,
        "CpuRealtimePeriod":0,
        "CpuRealtimeRuntime":0,
        "IOMaximumBandwidth":0,
        "IOMaximumIOps":0,
        "Isolation":"",
        "Links":[],
        "LogConfig":{"Config":{},"Type":""},
        "MemorySwappiness":-1,
        "PublishAllPorts":false,
        "Runtime":"",
        "StorageOpt":{},
        "Tmpfs":{},
        "VolumeDriver":"",
        "VolumesFrom":[]
      },
      "NetworkingConfig":{
        "EndpointsConfig":{
          "default":{
            "DNSNames":[],
            "EndpointID":"",
            "Gateway":"",
            "GlobalIPv6Address":"",
            "GlobalIPv6PrefixLen":0,
            "IPAddress":"",
            "IPPrefixLen":0,
            "IPv6Gateway":"",
            "NetworkID":""
          }
        }
      }
    }
    """#.utf8
)

@Test
// swiftlint:disable:next function_body_length
func `explicit idempotency keys replay one mutation and reject conflicting reuse`() async throws {
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
    let headers = ["Idempotency-Key": "create-fixture"]
    let first = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/containers/create?name=idempotent",
            headers: headers,
            body: Data(#"{"Image":"alpine:test"}"#.utf8)
        )
    )
    let second = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/containers/create?name=idempotent",
            headers: headers,
            body: Data(#"{"Image":"alpine:test"}"#.utf8)
        )
    )
    #expect(first.status == 201)
    #expect(second.status == 201)
    let firstBytes = try bytes(first)
    let secondBytes = try bytes(second)
    #expect(firstBytes == secondBytes)
    #expect(first.headers["X-Request-ID"] != nil)
    #expect(await runtime.listContainers(
        all: true,
        labels: [:],
        context: RuntimeRequestContext()
    ).count == 1)

    let conflict = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/containers/create?name=different",
            headers: headers,
            body: Data(#"{"Image":"alpine:test"}"#.utf8)
        )
    )
    #expect(conflict.status == 409)
    #expect(await runtime.listContainers(
        all: true,
        labels: [:],
        context: RuntimeRequestContext()
    ).count == 1)
}

@Test
// swiftlint:disable:next function_body_length
func `exposed ports and empty Docker host IP retain their semantics`() async throws {
    let runtime = InMemoryRuntime()
    await runtime.seedImage(
        ImageSnapshot(
            id: "sha256:fixture",
            references: ["fixture:latest"],
            createdAt: Date(timeIntervalSince1970: 1),
            size: 1
        )
    )
    let router = DockerRouter(runtime: runtime)
    let body = try JSONSerialization.data(
        withJSONObject: [
            "Image": "fixture:latest",
            "ExposedPorts": [
                "8080/tcp": [:],
                "9090/tcp": [:]
            ],
            "HostConfig": [
                "PortBindings": [
                    "8080/tcp": [["HostIp": "", "HostPort": "18080"]]
                ]
            ]
        ]
    )

    let response = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/v1.53/containers/create?name=host-ip",
            body: body
        )
    )
    #expect(response.status == 201)
    let snapshot = try #require(
        await runtime.listContainers(
            all: true,
            labels: [:],
            context: RuntimeRequestContext()
        ).first
    )
    #expect(snapshot.spec.ports.first?.hostAddress == "0.0.0.0")
    #expect(snapshot.spec.ports.map(\.containerPort) == [8080, 9090])
    #expect(snapshot.imageID == "sha256:fixture")

    let list = await router.respond(
        to: DockerHTTPRequest(method: .get, target: "/v1.53/containers/json?all=1")
    )
    let summaries = try #require(
        JSONSerialization.jsonObject(with: bytes(list)) as? [[String: Any]]
    )
    #expect(summaries.first?["Image"] as? String == "fixture:latest")
    #expect(summaries.first?["ImageID"] as? String == "sha256:fixture")

    let inspect = await router.respond(
        to: DockerHTTPRequest(
            method: .get,
            target: "/v1.53/containers/\(snapshot.dockerID.rawValue)/json"
        )
    )
    let inspected = try #require(
        JSONSerialization.jsonObject(with: bytes(inspect)) as? [String: Any]
    )
    #expect(inspected["Image"] as? String == "sha256:fixture")
    let config = try #require(inspected["Config"] as? [String: Any])
    let exposed = try #require(config["ExposedPorts"] as? [String: Any])
    #expect(Set(exposed.keys) == ["8080/tcp", "9090/tcp"])
}

@Test
// swiftlint:disable:next function_body_length
func `production router journals and labels owned container mutations`() async throws {
    let directory = FileManager.default.temporaryDirectory
        .appendingPathComponent(
            "devcontainer-router-coordinator-\(UUID().uuidString)",
            isDirectory: true
        )
    defer { try? FileManager.default.removeItem(at: directory) }
    let store = try SQLiteStateStore(
        path: directory.appendingPathComponent("state.sqlite")
    )
    let runtime = InMemoryRuntime()
    await runtime.seedImage(
        ImageSnapshot(
            id: "sha256:journalled",
            references: ["fixture:latest"],
            createdAt: Date(timeIntervalSince1970: 1),
            size: 1
        )
    )
    let router = DockerRouter(
        runtime: runtime,
        coordinator: ProjectCoordinator(store: store)
    )
    let body = try JSONSerialization.data(
        withJSONObject: [
            "Image": "fixture:latest",
            "Labels": ["devcontainer.local_folder": "/workspace"]
        ]
    )

    let created = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/v1.53/containers/create?name=journalled",
            headers: ["X-Request-ID": "fixture-correlation"],
            body: body
        )
    )
    #expect(created.status == 201)
    let project = try #require(await store.listProjects().first)
    let resources = try await store.resources(project: project.key)
    let snapshot = try #require(
        await runtime.listContainers(
            all: true,
            labels: [:],
            context: RuntimeRequestContext()
        ).first
    )
    #expect(project.reconciliationState == .clean)
    #expect(project.desiredGeneration == 1)
    #expect(resources.map(\.runtimeID) == [snapshot.runtimeID])
    #expect(snapshot.spec.labels[RuntimeLabels.project] == project.key.rawValue)
    #expect(snapshot.spec.labels[RuntimeLabels.provider] == BackendProvider.stock.rawValue)
    #expect(snapshot.spec.labels[RuntimeLabels.generation] == "1")
    #expect(snapshot.spec.labels[RuntimeLabels.operation] != nil)
    #expect(try await store.unfinishedOperations().isEmpty)

    let networkCreate = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/v1.53/networks/create",
            body: Data(
                #"""
                {
                  "Name":"journalled-network",
                  "Labels":{"devcontainer.local_folder":"/workspace"}
                }
                """#.utf8
            )
        )
    )
    #expect(networkCreate.status == 201)
    let networkObject = try #require(
        JSONSerialization.jsonObject(
            with: bytes(networkCreate)
        ) as? [String: Any]
    )
    let networkID = try #require(networkObject["Id"] as? String)
    let connectBody = try JSONSerialization.data(
        withJSONObject: ["Container": snapshot.dockerID.rawValue]
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/v1.53/networks/\(networkID)/connect",
                body: connectBody
            )
        ).status == 200
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/v1.53/networks/\(networkID)/disconnect",
                body: connectBody
            )
        ).status == 200
    )
    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .delete,
                target: "/v1.53/networks/\(networkID)"
            )
        ).status == 204
    )

    let volumeCreate = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/v1.53/volumes/create",
            body: Data(
                #"""
                {
                  "Name":"journalled-volume",
                  "Labels":{"devcontainer.local_folder":"/workspace"}
                }
                """#.utf8
            )
        )
    )
    #expect(volumeCreate.status == 201)
    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .delete,
                target: "/v1.53/volumes/journalled-volume"
            )
        ).status == 204
    )

    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/v1.53/containers/\(snapshot.dockerID.rawValue)/start"
            )
        ).status == 204
    )
    let execCreate = await router.respond(
        to: DockerHTTPRequest(
            method: .post,
            target: "/v1.53/containers/\(snapshot.dockerID.rawValue)/exec",
            body: Data(#"{"Cmd":["true"],"AttachStdout":true}"#.utf8)
        )
    )
    let execCreateBody = try bytes(execCreate)
    let execDiagnostic =
        String(data: execCreateBody, encoding: .utf8)
            ?? "non-UTF-8 exec response"
    #expect(
        execCreate.status == 201,
        Comment(rawValue: execDiagnostic)
    )
    let execObject = try #require(
        JSONSerialization.jsonObject(
            with: execCreateBody
        ) as? [String: Any]
    )
    let execID = try #require(execObject["Id"] as? String)
    #expect(
        await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/v1.53/exec/\(execID)/start",
                body: Data(#"{"Detach":true,"Tty":false}"#.utf8)
            )
        ).status == 200
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
    #expect(infoObject?["MemoryLimit"] as? Bool == false)
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
