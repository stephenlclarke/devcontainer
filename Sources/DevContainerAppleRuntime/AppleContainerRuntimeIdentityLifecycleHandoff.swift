//===----------------------------------------------------------------------===//
// Copyright 2026 devcontainer project authors.
// Licensed under the Apache License, Version 2.0.
//===----------------------------------------------------------------------===//

import ContainerEngineRuntimeSPI
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

public extension AppleContainerRuntime {
    /// Exports the quiesced stock-provider identity/lifecycle ownership record.
    /// Event history is intentionally empty because the legacy poller is not a
    /// durable journal; fabricating that history would create a second truth.
    func portableIdentityLifecycleHandoffContainers(
        resourceIDs: [String],
        providerFingerprint: String,
        context: RuntimeRequestContext
    ) async throws -> [ProviderHandoffIdentityLifecycleContainerV1] {
        let lifecycleMutationRevision = containerLifecycleMutationRevision
        let inventory = try await listContainers(
            all: true,
            labels: [:],
            context: context
        )
        try Self.requireIdentityLifecycleHandoffQuiescence(
            resourceIDs: resourceIDs,
            inventory: inventory,
            startingRuntimeIDs: Set(containerStartOperations.keys),
            runningExecRuntimeIDs: Set(
                execs.values.lazy.filter(\.running).map(\.containerID.rawValue)
            ),
            mutatingContainerIdentifiers: Set(
                containerLifecycleMutationRegistrations.keys
            ),
            mutationRevisionUnchanged:
            lifecycleMutationRevision == containerLifecycleMutationRevision
        )
        return try Self.collectPortableIdentityLifecycleHandoffContainers(
            resourceIDs: resourceIDs,
            providerFingerprint: providerFingerprint,
            inventory: inventory
        )
    }

    static func requireIdentityLifecycleHandoffQuiescence(
        resourceIDs: [String],
        inventory: [DevContainerModel.ContainerSnapshot],
        startingRuntimeIDs: Set<String>,
        runningExecRuntimeIDs: Set<String>,
        mutatingContainerIdentifiers: Set<String> = [],
        mutationRevisionUnchanged: Bool = true
    ) throws {
        let selected = Set(resourceIDs)
        let matches = inventory.filter { snapshot in
            selected.isEmpty
                || selected.contains(snapshot.runtimeID.rawValue)
                || selected.contains(snapshot.dockerID.rawValue)
                || selected.contains(snapshot.spec.name)
        }
        let selectedMutationInFlight = selected.isEmpty
            ? !mutatingContainerIdentifiers.isEmpty
            : matches.contains(where: { snapshot in
                mutatingContainerIdentifiers.contains(snapshot.runtimeID.rawValue)
                    || mutatingContainerIdentifiers.contains(snapshot.dockerID.rawValue)
                    || mutatingContainerIdentifiers.contains(snapshot.spec.name)
            })
        guard mutationRevisionUnchanged,
              !selectedMutationInFlight,
              !matches.contains(where: {
                  startingRuntimeIDs.contains($0.runtimeID.rawValue)
                      || runningExecRuntimeIDs.contains($0.runtimeID.rawValue)
              })
        else {
            throw DevContainerError(
                .conflict,
                message:
                "identity/lifecycle handoff requires quiesced container mutations"
            )
        }
    }

    static func collectPortableIdentityLifecycleHandoffContainers(
        resourceIDs: [String],
        providerFingerprint: String,
        inventory: [DevContainerModel.ContainerSnapshot]
    ) throws -> [ProviderHandoffIdentityLifecycleContainerV1] {
        let selected = Set(resourceIDs)
        let matches = inventory.filter { snapshot in
            selected.isEmpty
                || selected.contains(snapshot.runtimeID.rawValue)
                || selected.contains(snapshot.dockerID.rawValue)
                || selected.contains(snapshot.spec.name)
        }
        guard selected.isEmpty || matches.count == selected.count else {
            throw DevContainerError(
                .notFound,
                message: "identity/lifecycle handoff contains an unknown resource"
            )
        }
        return try matches.map {
            try portableHandoffContainer(
                snapshot: $0,
                providerFingerprint: providerFingerprint
            )
        }.sorted {
            $0.lifecycle.containerID.utf8.lexicographicallyPrecedes(
                $1.lifecycle.containerID.utf8
            )
        }
    }

    private static func portableHandoffContainer(
        snapshot: DevContainerModel.ContainerSnapshot,
        providerFingerprint: String
    ) throws -> ProviderHandoffIdentityLifecycleContainerV1 {
        let state = try portableLifecycleState(snapshot)
        return ProviderHandoffIdentityLifecycleContainerV1(
            lifecycle: ContainerLifecycleRecordV2(
                containerID: snapshot.dockerID.rawValue,
                canonicalName: snapshot.spec.name,
                immutableBundleKey: snapshot.runtimeID.rawValue,
                selectedProviderFingerprint: providerFingerprint,
                intent: ContainerLifecycleIntentV2(
                    autoRemove: snapshot.spec.autoRemove
                ),
                snapshot: ContainerLifecycleSnapshotV2(
                    state: state,
                    removalInProgress: state == .removing,
                    exitCode: snapshot.exitCode ?? 0,
                    startedAt: snapshot.startedAt,
                    finishedAt: snapshot.finishedAt,
                    processGeneration: snapshot.startedAt == nil ? nil : 1,
                    transitionRevision: 1,
                    operationGeneration: 0
                )
            )
        )
    }

    private static func portableLifecycleState(
        _ snapshot: DevContainerModel.ContainerSnapshot
    ) throws -> ContainerPublicStateV2 {
        switch snapshot.state {
        case .created:
            return .created
        case .stopped:
            return snapshot.startedAt == nil ? .created : .exited
        case .removing:
            return .removing
        case .running:
            throw DevContainerError(
                .conflict,
                message:
                "container \(snapshot.dockerID.rawValue) is not quiesced for identity/lifecycle handoff"
            )
        case .unknown:
            throw DevContainerError(
                .stateCorruption,
                message:
                "container \(snapshot.dockerID.rawValue) has unknown lifecycle state"
            )
        }
    }
}
