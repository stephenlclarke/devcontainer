// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerTestSupport
import Foundation
import Testing

struct DockerBindSourcePolicyTests {
    @Test(arguments: [true, false, nil] as [Bool?])
    func `structured bind preserves creation policy`(_ policy: Bool?) async throws {
        let runtime = InMemoryRuntime()
        await runtime.seedImage(.init(id: "sha256:fixture", references: ["fixture:test"], createdAt: Date(), size: 1))
        var options: [String: Any] = [:]
        options["CreateMountpoint"] = policy
        let response = try await DockerRouter(runtime: runtime).respond(to: .init(
            method: .post, target: "/containers/create?name=bind-policy",
            body: JSONSerialization.data(withJSONObject: [
                "Image": "fixture:test", "HostConfig": ["Mounts": [[
                    "Type": "bind", "Source": "/tmp/source", "Target": "/work", "ReadOnly": true,
                    "BindOptions": options
                ]]]
            ])
        ))
        #expect(response.status == 201)
        let snapshot = try await runtime.inspectContainer(id: "bind-policy", context: .init())
        let mount = try #require(snapshot.spec.mounts.first)
        #expect(mount.createSourceDirectory == policy)
        #expect(mount.readOnly)
        #expect(mount.source == "/tmp/source")
    }

    @Test func `legacy binds create directories but not named volumes`() throws {
        let mounts = try DockerRouter(runtime: InMemoryRuntime()).bindMounts(["/tmp/host:/work:ro", "cache:/cache"])
        #expect(mounts[0].createSourceDirectory == true)
        #expect(mounts[1].createSourceDirectory == nil)
    }

    @Test func `non bind creation option remains rejected`() async {
        let response = await DockerRouter(runtime: InMemoryRuntime()).respond(to: .init(
            method: .post, target: "/containers/create",
            body: Data(#"""
            {"Image":"fixture:test","Mounts":[{"Type":"volume","Source":"cache","Target":"/work",
            "BindOptions":{"CreateMountpoint":true}}]}
            """#.utf8)
        ))
        #expect(response.status == 501)
    }
}
