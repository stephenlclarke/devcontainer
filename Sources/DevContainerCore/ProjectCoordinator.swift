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
import DevContainerState
import Foundation

public actor ProjectCoordinator {
    private let store: any ProjectStateStore

    public init(store: any ProjectStateStore) {
        self.store = store
    }

    @discardableResult
    public func claim(
        project: ProjectKey,
        provider: BackendProvider,
        composeProject: String? = nil,
        projectDirectory: String? = nil,
        configurationHash: String? = nil
    ) async throws -> ProjectRecord {
        try await store.claimProject(
            key: project,
            provider: provider,
            composeProject: composeProject,
            projectDirectory: projectDirectory,
            configurationHash: configurationHash
        )
    }

    public func provider(for project: ProjectKey) async throws -> BackendProvider? {
        try await store.project(key: project)?.provider
    }

    public func drainAndReset(project: ProjectKey) async throws {
        try await store.releaseProject(key: project)
    }

    public func withMutation<Result: Sendable>(
        project: ProjectKey,
        provider: BackendProvider,
        composeProject: String? = nil,
        projectDirectory: String? = nil,
        configurationHash: String,
        requestKind: String,
        requestHash: String,
        resourceKey: String? = nil,
        operation body: @Sendable (RuntimeRequestContext) async throws -> Result
    ) async throws -> Result {
        let claim = try await claim(
            project: project,
            provider: provider,
            composeProject: composeProject,
            projectDirectory: projectDirectory,
            configurationHash: configurationHash
        )
        let operationID = OperationID.random()
        let generation = claim.desiredGeneration + 1
        let now = Date()
        let operation = OperationRecord(
            id: operationID,
            project: project,
            resourceKey: resourceKey,
            requestKind: requestKind,
            requestHash: requestHash,
            createdAt: now,
            updatedAt: now
        )
        try await store.beginOperation(operation)
        try await store.setProjectState(
            key: project,
            desiredState: .running,
            reconciliationState: .applying,
            generation: generation
        )

        let context = RuntimeRequestContext(
            operationID: operationID,
            project: project,
            generation: generation
        )
        do {
            let result = try await body(context)
            try await store.updateOperation(id: operationID, phase: .applied, errorCode: nil)
            try await store.setProjectState(
                key: project,
                desiredState: .running,
                reconciliationState: .clean,
                generation: generation
            )
            try await store.updateOperation(id: operationID, phase: .committed, errorCode: nil)
            return result
        } catch {
            let errorCode = (error as? DevContainerError)?.code.rawValue
            try? await store.updateOperation(id: operationID, phase: .failed, errorCode: errorCode)
            try? await store.setProjectState(
                key: project,
                desiredState: .running,
                reconciliationState: .failed,
                generation: generation
            )
            throw error
        }
    }

    public func recoveryOperations() async throws -> [OperationRecord] {
        try await store.unfinishedOperations()
    }
}
