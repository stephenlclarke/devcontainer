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
import ContainerResource
import Darwin
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

public actor AppleContainerRuntime: DevContainerRuntime {
    struct RequestedContainer {
        var spec: ContainerSpec
        var createdAt: Date?
    }

    struct ContainerExit: Sendable {
        let code: Int32
        let finishedAt: Date
    }

    struct CreateOptionSupport: Sendable {
        let hostname: Bool
        let publish: Bool
        let privileged: Bool
        let securityOptions: Bool
    }

    static let dockerIDLabel = "io.github.stephenlclarke.devcontainer.docker-id"

    public let executable: URL
    let environment: [String: String]
    let useDirectProcessAPI: Bool
    let useDirectContainerAPI: Bool
    let apiClient: ContainerClient
    let resourceClient: any AppleContainerResourceClient
    let networkClient: any AppleNetworkResourceClient
    let metadataStore: (any RuntimeMetadataStore)?
    let managedVolumes: ManagedVolumeStore
    let portForwarding = PortForwarding()
    var execs: [ExecID: ExecSnapshot] = [:]
    var requestedContainers: [String: RequestedContainer] = [:]
    var startedContainers: Set<String> = []
    var containerStartedAt: [String: Date] = [:]
    var containerExitTasks: [String: Task<ContainerExit, any Error>] = [:]
    var containerExits: [String: ContainerExit] = [:]
    var directProcessLaunchTail: Task<Void, Never>?
    var createOptionSupport: CreateOptionSupport?
    var managedHostsState: [String: AppleManagedHostsState] = [:]
    var eventGeneration: UInt64 = 0
    var eventWaiters: [UUID: CheckedContinuation<Void, Never>] = [:]

    public init(
        executable: URL,
        environment: [String: String] = ProcessInfo.processInfo.environment,
        useDirectProcessAPI: Bool = true,
        useDirectContainerAPI: Bool = true,
        metadataStore: (any RuntimeMetadataStore)? = nil,
        volumeRoot: URL? = nil
    ) throws {
        let apiClient = ContainerClient()
        self.apiClient = apiClient
        resourceClient = apiClient
        networkClient = NetworkClient()
        let resolved = executable.standardizedFileURL
        guard resolved.isFileURL, FileManager.default.isExecutableFile(atPath: resolved.path) else {
            throw DevContainerError(
                .runtimeUnavailable,
                message: "Apple container CLI is not executable at \(resolved.path)"
            )
        }
        self.executable = resolved
        self.environment = Self.filteredEnvironment(environment)
        self.useDirectProcessAPI = useDirectProcessAPI
        self.useDirectContainerAPI = useDirectContainerAPI
        self.metadataStore = metadataStore
        managedVolumes = try ManagedVolumeStore(
            root: volumeRoot ?? Self.defaultVolumeRoot
        )
    }

    init(
        executable: URL,
        environment: [String: String] = ProcessInfo.processInfo.environment,
        useDirectProcessAPI: Bool = false,
        useDirectContainerAPI: Bool = true,
        metadataStore: (any RuntimeMetadataStore)? = nil,
        volumeRoot: URL? = nil,
        resourceClient: any AppleContainerResourceClient,
        networkClient: any AppleNetworkResourceClient
    ) throws {
        apiClient = ContainerClient()
        self.resourceClient = resourceClient
        self.networkClient = networkClient
        let resolved = executable.standardizedFileURL
        guard resolved.isFileURL, FileManager.default.isExecutableFile(atPath: resolved.path) else {
            throw DevContainerError(
                .runtimeUnavailable,
                message: "Apple container CLI is not executable at \(resolved.path)"
            )
        }
        self.executable = resolved
        self.environment = Self.filteredEnvironment(environment)
        self.useDirectProcessAPI = useDirectProcessAPI
        self.useDirectContainerAPI = useDirectContainerAPI
        self.metadataStore = metadataStore
        managedVolumes = try ManagedVolumeStore(
            root: volumeRoot ?? Self.defaultVolumeRoot
        )
    }
}

public extension AppleContainerRuntime {
    func descriptor(context _: RuntimeRequestContext) async throws -> ProtocolDescriptor {
        let result = try await command(["system", "version", "--format", "json"])
        try requireSuccess(result, operation: "version probe")
        let records = try JSONDecoder().decode([AppleVersionRecord].self, from: result.standardOutput)
        guard let record = records.first(where: { $0.appName == "container" }) ?? records.first else {
            throw DevContainerError(
                .providerProtocolMismatch, message: "Apple container returned no version record"
            )
        }
        return ProtocolDescriptor(
            provider: .stock,
            providerVersion: record.version,
            providerCommit: record.commit ?? "unspecified",
            distribution: record.distribution ?? "apple",
            capabilities: [
                .archive: .emulated,
                .attach: .emulated,
                .build: .native,
                .containers: .native,
                .events: .emulated,
                .exec: .native,
                .images: .native,
                .networks: .native,
                .portForwarding: .emulated,
                .registryAuthentication: .native,
                .volumes: .emulated
            ]
        )
    }

    /// Releases all host-side compatibility resources owned by this adapter.
    func shutdown() async {
        await portForwarding.stopAll()
    }

    func listContainers(
        all: Bool,
        labels: [String: String],
        context _: RuntimeRequestContext
    ) async throws -> [DevContainerModel.ContainerSnapshot] {
        if useDirectContainerAPI {
            return try await listContainersDirect(all: all, labels: labels)
        }
        var arguments = ["list"]
        if all {
            arguments.append("--all")
        }
        arguments += ["--format", "json"]
        let result = try await command(arguments)
        try requireSuccess(result, operation: "container list")
        let values = try parseJSONObjectArray(result.standardOutput)
        var snapshots: [DevContainerModel.ContainerSnapshot] = []
        var observedRuntimeIDs = Set<String>()
        for value in values {
            let snapshot = try await containerSnapshotWithMetadata(value)
            observedRuntimeIDs.insert(snapshot.runtimeID.rawValue)
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

    internal func containerSnapshotWithMetadata(
        _ value: [String: Any]
    ) async throws -> DevContainerModel.ContainerSnapshot {
        try await containerSnapshotWithMetadata(containerRecord(value))
    }

    internal func containerSnapshotWithMetadata(
        _ record: AppleContainerRecord
    ) async throws -> DevContainerModel.ContainerSnapshot {
        var snapshot = containerSnapshot(record)
        if snapshot.state == .stopped,
           let exit = containerExits[snapshot.runtimeID.rawValue]
        {
            snapshot.exitCode = exit.code
            snapshot.finishedAt = exit.finishedAt
        }
        guard let metadataStore else {
            return snapshot
        }
        if let metadata = try await metadataStore.containerMetadata(
            id: snapshot.runtimeID.rawValue
        ) {
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

    internal func removeOrphanedContainerMetadata(
        observedRuntimeIDs: Set<String>
    ) async throws {
        guard let metadataStore else {
            return
        }
        for metadata in try await metadataStore.listContainerMetadata()
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

    func inspectContainer(
        id: String,
        context: RuntimeRequestContext
    ) async throws -> DevContainerModel.ContainerSnapshot {
        if useDirectContainerAPI,
           let exact = try await inspectContainerDirect(id: id)
        {
            return exact
        }
        let matches = try await listContainers(all: true, labels: [:], context: context)
        if let exact = matches.first(where: {
            $0.runtimeID.rawValue == id || $0.dockerID.rawValue == id || $0.spec.name == id
        }) {
            return exact
        }
        let prefixes = matches.filter {
            $0.runtimeID.rawValue.hasPrefix(id) || $0.dockerID.rawValue.hasPrefix(id)
        }
        guard prefixes.count == 1, let snapshot = prefixes.first else {
            throw DevContainerError(.notFound, message: "container \(id) was not found")
        }
        return snapshot
    }

    func createContainer(
        spec: ContainerSpec,
        context: RuntimeRequestContext
    ) async throws -> DevContainerModel.ContainerSnapshot {
        containerExitTasks.removeValue(forKey: spec.name)?.cancel()
        containerExits.removeValue(forKey: spec.name)
        managedHostsState.removeValue(forKey: spec.name)
        let result = try await command(containerCreateArguments(spec))
        try requireSuccess(result, operation: "container create")
        requestedContainers[spec.name] = RequestedContainer(
            spec: spec,
            createdAt: nil
        )
        signalEventPollers()
        let snapshot = try await inspectContainer(id: spec.name, context: context)
        try await recordContainerMetadata(snapshot: snapshot, spec: spec)
        return snapshot
    }

    private func containerCreateArguments(_ spec: ContainerSpec) async throws -> [String] {
        let optionSupport = try await supportedCreateOptions()
        var arguments = try containerConfigurationArguments(
            spec,
            optionSupport: optionSupport
        )
        for mount in spec.mounts {
            arguments += try await mountArguments(mount)
        }
        if optionSupport.publish {
            arguments += spec.ports.compactMap(Self.nativePublishArguments).flatMap(\.self)
        }
        // Ephemeral host ports are resolved after the VM starts because
        // Apple's stored configuration retains port zero rather than the
        // listener selected by the kernel.
        arguments += spec.networks.flatMap { ["--network", $0.name] }
        arguments.append(spec.image)
        arguments += Array(spec.entrypoint.dropFirst()) + spec.command
        return arguments
    }

    static func nativePublishArguments(_ binding: PortBinding) -> [String]? {
        guard let hostPort = binding.hostPort, hostPort > 0 else {
            return nil
        }
        let hostAddress =
            binding.hostAddress.contains(":")
                ? "[\(binding.hostAddress)]"
                : binding.hostAddress
        return [
            "--publish",
            "\(hostAddress):\(hostPort):\(binding.containerPort)/"
                + binding.protocolName.lowercased()
        ]
    }

    internal func supportedCreateOptions() async throws -> CreateOptionSupport {
        if let createOptionSupport {
            return createOptionSupport
        }
        let result = try await command(["create", "--help"])
        try requireSuccess(result, operation: "container create capability probe")
        let help =
            String(
                bytes: result.standardOutput + result.standardError,
                encoding: .utf8
            ) ?? ""
        let support = CreateOptionSupport(
            hostname: help.contains("--hostname"),
            publish: help.contains("--publish"),
            privileged: help.contains("--privileged"),
            securityOptions: help.contains("--security-opt")
        )
        createOptionSupport = support
        return support
    }

    private func containerConfigurationArguments(
        _ spec: ContainerSpec,
        optionSupport: CreateOptionSupport
    ) throws -> [String] {
        if spec.hostname?.isEmpty == false, !optionSupport.hostname {
            throw DevContainerError(
                .unsupportedCapability,
                message:
                "this Apple container distribution cannot set a container hostname; "
                    + "use a distribution whose create command exposes --hostname"
            )
        }
        if !spec.securityOptions.isEmpty, !optionSupport.securityOptions {
            throw DevContainerError(
                .unsupportedCapability,
                message:
                "this Apple container distribution cannot enforce Docker security options; "
                    + "use a distribution whose create command exposes --security-opt"
            )
        }

        var arguments = ["create", "--name", spec.name]
        arguments += spec.environment.sorted { $0.key < $1.key }
            .flatMap { ["--env", "\($0.key)=\($0.value)"] }
        arguments += spec.labels.sorted { $0.key < $1.key }
            .flatMap { key, value -> [String] in
                // apple/container 1.1's CLI rejects a label whenever its value has
                // another "=". The complete Docker label set remains in the durable
                // adapter metadata and is projected by inspect/list after creation.
                guard !key.contains("="), !value.contains("=") else {
                    return []
                }
                return ["--label", "\(key)=\(value)"]
            }
        arguments += Self.optionalArgument("--workdir", value: spec.workingDirectory)
        arguments += Self.optionalArgument("--user", value: spec.user)
        arguments += Self.optionalArgument("--hostname", value: spec.hostname)
        arguments += [
            (spec.terminal, "--tty"),
            (spec.openStandardInput, "--interactive"),
            (spec.initProcess, "--init")
        ].compactMap { $0.0 ? $0.1 : nil }
        if spec.privileged, optionSupport.privileged {
            arguments.append("--privileged")
        }
        var capabilitiesToAdd = spec.capabilitiesToAdd
        if spec.privileged, !optionSupport.privileged,
           !capabilitiesToAdd.contains(where: { $0.caseInsensitiveCompare("ALL") == .orderedSame })
        {
            // Stock apple/container 1.1 exposes guest privilege through
            // Linux capabilities rather than a Docker-style --privileged
            // switch. Each container remains isolated in its own VM.
            capabilitiesToAdd.insert("ALL", at: 0)
        }
        arguments += capabilitiesToAdd.flatMap { ["--cap-add", $0] }
        arguments += spec.capabilitiesToDrop.flatMap { ["--cap-drop", $0] }
        arguments += spec.securityOptions.flatMap { ["--security-opt", $0] }
        arguments += Self.optionalArgument("--entrypoint", value: spec.entrypoint.first)
        return arguments
    }

    private static func optionalArgument(_ flag: String, value: String?) -> [String] {
        guard let value, !value.isEmpty else {
            return []
        }
        return [flag, value]
    }

    private func mountArguments(_ mount: RuntimeMount) async throws -> [String] {
        switch mount.type {
        case .bind:
            return ["--mount", Self.mountValue(mount, type: "bind", source: mount.source)]
        case .volume where mount.anonymous == true:
            // Image-declared volumes remain private on the native writable
            // root filesystem, which also keeps overlay storage on EXT4.
            return []
        case .volume where Self.requiresNativeVolume(name: mount.source):
            _ = try await createNativeVolumeIfNeeded(spec: VolumeSpec(name: mount.source))
            return ["--mount", Self.mountValue(mount, type: "volume", source: mount.source)]
        case .volume:
            let volume = try managedVolumes.create(spec: VolumeSpec(name: mount.source))
            return ["--mount", Self.mountValue(mount, type: "bind", source: volume.mountpoint)]
        case .tmpfs:
            return ["--tmpfs", mount.destination]
        }
    }

    private static func mountValue(
        _ mount: RuntimeMount,
        type: String,
        source: String
    ) -> String {
        "type=\(type),source=\(source),target=\(mount.destination)"
            + (mount.readOnly ? ",readonly" : "")
    }

    private func recordContainerMetadata(
        snapshot: DevContainerModel.ContainerSnapshot,
        spec: ContainerSpec
    ) async throws {
        guard let metadataStore else {
            return
        }
        do {
            try await metadataStore.recordContainerMetadata(
                RuntimeContainerMetadata(
                    runtimeID: snapshot.runtimeID,
                    dockerID: snapshot.dockerID,
                    spec: spec,
                    createdAt: snapshot.createdAt
                )
            )
        } catch {
            try? await requireSuccess(
                command(["delete", "--force", snapshot.runtimeID.rawValue]),
                operation: "rollback container create"
            )
            throw error
        }
    }

    func copyArchiveFromContainer(
        id: String,
        path: String,
        context: RuntimeRequestContext
    ) async throws -> RuntimeArchive {
        let snapshot = try await inspectContainer(id: id, context: context)
        let resolved = snapshot.runtimeID.rawValue
        return try await withContainerRunningForArchiveTransfer(
            snapshot: snapshot,
            context: context
        ) {
            let temporary = try TemporaryDirectory(base: Self.transferDirectory)
            defer { temporary.remove() }
            let requestedName = URL(fileURLWithPath: path).lastPathComponent
            let archiveName = requestedName.isEmpty ? "root" : requestedName
            let copied = temporary.url.appendingPathComponent(archiveName)
            try await copyContainerResourceOut(
                id: resolved,
                source: path,
                destination: copied.path,
                operation: "container copy-out"
            )
            let stat = try Self.archiveStat(
                url: copied,
                requestedName: requestedName.isEmpty ? "/" : requestedName
            )
            let tarResult = try await AppleCommandRunner.run(
                executable: URL(fileURLWithPath: "/usr/bin/tar"),
                arguments: ["-cf", "-", "-C", temporary.url.path, archiveName],
                environment: environment
            )
            try requireSuccess(tarResult, operation: "archive creation")
            return RuntimeArchive(data: tarResult.standardOutput, stat: stat)
        }
    }

    func copyArchiveToContainer(
        id: String,
        path: String,
        archive: Data,
        context: RuntimeRequestContext
    ) async throws {
        guard archive.count <= 1_073_741_824 else {
            throw DevContainerError(.invalidRequest, message: "archive exceeds the 1 GiB request limit")
        }
        try TarArchiveValidator.validate(archive)
        let snapshot = try await inspectContainer(id: id, context: context)
        let resolved = snapshot.runtimeID.rawValue
        try await withContainerRunningForArchiveTransfer(
            snapshot: snapshot,
            context: context
        ) {
            let temporary = try TemporaryDirectory(base: Self.transferDirectory)
            defer { temporary.remove() }
            let extractResult = try await AppleCommandRunner.run(
                executable: URL(fileURLWithPath: "/usr/bin/tar"),
                arguments: ["-xf", "-", "-C", temporary.url.path],
                environment: environment,
                input: archive
            )
            try requireSuccess(extractResult, operation: "archive extraction")
            let staging = "/tmp/.devcontainer-copy-\(UUID().uuidString.lowercased())"
            do {
                try await copyContainerResourceIn(
                    id: resolved,
                    source: temporary.url.path,
                    destination: staging,
                    operation: "container archive upload"
                )
                let copyResult = try await command([
                    "exec",
                    resolved,
                    "sh",
                    "-c",
                    "mkdir -p -- \"$1\" && cp -a -- \"$2\"/. \"$1\"/",
                    "devcontainer-copy",
                    path,
                    staging
                ])
                try requireSuccess(copyResult, operation: "container archive extraction")
            } catch {
                await removeTransferStaging(staging, containerID: resolved)
                throw error
            }
            await removeTransferStaging(staging, containerID: resolved)
        }
        managedHostsState.removeValue(forKey: resolved)
    }

    /// Apple currently exposes copy operations only while the container VM is
    /// running. Docker permits archive transfer for created and stopped
    /// containers, which BuildKit relies on while bootstrapping its driver.
    /// Start the VM only for the duration of the transfer and restore the
    /// externally visible stopped state afterwards.
    private func withContainerRunningForArchiveTransfer<T: Sendable>(
        snapshot: DevContainerModel.ContainerSnapshot,
        context: RuntimeRequestContext,
        operation: () async throws -> T
    ) async throws -> T {
        let resolved = snapshot.runtimeID.rawValue
        let needsTransientStart = snapshot.state != .running
        if needsTransientStart {
            try await requireSuccess(
                command(["start", resolved]),
                operation: "container archive transfer start"
            )
            try await waitForContainerState(
                id: resolved,
                expected: .running,
                context: context
            )
        }

        do {
            let value = try await operation()
            if needsTransientStart {
                try await stopTransientArchiveContainer(
                    id: resolved,
                    context: context
                )
            }
            return value
        } catch {
            if needsTransientStart {
                try? await stopTransientArchiveContainer(
                    id: resolved,
                    context: context
                )
            }
            throw error
        }
    }

    private func stopTransientArchiveContainer(
        id: String,
        context: RuntimeRequestContext
    ) async throws {
        try await requireSuccess(
            command(["stop", "--time", "10", id]),
            operation: "container archive transfer stop"
        )
        try await waitForContainerState(
            id: id,
            expected: .created,
            context: context,
            acceptsStopped: true
        )
    }

    private func waitForContainerState(
        id: String,
        expected: RuntimeContainerState,
        context: RuntimeRequestContext,
        acceptsStopped: Bool = false
    ) async throws {
        for _ in 0 ..< 100 {
            let current = try await inspectContainer(id: id, context: context).state
            if current == expected || (acceptsStopped && current == .stopped) {
                return
            }
            try await Task.sleep(for: .milliseconds(20))
        }
        throw DevContainerError(
            .deadlineExceeded,
            message: "container \(id) did not reach the required archive-transfer state"
        )
    }

    private func removeTransferStaging(
        _ staging: String,
        containerID: String
    ) async {
        _ = try? await command([
            "exec",
            containerID,
            "rm",
            "-rf",
            "--",
            staging
        ])
    }

    func listImages(context _: RuntimeRequestContext) async throws -> [ImageSnapshot] {
        let result = try await command(["image", "list", "--format", "json"])
        try requireSuccess(result, operation: "image list")
        return try parseJSONObjectArray(result.standardOutput).compactMap(imageSnapshot)
    }

    func inspectImage(
        reference: String,
        context: RuntimeRequestContext
    ) async throws -> ImageSnapshot {
        let images = try await listImages(context: context)
        guard
            let image = images.first(where: {
                $0.id == reference
                    || Self.imageDigest(reference) == $0.id
                    || $0.references.contains(where: {
                        Self.equivalentImageReference($0, reference)
                    })
            })
        else {
            throw DevContainerError(.notFound, message: "image \(reference) was not found")
        }
        return image
    }

    func pullImage(
        reference: String,
        context _: RuntimeRequestContext
    ) async throws -> AsyncThrowingStream<Data, any Error> {
        let session = try process([
            "image",
            "pull",
            "--progress",
            "plain",
            "--platform",
            Self.defaultPlatform,
            reference
        ])
        return Self.dataStream(session: session)
    }

    func loadImage(
        archive: Data,
        context _: RuntimeRequestContext
    ) async throws -> AsyncThrowingStream<Data, any Error> {
        let temporary = try TemporaryDirectory()
        defer { temporary.remove() }
        let archiveURL = temporary.url.appendingPathComponent("image.tar")
        try archive.write(to: archiveURL, options: .atomic)
        let result = try await command([
            "image",
            "load",
            "--input",
            archiveURL.path
        ])
        try requireSuccess(result, operation: "image load")
        return AsyncThrowingStream { continuation in
            if !result.standardOutput.isEmpty {
                continuation.yield(result.standardOutput)
            }
            if !result.standardError.isEmpty {
                continuation.yield(result.standardError)
            }
            continuation.finish()
        }
    }

    func buildImage(
        request: ImageBuildRequest,
        context _: RuntimeRequestContext
    ) async throws -> AsyncThrowingStream<Data, any Error> {
        try TarArchiveValidator.validate(request.context)
        let temporary = try TemporaryDirectory()
        let extractResult = try await AppleCommandRunner.run(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: ["-xf", "-", "-C", temporary.url.path],
            environment: environment,
            input: request.context
        )
        try requireSuccess(extractResult, operation: "build context extraction")
        var arguments = ["build", "--file", request.dockerfile, "--progress", "plain"]
        for tag in request.tags {
            arguments += ["--tag", tag]
        }
        for (key, value) in request.buildArguments.sorted(by: { $0.key < $1.key }) {
            arguments += ["--build-arg", "\(key)=\(value)"]
        }
        if let target = request.target {
            arguments += ["--target", target]
        }
        for (key, value) in request.labels.sorted(by: { $0.key < $1.key }) {
            arguments += ["--label", "\(key)=\(value)"]
        }
        arguments.append(temporary.url.path)
        let result = try await command(arguments)
        temporary.remove()
        try requireSuccess(result, operation: "image build")
        return AsyncThrowingStream { continuation in
            continuation.yield(result.standardOutput)
            continuation.finish()
        }
    }

    func tagImage(
        source: String,
        target: String,
        context _: RuntimeRequestContext
    ) async throws {
        try await requireSuccess(
            command(["image", "tag", source, target]),
            operation: "image tag"
        )
    }

    func removeImage(
        reference: String,
        force: Bool,
        context _: RuntimeRequestContext
    ) async throws {
        var arguments = ["image", "delete"]
        if force {
            arguments.append("--force")
        }
        arguments.append(reference)
        try await requireSuccess(command(arguments), operation: "image delete")
    }

    func listNetworks(context _: RuntimeRequestContext) async throws -> [NetworkSnapshot] {
        if useDirectContainerAPI {
            do {
                return try await networkClient.list().map(networkSnapshot)
            } catch {
                throw directAPIError(error, operation: "network list")
            }
        }
        let result = try await command(["network", "list", "--format", "json"])
        try requireSuccess(result, operation: "network list")
        return try parseJSONObjectArray(result.standardOutput).compactMap(networkSnapshot)
    }

    func inspectNetwork(
        id: String,
        context: RuntimeRequestContext
    ) async throws -> NetworkSnapshot {
        if useDirectContainerAPI {
            do {
                return try await networkSnapshot(networkClient.get(id: id))
            } catch {
                throw directAPIError(error, operation: "network inspect")
            }
        }
        let networks = try await listNetworks(context: context)
        guard let network = networks.first(where: { $0.id == id || $0.spec.name == id }) else {
            throw DevContainerError(.notFound, message: "network \(id) was not found")
        }
        return network
    }

    func createNetwork(
        spec: NetworkSpec,
        context: RuntimeRequestContext
    ) async throws -> NetworkSnapshot {
        if useDirectContainerAPI {
            do {
                let configuration = try NetworkConfiguration(
                    name: spec.name,
                    mode: spec.internalNetwork ? .hostOnly : .nat,
                    labels: ResourceLabels(spec.labels),
                    plugin: "container-network-vmnet"
                )
                return try await networkSnapshot(
                    networkClient.create(
                        configuration: configuration
                    )
                )
            } catch {
                throw directAPIError(error, operation: "network create")
            }
        }
        var arguments = ["network", "create"]
        for (key, value) in spec.labels.sorted(by: { $0.key < $1.key }) {
            arguments += ["--label", "\(key)=\(value)"]
        }
        if spec.internalNetwork {
            arguments.append("--internal")
        }
        arguments.append(spec.name)
        try await requireSuccess(command(arguments), operation: "network create")
        return try await inspectNetwork(id: spec.name, context: context)
    }

    func connectNetwork(
        id: String,
        containerID: String,
        aliases: [String],
        context: RuntimeRequestContext
    ) async throws {
        _ = aliases
        _ = try await resolveContainerID(containerID, context: context)
        _ = try await inspectNetwork(id: id, context: context)
        throw DevContainerError(
            .unsupportedCapability,
            message: "stock Apple container requires networks and aliases at container creation"
        )
    }

    func disconnectNetwork(
        id: String,
        containerID: String,
        force: Bool,
        context: RuntimeRequestContext
    ) async throws {
        _ = force
        _ = try await resolveContainerID(containerID, context: context)
        _ = try await inspectNetwork(id: id, context: context)
        throw DevContainerError(
            .unsupportedCapability,
            message: "stock Apple container cannot change network attachments after creation"
        )
    }

    func removeNetwork(
        id: String,
        context _: RuntimeRequestContext
    ) async throws {
        if useDirectContainerAPI {
            do {
                try await networkClient.delete(id: id)
            } catch {
                throw directAPIError(error, operation: "network delete")
            }
            return
        }
        try await requireSuccess(
            command(["network", "delete", id]),
            operation: "network delete"
        )
    }

    func listVolumes(context _: RuntimeRequestContext) async throws -> [VolumeSnapshot] {
        let managed = try managedVolumes.list()
        let native = try await nativeBuildKitVolumes()
        return (managed + native).sorted { $0.name < $1.name }
    }

    func inspectVolume(
        name: String,
        context: RuntimeRequestContext
    ) async throws -> VolumeSnapshot {
        _ = context
        if Self.requiresNativeVolume(name: name) {
            guard
                let volume = try await nativeBuildKitVolumes().first(where: {
                    $0.name == name
                })
            else {
                throw DevContainerError(.notFound, message: "volume \(name) was not found")
            }
            return volume
        }
        return try managedVolumes.inspect(name: name)
    }

    func createVolume(
        spec: VolumeSpec,
        context: RuntimeRequestContext
    ) async throws -> VolumeSnapshot {
        _ = context
        if Self.requiresNativeVolume(name: spec.name) {
            return try await createNativeVolumeIfNeeded(spec: spec)
        }
        return try managedVolumes.create(spec: spec)
    }

    func removeVolume(
        name: String,
        force _: Bool,
        context: RuntimeRequestContext
    ) async throws {
        let containers = try await listContainers(
            all: true,
            labels: [:],
            context: context
        )
        guard
            !containers.contains(where: { container in
                container.spec.mounts.contains {
                    $0.type == .volume && $0.source == name
                }
            })
        else {
            throw DevContainerError(
                .conflict,
                message: "volume \(name) is in use by a container"
            )
        }
        if Self.requiresNativeVolume(name: name) {
            try await requireSuccess(
                command(["volume", "delete", name]),
                operation: "volume delete"
            )
            return
        }
        try managedVolumes.remove(name: name)
    }

    /// BuildKit state must use Linux-native storage; host VirtioFS cannot back its overlayfs
    /// snapshotter, so this narrowly identified Buildx-owned volume uses
    /// Apple's native EXT4 volume implementation. User-named Docker volumes
    /// continue through `ManagedVolumeStore` because Docker permits them to be
    /// attached concurrently to multiple containers.
    internal static func requiresNativeVolume(name: String) -> Bool {
        name.hasPrefix("buildx_buildkit_") && name.hasSuffix("_state")
    }
}
