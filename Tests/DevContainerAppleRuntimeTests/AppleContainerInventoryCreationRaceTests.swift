// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerizationError
import ContainerResource
@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

struct AppleContainerInventoryCreationRaceTests {
    @Test
    func `inventory reconciliation retains containers created during the native list`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let store = TestMetadataStore()
        let old = metadata(id: "removed")
        let created = metadata(id: "new-app")
        await store.recordContainerMetadata(old)
        let inventory = CreationDuringInventory(store: store, created: created)
        let runtime = try directRuntime(fixture: fixture, inventory: inventory, metadataStore: store)

        let listed = try await runtime.listContainersDirect(all: true, labels: [:], context: .init())

        #expect(listed.isEmpty)
        #expect(await store.containerMetadata(id: "removed") == nil)
        #expect(await store.containerMetadata(id: "new-app") == created)
    }

    private func metadata(id: String) -> RuntimeContainerMetadata {
        RuntimeContainerMetadata(
            runtimeID: RuntimeID(rawValue: id), dockerID: DockerID(rawValue: "docker-" + id),
            imageID: FakeAppleImageIdentityClient.digest,
            spec: ContainerSpec(name: id, image: "fixture:latest"), createdAt: Date()
        )
    }
}

private actor CreationDuringInventory: AppleContainerInventoryClient {
    let store: TestMetadataStore
    let created: RuntimeContainerMetadata

    init(store: TestMetadataStore, created: RuntimeContainerMetadata) {
        self.store = store
        self.created = created
    }

    func list() async throws -> [ContainerResource.ContainerSnapshot] {
        // The list observation precedes creation, while its response arrives after
        // the new durable identity. Reproduce that interleaving without sleeps.
        await store.recordContainerMetadata(created)
        return []
    }

    func get(id: String) throws -> ContainerResource.ContainerSnapshot {
        throw ContainerizationError(.notFound, message: id)
    }
}
