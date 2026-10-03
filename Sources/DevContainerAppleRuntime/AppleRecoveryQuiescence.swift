// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
import DevContainerRuntimeSPI

extension AppleContainerRuntime: RuntimeRecoveryProbe {
    public func requireRecoveryQuiescence(context: RuntimeRequestContext) async throws {
        try context.checkActive()
        guard useDirectContainerAPI else {
            throw DevContainerError(.unsupportedCapability, message: "CLI recovery quiescence is unavailable")
        }
        let revision = containerLifecycleMutationRevision
        guard containerLifecycleMutationRegistrations.isEmpty, containerIOClosures.isEmpty else {
            throw DevContainerError(.conflict, message: "Native container creation is not quiescent")
        }
        try await reconcilePendingContainerCreations(context: context)
        guard
            try await !requireCreationStore().hasPendingContainerCreations(),
            containerLifecycleMutationRegistrations.isEmpty, containerIOClosures.isEmpty,
            revision == containerLifecycleMutationRevision
        else {
            throw DevContainerError(.conflict, message: "Native container creation changed during recovery")
        }
    }

    private func reconcilePendingContainerCreations(context: RuntimeRequestContext) async throws {
        let store = try requireCreationStore()
        for creation in try await store.pendingContainerCreations() {
            do {
                try context.checkActive()
                _ = try await completeContainerCreation(
                    creation, spec: creation.spec, imageID: creation.imageID, context: context
                )
            } catch is CancellationError {
                throw CancellationError()
            } catch let error as DevContainerError where error.code == .cancelled {
                throw error
            } catch {
                let operation = creation.operationID.uuidString
                let createdAt = creation.nativeCreatedAt.timeIntervalSince1970
                throw DevContainerError(
                    .conflict,
                    message: "creation operation \(operation) for \(creation.runtimeID) at \(createdAt) "
                        + "remains pending: \(error.localizedDescription); exact native identity is required, "
                        + "and absence or replacement does not authorize deletion or clearing the record"
                )
            }
        }
    }
}
