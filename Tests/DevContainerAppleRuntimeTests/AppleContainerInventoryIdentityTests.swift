// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerResource
@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

struct AppleContainerInventoryIdentityTests {
    private let createdAt = Date(timeIntervalSince1970: 1_790_000_000.36559)
    private let labels = [AppleContainerRuntime.dockerIDLabel: "docker-fixture"]

    private func observed(at date: Date) -> ContainerResource.ContainerSnapshot {
        let base = nativeSnapshot(id: "fixture", labels: labels, status: .stopped)
        var configuration = base.configuration
        configuration.creationDate = date
        return ContainerResource.ContainerSnapshot(configuration: configuration, status: .stopped, networks: [])
    }

    private func cliRecord(_ snapshot: ContainerResource.ContainerSnapshot, fractional: Bool = false) -> [String: Any] {
        let formatter = ISO8601DateFormatter()
        if fractional {
            formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        }
        return [
            "id": snapshot.id,
            "configuration": [
                "creationDate": formatter.string(from: snapshot.configuration.creationDate),
                "labels": snapshot.configuration.labels,
                "image": [
                    "reference": snapshot.configuration.image.reference,
                    "descriptor": ["digest": snapshot.configuration.image.descriptor.digest]
                ],
                "initProcess": [
                    "executable": "/bin/sh",
                    "arguments": [],
                    "privileged": true,
                    "noNewPrivileges": true
                ],
                "hostname": "enhanced-host", "networks": [["network": "bridge", "options": ["aliases": ["workspace"]]]]
            ],
            "status": ["state": "stopped"], "exitCode": 17
        ]
    }

    private func metadata() -> RuntimeContainerMetadata {
        RuntimeContainerMetadata(
            runtimeID: RuntimeID(rawValue: "fixture"), dockerID: DockerID(rawValue: "docker-fixture"),
            imageID: "sha256:" + String(repeating: "c", count: 64),
            spec: ContainerSpec(
                name: "fixture", image: "fixture@sha256:" + String(repeating: "a", count: 64), labels: labels,
                networks: [NetworkAttachment(name: "bridge", aliases: ["workspace"])],
                privileged: true, securityOptions: ["no-new-privileges=true"]
            ),
            createdAt: createdAt
        )
    }

    @Test(arguments: [false, true])
    func `identity decoding ignores enhanced IPv6 only attachment schemas`(explicitNull: Bool) throws {
        var attachment: [String: Any] = ["network": "ipv6-only", "ipv6Address": "fd00::2/64"]
        if explicitNull {
            attachment["ipv4Address"] = NSNull()
            attachment["ipv4Gateway"] = NSNull()
        }
        let configuration: [String: Any] = [
            "id": "fixture", "creationDate": createdAt.timeIntervalSinceReferenceDate, "labels": labels,
            "image": ["reference": "fixture:latest", "descriptor": ["digest": "sha256:immutable"]]
        ]
        let payload = try JSONSerialization.data(withJSONObject: [[
            "configuration": configuration, "status": "running", "networks": [attachment]
        ]])
        let value = try AppleContainerIdentity.decode(payload, id: "fixture")
        #expect(value.creationDate == createdAt)
        #expect(value.id == "fixture")
        #expect(value.labels == labels)
        #expect(value.image.reference == "fixture:latest")
        #expect(value.image.descriptor.digest == "sha256:immutable")
        #expect(throws: (any Error).self) { try AppleContainerIdentity.decode(payload, id: "other") }
        #expect(throws: (any Error).self) { try AppleContainerIdentity.decode(Data("[]".utf8), id: "fixture") }
        #expect(throws: (any Error).self) { try AppleContainerIdentity.decode(Data("{}".utf8), id: "fixture") }
        let duplicate = try JSONSerialization.data(withJSONObject: [
            ["configuration": configuration], ["configuration": configuration]
        ])
        #expect(throws: (any Error).self) { try AppleContainerIdentity.decode(duplicate, id: "fixture") }
    }

    @Test(arguments: [false, true], [false, true])
    func `enhanced inventory preserves precise identity and metadata`(
        inMemory: Bool, fractional: Bool
    ) async throws {
        let fixture = try FakeAppleCLI(distribution: "container-compose")
        let native = observed(at: createdAt)
        try fixture.setContainerInventory([cliRecord(native, fractional: fractional)])
        let inventory = FakeContainerInventory(snapshots: [native])
        let store = TestMetadataStore()
        let expected = metadata()
        await store.recordContainerMetadata(expected)
        let runtime = try directRuntime(fixture: fixture, inventory: inventory, metadataStore: store)
        if inMemory {
            await runtime.seedIdentityRequest(expected)
        }

        let snapshots = try await runtime.listContainers(all: true, labels: [:], context: RuntimeRequestContext())
        let snapshot = try #require(snapshots.first)
        #expect(snapshot.createdAt == createdAt)
        #expect(snapshot.spec.image == expected.spec.image)
        #expect(snapshot.imageID == expected.imageID)
        #expect(snapshot.state == .created)
        #expect(snapshot.spec.hostname == "enhanced-host")
        #expect(snapshot.spec.privileged)
        #expect(snapshot.spec.networks == [NetworkAttachment(name: "bridge", aliases: ["workspace"])])
        #expect(snapshot.spec.securityOptions.contains("no-new-privileges=true"))
        #expect(await store.containerMetadata(id: "fixture") == expected)
        #expect(await inventory.getCallCount() == 1)
    }

    @Test
    func `same second replacement cannot inherit old metadata or image identity`() async throws {
        let fixture = try FakeAppleCLI(distribution: "container-compose")
        let native = observed(at: createdAt.addingTimeInterval(0.25))
        try fixture.setContainerInventory([cliRecord(native)])
        let inventory = FakeContainerInventory(snapshots: [native])
        let store = TestMetadataStore()
        let old = metadata()
        await store.recordContainerMetadata(old)
        let runtime = try directRuntime(fixture: fixture, inventory: inventory, metadataStore: store)
        await runtime.seedIdentityRequest(old)

        let snapshots = try await runtime.listContainers(all: true, labels: [:], context: RuntimeRequestContext())
        let snapshot = try #require(snapshots.first)
        #expect(snapshot.createdAt == native.configuration.creationDate)
        #expect(snapshot.imageID != old.imageID)
        #expect(snapshot.state == .stopped)
        #expect(await store.containerMetadata(id: "fixture") == nil)
    }

    @Test
    func `failed precise identity lookup preserves metadata`() async throws {
        let fixture = try FakeAppleCLI(distribution: "container-compose")
        let native = observed(at: createdAt)
        try fixture.setContainerInventory([cliRecord(native)])
        let inventory = FakeContainerInventory(snapshots: [native])
        await inventory.setGetFailure(.failed)
        let store = TestMetadataStore()
        let expected = metadata()
        await store.recordContainerMetadata(expected)
        let runtime = try directRuntime(fixture: fixture, inventory: inventory, metadataStore: store)
        await runtime.seedIdentityRequest(expected)

        await #expect(throws: DevContainerError.self) {
            try await runtime.listContainers(all: true, labels: [:], context: RuntimeRequestContext())
        }
        #expect(await store.containerMetadata(id: "fixture") == expected)
        #expect(await runtime.hasIdentityRequest())
    }

    @Test(arguments: ["id", "labels", "image", "digest", "date", "missing-date"])
    func `inconsistent observations preserve all local identity state`(field: String) async throws {
        let fixture = try FakeAppleCLI(distribution: "container-compose")
        let native = observed(at: createdAt)
        var value = cliRecord(native)
        var configuration = try #require(value["configuration"] as? [String: Any])
        switch field {
        case "id": value["id"] = "other"
        case "labels": configuration["labels"] = ["different": "labels"]
        case "image":
            configuration["image"] = [
                "reference": "other:latest", "descriptor": ["digest": native.configuration.image.descriptor.digest]
            ]
        case "digest":
            configuration["image"] = ["reference": "fixture:latest", "descriptor": ["digest": "sha256:wrong"]]
        case "date": configuration["creationDate"] = "2000-01-01T00:00:00Z"
        default: configuration.removeValue(forKey: "creationDate")
        }
        value["configuration"] = configuration
        try fixture.setContainerInventory([value])
        let inventory = FakeContainerInventory(snapshots: [native], returnFirstForUnknownID: true)
        let store = TestMetadataStore()
        let expected = metadata()
        await store.recordContainerMetadata(expected)
        let runtime = try directRuntime(fixture: fixture, inventory: inventory, metadataStore: store)
        await runtime.seedIdentityRequest(expected)
        await #expect(throws: DevContainerError.self) {
            try await runtime.listContainers(all: true, labels: [:], context: RuntimeRequestContext())
        }
        #expect(await store.containerMetadata(id: "fixture") == expected)
        #expect(await runtime.hasIdentityRequest())
    }

    @Test
    func `first adoption and repeated enhanced observations retain native precision`() async throws {
        let fixture = try FakeAppleCLI(distribution: "container-compose")
        let base = observed(at: createdAt)
        var configuration = base.configuration
        configuration.labels = [:]
        let native = ContainerResource.ContainerSnapshot(configuration: configuration, status: .stopped, networks: [])
        try fixture.setContainerInventory([cliRecord(native)])
        let store = TestMetadataStore()
        let inventory = FakeContainerInventory(snapshots: [native])
        let runtime = try directRuntime(fixture: fixture, inventory: inventory, metadataStore: store)
        let first = try await runtime.listContainers(all: true, labels: [:], context: RuntimeRequestContext())
        let snapshot = try #require(first.first)
        let adopted = try #require(await store.containerMetadata(id: "fixture"))
        #expect(adopted.createdAt == createdAt)
        #expect(snapshot.spec.privileged)
        #expect(snapshot.spec.hostname == "enhanced-host")
        #expect(snapshot.spec.networks == [NetworkAttachment(name: "bridge", aliases: ["workspace"])])
        #expect(snapshot.spec.securityOptions.contains("no-new-privileges=true"))
        let second = try await runtime.listContainers(all: true, labels: [:], context: RuntimeRequestContext())
        #expect(second.first?.dockerID == snapshot.dockerID)
        #expect(await store.containerMetadata(id: "fixture") == adopted)
        #expect(await inventory.getCallCount() == 2)
    }
}

private extension AppleContainerRuntime {
    func seedIdentityRequest(_ metadata: RuntimeContainerMetadata) {
        requestedContainers[metadata.runtimeID.rawValue] = RequestedContainer(
            spec: metadata.spec, imageID: metadata.imageID, createdAt: metadata.createdAt
        )
    }

    func hasIdentityRequest() -> Bool {
        requestedContainers["fixture"] != nil
    }
}
