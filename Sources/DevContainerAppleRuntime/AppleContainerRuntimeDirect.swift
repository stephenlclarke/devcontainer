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

protocol AppleContainerResourceClient: Sendable {
    func list(filters: ContainerListFilters) async throws
        -> [ContainerResource.ContainerSnapshot]
    func get(id: String) async throws -> ContainerResource.ContainerSnapshot
    func copyIn(
        id: String,
        source: String,
        destination: String,
        mode: UInt32,
        createParents: Bool
    ) async throws
    func copyOut(
        id: String,
        source: String,
        destination: String,
        createParents: Bool
    ) async throws
}

extension ContainerClient: AppleContainerResourceClient {}

protocol AppleNetworkResourceClient: Sendable {
    func create(configuration: NetworkConfiguration) async throws
        -> NetworkResource
    func list() async throws -> [NetworkResource]
    func get(id: String) async throws -> NetworkResource
    func delete(id: String) async throws
}

extension NetworkClient: AppleNetworkResourceClient {}

extension AppleContainerRuntime {
    func copyContainerResourceIn(
        id: String,
        source: String,
        destination: String,
        operation: String
    ) async throws {
        if useDirectContainerAPI {
            do {
                try await resourceClient.copyIn(
                    id: id,
                    source: source,
                    destination: destination,
                    mode: 0o644,
                    createParents: true
                )
            } catch {
                throw directAPIError(error, operation: operation)
            }
            return
        }
        try await requireSuccess(
            command(["cp", source, "\(id):\(destination)"]),
            operation: operation
        )
    }

    func copyContainerResourceOut(
        id: String,
        source: String,
        destination: String,
        operation: String
    ) async throws {
        if useDirectContainerAPI {
            do {
                try await resourceClient.copyOut(
                    id: id,
                    source: source,
                    destination: destination,
                    createParents: true
                )
            } catch {
                throw directAPIError(error, operation: operation)
            }
            return
        }
        try await requireSuccess(
            command(["cp", "\(id):\(source)", destination]),
            operation: operation
        )
    }

    func listContainersDirect(
        all: Bool,
        labels: [String: String]
    ) async throws -> [DevContainerModel.ContainerSnapshot] {
        let values: [ContainerResource.ContainerSnapshot]
        do {
            values = try await resourceClient.list(filters: .all.withoutMachines())
        } catch {
            throw directAPIError(error, operation: "container list")
        }

        var snapshots: [DevContainerModel.ContainerSnapshot] = []
        var observedRuntimeIDs = Set<String>()
        for value in values {
            let snapshot = try await containerSnapshotWithMetadata(
                containerRecord(value)
            )
            observedRuntimeIDs.insert(snapshot.runtimeID.rawValue)
            guard all || snapshot.state == .running else {
                continue
            }
            guard
                labels.allSatisfy({ key, expected in
                    guard let actual = snapshot.spec.labels[key] else {
                        return false
                    }
                    return expected.isEmpty || actual == expected
                })
            else {
                continue
            }
            snapshots.append(snapshot)
        }
        if all {
            try await removeOrphanedContainerMetadata(
                observedRuntimeIDs: observedRuntimeIDs
            )
        }
        return snapshots
    }

    func inspectContainerDirect(
        id: String
    ) async throws -> DevContainerModel.ContainerSnapshot? {
        let runtimeID =
            try await metadataStore?.containerMetadata(id: id)?
                .runtimeID.rawValue ?? id
        let value: ContainerResource.ContainerSnapshot
        do {
            value = try await resourceClient.get(id: runtimeID)
        } catch let error as ContainerizationError
            where error.code == .notFound
        {
            return nil
        } catch {
            throw directAPIError(error, operation: "container inspect")
        }
        let snapshot = try await containerSnapshotWithMetadata(
            containerRecord(value)
        )
        guard
            snapshot.runtimeID.rawValue == id
            || snapshot.dockerID.rawValue == id
            || snapshot.spec.name == id
        else {
            return nil
        }
        return snapshot
    }
}
