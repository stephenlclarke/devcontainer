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

public struct ProjectMutation: Sendable {
    public let project: ProjectKey
    public let provider: BackendProvider
    public let composeProject: String?
    public let projectDirectory: String?
    public let configurationHash: String
    public let requestKind: String
    public let requestHash: String
    public let resourceKey: String?
    public let releaseProjectWhenEmpty: Bool

    public init(
        project: ProjectKey,
        provider: BackendProvider,
        composeProject: String? = nil,
        projectDirectory: String? = nil,
        configurationHash: String,
        requestKind: String,
        requestHash: String,
        resourceKey: String? = nil,
        releaseProjectWhenEmpty: Bool = false
    ) {
        self.project = project
        self.provider = provider
        self.composeProject = composeProject
        self.projectDirectory = projectDirectory
        self.configurationHash = configurationHash
        self.requestKind = requestKind
        self.requestHash = requestHash
        self.resourceKey = resourceKey
        self.releaseProjectWhenEmpty = releaseProjectWhenEmpty
    }

    fileprivate func operation(
        id: OperationID,
        createdAt: Date
    ) -> OperationRecord {
        OperationRecord(
            id: id,
            project: project,
            resourceKey: resourceKey,
            requestKind: requestKind,
            requestHash: requestHash,
            createdAt: createdAt,
            updatedAt: createdAt
        )
    }
}

public struct ProjectMutationSession: Sendable {
    public let context: RuntimeRequestContext
    fileprivate let token: UUID
}

private struct ActiveProjectMutation: Sendable {
    let request: ProjectMutation
    let operationID: OperationID
    let generation: Int64
    let lockKey: String
}

public actor ProjectCoordinator {
    private let store: any ProjectStateStore
    private var lockedMutationKeys = Set<String>()
    private var mutationWaiters: [String: [CheckedContinuation<Void, Never>]] = [:]
    private var activeMutations: [UUID: ActiveProjectMutation] = [:]

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

    /// Runs intent, native mutation, reconciliation, and commit as one
    /// fail-closed transaction boundary.
    public func withMutation<Result: Sendable>(
        request: ProjectMutation,
        context baseContext: RuntimeRequestContext = RuntimeRequestContext(),
        operation body: @Sendable (RuntimeRequestContext) async throws -> Result
    ) async throws -> Result {
        let session = try await beginMutation(request: request, context: baseContext)
        do {
            let result = try await body(session.context)
            try await commitMutation(session)
            return result
        } catch {
            await failMutation(session, errorCode: Self.errorCode(for: error))
            throw error
        }
    }

    public func beginMutation(
        request: ProjectMutation,
        context baseContext: RuntimeRequestContext = RuntimeRequestContext()
    ) async throws -> ProjectMutationSession {
        let lockKey = request.project.rawValue
        await acquireMutationLock(lockKey)
        do {
            try baseContext.checkActive()
            let claim = try await claim(
                project: request.project,
                provider: request.provider,
                composeProject: request.composeProject,
                projectDirectory: request.projectDirectory,
                configurationHash: request.configurationHash
            )
            let operationID = baseContext.operationID
            let generation = claim.desiredGeneration + 1
            let now = Date()
            let operation = request.operation(id: operationID, createdAt: now)
            try await store.beginOperation(operation)
            try await store.setProjectState(
                key: request.project,
                desiredState: .running,
                reconciliationState: .applying,
                generation: generation
            )

            let context = RuntimeRequestContext(
                operationID: operationID,
                correlationID: baseContext.correlationID,
                project: request.project,
                generation: generation,
                deadline: baseContext.deadline,
                providerFingerprint: request.provider.rawValue,
                configurationHash: request.configurationHash
            )
            let token = UUID()
            activeMutations[token] = ActiveProjectMutation(
                request: request,
                operationID: operationID,
                generation: generation,
                lockKey: lockKey
            )
            return ProjectMutationSession(context: context, token: token)
        } catch {
            releaseMutationLock(lockKey)
            throw error
        }
    }

    public func commitMutation(_ session: ProjectMutationSession) async throws {
        guard let active = activeMutations.removeValue(forKey: session.token) else {
            return
        }
        defer { releaseMutationLock(active.lockKey) }
        do {
            try session.context.checkActive()
            try await store.updateOperation(
                id: active.operationID,
                phase: .applied,
                errorCode: nil
            )
            try await store.setProjectState(
                key: active.request.project,
                desiredState: .running,
                reconciliationState: .clean,
                generation: active.generation
            )
            try await store.updateOperation(
                id: active.operationID,
                phase: .committed,
                errorCode: nil
            )
            if active.request.releaseProjectWhenEmpty,
               try await store.resources(project: active.request.project).isEmpty
            {
                try await store.releaseProject(key: active.request.project)
            }
        } catch {
            await recordFailure(active, errorCode: Self.errorCode(for: error))
            throw error
        }
    }

    public func failMutation(
        _ session: ProjectMutationSession,
        errorCode: String?
    ) async {
        guard let active = activeMutations.removeValue(forKey: session.token) else {
            return
        }
        defer { releaseMutationLock(active.lockKey) }
        await recordFailure(active, errorCode: errorCode)
    }

    private func recordFailure(
        _ active: ActiveProjectMutation,
        errorCode: String?
    ) async {
        try? await store.updateOperation(
            id: active.operationID,
            phase: .failed,
            errorCode: errorCode
        )
        try? await store.setProjectState(
            key: active.request.project,
            desiredState: .running,
            reconciliationState: .failed,
            generation: active.generation
        )
    }

    private static func errorCode(for error: any Error) -> String? {
        if error is CancellationError {
            return DevContainerErrorCode.cancelled.rawValue
        }
        return (error as? DevContainerError)?.code.rawValue
    }

    public func recoveryOperations() async throws -> [OperationRecord] {
        try await store.unfinishedOperations()
    }

    public func failUnfinishedOperationsForManualRecovery() async throws
        -> [OperationRecord]
    {
        let operations = try await store.unfinishedOperations()
        for operation in operations {
            try await store.updateOperation(
                id: operation.id,
                phase: .failed,
                errorCode: "manual-recovery-required"
            )
            if let project = try await store.project(key: operation.project) {
                try await store.setProjectState(
                    key: operation.project,
                    desiredState: project.desiredState,
                    reconciliationState: .failed,
                    generation: project.desiredGeneration
                )
            }
        }
        return operations
    }

    public func recordResource(_ resource: ResourceRecord) async throws {
        try await store.recordResource(resource)
    }

    public func recordContainer(
        _ snapshot: ContainerSnapshot,
        provider: BackendProvider,
        context: RuntimeRequestContext,
        specificationHash: String,
        labelsHash: String
    ) async throws {
        guard let project = context.project, let generation = context.generation else {
            throw DevContainerError(
                .stateCorruption,
                message: "cannot record a container without project generation ownership"
            )
        }
        let now = Date()
        try await store.recordResource(
            ResourceRecord(
                runtimeKind: "container",
                runtimeID: snapshot.runtimeID,
                dockerID: snapshot.dockerID,
                project: project,
                logicalName: snapshot.spec.name,
                role: "devcontainer",
                provider: provider,
                specificationHash: specificationHash,
                generation: generation,
                observedState: snapshot.state.rawValue,
                labelsHash: labelsHash,
                createdAt: snapshot.createdAt,
                updatedAt: now
            )
        )
    }

    public func recordNetwork(
        _ snapshot: NetworkSnapshot,
        provider: BackendProvider,
        context: RuntimeRequestContext,
        specificationHash: String,
        labelsHash: String
    ) async throws {
        try await recordResource(
            runtimeKind: "network",
            runtimeID: RuntimeID(rawValue: snapshot.id),
            dockerID: DockerID(rawValue: snapshot.id),
            logicalName: snapshot.spec.name,
            role: "network",
            provider: provider,
            context: context,
            specificationHash: specificationHash,
            labelsHash: labelsHash,
            observedState: "active",
            createdAt: snapshot.createdAt
        )
    }

    public func recordVolume(
        _ snapshot: VolumeSnapshot,
        provider: BackendProvider,
        context: RuntimeRequestContext,
        specificationHash: String,
        labelsHash: String
    ) async throws {
        try await recordResource(
            runtimeKind: "volume",
            runtimeID: RuntimeID(rawValue: snapshot.name),
            dockerID: DockerID(rawValue: snapshot.name),
            logicalName: snapshot.name,
            role: "volume",
            provider: provider,
            context: context,
            specificationHash: specificationHash,
            labelsHash: labelsHash,
            observedState: "active",
            createdAt: snapshot.createdAt
        )
    }

    public func removeResource(runtimeID: RuntimeID) async throws {
        try await store.removeResource(runtimeID: runtimeID)
    }

    public func reconcileRemovedResource(
        runtimeID: RuntimeID,
        project: ProjectKey,
        releaseProjectWhenEmpty: Bool
    ) async throws {
        let lockKey = project.rawValue
        await acquireMutationLock(lockKey)
        defer { releaseMutationLock(lockKey) }
        try await store.removeResource(runtimeID: runtimeID)
        if releaseProjectWhenEmpty,
           try await store.project(key: project) != nil,
           try await store.resources(project: project).isEmpty
        {
            try await store.releaseProject(key: project)
        }
    }

    // swiftlint:disable:next function_parameter_count
    private func recordResource(
        runtimeKind: String,
        runtimeID: RuntimeID,
        dockerID: DockerID,
        logicalName: String,
        role: String,
        provider: BackendProvider,
        context: RuntimeRequestContext,
        specificationHash: String,
        labelsHash: String,
        observedState: String,
        createdAt: Date
    ) async throws {
        guard let project = context.project, let generation = context.generation else {
            throw DevContainerError(
                .stateCorruption,
                message: "cannot record a resource without project generation ownership"
            )
        }
        try await store.recordResource(
            ResourceRecord(
                runtimeKind: runtimeKind,
                runtimeID: runtimeID,
                dockerID: dockerID,
                project: project,
                logicalName: logicalName,
                role: role,
                provider: provider,
                specificationHash: specificationHash,
                generation: generation,
                observedState: observedState,
                labelsHash: labelsHash,
                createdAt: createdAt,
                updatedAt: Date()
            )
        )
    }

    private func acquireMutationLock(_ key: String) async {
        if lockedMutationKeys.insert(key).inserted {
            return
        }
        await withCheckedContinuation { continuation in
            mutationWaiters[key, default: []].append(continuation)
        }
    }

    private func releaseMutationLock(_ key: String) {
        if var waiters = mutationWaiters[key], !waiters.isEmpty {
            let next = waiters.removeFirst()
            mutationWaiters[key] = waiters.isEmpty ? nil : waiters
            next.resume()
            return
        }
        lockedMutationKeys.remove(key)
    }
}
