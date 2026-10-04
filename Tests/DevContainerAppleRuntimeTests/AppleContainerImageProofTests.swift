// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerResource
@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

extension AppleContainerRuntimeDirectTests {
    @Test
    func `typed list and inspect prove bare config ID from descriptor and platform`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        try fixture.setImageInventory([imageRecord("fixture:latest")])
        let config = FakeAppleImageIdentityClient.digest
        let native = nativeSnapshot(
            id: "fixture", labels: [AppleContainerRuntime.composeImageReferenceLabel: config], status: .running
        )
        let runtime = try directRuntime(fixture: fixture, inventory: FakeContainerInventory(snapshots: [native]))
        let context = RuntimeRequestContext()
        let listed = try await runtime.listContainersDirect(all: true, labels: [:], context: context)
        #expect(listed.first?.spec.image == config)
        let inspected = try await runtime.inspectContainerDirect(id: "fixture", context: context)
        #expect(inspected?.spec.image == config)
    }

    @Test
    func `typed inventory refuses a manifest digest mislabeled as config ID`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let manifest = "sha256:" + String(repeating: "a", count: 64)
        let native = nativeSnapshot(
            id: "fixture", labels: [AppleContainerRuntime.composeImageReferenceLabel: manifest], status: .running
        )
        let runtime = try directRuntime(fixture: fixture, inventory: FakeContainerInventory(snapshots: [native]))
        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.listContainersDirect(all: true, labels: [:], context: RuntimeRequestContext())
        }
    }

    @Test(arguments: ["list", "inspect", "creation"])
    func `typed image lookup refuses a replaced container incarnation`(path: String) async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        try fixture.setImageInventory([imageRecord("fixture:latest")])
        let configID = FakeAppleImageIdentityClient.digest
        let original = nativeSnapshot(
            id: "fixture", labels: [AppleContainerRuntime.composeImageReferenceLabel: configID], status: .stopped
        )
        let inventory = FakeContainerInventory(snapshots: [original])
        let held = HeldImageIdentityClient()
        let runtime = try directRuntime(fixture: fixture, inventory: inventory, images: held)
        let creation = try RuntimeContainerCreation(
            runtimeID: "fixture", nativeCreatedAt: original.configuration.creationDate,
            imageID: configID, spec: ContainerSpec(name: "fixture", image: configID),
            nativeConfiguration: JSONEncoder().encode(original.configuration)
        )
        let task = Task {
            switch path {
            case "list":
                _ = try await runtime.listContainersDirect(all: true, labels: [:], context: .init())
            case "inspect":
                _ = try await runtime.inspectContainerDirect(id: "fixture", context: .init())
            default:
                _ = try await runtime.verifiedCreationSnapshot(creation, context: .init())
            }
        }
        await held.waitForLookup()
        var replacement = original.configuration
        replacement.creationDate = replacement.creationDate.addingTimeInterval(0.25)
        await inventory.replaceSnapshots([.init(configuration: replacement, status: .stopped, networks: [])])
        await held.releaseLookup()
        await #expect(throws: DevContainerError.self) { try await task.value }
    }

    @Test
    func `creation image verification retains the caller deadline and cancellation`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let configID = FakeAppleImageIdentityClient.digest
        let original = nativeSnapshot(
            id: "fixture", labels: [AppleContainerRuntime.composeImageReferenceLabel: configID], status: .stopped
        )
        let runtime = try directRuntime(
            fixture: fixture, inventory: FakeContainerInventory(snapshots: [original])
        )
        let creation = try RuntimeContainerCreation(
            runtimeID: "fixture", nativeCreatedAt: original.configuration.creationDate,
            imageID: configID, spec: ContainerSpec(name: "fixture", image: configID),
            nativeConfiguration: JSONEncoder().encode(original.configuration)
        )
        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.verifiedCreationSnapshot(
                creation, context: .init(deadline: Date(timeIntervalSince1970: 1))
            )
        }
        let held = HeldImageIdentityClient()
        let cancelling = try directRuntime(
            fixture: fixture, inventory: FakeContainerInventory(snapshots: [original]), images: held
        )
        let task = Task {
            try await cancelling.verifiedCreationSnapshot(creation, context: .init())
        }
        await held.waitForLookup()
        task.cancel()
        await held.releaseLookup()
        await #expect(throws: DevContainerError.self) { _ = try await task.value }
    }
}

private actor HeldImageIdentityClient: AppleImageIdentityClient {
    private var entered = false
    private var entryWaiter: CheckedContinuation<Void, Never>?
    private var release: CheckedContinuation<Void, Never>?

    func waitForLookup() async {
        if entered {
            return
        }
        await withCheckedContinuation { entryWaiter = $0 }
    }

    func releaseLookup() {
        release?.resume()
        release = nil
    }

    func configurationIdentity(
        reference: String, descriptor: Data, platform: Data
    ) async throws -> AppleImageConfigurationIdentity {
        await withCheckedContinuation { continuation in
            release = continuation
            entered = true
            entryWaiter?.resume()
            entryWaiter = nil
        }
        return try await FakeAppleImageIdentityClient().configurationIdentity(
            reference: reference, descriptor: descriptor, platform: platform
        )
    }

    func deleteNamedReference(_ reference: String) async throws {
        try await FakeAppleImageIdentityClient().deleteNamedReference(reference)
    }
}
