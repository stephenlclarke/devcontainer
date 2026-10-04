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
                "platform": ["architecture": "arm64", "os": "linux"],
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
    func `enhanced CLI bare config ID is verified before stale metadata adoption`(replaced: Bool) async throws {
        let fixture = try FakeAppleCLI(distribution: "container-compose")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let configID = FakeAppleImageIdentityClient.digest
        let base = observed(at: createdAt)
        var configuration = base.configuration
        configuration.labels[AppleContainerRuntime.composeImageReferenceLabel] = configID
        let cli = ContainerResource.ContainerSnapshot(configuration: configuration, status: .stopped, networks: [])
        try fixture.setContainerInventory([cliRecord(cli, fractional: replaced)])
        if replaced {
            configuration.creationDate = configuration.creationDate.addingTimeInterval(0.25)
        }
        let native = ContainerResource.ContainerSnapshot(configuration: configuration, status: .stopped, networks: [])
        let store = TestMetadataStore()
        var old = metadata()
        if !replaced {
            old.imageID = configID
        }
        await store.recordContainerMetadata(old)
        let runtime = try directRuntime(
            fixture: fixture, inventory: FakeContainerInventory(snapshots: [native]), metadataStore: store
        )
        if replaced {
            await #expect(throws: DevContainerError.self) {
                _ = try await runtime.listContainers(all: true, labels: [:], context: .init())
            }
            #expect(await store.containerMetadata(id: "fixture") == old)
        } else {
            let listed = try await runtime.listContainers(all: true, labels: [:], context: .init())
            #expect(listed.first?.spec.image == configID)
            #expect(listed.first?.imageID == configID)
        }
    }

    @Test
    func `enhanced CLI cancellation during final native image observation prevents adoption`() async throws {
        let fixture = try FakeAppleCLI(distribution: "container-compose")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        var configuration = observed(at: createdAt).configuration
        configuration.labels[AppleContainerRuntime.composeImageReferenceLabel] = FakeAppleImageIdentityClient.digest
        let native = ContainerResource.ContainerSnapshot(configuration: configuration, status: .stopped, networks: [])
        try fixture.setContainerInventory([cliRecord(native)])
        let inventory = FakeContainerInventory(snapshots: [native])
        await inventory.holdGet(call: 3)
        let store = TestMetadataStore()
        let runtime = try directRuntime(fixture: fixture, inventory: inventory, metadataStore: store)
        let task = Task {
            try await runtime.listContainers(all: true, labels: [:], context: .init())
        }
        await inventory.waitForHeldGet()
        task.cancel()
        await inventory.releaseGet()
        await #expect(throws: DevContainerError.self) { _ = try await task.value }
        #expect(await store.containerMetadata(id: "fixture") == nil)
    }

    @Test
    func `enhanced CLI rejects a manifest digest posing as the config ID`() async throws {
        let fixture = try FakeAppleCLI(distribution: "container-compose")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let manifest = "sha256:" + String(repeating: "a", count: 64)
        var configuration = observed(at: createdAt).configuration
        configuration.labels[AppleContainerRuntime.composeImageReferenceLabel] = manifest
        let native = ContainerResource.ContainerSnapshot(configuration: configuration, status: .stopped, networks: [])
        try fixture.setContainerInventory([cliRecord(native)])
        let store = TestMetadataStore()
        let old = metadata()
        await store.recordContainerMetadata(old)
        let runtime = try directRuntime(
            fixture: fixture, inventory: FakeContainerInventory(snapshots: [native]), metadataStore: store
        )
        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.listContainers(all: true, labels: [:], context: .init())
        }
        #expect(await store.containerMetadata(id: "fixture") == old)
    }

    @Test
    func `same-incarnation metadata cannot replace a proven configuration ID`() async throws {
        let fixture = try FakeAppleCLI(distribution: "container-compose")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        var configuration = observed(at: createdAt).configuration
        configuration.labels[AppleContainerRuntime.composeImageReferenceLabel] = FakeAppleImageIdentityClient.digest
        let native = ContainerResource.ContainerSnapshot(configuration: configuration, status: .stopped, networks: [])
        try fixture.setContainerInventory([cliRecord(native)])
        let store = TestMetadataStore()
        let old = metadata() // Same incarnation, but a different persisted image ID.
        await store.recordContainerMetadata(old)
        let runtime = try directRuntime(
            fixture: fixture, inventory: FakeContainerInventory(snapshots: [native]), metadataStore: store
        )
        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.listContainers(all: true, labels: [:], context: .init())
        }
        #expect(await store.containerMetadata(id: "fixture") == old)
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
            "configuration": configuration, "status": "running", "networks": [attachment],
            "startedDate": createdAt.addingTimeInterval(10.125).timeIntervalSinceReferenceDate
        ]])
        let value = try AppleContainerIdentity.decode(payload, id: "fixture")
        #expect(value.creationDate == createdAt)
        #expect(value.id == "fixture")
        #expect(value.labels == labels)
        #expect(value.image.reference == "fixture:latest")
        #expect(value.image.descriptor.digest == "sha256:immutable")
        #expect(value.startedDate == createdAt.addingTimeInterval(10.125))
        #expect(throws: (any Error).self) { try AppleContainerIdentity.decode(payload, id: "other") }
        #expect(throws: (any Error).self) { try AppleContainerIdentity.decode(Data("[]".utf8), id: "fixture") }
        #expect(throws: (any Error).self) { try AppleContainerIdentity.decode(Data("{}".utf8), id: "fixture") }
        let duplicate = try JSONSerialization.data(withJSONObject: [
            ["configuration": configuration], ["configuration": configuration]
        ])
        #expect(throws: (any Error).self) { try AppleContainerIdentity.decode(duplicate, id: "fixture") }
    }

    @Test(arguments: ["missing", "null", "ipv6-only"])
    func `raw native image authority proves a bare alias without decoding attachments`(
        attachmentShape: String
    ) async throws {
        var attachment: [String: Any] = ["network": "ipv6-only"]
        if attachmentShape == "null" {
            attachment["ipv4Address"] = NSNull()
            attachment["ipv4Gateway"] = NSNull()
        }
        if attachmentShape == "ipv6-only" {
            attachment["ipv6Address"] = "fd00::2/64"
        }
        let alias = FakeAppleImageIdentityClient.digest
        let payload = try JSONSerialization.data(withJSONObject: [[
            "configuration": [
                "id": "fixture", "creationDate": createdAt.timeIntervalSinceReferenceDate,
                "labels": [AppleContainerRuntime.composeImageReferenceLabel: alias],
                "image": ["reference": "fixture:latest", "descriptor": [
                    "digest": "sha256:" + String(repeating: "a", count: 64)
                ]],
                "platform": ["architecture": "arm64", "os": "linux"]
            ],
            "startedDate": NSNull(), "networks": [attachment]
        ]])
        let authority = try AppleContainerImageAuthority.decode(payload, id: "fixture")
        let proved = try await FakeAppleImageIdentityClient().configurationIdentity(
            reference: authority.identity.image.reference,
            descriptor: authority.descriptor, platform: authority.platform
        )
        #expect(proved.digest == alias)
        #expect(try authority.matches(AppleContainerImageAuthority.decode(payload, id: "fixture")))
        #expect(throws: (any Error).self) {
            try AppleContainerImageAuthority.decode(payload, id: "other")
        }
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

    @Test(arguments: [false, true])
    func `enhanced running inventory retains exact process generation across same second restarts`(
        fractional: Bool
    ) async throws {
        let fixture = try FakeAppleCLI(distribution: "container-compose")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let configuration = observed(at: createdAt).configuration
        let inventory = FakeContainerInventory(snapshots: [])
        let store = TestMetadataStore()
        await store.recordContainerMetadata(metadata())
        let runtime = try directRuntime(fixture: fixture, inventory: inventory, metadataStore: store)
        let formatter = ISO8601DateFormatter()
        if fractional {
            formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        }
        for offset in [0.125, 0.375] {
            let startedAt = createdAt.addingTimeInterval(10 + offset)
            let native = ContainerResource.ContainerSnapshot(
                configuration: configuration, status: .running, networks: [], startedDate: startedAt
            )
            await inventory.replaceSnapshots([native])
            var value = cliRecord(native, fractional: fractional)
            value["status"] = ["state": "running", "startedDate": formatter.string(from: startedAt)]
            try fixture.setContainerInventory([value])
            let snapshot = try await runtime.inspectContainer(id: "fixture", context: .init())
            #expect(snapshot.state == .running)
            #expect(snapshot.startedAt == startedAt)
        }
        #expect(await inventory.getCallCount() == 2)
        await runtime.shutdown()
    }

    @Test(arguments: ["different", "missing", "malformed"])
    func `inconsistent native process generation rejects CLI observation`(variation: String) async throws {
        let fixture = try FakeAppleCLI(distribution: "container-compose")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let startedAt = createdAt.addingTimeInterval(10.125)
        let native = ContainerResource.ContainerSnapshot(
            configuration: observed(at: createdAt).configuration, status: .running, networks: [], startedDate: startedAt
        )
        var value = cliRecord(native)
        var status = ["state": "running"]
        if variation != "missing" {
            status["startedDate"] = variation == "malformed"
                ? "invalid" : ISO8601DateFormatter().string(from: startedAt.addingTimeInterval(2))
        }
        value["status"] = status
        try fixture.setContainerInventory([value])
        let inventory = FakeContainerInventory(snapshots: [native])
        let store = TestMetadataStore()
        let expected = metadata()
        await store.recordContainerMetadata(expected)
        let runtime = try directRuntime(fixture: fixture, inventory: inventory, metadataStore: store)
        await #expect(throws: DevContainerError.self) {
            try await runtime.inspectContainer(id: "fixture", context: .init())
        }
        #expect(await store.containerMetadata(id: "fixture") == expected)
        await runtime.shutdown()
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
