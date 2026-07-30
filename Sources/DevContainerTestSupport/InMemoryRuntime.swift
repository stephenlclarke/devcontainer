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

public actor InMemoryRuntime: DevContainerRuntime {
    private let runtimeDescriptor: ProtocolDescriptor
    private let execSession: (any RuntimeProcessSession)?
    private let descriptorDelay: Duration?
    private var requestCancellationCount = 0
    private var containers: [RuntimeID: ContainerSnapshot] = [:]
    private var dockerToRuntime: [DockerID: RuntimeID] = [:]
    private var execs: [ExecID: ExecSnapshot] = [:]
    private var images: [String: ImageSnapshot] = [:]
    private var networks: [String: NetworkSnapshot] = [:]
    private var volumes: [String: VolumeSnapshot] = [:]
    private var archives: [String: Data] = [:]
    private var eventValues: [RuntimeEvent] = []
    private var nextEventSequence: Int64 = 1

    public init(
        provider: BackendProvider = .stock,
        version: String = "test",
        commit: String = "test",
        distribution: String = "test",
        execSession: (any RuntimeProcessSession)? = nil,
        descriptorDelay: Duration? = nil
    ) {
        self.execSession = execSession
        self.descriptorDelay = descriptorDelay
        runtimeDescriptor = ProtocolDescriptor(
            provider: provider,
            providerVersion: version,
            providerCommit: commit,
            distribution: distribution,
            capabilities: Dictionary(
                uniqueKeysWithValues: RuntimeCapability.allCases.map { ($0, .native) }
            )
        )
    }

    public func descriptor(context _: RuntimeRequestContext) async throws -> ProtocolDescriptor {
        if let descriptorDelay {
            do {
                try await Task.sleep(for: descriptorDelay)
            } catch {
                requestCancellationCount += 1
                throw error
            }
        }
        return runtimeDescriptor
    }

    public var observedRequestCancellationCount: Int {
        requestCancellationCount
    }

    public func listImages(context _: RuntimeRequestContext) -> [ImageSnapshot] {
        images.values.sorted { $0.id < $1.id }
    }

    public func inspectImage(
        reference: String,
        context _: RuntimeRequestContext
    ) throws -> ImageSnapshot {
        guard let image = image(reference: reference) else {
            throw DevContainerError(.notFound, message: "image \(reference) was not found")
        }
        return image
    }

    public func pullImage(
        reference: String,
        context _: RuntimeRequestContext
    ) -> AsyncThrowingStream<Data, any Error> {
        let snapshot = ImageSnapshot(
            id: "sha256:\(Self.identifier())",
            references: [reference],
            createdAt: Date(),
            size: 1
        )
        images[snapshot.id] = snapshot
        return Self.dataStream([
            Self.jsonLine(["status": "Pulling", "id": reference]),
            Self.jsonLine(["status": "Download complete", "id": reference])
        ])
    }

    public func loadImage(
        archive: Data,
        context _: RuntimeRequestContext
    ) -> AsyncThrowingStream<Data, any Error> {
        let id = "sha256:\(Self.identifier())"
        images[id] = ImageSnapshot(
            id: id,
            references: [],
            createdAt: Date(),
            size: UInt64(archive.count)
        )
        return Self.dataStream([
            Self.jsonLine(["stream": "Loaded image: \(id)\n"])
        ])
    }

    public func buildImage(
        request: ImageBuildRequest,
        context _: RuntimeRequestContext
    ) -> AsyncThrowingStream<Data, any Error> {
        let id = "sha256:\(Self.identifier())"
        let snapshot = ImageSnapshot(
            id: id,
            references: request.tags,
            createdAt: Date(),
            size: UInt64(request.context.count)
        )
        images[id] = snapshot
        return Self.dataStream([
            Self.jsonLine(["stream": "Step 1/1 : FROM scratch\n"]),
            Self.jsonLine(["aux": "{\"ID\":\"\(id)\"}"])
        ])
    }

    public func tagImage(
        source: String,
        target: String,
        context _: RuntimeRequestContext
    ) throws {
        guard let match = image(reference: source) else {
            throw DevContainerError(.notFound, message: "image \(source) was not found")
        }
        var updated = match
        if !updated.references.contains(target) {
            updated.references.append(target)
        }
        images[updated.id] = updated
    }

    public func removeImage(
        reference: String,
        force _: Bool,
        context _: RuntimeRequestContext
    ) throws {
        guard let match = image(reference: reference) else {
            throw DevContainerError(.notFound, message: "image \(reference) was not found")
        }
        images.removeValue(forKey: match.id)
    }

    public func listContainers(
        all: Bool,
        labels: [String: String],
        context _: RuntimeRequestContext
    ) -> [ContainerSnapshot] {
        containers.values.filter { snapshot in
            (all || snapshot.state == .running)
                && labels.allSatisfy { key, value in
                    guard let actual = snapshot.spec.labels[key] else {
                        return false
                    }
                    return value.isEmpty || actual == value
                }
        }.sorted { $0.spec.name < $1.spec.name }
    }

    public func inspectContainer(
        id: String,
        context _: RuntimeRequestContext
    ) throws -> ContainerSnapshot {
        try container(id: id)
    }

    public func createContainer(
        spec: ContainerSpec,
        context _: RuntimeRequestContext
    ) throws -> ContainerSnapshot {
        guard !containers.values.contains(where: { $0.spec.name == spec.name }) else {
            throw DevContainerError(.conflict, message: "container name \(spec.name) is already in use")
        }
        guard let image = image(reference: spec.image) else {
            throw DevContainerError(.notFound, message: "image \(spec.image) was not found")
        }
        let runtimeID = RuntimeID(rawValue: Self.identifier())
        let dockerID = DockerID(rawValue: Self.identifier())
        let snapshot = ContainerSnapshot(
            runtimeID: runtimeID,
            dockerID: dockerID,
            imageID: image.id,
            spec: spec,
            state: .created,
            createdAt: Date()
        )
        containers[runtimeID] = snapshot
        dockerToRuntime[dockerID] = runtimeID
        appendEvent(resourceID: dockerID.rawValue, action: .create, attributes: spec.labels)
        return snapshot
    }

    public func startContainer(
        id: String,
        context _: RuntimeRequestContext
    ) throws {
        var snapshot = try container(id: id)
        snapshot.state = .running
        snapshot.startedAt = Date()
        snapshot.finishedAt = nil
        snapshot.exitCode = nil
        containers[snapshot.runtimeID] = snapshot
        appendEvent(
            resourceID: snapshot.dockerID.rawValue, action: .start, attributes: snapshot.spec.labels
        )
    }

    public func stopContainer(
        id: String,
        timeout _: Duration?,
        context _: RuntimeRequestContext
    ) throws {
        var snapshot = try container(id: id)
        snapshot.state = .stopped
        snapshot.finishedAt = Date()
        snapshot.exitCode = 0
        containers[snapshot.runtimeID] = snapshot
        appendEvent(
            resourceID: snapshot.dockerID.rawValue, action: .stop, attributes: snapshot.spec.labels
        )
    }

    public func killContainer(
        id: String,
        signal _: String,
        context: RuntimeRequestContext
    ) throws {
        try stopContainer(id: id, timeout: nil, context: context)
    }

    public func renameContainer(
        id: String,
        name: String,
        context _: RuntimeRequestContext
    ) throws {
        guard !name.isEmpty else {
            throw DevContainerError(.invalidRequest, message: "container name is empty")
        }
        var snapshot = try container(id: id)
        guard
            !containers.values.contains(where: {
                $0.runtimeID != snapshot.runtimeID && $0.spec.name == name
            })
        else {
            throw DevContainerError(.conflict, message: "container name \(name) is already in use")
        }
        snapshot.spec.name = name
        containers[snapshot.runtimeID] = snapshot
    }

    public func removeContainer(
        id: String,
        force: Bool,
        context _: RuntimeRequestContext
    ) throws {
        let snapshot = try container(id: id)
        guard snapshot.state != .running || force else {
            throw DevContainerError(.conflict, message: "container \(id) is running")
        }
        containers.removeValue(forKey: snapshot.runtimeID)
        dockerToRuntime.removeValue(forKey: snapshot.dockerID)
        appendEvent(
            resourceID: snapshot.dockerID.rawValue, action: .destroy, attributes: snapshot.spec.labels
        )
    }

    public func waitContainer(
        id: String,
        context: RuntimeRequestContext
    ) throws -> Int32 {
        let snapshot = try container(id: id)
        guard snapshot.state != .running else {
            throw DevContainerError(.conflict, message: "test container \(id) is still running")
        }
        if snapshot.spec.autoRemove {
            try removeContainer(id: id, force: true, context: context)
        }
        return snapshot.exitCode ?? 0
    }

    public func containerLogs(
        id: String,
        follow _: Bool,
        standardOutput: Bool,
        standardError: Bool,
        context _: RuntimeRequestContext
    ) throws -> AsyncThrowingStream<RuntimeIOFrame, any Error> {
        _ = try container(id: id)
        var frames: [RuntimeIOFrame] = []
        if standardOutput {
            frames.append(RuntimeIOFrame(channel: .standardOutput, data: Data("stdout\n".utf8)))
        }
        if standardError {
            frames.append(RuntimeIOFrame(channel: .standardError, data: Data("stderr\n".utf8)))
        }
        return Self.frameStream(frames)
    }

    public func attachContainer(
        id: String,
        terminal: Bool,
        context _: RuntimeRequestContext
    ) throws -> any RuntimeProcessSession {
        _ = try container(id: id)
        return InMemoryProcessSession(
            frames: [
                RuntimeIOFrame(
                    channel: .standardOutput,
                    data: Data((terminal ? "terminal\n" : "attached\n").utf8)
                )
            ],
            exitCode: 0
        )
    }

    public func createExec(
        containerID: String,
        spec: ExecSpec,
        context _: RuntimeRequestContext
    ) throws -> ExecSnapshot {
        let container = try container(id: containerID)
        guard container.state == .running else {
            throw DevContainerError(.conflict, message: "container \(containerID) is not running")
        }
        let exec = ExecSnapshot(
            id: .random(),
            containerID: container.runtimeID,
            spec: spec
        )
        execs[exec.id] = exec
        appendEvent(
            resourceID: container.dockerID.rawValue, action: .execCreate,
            attributes: container.spec.labels
        )
        return exec
    }

    public func startExec(
        id: ExecID,
        context _: RuntimeRequestContext
    ) async throws -> any RuntimeProcessSession {
        guard var exec = execs[id] else {
            throw DevContainerError(.notFound, message: "exec \(id) was not found")
        }
        guard !exec.running, exec.exitCode == nil else {
            throw DevContainerError(.conflict, message: "exec \(id) has already started")
        }
        exec.running = false
        exec.exitCode = 0
        execs[id] = exec
        let session: any RuntimeProcessSession
        if let execSession {
            session = execSession
        } else {
            let text = exec.spec.command.joined(separator: " ") + "\n"
            session = InMemoryProcessSession(
                frames: [RuntimeIOFrame(channel: .standardOutput, data: Data(text.utf8))],
                exitCode: 0
            )
        }
        if exec.spec.terminal,
           let width = exec.spec.terminalWidth,
           let height = exec.spec.terminalHeight,
           width > 0,
           height > 0
        {
            try await session.resize(width: width, height: height)
        }
        return session
    }

    public func inspectExec(
        id: ExecID,
        context _: RuntimeRequestContext
    ) throws -> ExecSnapshot {
        guard let exec = execs[id] else {
            throw DevContainerError(.notFound, message: "exec \(id) was not found")
        }
        return exec
    }

    public func copyArchiveFromContainer(
        id: String,
        path: String,
        context _: RuntimeRequestContext
    ) throws -> RuntimeArchive {
        let snapshot = try container(id: id)
        return RuntimeArchive(
            data: archives["\(snapshot.runtimeID):\(path)"] ?? Data(),
            stat: ArchivePathStat(
                name: URL(fileURLWithPath: path).lastPathComponent,
                size: 0,
                mode: (1 << 31) | 0o755,
                modificationTime: snapshot.createdAt
            )
        )
    }

    public func copyArchiveToContainer(
        id: String,
        path: String,
        archive: Data,
        context _: RuntimeRequestContext
    ) throws {
        let snapshot = try container(id: id)
        archives["\(snapshot.runtimeID):\(path)"] = archive
    }

    public func listNetworks(context _: RuntimeRequestContext) -> [NetworkSnapshot] {
        networks.values.sorted { $0.spec.name < $1.spec.name }
    }

    public func inspectNetwork(
        id: String,
        context _: RuntimeRequestContext
    ) throws -> NetworkSnapshot {
        guard let network = networks[id] ?? networks.values.first(where: { $0.spec.name == id }) else {
            throw DevContainerError(.notFound, message: "network \(id) was not found")
        }
        return network
    }

    public func createNetwork(
        spec: NetworkSpec,
        context _: RuntimeRequestContext
    ) throws -> NetworkSnapshot {
        guard !networks.values.contains(where: { $0.spec.name == spec.name }) else {
            throw DevContainerError(.conflict, message: "network \(spec.name) already exists")
        }
        let network = NetworkSnapshot(id: Self.identifier(), spec: spec, createdAt: Date())
        networks[network.id] = network
        return network
    }

    public func connectNetwork(
        id: String,
        containerID: String,
        aliases _: [String],
        context _: RuntimeRequestContext
    ) throws {
        var network = try inspectNetwork(id: id, context: RuntimeRequestContext())
        let snapshot = try container(id: containerID)
        network.containers[snapshot.runtimeID] = "192.0.2.2"
        networks[network.id] = network
    }

    public func disconnectNetwork(
        id: String,
        containerID: String,
        force _: Bool,
        context _: RuntimeRequestContext
    ) throws {
        var network = try inspectNetwork(id: id, context: RuntimeRequestContext())
        let snapshot = try container(id: containerID)
        network.containers.removeValue(forKey: snapshot.runtimeID)
        networks[network.id] = network
    }

    public func removeNetwork(
        id: String,
        context: RuntimeRequestContext
    ) throws {
        let network = try inspectNetwork(id: id, context: context)
        guard network.containers.isEmpty else {
            throw DevContainerError(.conflict, message: "network \(id) is in use")
        }
        networks.removeValue(forKey: network.id)
    }

    public func listVolumes(context _: RuntimeRequestContext) -> [VolumeSnapshot] {
        volumes.values.sorted { $0.name < $1.name }
    }

    public func inspectVolume(
        name: String,
        context _: RuntimeRequestContext
    ) throws -> VolumeSnapshot {
        guard let volume = volumes[name] else {
            throw DevContainerError(.notFound, message: "volume \(name) was not found")
        }
        return volume
    }

    public func createVolume(
        spec: VolumeSpec,
        context _: RuntimeRequestContext
    ) -> VolumeSnapshot {
        if let existing = volumes[spec.name] {
            return existing
        }
        let volume = VolumeSnapshot(
            name: spec.name,
            spec: spec,
            mountpoint: "/volumes/\(spec.name)",
            createdAt: Date()
        )
        volumes[spec.name] = volume
        return volume
    }

    public func removeVolume(
        name: String,
        force _: Bool,
        context _: RuntimeRequestContext
    ) throws {
        guard volumes.removeValue(forKey: name) != nil else {
            throw DevContainerError(.notFound, message: "volume \(name) was not found")
        }
    }

    public func events(
        since: Date?,
        until: Date?,
        labels: [String: String],
        context _: RuntimeRequestContext
    ) -> AsyncThrowingStream<RuntimeEvent, any Error> {
        let filtered = eventValues.filter { event in
            (since.map { event.timestamp >= $0 } ?? true) && (until.map { event.timestamp <= $0 } ?? true)
                && labels.allSatisfy { event.attributes[$0.key] == $0.value }
        }
        return AsyncThrowingStream { continuation in
            filtered.forEach { continuation.yield($0) }
            continuation.finish()
        }
    }

    public func seedImage(_ snapshot: ImageSnapshot) {
        images[snapshot.id] = snapshot
    }

    private func image(reference: String) -> ImageSnapshot? {
        images[reference] ?? images.values.first { $0.references.contains(reference) }
    }

    private func container(id: String) throws -> ContainerSnapshot {
        if let snapshot = containers[RuntimeID(rawValue: id)] {
            return snapshot
        }
        if let runtimeID = dockerToRuntime[DockerID(rawValue: id)],
           let snapshot = containers[runtimeID]
        {
            return snapshot
        }
        if let snapshot = containers.values.first(where: { $0.spec.name == id }) {
            return snapshot
        }
        throw DevContainerError(.notFound, message: "container \(id) was not found")
    }

    private func appendEvent(
        resourceID: String,
        action: RuntimeEventAction,
        attributes: [String: String]
    ) {
        eventValues.append(
            RuntimeEvent(
                sequence: nextEventSequence,
                timestamp: Date(),
                resourceID: resourceID,
                action: action,
                attributes: attributes
            )
        )
        nextEventSequence += 1
    }

    private static func identifier() -> String {
        let base = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        return base + base
    }

    private static func jsonLine(_ object: [String: String]) -> Data {
        let data =
            (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])) ?? Data()
        return data + Data("\n".utf8)
    }

    private static func dataStream(
        _ values: [Data]
    ) -> AsyncThrowingStream<Data, any Error> {
        AsyncThrowingStream { continuation in
            values.forEach { continuation.yield($0) }
            continuation.finish()
        }
    }

    private static func frameStream(
        _ values: [RuntimeIOFrame]
    ) -> AsyncThrowingStream<RuntimeIOFrame, any Error> {
        AsyncThrowingStream { continuation in
            values.forEach { continuation.yield($0) }
            continuation.finish()
        }
    }
}

public actor InMemoryProcessSession: RuntimeProcessSession {
    public nonisolated let frames: AsyncThrowingStream<RuntimeIOFrame, any Error>
    private let continuation: AsyncThrowingStream<RuntimeIOFrame, any Error>.Continuation
    private let exitCode: Int32
    private var input = Data()
    private var cancelled = false
    private var size: (width: UInt16, height: UInt16)?

    public init(frames values: [RuntimeIOFrame], exitCode: Int32) {
        var capturedContinuation: AsyncThrowingStream<RuntimeIOFrame, any Error>.Continuation?
        frames = AsyncThrowingStream { continuation in
            capturedContinuation = continuation
        }
        guard let capturedContinuation else {
            preconditionFailure("stream continuation was not created")
        }
        continuation = capturedContinuation
        self.exitCode = exitCode
        values.forEach { continuation.yield($0) }
        continuation.finish()
    }

    public func write(_ data: Data) {
        input.append(data)
    }

    public func closeStandardInput() {
        // The in-memory session has no open file descriptor to close.
    }

    public func resize(width: UInt16, height: UInt16) {
        size = (width, height)
    }

    public func terminalSize() -> (width: UInt16, height: UInt16)? {
        size
    }

    public func wait() throws -> Int32 {
        if cancelled {
            throw CancellationError()
        }
        return exitCode
    }

    public func cancel() {
        cancelled = true
    }
}
