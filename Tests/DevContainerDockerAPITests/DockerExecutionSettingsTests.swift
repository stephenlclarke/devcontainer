// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerTestSupport
import Foundation
import Testing

struct DockerExecutionSettingsTests {
    @Test(arguments: ["none", "bridge", "default", "project_private", "", nil] as [String?])
    func `inspect preserves the accepted network mode`(_ requested: String?) async throws {
        let runtime = InMemoryRuntime()
        await runtime.seedImage(.init(id: "sha256:network-mode", references: ["mode:test"], createdAt: Date(), size: 1))
        _ = try await runtime.createNetwork(spec: NetworkSpec(name: "project_private"), context: .init())
        let router = DockerRouter(runtime: runtime)
        var host: [String: Any] = [:]
        if let requested {
            host["NetworkMode"] = requested
        }
        let body = try JSONSerialization.data(withJSONObject: ["Image": "mode:test", "HostConfig": host])
        let created = await router.respond(to: .init(method: .post, target: "/containers/create?name=mode", body: body))
        #expect(created.status == 201)
        let inspected = await router.respond(to: .init(method: .get, target: "/containers/mode/json"))
        let object = try #require(JSONSerialization.jsonObject(with: bytes(inspected)) as? [String: Any])
        let actual = try #require(object["HostConfig"] as? [String: Any])
        let expected = [nil, "", "default"].contains(requested) ? "bridge" : requested
        #expect(actual["NetworkMode"] as? String == expected)
    }

    @Test func `legacy isolated container inspection reports none`() async throws {
        let runtime = InMemoryRuntime()
        await runtime.seedImage(.init(id: "sha256:legacy", references: ["legacy:test"], createdAt: Date(), size: 1))
        _ = try await runtime.createContainer(
            spec: .init(name: "legacy", image: "legacy:test", networks: [.init(name: "none")]), context: .init()
        )
        let router = DockerRouter(runtime: runtime)
        let inspected = await router.respond(to: .init(method: .get, target: "/containers/legacy/json"))
        let object = try #require(JSONSerialization.jsonObject(with: bytes(inspected)) as? [String: Any])
        #expect((object["HostConfig"] as? [String: Any])?["NetworkMode"] as? String == "none")
    }

    @Test(arguments: ["none", "project_primary", "bridge"])
    func `explicit endpoints do not replace the retained selector`(_ mode: String) async throws {
        // Pinned Docker 29.5.2 accepts and starts these combinations. HostConfig
        // retains the selector while NetworkingConfig selects the attachments.
        let runtime = InMemoryRuntime()
        await runtime.seedImage(.init(id: "sha256:mode", references: ["mode:test"], createdAt: Date(), size: 1))
        for name in ["project_primary", "project_endpoint"] {
            _ = try await runtime.createNetwork(spec: NetworkSpec(name: name), context: .init())
        }
        let router = DockerRouter(runtime: runtime)
        let body = try JSONSerialization.data(withJSONObject: [
            "Image": "mode:test", "HostConfig": ["NetworkMode": mode],
            "NetworkingConfig": ["EndpointsConfig": ["project_endpoint": ["Aliases": ["service"]]]]
        ])
        let response = await router.respond(to: .init(
            method: .post, target: "/containers/create?name=mode", body: body
        ))
        #expect(response.status == 201)
        let native = try await runtime.inspectContainer(id: "mode", context: .init())
        #expect(native.spec.networks == [.init(name: "project_endpoint", aliases: ["service"])])
        let inspected = await router.respond(to: .init(method: .get, target: "/containers/mode/json"))
        let object = try #require(JSONSerialization.jsonObject(with: bytes(inspected)) as? [String: Any])
        #expect((object["HostConfig"] as? [String: Any])?["NetworkMode"] as? String == mode)
    }

    @Test(arguments: [true, false, nil] as [Bool?])
    func `inspect reports the effective automatic removal policy`(_ requested: Bool?) async throws {
        let runtime = InMemoryRuntime()
        await runtime.seedImage(.init(
            id: "sha256:auto-remove", references: ["auto-remove:test"], createdAt: Date(), size: 1
        ))
        let router = DockerRouter(runtime: runtime)
        var host: [String: Any] = [:]
        if let requested {
            host["AutoRemove"] = requested
        }
        let body = try JSONSerialization.data(withJSONObject: ["Image": "auto-remove:test", "HostConfig": host])
        let created = await router.respond(to: .init(
            method: .post, target: "/containers/create?name=auto-remove", body: body
        ))
        #expect(created.status == 201)
        let inspected = await router.respond(to: .init(method: .get, target: "/containers/auto-remove/json"))
        #expect(inspected.status == 200)
        let object = try #require(JSONSerialization.jsonObject(with: bytes(inspected)) as? [String: Any])
        let actual = try #require(object["HostConfig"] as? [String: Any])
        #expect(actual["AutoRemove"] as? Bool == (requested ?? false))
    }

    @Test func `create and inspect execution settings`() async throws {
        let runtime = InMemoryRuntime()
        await runtime.seedImage(.init(id: "sha256:settings", references: ["settings:test"], createdAt: Date(), size: 1))
        let router = DockerRouter(runtime: runtime)
        let host: [String: Any] = [
            "Memory": 268_435_456, "ShmSize": 33_554_432, "ReadonlyRootfs": true,
            "Sysctls": ["net.ipv4.ip_forward": "1"]
        ]
        let body = try JSONSerialization.data(
            withJSONObject: ["Image": "settings:test", "HostConfig": host, "StopSignal": "SIGUSR1"]
        )
        let created = await router.respond(to: .init(
            method: .post, target: "/containers/create?name=settings", body: body
        ))
        #expect(created.status == 201)
        let inspected = await router.respond(to: .init(method: .get, target: "/containers/settings/json"))
        let object = try #require(JSONSerialization.jsonObject(with: bytes(inspected)) as? [String: Any])
        let actual = try #require(object["HostConfig"] as? NSDictionary)
        for (key, value) in host {
            #expect(NSDictionary(dictionary: [key: actual[key] as Any]) == [key: value] as NSDictionary)
        }
        #expect((object["Config"] as? [String: Any])?["StopSignal"] as? String == "SIGUSR1")
    }

    @Test(arguments: ["Memory", "ShmSize"], [-1, -9_223_372_036_854_775_807])
    func `rejects negative bytes`(_ field: String, _ value: Int64) async throws {
        let body = try JSONSerialization.data(withJSONObject: ["Image": "missing", "HostConfig": [field: value]])
        let response = await DockerRouter(runtime: InMemoryRuntime()).respond(
            to: .init(method: .post, target: "/containers/create", body: body)
        )
        #expect(response.status == 400)
    }

    @Test func `zero and missing values retain defaults`() throws {
        let request = try JSONDecoder().decode(DockerCreateContainerRequest.self, from: Data(
            #"{"Image":"image","StopSignal":"","HostConfig":{"Memory":0,"ShmSize":0}}"#.utf8
        ))
        let settings = try DockerRouter(runtime: InMemoryRuntime()).executionSettings(request)
        #expect(settings == .init())
    }
}
