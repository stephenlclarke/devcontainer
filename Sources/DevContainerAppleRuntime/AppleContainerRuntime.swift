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
import Darwin
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

// The adapter is already split by lifecycle, direct client, events, process,
// and support concerns; the remaining protocol surface stays in this file.
// swiftlint:disable file_length

public actor AppleContainerRuntime: DevContainerRuntime {
    struct RequestedContainer {
        var spec: ContainerSpec
        var imageID: String?
        var createdAt: Date?
    }

    struct ContainerExit: Sendable {
        let code: Int32
        let finishedAt: Date
    }

    struct ContainerStartOperation {
        let registration: UUID
        let task: Task<Void, any Error>
    }

    struct CreateOptionSupport: Sendable {
        let hostname: Bool
        let privileged: Bool
        let securityOptions: Bool
        let dns: Bool
    }

    private struct NativeBuildInput {
        let contextRoot: URL
        let dockerfile: URL
        let temporary: TemporaryDirectory?
    }

    static let dockerIDLabel = "io.github.stephenlclarke.devcontainer.docker-id"
    private static let nativeResourceRoleLabel = "com.apple.container.resource.role"
    private static let nativePluginLabel = "com.apple.container.plugin"

    public let executable: URL
    let environment: [String: String]
    let useDirectProcessAPI: Bool
    let useDirectContainerAPI: Bool
    let apiClient: ContainerClient
    let inventoryClient: any AppleContainerInventoryClient
    let fileClient: any AppleContainerFileClient
    let networkClient: any AppleNetworkClient
    let metadataStore: (any RuntimeMetadataStore)?
    let managedVolumes: ManagedVolumeStore
    let portForwarding = PortForwarding()
    var execs: [ExecID: ExecSnapshot] = [:]
    var requestedContainers: [String: RequestedContainer] = [:]
    var startedContainers: Set<String> = []
    var containerStartedAt: [String: Date] = [:]
    var containerExitTasks: [String: Task<ContainerExit, any Error>] = [:]
    var containerExits: [String: ContainerExit] = [:]
    var containerStartOperations: [String: ContainerStartOperation] = [:]
    var directProcessLaunchTail: Task<Void, Never>?
    var createOptionSupport: CreateOptionSupport?
    var directContainerInventorySupported: Bool?
    var managedHostsState: [String: AppleManagedHostsState] = [:]
    var eventPollerState: AppleEventPoller?

    public init(
        executable: URL,
        environment: [String: String] = ProcessInfo.processInfo.environment,
        useDirectProcessAPI: Bool = true,
        useDirectContainerAPI: Bool = true,
        metadataStore: (any RuntimeMetadataStore)? = nil,
        volumeRoot: URL? = nil
    ) throws {
        let apiClient = ContainerClient()
        try self.init(
            executable: executable,
            environment: environment,
            useDirectProcessAPI: useDirectProcessAPI,
            useDirectContainerAPI: useDirectContainerAPI,
            metadataStore: metadataStore,
            volumeRoot: volumeRoot,
            apiClient: apiClient,
            inventoryClient: LiveAppleContainerInventoryClient(client: apiClient),
            fileClient: LiveAppleContainerFileClient(client: apiClient),
            networkClient: AppleNetworkClientAdapter()
        )
    }

    init(
        executable: URL,
        environment: [String: String],
        useDirectProcessAPI: Bool,
        useDirectContainerAPI: Bool,
        metadataStore: (any RuntimeMetadataStore)?,
        volumeRoot: URL?,
        apiClient: ContainerClient,
        inventoryClient: any AppleContainerInventoryClient,
        fileClient: any AppleContainerFileClient,
        networkClient: any AppleNetworkClient
    ) throws {
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
        self.apiClient = apiClient
        self.inventoryClient = inventoryClient
        self.fileClient = fileClient
        self.networkClient = networkClient
        self.metadataStore = metadataStore
        managedVolumes = try ManagedVolumeStore(
            root: volumeRoot ?? Self.defaultVolumeRoot
        )
    }
}

public extension AppleContainerRuntime {
    func descriptor(context: RuntimeRequestContext) async throws -> ProtocolDescriptor {
        let record = try await appleVersionRecord(context: context)
        directContainerInventorySupported =
            Self.supportsDirectContainerInventory(record)
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

    private func appleVersionRecord(
        context: RuntimeRequestContext
    ) async throws -> AppleVersionRecord {
        try context.checkActive()
        let result = try await command(["system", "version", "--format", "json"])
        try requireSuccess(result, operation: "version probe")
        try context.checkActive()
        let records = try JSONDecoder().decode([AppleVersionRecord].self, from: result.standardOutput)
        guard let record = records.first(where: { $0.appName == "container" }) ?? records.first else {
            throw DevContainerError(
                .providerProtocolMismatch, message: "Apple container returned no version record"
            )
        }
        return record
    }

    private static func supportsDirectContainerInventory(
        _ record: AppleVersionRecord
    ) -> Bool {
        (record.distribution ?? "apple") == "apple"
    }

    private func canUseDirectContainerInventory(
        context: RuntimeRequestContext
    ) async throws -> Bool {
        guard useDirectContainerAPI else {
            return false
        }
        if let directContainerInventorySupported {
            return directContainerInventorySupported
        }
        let record = try await appleVersionRecord(context: context)
        let supported = Self.supportsDirectContainerInventory(record)
        directContainerInventorySupported = supported
        return supported
    }

    /// Releases all host-side compatibility resources owned by this adapter.
    func shutdown() async {
        await eventPollerState?.shutdown()
        await portForwarding.stopAll()
    }

    func listContainers(
        all: Bool,
        labels: [String: String],
        context: RuntimeRequestContext
    ) async throws -> [ContainerSnapshot] {
        if try await canUseDirectContainerInventory(context: context) {
            return try await listContainersDirect(
                all: all,
                labels: labels,
                context: context
            )
        }
        let snapshots = try await loadContainerInventory(all: all, context: context)
        return snapshots.filter { snapshot in
            labels.allSatisfy { key, expected in
                guard let actual = snapshot.spec.labels[key] else {
                    return false
                }
                return expected.isEmpty || actual == expected
            }
        }
    }

    private func loadContainerInventory(
        all: Bool,
        context: RuntimeRequestContext
    ) async throws -> [ContainerSnapshot] {
        var arguments = ["list"]
        if all {
            arguments.append("--all")
        }
        arguments += ["--format", "json"]
        let result = try await command(arguments)
        try requireSuccess(result, operation: "container list")
        let values = try parseJSONObjectArray(result.standardOutput)
        let observed = try values.map(containerSnapshot).filter {
            !Self.isInternalBuilderResource($0)
        }
        var snapshots: [ContainerSnapshot] = []
        snapshots.reserveCapacity(observed.count)
        var observedRuntimeIDs = Set<String>()
        let metadata = try await containerMetadataByRuntimeID()
        let requiresImageResolution = observed.contains {
            $0.imageID == nil
                && metadata[$0.runtimeID.rawValue]?.imageID == nil
        }
        let images = requiresImageResolution
            ? try await listImages(context: context)
            : []
        for observed in observed {
            let imageID = observed.imageID
                ?? metadata[observed.runtimeID.rawValue]?.imageID
                ?? Self.imageID(for: observed.spec.image, in: images)
            let snapshot = try await containerSnapshotWithMetadata(
                observed,
                metadata: metadata[observed.runtimeID.rawValue],
                imageID: imageID
            )
            observedRuntimeIDs.insert(snapshot.runtimeID.rawValue)
            snapshots.append(snapshot)
        }
        if all {
            try await removeOrphanedContainerMetadata(
                observedRuntimeIDs: observedRuntimeIDs,
                metadata: metadata
            )
        }
        return snapshots
    }

    static func imageID(
        for reference: String,
        in images: [ImageSnapshot]
    ) -> String? {
        images.first {
            $0.id == reference
                || imageDigest(reference) == $0.id
                || $0.references.contains {
                    equivalentImageReference($0, reference)
                }
        }?.id
    }

    static func isInternalBuilderResource(_ snapshot: ContainerSnapshot) -> Bool {
        snapshot.spec.labels[nativeResourceRoleLabel] == "builder"
            && snapshot.spec.labels[nativePluginLabel] == "builder"
    }

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
        metadata: RuntimeContainerMetadata?,
        imageID: String?
    ) async throws -> ContainerSnapshot {
        var snapshot = snapshot
        snapshot.imageID = imageID
        if snapshot.state == .stopped,
           let exit = containerExits[snapshot.runtimeID.rawValue]
        {
            snapshot.exitCode = exit.code
            snapshot.finishedAt = exit.finishedAt
        }
        guard let metadataStore else {
            return snapshot
        }
        if var metadata {
            if Self.sameContainerIncarnation(
                metadataCreatedAt: metadata.createdAt,
                observedCreatedAt: snapshot.createdAt
            ) {
                if metadata.imageID == nil, let imageID {
                    metadata.imageID = imageID
                    try await metadataStore.recordContainerMetadata(metadata)
                }
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
            imageID: imageID,
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

    func inspectContainer(
        id: String,
        context: RuntimeRequestContext
    ) async throws -> ContainerSnapshot {
        if try await canUseDirectContainerInventory(context: context) {
            if let exact = try await inspectContainerDirect(
                id: id,
                context: context
            ) {
                return exact
            }
            return try await resolvedContainerSnapshot(
                id: id,
                in: listContainersDirect(
                    all: true,
                    labels: [:],
                    context: context
                )
            )
        }
        let matches = try await listContainers(all: true, labels: [:], context: context)
        return try resolvedContainerSnapshot(id: id, in: matches)
    }

    internal func resolvedContainerSnapshot(
        id: String,
        in matches: [ContainerSnapshot]
    ) throws -> ContainerSnapshot {
        if let exact = matches.first(where: {
            $0.runtimeID.rawValue == id || $0.dockerID.rawValue == id || $0.spec.name == id
        }) {
            return exact
        }
        let prefixes = matches.filter {
            $0.runtimeID.rawValue.hasPrefix(id) || $0.dockerID.rawValue.hasPrefix(id)
        }
        guard !prefixes.isEmpty else {
            throw DevContainerError(.notFound, message: "container \(id) was not found")
        }
        guard prefixes.count == 1, let snapshot = prefixes.first else {
            throw DevContainerError(
                .invalidRequest,
                message: "container ID prefix \(id) is ambiguous"
            )
        }
        return snapshot
    }

    func createContainer(
        spec: ContainerSpec,
        context: RuntimeRequestContext
    ) async throws -> ContainerSnapshot {
        containerExitTasks.removeValue(forKey: spec.name)?.cancel()
        containerExits.removeValue(forKey: spec.name)
        let image = try await inspectImage(reference: spec.image, context: context)
        let result = try await command(containerCreateArguments(spec))
        try requireSuccess(result, operation: "container create")
        requestedContainers[spec.name] = RequestedContainer(
            spec: spec,
            imageID: image.id,
            createdAt: nil
        )
        var snapshot = try await inspectContainer(id: spec.name, context: context)
        snapshot.imageID = image.id
        try await recordContainerMetadata(snapshot: snapshot, spec: spec)
        await signalEventPollers()
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
        // Port publishing is owned by PortForwarding after the VM starts.
        // This keeps fixed and ephemeral listeners consistent across stock
        // and custom Apple container distributions.
        arguments += spec.networks.flatMap { ["--network", $0.name] }
        arguments.append(spec.image)
        arguments += Array(spec.entrypoint.dropFirst()) + spec.command
        return arguments
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
            privileged: help.contains("--privileged"),
            securityOptions: help.contains("--security-opt"),
            dns: help.contains("--dns")
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
        if spec.privileged, !optionSupport.privileged {
            throw DevContainerError(
                .unsupportedCapability,
                message:
                "this Apple container distribution cannot enforce Docker privileged mode; "
                    + "use a tagged distribution whose create command exposes --privileged"
            )
        }

        var arguments = ["create", "--name", spec.name]
        arguments += spec.environment.sorted { $0.key < $1.key }
            .flatMap { ["--env", "\($0.key)=\($0.value)"] }
        arguments += spec.labels.filter {
            !$0.key.contains("=") && !$0.value.contains("=")
        }.sorted { $0.key < $1.key }
            .flatMap { ["--label", "\($0.key)=\($0.value)"] }
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
        arguments += spec.capabilitiesToAdd.flatMap { ["--cap-add", $0] }
        arguments += spec.capabilitiesToDrop.flatMap { ["--cap-drop", $0] }
        arguments += spec.securityOptions.flatMap { ["--security-opt", $0] }
        if optionSupport.dns, Self.requiresHostDNS(spec) {
            arguments += Self.hostBuildDNSArguments()
        }
        arguments += Self.optionalArgument("--entrypoint", value: spec.entrypoint.first)
        return arguments
    }

    static func requiresHostDNS(_ spec: ContainerSpec) -> Bool {
        spec.name.hasPrefix("buildx_buildkit_")
            && spec.image.contains("buildkit")
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
        snapshot: ContainerSnapshot,
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
                    imageID: snapshot.imageID,
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
            if useDirectContainerAPI {
                do {
                    try context.checkActive()
                    try await fileClient.copyOut(
                        id: resolved,
                        source: path,
                        destination: copied.path
                    )
                    try context.checkActive()
                } catch {
                    throw directAPIError(error, operation: "container copy-out")
                }
            } else {
                let copyResult = try await command([
                    "cp",
                    "\(resolved):\(path)",
                    copied.path
                ])
                try requireSuccess(copyResult, operation: "container copy-out")
            }
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

    // swiftlint:disable:next function_body_length
    func copyArchiveToContainer(
        id: String,
        path: String,
        archive: Data,
        context: RuntimeRequestContext
    ) async throws {
        guard archive.count <= 1_073_741_824 else {
            throw DevContainerError(.invalidRequest, message: "archive exceeds the 1 GiB request limit")
        }
        let extractionInput = try TarArchiveValidator.validatedForExtraction(archive)
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
                input: extractionInput
            )
            try requireSuccess(extractResult, operation: "archive extraction")
            let staging = "/tmp/.devcontainer-copy-\(UUID().uuidString.lowercased())"
            do {
                if useDirectContainerAPI {
                    do {
                        try context.checkActive()
                        try await fileClient.copyIn(
                            id: resolved,
                            source: temporary.url.path,
                            destination: staging
                        )
                        try context.checkActive()
                    } catch {
                        throw directAPIError(
                            error,
                            operation: "container archive upload"
                        )
                    }
                } else {
                    let uploadResult = try await command([
                        "cp",
                        temporary.url.path,
                        "\(resolved):\(staging)"
                    ])
                    try requireSuccess(
                        uploadResult,
                        operation: "container archive upload"
                    )
                }
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
        // An archive may replace /etc/hosts while leaving the container
        // incarnation unchanged. Force the next reconciliation to re-read it.
        managedHostsState.removeValue(forKey: resolved)
    }

    /// Apple currently exposes copy operations only while the container VM is
    /// running. Docker permits archive transfer for created and stopped
    /// containers, which BuildKit relies on while bootstrapping its driver.
    /// Start the VM only for the duration of the transfer and restore the
    /// externally visible stopped state afterwards.
    private func withContainerRunningForArchiveTransfer<T: Sendable>(
        snapshot: ContainerSnapshot,
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
            managedHostsState.removeValue(forKey: resolved)
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
        let extractionInput = try TarArchiveValidator.validatedForExtraction(
            request.context
        )
        let temporary = try TemporaryDirectory()
        let extractResult = try await AppleCommandRunner.run(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: ["-xf", "-", "-C", temporary.url.path],
            environment: environment,
            input: extractionInput
        )
        try requireSuccess(extractResult, operation: "build context extraction")
        let dockerfile = try buildDockerfile(
            request.dockerfile,
            contextRoot: temporary.url
        )
        let buildInput = try await nativeBuildInput(
            dockerfile: dockerfile,
            contextRoot: temporary.url
        )
        defer {
            buildInput.temporary?.remove()
            temporary.remove()
        }
        var arguments = [
            "build",
            "--file",
            buildInput.dockerfile.path,
            "--progress",
            "plain"
        ]
        arguments += Self.hostBuildDNSArguments()
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
        arguments.append(buildInput.contextRoot.path)
        let result = try await command(arguments)
        try requireSuccess(result, operation: "image build")
        return AsyncThrowingStream { continuation in
            continuation.yield(result.standardOutput)
            continuation.finish()
        }
    }

    static func buildDNSArguments(
        resolverConfiguration: String
    ) -> [String] {
        var seen: Set<String> = []
        var arguments: [String] = []
        for line in resolverConfiguration.split(whereSeparator: \.isNewline) {
            let fields = line.split(whereSeparator: \.isWhitespace)
            guard fields.count >= 2, fields[0] == "nameserver" else {
                continue
            }
            let nameserver = String(fields[1])
            guard isIPAddress(nameserver), seen.insert(nameserver).inserted else {
                continue
            }
            arguments += ["--dns", nameserver]
        }
        return arguments
    }

    private static func hostBuildDNSArguments() -> [String] {
        guard
            let resolverConfiguration = try? String(
                contentsOfFile: "/etc/resolv.conf",
                encoding: .utf8
            )
        else {
            return []
        }
        return buildDNSArguments(
            resolverConfiguration: resolverConfiguration
        )
    }

    private static func isIPAddress(_ value: String) -> Bool {
        let address = value.split(separator: "%", maxSplits: 1).first.map(String.init) ?? value
        var ipv4 = in_addr()
        if value.withCString({ inet_pton(AF_INET, $0, &ipv4) }) == 1 {
            return true
        }
        var ipv6 = in6_addr()
        return address.withCString { inet_pton(AF_INET6, $0, &ipv6) } == 1
    }

    private func nativeBuildInput(
        dockerfile: URL,
        contextRoot: URL
    ) async throws -> NativeBuildInput {
        guard try isFeatureContentStagingDockerfile(dockerfile) else {
            return NativeBuildInput(
                contextRoot: contextRoot,
                dockerfile: dockerfile,
                temporary: nil
            )
        }
        let prepared = try TemporaryDirectory()
        let archive = prepared.url.appendingPathComponent("context.tar")
        let archiveResult = try await AppleCommandRunner.run(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: ["-cf", archive.path, "-C", contextRoot.path, "."],
            environment: environment
        )
        try requireSuccess(
            archiveResult,
            operation: "Feature content archive creation"
        )
        let preparedDockerfile = prepared.url.appendingPathComponent("Dockerfile")
        try Data(
            "FROM scratch\nADD context.tar /tmp/build-features/\n".utf8
        ).write(to: preparedDockerfile, options: .atomic)
        return NativeBuildInput(
            contextRoot: prepared.url,
            dockerfile: preparedDockerfile,
            temporary: prepared
        )
    }

    private func isFeatureContentStagingDockerfile(_ dockerfile: URL) throws -> Bool {
        let contents = try String(contentsOf: dockerfile, encoding: .utf8)
        let instructions = contents
            .split(whereSeparator: \.isNewline)
            .map {
                $0.split(whereSeparator: \.isWhitespace)
                    .joined(separator: " ")
            }
            .filter { !$0.isEmpty }
        return instructions == [
            "FROM scratch",
            "COPY . /tmp/build-features/"
        ]
    }

    private func buildDockerfile(
        _ path: String,
        contextRoot: URL
    ) throws -> URL {
        guard !path.isEmpty, !path.hasPrefix("/") else {
            throw DevContainerError(
                .invalidRequest,
                message: "Dockerfile path must be relative to the build context"
            )
        }
        let root = contextRoot.resolvingSymlinksInPath().standardizedFileURL
        let candidate = contextRoot.appendingPathComponent(path)
            .resolvingSymlinksInPath().standardizedFileURL
        var isDirectory = ObjCBool(false)
        guard candidate.path.hasPrefix(root.path + "/"),
              FileManager.default.fileExists(
                  atPath: candidate.path,
                  isDirectory: &isDirectory
              ),
              !isDirectory.boolValue
        else {
            throw DevContainerError(
                .invalidRequest,
                message: "Dockerfile does not exist inside the build context: \(path)"
            )
        }
        return candidate
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
                return try await networkClient.list()
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
                return try await networkClient.get(id: id)
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
                return try await networkClient.create(spec: spec)
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

    /// BuildKit's state volume must be a Linux-native filesystem. A host
    /// directory projected through VirtioFS cannot back BuildKit's overlayfs
    /// snapshotter, so this narrowly identified Buildx-owned volume uses
    /// Apple's native EXT4 volume implementation. User-named Docker volumes
    /// continue through `ManagedVolumeStore` because Docker permits them to be
    /// attached concurrently to multiple containers.
    internal static func requiresNativeVolume(name: String) -> Bool {
        name.hasPrefix("buildx_buildkit_") && name.hasSuffix("_state")
    }
}
