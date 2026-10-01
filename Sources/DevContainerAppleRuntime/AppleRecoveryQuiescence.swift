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
        guard containerLifecycleMutationRegistrations.isEmpty, containerIOClosures.isEmpty,
              try await !requireCreationStore().hasPendingContainerCreations(),
              containerLifecycleMutationRegistrations.isEmpty, containerIOClosures.isEmpty,
              revision == containerLifecycleMutationRevision
        else {
            throw DevContainerError(.conflict, message: "Native container creation is not quiescent")
        }
    }
}
