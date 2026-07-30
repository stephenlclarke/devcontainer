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

import ContainerAPIClient
import ContainerizationError
import ContainerResource
import DevContainerModel

protocol AppleContainerInventoryClient: Sendable {
    func list() async throws -> [ContainerResource.ContainerSnapshot]
    func get(id: String) async throws -> ContainerResource.ContainerSnapshot
}

protocol AppleContainerFileClient: Sendable {
    func copyIn(
        id: String,
        source: String,
        destination: String
    ) async throws
    func copyOut(
        id: String,
        source: String,
        destination: String
    ) async throws
}

struct LiveAppleContainerFileClient: AppleContainerFileClient {
    let client: ContainerClient

    func copyIn(
        id: String,
        source: String,
        destination: String
    ) async throws {
        try await client.copyIn(
            id: id,
            source: source,
            destination: destination
        )
    }

    func copyOut(
        id: String,
        source: String,
        destination: String
    ) async throws {
        try await client.copyOut(
            id: id,
            source: source,
            destination: destination
        )
    }
}

struct LiveAppleContainerInventoryClient: AppleContainerInventoryClient {
    let client: ContainerClient

    func list() async throws -> [ContainerResource.ContainerSnapshot] {
        try await client.list(filters: .all.withoutMachines())
    }

    func get(id: String) async throws -> ContainerResource.ContainerSnapshot {
        try await client.get(id: id)
    }
}

protocol AppleNetworkClient: Sendable {
    func list() async throws -> [NetworkSnapshot]
    func get(id: String) async throws -> NetworkSnapshot
    func create(spec: NetworkSpec) async throws -> NetworkSnapshot
    func delete(id: String) async throws
}

final class AppleNetworkClientAdapter: AppleNetworkClient {
    private let client = NetworkClient()

    func list() async throws -> [NetworkSnapshot] {
        try await client.list().map(Self.snapshot)
    }

    func get(id: String) async throws -> NetworkSnapshot {
        try await Self.snapshot(client.get(id: id))
    }

    func create(spec: NetworkSpec) async throws -> NetworkSnapshot {
        let configuration = try NetworkConfiguration(
            name: spec.name,
            mode: spec.internalNetwork ? .hostOnly : .nat,
            labels: ResourceLabels(spec.labels),
            plugin: "container-network-vmnet"
        )
        return try await Self.snapshot(
            client.create(configuration: configuration)
        )
    }

    func delete(id: String) async throws {
        try await client.delete(id: id)
    }

    private static func snapshot(_ value: NetworkResource) -> NetworkSnapshot {
        NetworkSnapshot(
            id: value.id,
            spec: NetworkSpec(
                name: value.name,
                labels: value.configuration.labels.dictionary,
                driver: value.configuration.plugin,
                internalNetwork: value.configuration.mode == .hostOnly
            ),
            createdAt: value.creationDate
        )
    }
}

extension AppleContainerRuntime {
    // Inventory reconciliation keeps metadata, image identity, filters, and
    // orphan cleanup in one pass.
    // swiftlint:disable:next function_body_length
    func listContainersDirect(
        all: Bool,
        labels: [String: String],
        context: RuntimeRequestContext
    ) async throws -> [DevContainerModel.ContainerSnapshot] {
        try context.checkActive()
        let values: [ContainerResource.ContainerSnapshot]
        do {
            values = try await inventoryClient.list()
        } catch {
            throw directAPIError(error, operation: "container list")
        }

        let metadata = try await containerMetadataByRuntimeID()
        var observedValues: [DevContainerModel.ContainerSnapshot] = []
        observedValues.reserveCapacity(values.count)
        for value in values {
            let observed = try containerSnapshot(containerRecord(value))
            if !Self.isInternalBuilderResource(observed) {
                observedValues.append(observed)
            }
        }
        let requiresImageResolution = observedValues.contains {
            $0.imageID == nil
                && metadata[$0.runtimeID.rawValue]?.imageID == nil
        }
        let images = requiresImageResolution
            ? try await listImages(context: context)
            : []

        var snapshots: [DevContainerModel.ContainerSnapshot] = []
        snapshots.reserveCapacity(observedValues.count)
        var observedRuntimeIDs = Set<String>()
        for observed in observedValues {
            let imageID = observed.imageID
                ?? metadata[observed.runtimeID.rawValue]?.imageID
                ?? Self.imageID(for: observed.spec.image, in: images)
            let snapshot = try await containerSnapshotWithMetadata(
                observed,
                metadata: metadata[observed.runtimeID.rawValue],
                imageID: imageID
            )
            observedRuntimeIDs.insert(snapshot.runtimeID.rawValue)
            guard all || snapshot.state == .running else {
                continue
            }
            guard labels.allSatisfy({ key, expected in
                guard let actual = snapshot.spec.labels[key] else {
                    return false
                }
                return expected.isEmpty || actual == expected
            }) else {
                continue
            }
            snapshots.append(snapshot)
        }
        if all {
            try await removeOrphanedContainerMetadata(
                observedRuntimeIDs: observedRuntimeIDs,
                metadata: metadata
            )
        }
        try context.checkActive()
        return snapshots
    }

    func inspectContainerDirect(
        id: String,
        context: RuntimeRequestContext
    ) async throws -> DevContainerModel.ContainerSnapshot? {
        try context.checkActive()
        let runtimeID =
            try await metadataStore?.containerMetadata(id: id)?
                .runtimeID.rawValue ?? id
        let value: ContainerResource.ContainerSnapshot
        do {
            value = try await inventoryClient.get(id: runtimeID)
        } catch let error as ContainerizationError where error.code == .notFound {
            return nil
        } catch {
            throw directAPIError(error, operation: "container inspect")
        }
        let observed = try containerSnapshot(containerRecord(value))
        let metadata = try await metadataStore?.containerMetadata(
            id: observed.runtimeID.rawValue
        )
        var imageID = observed.imageID ?? metadata?.imageID
        if imageID == nil {
            imageID = try await Self.imageID(
                for: observed.spec.image,
                in: listImages(context: context)
            )
        }
        let snapshot = try await containerSnapshotWithMetadata(
            observed,
            metadata: metadata,
            imageID: imageID
        )
        guard
            snapshot.runtimeID.rawValue == id
            || snapshot.dockerID.rawValue == id
            || snapshot.spec.name == id
        else {
            return nil
        }
        try context.checkActive()
        return snapshot
    }
}
