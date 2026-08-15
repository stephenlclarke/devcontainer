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

public protocol RuntimeProcessSession: Sendable {
    var frames: AsyncThrowingStream<RuntimeIOFrame, any Error> { get }

    func write(_ data: Data) async throws
    func closeStandardInput() async throws
    func resize(width: UInt16, height: UInt16) async throws
    func wait() async throws -> Int32
    func cancel() async
}

public protocol RuntimeIdentityProvider: Sendable {
    func descriptor(context: RuntimeRequestContext) async throws -> ProtocolDescriptor
}

public struct RuntimeContainerMetadata: Codable, Equatable, Sendable {
    public var runtimeID: RuntimeID
    public var dockerID: DockerID
    public var imageID: String?
    public var spec: ContainerSpec
    public var createdAt: Date
    public var startedAt: Date?

    public init(
        runtimeID: RuntimeID,
        dockerID: DockerID,
        imageID: String? = nil,
        spec: ContainerSpec,
        createdAt: Date,
        startedAt: Date? = nil
    ) {
        self.runtimeID = runtimeID
        self.dockerID = dockerID
        self.imageID = imageID
        self.spec = spec
        self.createdAt = createdAt
        self.startedAt = startedAt
    }
}

/// Persists Docker-only lifecycle details which the Apple runtime does not
/// expose after the compatibility service restarts.
public protocol RuntimeMetadataStore: Sendable {
    func recordContainerMetadata(_ metadata: RuntimeContainerMetadata) async throws
    func containerMetadata(id: String) async throws -> RuntimeContainerMetadata?
    func listContainerMetadata() async throws -> [RuntimeContainerMetadata]
    func markContainerStarted(id: String, at date: Date) async throws
    func removeContainerMetadata(id: String) async throws
}

public protocol ImageRuntime: Sendable {
    func listImages(context: RuntimeRequestContext) async throws -> [ImageSnapshot]
    func inspectImage(reference: String, context: RuntimeRequestContext) async throws -> ImageSnapshot
    func pullImage(reference: String, context: RuntimeRequestContext) async throws
        -> AsyncThrowingStream<Data, any Error>
    func loadImage(
        archive: Data,
        context: RuntimeRequestContext
    ) async throws -> AsyncThrowingStream<Data, any Error>
    func buildImage(
        request: ImageBuildRequest,
        context: RuntimeRequestContext
    ) async throws -> AsyncThrowingStream<Data, any Error>
    func tagImage(source: String, target: String, context: RuntimeRequestContext) async throws
    func removeImage(reference: String, force: Bool, context: RuntimeRequestContext) async throws
}

public protocol ContainerRuntime: Sendable {
    func listContainers(
        all: Bool,
        labels: [String: String],
        context: RuntimeRequestContext
    ) async throws -> [ContainerSnapshot]
    func inspectContainer(id: String, context: RuntimeRequestContext) async throws
        -> ContainerSnapshot
    func createContainer(spec: ContainerSpec, context: RuntimeRequestContext) async throws
        -> ContainerSnapshot
    func startContainer(id: String, context: RuntimeRequestContext) async throws
    func stopContainer(id: String, timeout: Duration?, context: RuntimeRequestContext) async throws
    func restartContainer(id: String, timeout: Duration?, context: RuntimeRequestContext) async throws
    func killContainer(id: String, signal: String, context: RuntimeRequestContext) async throws
    func renameContainer(id: String, name: String, context: RuntimeRequestContext) async throws
    func removeContainer(id: String, force: Bool, context: RuntimeRequestContext) async throws
    func waitContainer(id: String, context: RuntimeRequestContext) async throws -> Int32
    func containerLogs(
        id: String,
        follow: Bool,
        standardOutput: Bool,
        standardError: Bool,
        context: RuntimeRequestContext
    ) async throws -> AsyncThrowingStream<RuntimeIOFrame, any Error>
    func attachContainer(
        id: String,
        terminal: Bool,
        context: RuntimeRequestContext
    ) async throws -> any RuntimeProcessSession
}

public extension ContainerRuntime {
    /// Compatibility fallback for providers that have not yet adopted an
    /// authority-owned restart transaction.
    func restartContainer(
        id: String,
        timeout: Duration?,
        context: RuntimeRequestContext
    ) async throws {
        try await stopContainer(id: id, timeout: timeout, context: context)
        try await startContainer(id: id, context: context)
    }
}

public protocol ProcessRuntime: Sendable {
    func createExec(
        containerID: String,
        spec: ExecSpec,
        context: RuntimeRequestContext
    ) async throws -> ExecSnapshot
    func startExec(
        id: ExecID,
        context: RuntimeRequestContext
    ) async throws -> any RuntimeProcessSession
    func inspectExec(id: ExecID, context: RuntimeRequestContext) async throws -> ExecSnapshot
}

public protocol ArchiveRuntime: Sendable {
    func copyArchiveFromContainer(
        id: String,
        path: String,
        context: RuntimeRequestContext
    ) async throws -> RuntimeArchive
    func copyArchiveToContainer(
        id: String,
        path: String,
        archive: Data,
        context: RuntimeRequestContext
    ) async throws
}

public protocol NetworkRuntime: Sendable {
    func listNetworks(context: RuntimeRequestContext) async throws -> [NetworkSnapshot]
    func inspectNetwork(id: String, context: RuntimeRequestContext) async throws -> NetworkSnapshot
    func createNetwork(spec: NetworkSpec, context: RuntimeRequestContext) async throws
        -> NetworkSnapshot
    func connectNetwork(
        id: String,
        containerID: String,
        aliases: [String],
        context: RuntimeRequestContext
    ) async throws
    func disconnectNetwork(
        id: String,
        containerID: String,
        force: Bool,
        context: RuntimeRequestContext
    ) async throws
    func removeNetwork(id: String, context: RuntimeRequestContext) async throws
}

public protocol VolumeRuntime: Sendable {
    func listVolumes(context: RuntimeRequestContext) async throws -> [VolumeSnapshot]
    func inspectVolume(name: String, context: RuntimeRequestContext) async throws -> VolumeSnapshot
    func createVolume(spec: VolumeSpec, context: RuntimeRequestContext) async throws -> VolumeSnapshot
    func removeVolume(name: String, force: Bool, context: RuntimeRequestContext) async throws
}

public protocol EventRuntime: Sendable {
    func events(
        since: Date?,
        until: Date?,
        labels: [String: String],
        context: RuntimeRequestContext
    ) async throws -> AsyncThrowingStream<RuntimeEvent, any Error>
}

public protocol DevContainerRuntime:
    ArchiveRuntime,
    ContainerRuntime,
    EventRuntime,
    ImageRuntime,
    NetworkRuntime,
    ProcessRuntime,
    RuntimeIdentityProvider,
    VolumeRuntime
{}
