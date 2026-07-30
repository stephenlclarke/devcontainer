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

import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

extension AppleContainerRuntime {
    func containerMetadataByRuntimeID() async throws
        -> [String: RuntimeContainerMetadata]
    {
        guard let metadataStore else {
            return [:]
        }
        var metadata: [String: RuntimeContainerMetadata] = [:]
        for value in try await metadataStore.listContainerMetadata() {
            metadata[value.runtimeID.rawValue] = value
        }
        return metadata
    }

    func containerSnapshotWithMetadata(
        _ snapshot: ContainerSnapshot,
        metadata: RuntimeContainerMetadata?
    ) async throws -> ContainerSnapshot {
        var snapshot = snapshot
        if snapshot.state == .stopped,
           let exit = containerExits[snapshot.runtimeID.rawValue]
        {
            snapshot.exitCode = exit.code
            snapshot.finishedAt = exit.finishedAt
        }
        guard let metadataStore else {
            return snapshot
        }
        if let metadata {
            if Self.sameContainerIncarnation(
                metadataCreatedAt: metadata.createdAt,
                observedCreatedAt: snapshot.createdAt
            ) {
                return apply(metadata: metadata, to: snapshot)
            }
            // Native Compose may recreate a stable name. Never project the
            // previous Docker identity onto that new native container.
            try await metadataStore.removeContainerMetadata(
                id: snapshot.runtimeID.rawValue
            )
        }
        if snapshot.spec.labels[Self.dockerIDLabel] != nil {
            return snapshot
        }
        let metadata = RuntimeContainerMetadata(
            runtimeID: snapshot.runtimeID,
            dockerID: DockerID(rawValue: Self.syntheticDockerIdentifier()),
            spec: snapshot.spec,
            createdAt: snapshot.createdAt,
            startedAt: snapshot.startedAt
        )
        try await metadataStore.recordContainerMetadata(metadata)
        return apply(metadata: metadata, to: snapshot)
    }

    func removeOrphanedContainerMetadata(
        observedRuntimeIDs: Set<String>,
        metadata: [String: RuntimeContainerMetadata]
    ) async throws {
        guard let metadataStore else {
            return
        }
        for metadata in metadata.values
            where !observedRuntimeIDs.contains(metadata.runtimeID.rawValue)
        {
            try await metadataStore.removeContainerMetadata(
                id: metadata.runtimeID.rawValue
            )
            discardContainerState(
                id: metadata.runtimeID.rawValue,
                dockerID: metadata.dockerID.rawValue,
                name: metadata.spec.name
            )
        }
    }

    private static func syntheticDockerIdentifier() -> String {
        let first = UUID().uuidString.replacingOccurrences(
            of: "-",
            with: ""
        ).lowercased()
        let second = UUID().uuidString.replacingOccurrences(
            of: "-",
            with: ""
        ).lowercased()
        return first + second
    }
}
