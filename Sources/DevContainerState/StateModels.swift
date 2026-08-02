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
import Foundation

public enum DesiredProjectState: String, Codable, Sendable {
    case running
    case stopped
    case absent
}

public enum ReconciliationState: String, Codable, Sendable {
    case clean
    case applying
    case conflict
    case failed
}

public struct ProjectRecord: Codable, Equatable, Sendable {
    public var key: ProjectKey
    public var provider: BackendProvider
    public var composeProject: String?
    public var projectDirectory: String?
    public var configurationHash: String?
    public var desiredGeneration: Int64
    public var desiredState: DesiredProjectState
    public var reconciliationState: ReconciliationState
    public var createdAt: Date
    public var updatedAt: Date

    public init(
        key: ProjectKey,
        provider: BackendProvider,
        composeProject: String? = nil,
        projectDirectory: String? = nil,
        configurationHash: String? = nil,
        desiredGeneration: Int64 = 0,
        desiredState: DesiredProjectState = .running,
        reconciliationState: ReconciliationState = .clean,
        createdAt: Date,
        updatedAt: Date
    ) {
        self.key = key
        self.provider = provider
        self.composeProject = composeProject
        self.projectDirectory = projectDirectory
        self.configurationHash = configurationHash
        self.desiredGeneration = desiredGeneration
        self.desiredState = desiredState
        self.reconciliationState = reconciliationState
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }
}

public struct ResourceRecord: Codable, Equatable, Sendable {
    public var runtimeKind: String
    public var runtimeID: RuntimeID
    public var dockerID: DockerID
    public var project: ProjectKey
    public var logicalName: String
    public var role: String
    public var provider: BackendProvider
    public var specificationHash: String
    public var generation: Int64
    public var observedState: String
    public var labelsHash: String
    public var createdAt: Date
    public var updatedAt: Date

    public init(
        runtimeKind: String,
        runtimeID: RuntimeID,
        dockerID: DockerID,
        project: ProjectKey,
        logicalName: String,
        role: String,
        provider: BackendProvider,
        specificationHash: String,
        generation: Int64,
        observedState: String,
        labelsHash: String,
        createdAt: Date,
        updatedAt: Date
    ) {
        self.runtimeKind = runtimeKind
        self.runtimeID = runtimeID
        self.dockerID = dockerID
        self.project = project
        self.logicalName = logicalName
        self.role = role
        self.provider = provider
        self.specificationHash = specificationHash
        self.generation = generation
        self.observedState = observedState
        self.labelsHash = labelsHash
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }
}

public enum OperationPhase: String, Codable, Sendable {
    case intent
    case applied
    case committed
    case failed
}

public struct StateRetentionPolicy: Equatable, Sendable {
    public var maximumEventCount: Int
    public var maximumCompletedOperationCount: Int
    public var maximumAge: TimeInterval

    public init(
        maximumEventCount: Int = 100_000,
        maximumCompletedOperationCount: Int = 10000,
        maximumAge: TimeInterval = 30 * 24 * 60 * 60
    ) {
        precondition(maximumEventCount >= 0)
        precondition(maximumCompletedOperationCount >= 0)
        precondition(maximumAge >= 0)
        self.maximumEventCount = maximumEventCount
        self.maximumCompletedOperationCount = maximumCompletedOperationCount
        self.maximumAge = maximumAge
    }
}

public struct StateRetentionResult: Equatable, Sendable {
    public var deletedEvents: Int
    public var deletedOperations: Int
    public var retainedEvents: Int
    public var retainedOperations: Int

    public init(
        deletedEvents: Int,
        deletedOperations: Int,
        retainedEvents: Int,
        retainedOperations: Int
    ) {
        self.deletedEvents = deletedEvents
        self.deletedOperations = deletedOperations
        self.retainedEvents = retainedEvents
        self.retainedOperations = retainedOperations
    }
}

public struct OperationRecord: Codable, Equatable, Sendable {
    public var id: OperationID
    public var project: ProjectKey
    public var resourceKey: String?
    public var requestKind: String
    public var requestHash: String
    public var phase: OperationPhase
    public var retryClass: String
    public var errorCode: String?
    public var createdAt: Date
    public var updatedAt: Date

    public init(
        id: OperationID,
        project: ProjectKey,
        resourceKey: String? = nil,
        requestKind: String,
        requestHash: String,
        phase: OperationPhase = .intent,
        retryClass: String = "idempotent",
        errorCode: String? = nil,
        createdAt: Date,
        updatedAt: Date
    ) {
        self.id = id
        self.project = project
        self.resourceKey = resourceKey
        self.requestKind = requestKind
        self.requestHash = requestHash
        self.phase = phase
        self.retryClass = retryClass
        self.errorCode = errorCode
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }
}

public protocol ProjectStateStore: Sendable {
    func claimProject(
        key: ProjectKey,
        provider: BackendProvider,
        composeProject: String?,
        projectDirectory: String?,
        configurationHash: String?
    ) async throws -> ProjectRecord
    func project(key: ProjectKey) async throws -> ProjectRecord?
    func listProjects() async throws -> [ProjectRecord]
    func setProjectState(
        key: ProjectKey,
        desiredState: DesiredProjectState,
        reconciliationState: ReconciliationState,
        generation: Int64
    ) async throws
    func releaseProject(key: ProjectKey) async throws
    func recordResource(_ resource: ResourceRecord) async throws
    func removeResource(runtimeID: RuntimeID) async throws
    func removeResources(project: ProjectKey) async throws
    func resources(project: ProjectKey) async throws -> [ResourceRecord]
    func beginOperation(_ operation: OperationRecord) async throws
    func updateOperation(id: OperationID, phase: OperationPhase, errorCode: String?) async throws
    func unfinishedOperations() async throws -> [OperationRecord]
    func appendEvent(_ event: RuntimeEvent) async throws
    func events(after sequence: Int64, limit: Int) async throws -> [RuntimeEvent]
}
