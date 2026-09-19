// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerResource
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

extension AppleContainerRuntime {
    func completeContainerCreation(
        _ creation: RuntimeContainerCreation?, spec: ContainerSpec,
        imageID: String, context: RuntimeRequestContext
    ) async throws -> DevContainerModel.ContainerSnapshot {
        guard let creation else {
            requestedContainers[spec.name] = RequestedContainer(spec: spec, imageID: imageID, createdAt: nil)
            var snapshot = try await inspectContainer(id: spec.name, context: context)
            snapshot.imageID = imageID
            try await recordContainerMetadata(snapshot: snapshot, spec: spec)
            return snapshot
        }
        var snapshot = try await verifiedCreationSnapshot(creation)
        let storedSpec = creation.spec
        if spec.labels[Self.dockerIDLabel] == nil {
            snapshot.dockerID = DockerID(rawValue: Self.syntheticDockerIdentifier())
        }
        try context.checkActive()
        try await requireCreationStore().finishContainerCreation(
            RuntimeContainerMetadata(
                runtimeID: snapshot.runtimeID, dockerID: snapshot.dockerID,
                imageID: imageID, spec: storedSpec, createdAt: snapshot.createdAt
            ),
            operationID: creation.operationID
        )
        requestedContainers[spec.name] = RequestedContainer(
            spec: storedSpec, imageID: imageID, createdAt: snapshot.createdAt
        )
        snapshot.imageID = imageID
        snapshot.spec = Self.effectiveContainerSpec(requested: spec, observed: snapshot.spec)
        if snapshot.state == .stopped {
            snapshot.state = .created
        }
        return snapshot
    }

    func requireCreationStore() throws -> any RuntimeCreationStore {
        guard let store = metadataStore as? any RuntimeCreationStore else {
            throw DevContainerError(
                .unsupportedCapability,
                message: "native container creation requires a durable RuntimeCreationStore"
            )
        }
        return store
    }

    /// Revalidate the final observation, not an earlier create RPC response.
    /// Never persist a replacement's timestamp with the requested image/spec.
    func verifiedCreationSnapshot(_ creation: RuntimeContainerCreation) async throws -> DevContainerModel
        .ContainerSnapshot
    {
        let expected = try JSONDecoder().decode(ContainerConfiguration.self, from: creation.nativeConfiguration)
        let observed = try await inventoryClient.get(id: creation.runtimeID)
        try AppleContainerCreateProjection.verify(observed.configuration, expected: expected)
        let expectedHosts = try managedNetworkHostsIdentity(configuration: expected)
        let observedHosts = try managedNetworkHostsIdentity(configuration: observed.configuration)
        guard expectedHosts == observedHosts else {
            throw DevContainerError(.providerProtocolMismatch, message: "Created network hosts mount identity changed")
        }
        if let observedHosts {
            // Validate persisted inode provenance without changing the file.
            try managedNetworkHosts.update(identity: observedHosts) { $0 }
        }
        guard observed.configuration.creationDate == creation.nativeCreatedAt else {
            throw DevContainerError(
                .providerProtocolMismatch, message: "created container incarnation changed before completion"
            )
        }
        return try containerSnapshot(containerRecord(observed))
    }

    /// Never delete by name after a failed create: stock Apple has no conditional
    /// delete. Preserve the write-ahead intent and block launches of that object.
    /// Absence does not prove a timed-out create cannot still complete. Retain
    /// intent until explicit reconciliation/removal; a different incarnation may
    /// be used, but never erases the earlier operation's unresolved evidence.
    func requireCompletedCreation(id: String, forCreate: Bool = false) async throws {
        guard let store = metadataStore as? any RuntimeCreationStore,
              let creation = try await store.pendingContainerCreation(id: id)
        else { return }
        guard !forCreate else { throw Self.incompleteCreation(id: id) }
        let observed: ContainerResource.ContainerSnapshot
        do {
            observed = try await inventoryClient.get(id: id)
        } catch {
            let mapped = directAPIError(error, operation: "reconcile container creation")
            guard mapped.code == .notFound else { throw mapped }
            throw Self.incompleteCreation(id: id)
        }
        guard observed.id == id else {
            throw DevContainerError(
                .providerProtocolMismatch, message: "creation reconciliation returned a different container"
            )
        }
        guard observed.configuration.creationDate != creation.nativeCreatedAt else {
            throw Self.incompleteCreation(id: id)
        }
        // A native client replaced the failed object. Do not block or delete the
        // replacement and do not project the rejected object's metadata onto it.
        requestedContainers.removeValue(forKey: id)
    }

    private static func incompleteCreation(id: String) -> DevContainerError {
        DevContainerError(
            .conflict,
            message: "container \(id) has an incomplete create; inspect and explicitly reconcile its retained creation record before retrying"
        )
    }
}
