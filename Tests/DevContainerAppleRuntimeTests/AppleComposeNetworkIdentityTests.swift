// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

struct AppleComposeNetworkIdentityTests {
    private var labels: [String: String] {
        [
            "com.apple.container.compose.version": "1",
            "com.apple.container.compose.project": "example",
            "com.docker.compose.project": "example",
            "com.apple.container.compose.service": "database",
            "com.docker.compose.service": "database",
            "com.apple.container.compose.oneoff": "false",
            "com.docker.compose.oneoff": "false"
        ]
    }

    @Test
    func `complete mirrored native identity supplies a service alias`() {
        #expect(AppleContainerRuntime.nativeComposeServiceName(labels: labels) == "database")
        #expect(AppleContainerRuntime.nativeComposeServiceName(labels: [:]) == nil)
    }

    @Test(arguments: [
        "com.apple.container.compose.version", "com.apple.container.compose.project",
        "com.docker.compose.project", "com.apple.container.compose.service",
        "com.docker.compose.service", "com.apple.container.compose.oneoff", "com.docker.compose.oneoff"
    ])
    func `partial and conflicting identities do not supply aliases`(key: String) {
        var value = labels
        value.removeValue(forKey: key)
        #expect(AppleContainerRuntime.nativeComposeServiceName(labels: value) == nil)
        value[key] = "conflict"
        #expect(AppleContainerRuntime.nativeComposeServiceName(labels: value) == nil)
    }

    @Test(arguments: [
        "",
        "database\n127.0.0.1 attacker",
        "database other",
        "db\u{0}",
        String(repeating: "a", count: 254)
    ])
    func `unsafe service names are rejected even when both mirrors agree`(name: String) {
        var value = labels
        value["com.apple.container.compose.service"] = name
        value["com.docker.compose.service"] = name
        #expect(AppleContainerRuntime.nativeComposeServiceName(labels: value) == nil)
    }

    @Test
    func `service alias uses only an observed shared network address`() {
        let database = snapshot(name: "example-database-1", networks: ["shared", "private"])
        let target = snapshot(name: "example-app-1", networks: ["shared"])
        let hosts = AppleContainerRuntime.managedHosts(target: target, containers: [database])
        #expect(hosts.contains("192.0.2.2 database example-database-1"))
        #expect(!hosts.contains("198.51.100.2"))
        let isolated = snapshot(name: "isolated-app-1", networks: ["different"])
        #expect(!AppleContainerRuntime.managedHosts(target: isolated, containers: [database]).contains("database"))
        var missingAddress = database
        missingAddress.networkAddresses = [:]
        #expect(!AppleContainerRuntime.managedHosts(target: target, containers: [missingAddress]).contains("database"))
    }

    @Test
    func `observed restart generation takes precedence over adopted metadata`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
        var observed = snapshot(name: "app", networks: ["shared"])
        observed.startedAt = Date(timeIntervalSince1970: 3)
        let metadata = RuntimeContainerMetadata(
            runtimeID: observed.runtimeID, dockerID: observed.dockerID, spec: observed.spec,
            createdAt: observed.createdAt, startedAt: Date(timeIntervalSince1970: 2)
        )
        #expect(await runtime.apply(metadata: metadata, to: observed).startedAt == observed.startedAt)
        observed.startedAt = nil
        #expect(await runtime.apply(metadata: metadata, to: observed).startedAt == metadata.startedAt)
    }

    private func snapshot(name: String, networks: [String]) -> ContainerSnapshot {
        ContainerSnapshot(
            runtimeID: RuntimeID(rawValue: name), dockerID: DockerID(rawValue: name),
            spec: ContainerSpec(
                name: name,
                image: "alpine",
                labels: labels,
                networks: networks.map { NetworkAttachment(name: $0) }
            ),
            state: .running, createdAt: Date(timeIntervalSince1970: 1),
            networkAddresses: ["shared": "192.0.2.2", "private": "198.51.100.2"]
        )
    }
}
