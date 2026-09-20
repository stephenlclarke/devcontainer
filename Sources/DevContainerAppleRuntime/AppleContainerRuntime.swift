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
    enum ContainerStartOperationKind {
        case start
        case restart
    }

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
        let kind: ContainerStartOperationKind
        let task: Task<Void, any Error>
    }

    struct ContainerMetadataAdoptionOperation {
        let registration: UUID
        let createdAt: Date
        let task: Task<RuntimeContainerMetadata, any Error>
    }

    struct CreateOptionSupport: Sendable {
        let hostname: Bool
        let publish: Bool
        let privileged: Bool
        let securityOptions: Bool
        let dns: Bool
    }

    private struct NativeBuildInput {
        let contextRoot: URL
        let dockerfile: URL
        let temporary: TemporaryDirectory?
    }

    struct StorageRoots {
        let volumes: URL?
        let transfers: URL?
    }

    struct LoggingClients {
        let records: (any AppleContainerLoggingRecordClient)?
        let handoff: (any AppleContainerLoggingHandoffClient)?
    }

    struct DirectClients {
        let api: ContainerClient
        let bootstrap: any AppleContainerBootstrapClient
        let inventory: any AppleContainerInventoryClient
        let files: any AppleContainerFileClient
        let networks: any AppleNetworkClient
        let allocatedNetworks: any AppleNetworkAllocationClient
        let images: any AppleImageIdentityClient
        let creator: any AppleContainerCreateClient
        let loggingRecords: any AppleContainerLoggingRecordClient
        let loggingHandoffClientOverride: (any AppleContainerLoggingHandoffClient)?

        init(
            api: ContainerClient,
            inventory: any AppleContainerInventoryClient,
            files: any AppleContainerFileClient,
            networks: any AppleNetworkClient,
            allocatedNetworks: any AppleNetworkAllocationClient = LiveAppleNetworkAllocationClient(),
            bootstrap: (any AppleContainerBootstrapClient)? = nil,
            images: any AppleImageIdentityClient = LiveAppleImageIdentityClient(),
            creator: (any AppleContainerCreateClient)? = nil,
            logging: LoggingClients = LoggingClients(records: nil, handoff: nil)
        ) {
            self.api = api
            self.bootstrap = bootstrap ?? LiveAppleContainerBootstrapClient(client: api)
            self.inventory = inventory
            self.files = files
            self.networks = networks
            self.allocatedNetworks = allocatedNetworks
            self.images = images
            self.creator = creator ?? LiveAppleContainerCreateClient(client: api)
            loggingRecords = logging.records
                ?? LiveAppleContainerLoggingRecordClient(client: api)
            loggingHandoffClientOverride = logging.handoff
        }
    }

    static let dockerIDLabel = "io.github.stephenlclarke.devcontainer.docker-id"
    private static let nativeResourceRoleLabel = "com.apple.container.resource.role"
    private static let nativePluginLabel = "com.apple.container.plugin"

    public let executable: URL
    let environment: [String: String]
    let useDirectProcessAPI: Bool
    let useDirectContainerAPI: Bool
    let apiClient: ContainerClient
    let bootstrapClient: any AppleContainerBootstrapClient
    let loggingRecordClient: any AppleContainerLoggingRecordClient
    let loggingHandoffClientOverride: (any AppleContainerLoggingHandoffClient)?
    let inventoryClient: any AppleContainerInventoryClient
    let fileClient: any AppleContainerFileClient
    let networkClient: any AppleNetworkClient
    let networkAllocationClient: any AppleNetworkAllocationClient
    let metadataStore: (any RuntimeMetadataStore)?
    let imageIdentityClient: any AppleImageIdentityClient
    let containerCreateClient: any AppleContainerCreateClient
    let managedVolumes: ManagedVolumeStore
    let managedNetworkHosts: ManagedNetworkHostsStore
    let transferRoot: URL
    let portForwarding = PortForwarding()
    var portForwardingObservers: [String: ApplePortForwardingObservation] = [:]
    var portForwardingObservationTasks: [UUID: Task<Void, Never>] = [:]
    var portForwardingShuttingDown = false
    var execs: [ExecID: ExecSnapshot] = [:]
    var requestedContainers: [String: RequestedContainer] = [:]
    var startedContainers: Set<String> = []
    var containerStartedAt: [String: Date] = [:]
    var containerExitTasks: [String: Task<ContainerExit, any Error>] = [:]
    var containerExitRegistrations: [String: UUID] = [:]
    var containerExits: [String: ContainerExit] = [:]
    var containerIO: [String: AppleContainerIO] = [:]
    var containerIOClosures: [String: AppleContainerIOClosure] = [:]
    var containerStartOperations: [String: ContainerStartOperation] = [:]
    var containerMetadataAdoptionOperations:
        [String: ContainerMetadataAdoptionOperation] = [:]
    var automaticRemovalRegistrations: [String: UUID] = [:]
    var containerLifecycleMutationRegistrations: [String: Set<UUID>] = [:]
    var containerLifecycleMutationRevision: UInt64 = 0
    var directProcessLaunchTail: Task<Void, Never>?
    var createOptionSupport: CreateOptionSupport?
    var directContainerInventorySupported: Bool?
    var managedHostsState: [String: AppleManagedHostsState] = [:]
    var networkHostsOperation: (id: UUID, task: Task<[ContainerSnapshot], any Error>)?
    var eventPollerState: AppleEventPoller?

    public init(
        executable: URL,
        environment: [String: String] = ProcessInfo.processInfo.environment,
        useDirectProcessAPI: Bool = true,
        useDirectContainerAPI: Bool = true,
        metadataStore: (any RuntimeMetadataStore)? = nil,
        volumeRoot: URL? = nil,
        transferRoot: URL? = nil
    ) throws {
        let apiClient = ContainerClient()
        try self.init(
            executable: executable,
            environment: environment,
            useDirectProcessAPI: useDirectProcessAPI,
            useDirectContainerAPI: useDirectContainerAPI,
            metadataStore: metadataStore,
            storageRoots: StorageRoots(volumes: volumeRoot, transfers: transferRoot),
            clients: DirectClients(
                api: apiClient,
                inventory: LiveAppleContainerInventoryClient(client: apiClient),
                files: LiveAppleContainerFileClient(client: apiClient),
                networks: AppleNetworkClientAdapter()
            )
        )
    }

    init(
        executable: URL,
        environment: [String: String],
        useDirectProcessAPI: Bool,
        useDirectContainerAPI: Bool,
        metadataStore: (any RuntimeMetadataStore)?,
        storageRoots: StorageRoots,
        clients: DirectClients
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
        apiClient = clients.api
        bootstrapClient = clients.bootstrap
        loggingRecordClient = clients.loggingRecords
        loggingHandoffClientOverride = clients.loggingHandoffClientOverride
        inventoryClient = clients.inventory
        fileClient = clients.files
        networkClient = clients.networks
        networkAllocationClient = clients.allocatedNetworks
        imageIdentityClient = clients.images
        containerCreateClient = clients.creator
        self.metadataStore = metadataStore
        transferRoot = storageRoots.transfers ?? Self.transferDirectory
        managedVolumes = try ManagedVolumeStore(
            root: storageRoots.volumes ?? Self.defaultVolumeRoot
        )
        managedNetworkHosts = try ManagedNetworkHostsStore(
            root: (storageRoots.volumes ?? Self.defaultVolumeRoot)
                .deletingLastPathComponent().appendingPathComponent("network-hosts", isDirectory: true)
        )
    }
}

public extension AppleContainerRuntime {
    func descriptor(context: RuntimeRequestContext) async throws -> ProtocolDescriptor {
        let record = try await appleVersionRecord(context: context)
        directContainerInventorySupported =
            Self.supportsDirectContainerInventory(record)
        return ProtocolDescriptor(
            provider: Self.backendProvider(record),
            providerVersion: record.version,
            providerCommit: record.commit ?? "unspecified",
            distribution: record.distribution ?? "apple",
            capabilities: [
                .archive: .emulated,
                .attach: .emulated,
                .build: .native,
                .containers: .native,
                .composeHealthPolicy: .emulated,
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

    private static func backendProvider(
        _ record: AppleVersionRecord
    ) -> BackendProvider {
        (record.distribution ?? "apple") == "apple"
            ? .stock
            : .containerCompose
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
        portForwardingShuttingDown = true
        await eventPollerState?.shutdown()
        let forwardingObservers = Array(portForwardingObservationTasks.values)
        portForwardingObservers.removeAll()
        for observer in forwardingObservers {
            observer.cancel()
        }
        for observer in forwardingObservers {
            await observer.value
        }
        await portForwarding.stopAll()
        for task in containerExitTasks.values {
            task.cancel()
        }
        containerExitTasks.removeAll()
        containerExitRegistrations.removeAll()
        let channels = Array(containerIO.values)
        containerIO.removeAll()
        for channel in channels {
            await channel.shutdown()
        }
        let closures = Array(containerIOClosures.values)
        containerIOClosures.removeAll()
        for closure in closures {
            await closure.task.value
        }
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
        // Validate the entire CLI observation before reconciliation can discard
        // requested state. Enhanced JSON retains fields absent from stock APIs,
        // but its ISO-8601 encoder omits subsecond creation identity.
        var records: [AppleContainerRecord] = []
        for value in values {
            try await records.append(preciseContainerRecord(value, context: context))
        }
        let observed = records.map(containerSnapshot).filter {
            !Self.isInternalBuilderResource($0)
        }
        var snapshots: [ContainerSnapshot] = []
        snapshots.reserveCapacity(observed.count)
        var observedRuntimeIDs = Set<String>()
        let metadata = try await containerMetadataByRuntimeID()
        let currentMetadata = Self.matchingContainerMetadata(metadata, observed: observed)
        let requiresImageResolution = observed.contains {
            $0.imageID == nil
                && currentMetadata[$0.runtimeID.rawValue]?.imageID == nil
        }
        let images = requiresImageResolution
            ? try await resolvedImages(context: context)
            : []
        for observed in observed {
            let imageID = observed.imageID
                ?? currentMetadata[observed.runtimeID.rawValue]?.imageID
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

    internal static func imageID(
        for reference: String,
        in images: [ResolvedAppleImage]
    ) -> String? {
        images.first { $0.matches(reference) }?.snapshot.id
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
        if let store = metadataStore as? any RuntimeCreationStore,
           try await store.pendingContainerCreation(id: snapshot.runtimeID.rawValue) != nil
        {
            // Observability is retained, but unverified creation cannot be
            // promoted into successful metadata by ordinary reconciliation.
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
            guard metadata.spec.labels[Self.managedNetworkHostsLabel] == nil else {
                throw DevContainerError(.conflict, message: "Managed hosts ownership must be recovered before adoption")
            }
            try await metadataStore.removeContainerMetadata(
                id: snapshot.runtimeID.rawValue
            )
        }
        if snapshot.spec.labels[Self.dockerIDLabel] != nil {
            return snapshot
        }
        let metadata = try await adoptContainerMetadata(
            snapshot,
            imageID: imageID,
            store: metadataStore
        )
        return apply(metadata: metadata, to: snapshot)
    }

    private func adoptContainerMetadata(
        _ snapshot: ContainerSnapshot,
        imageID: String?,
        store: any RuntimeMetadataStore
    ) async throws -> RuntimeContainerMetadata {
        let runtimeID = snapshot.runtimeID.rawValue
        if let operation = containerMetadataAdoptionOperations[runtimeID] {
            if Self.sameContainerIncarnation(
                metadataCreatedAt: operation.createdAt,
                observedCreatedAt: snapshot.createdAt
            ) {
                return try await operation.task.value
            }
            _ = try? await operation.task.value
            finishContainerMetadataAdoption(
                id: runtimeID,
                registration: operation.registration
            )
            return try await adoptContainerMetadata(
                snapshot,
                imageID: imageID,
                store: store
            )
        }
        let registration = UUID()
        let task = makeContainerMetadataAdoptionTask(
            snapshot,
            imageID: imageID,
            store: store
        )
        containerMetadataAdoptionOperations[runtimeID] =
            ContainerMetadataAdoptionOperation(
                registration: registration,
                createdAt: snapshot.createdAt,
                task: task
            )
        do {
            let metadata = try await task.value
            finishContainerMetadataAdoption(
                id: runtimeID,
                registration: registration
            )
            return metadata
        } catch {
            finishContainerMetadataAdoption(
                id: runtimeID,
                registration: registration
            )
            throw error
        }
    }

    private func makeContainerMetadataAdoptionTask(
        _ snapshot: ContainerSnapshot,
        imageID: String?,
        store: any RuntimeMetadataStore
    ) -> Task<RuntimeContainerMetadata, any Error> {
        Task {
            if let persisted = try await store.containerMetadata(
                id: snapshot.runtimeID.rawValue
            ), Self.sameContainerIncarnation(
                metadataCreatedAt: persisted.createdAt,
                observedCreatedAt: snapshot.createdAt
            ) {
                return persisted
            }
            let candidate = RuntimeContainerMetadata(
                runtimeID: snapshot.runtimeID,
                dockerID: DockerID(rawValue: Self.syntheticDockerIdentifier()),
                imageID: imageID,
                spec: snapshot.spec,
                createdAt: snapshot.createdAt,
                startedAt: snapshot.startedAt
            )
            try await store.recordContainerMetadata(candidate)
            guard let persisted = try await store.containerMetadata(
                id: snapshot.runtimeID.rawValue
            ), Self.sameContainerIncarnation(
                metadataCreatedAt: persisted.createdAt,
                observedCreatedAt: snapshot.createdAt
            ) else {
                throw DevContainerError(
                    .stateCorruption,
                    message: "container identity adoption was not durable"
                )
            }
            return persisted
        }
    }

    private func finishContainerMetadataAdoption(
        id: String,
        registration: UUID
    ) {
        guard containerMetadataAdoptionOperations[id]?.registration == registration else {
            return
        }
        containerMetadataAdoptionOperations.removeValue(forKey: id)
    }

    static func syntheticDockerIdentifier() -> String {
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
            // Native absence is not proof that the owned backing files have
            // been removed. Retain the durable identity for explicit recovery.
            guard metadata.spec.labels[Self.managedNetworkHostsLabel] == nil else { continue }
            try await metadataStore.removeContainerMetadata(
                id: metadata.runtimeID.rawValue
            )
            await discardContainerState(
                id: metadata.runtimeID.rawValue,
                dockerID: metadata.dockerID.rawValue,
                name: metadata.spec.name
            )?.shutdown()
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
        guard spec.labels[Self.managedNetworkHostsLabel] == nil else {
            throw DevContainerError(.invalidRequest, message: "Managed network hosts label is runtime-owned")
        }
        let digestAddressed = spec.image.hasPrefix("sha256:") || spec.image.contains("@")
        if digestAddressed, !useDirectContainerAPI {
            try Self.requireNamedImageMutation(spec.image)
        }
        var spec = try applyingOutputLogPolicy(to: spec)
        let mutation = beginContainerLifecycleMutation(id: spec.name)
        var mutationIdentifiers: Set<String> = [spec.name]
        defer {
            finishContainerLifecycleMutation(
                identifiers: mutationIdentifiers,
                registration: mutation
            )
        }
        let optionSupport = try await supportedCreateOptions()
        spec.ports = spec.ports.map {
            Self.configuredPortBinding(
                $0,
                nativePublishingSupported: optionSupport.publish
            )
        }
        containerExitTasks.removeValue(forKey: spec.name)?.cancel()
        containerExitRegistrations.removeValue(forKey: spec.name)
        containerExits.removeValue(forKey: spec.name)
        let resolved = try await resolvedImage(reference: spec.image, context: context)
        let image = resolved.snapshot
        spec = try Self.resolveCreateProcess(spec, image: image)
        let creation: RuntimeContainerCreation?
        do {
            creation = try await performContainerCreate(
                spec: spec, image: resolved, optionSupport: optionSupport, context: context
            )
        } catch {
            throw directAPIError(error, operation: "container create")
        }
        let snapshot = try await completeContainerCreation(
            creation, spec: spec, imageID: image.id, context: context
        )
        mutationIdentifiers.formUnion([
            snapshot.runtimeID.rawValue,
            snapshot.dockerID.rawValue
        ])
        includeContainerLifecycleMutation(
            identifiers: mutationIdentifiers,
            registration: mutation
        )
        await signalEventPollers()
        return snapshot
    }

    private static func resolveCreateProcess(_ spec: ContainerSpec, image: ImageSnapshot) throws -> ContainerSpec {
        guard spec.inheritImageEntrypoint != nil else { return spec }
        return try spec.resolvingImageProcess(imageEntrypoint: image.entrypoint, imageCommand: image.command)
    }

    private func performContainerCreate(
        spec: ContainerSpec, image: ResolvedAppleImage,
        optionSupport: CreateOptionSupport, context: RuntimeRequestContext
    ) async throws -> RuntimeContainerCreation? {
        guard useDirectContainerAPI else {
            if spec.executionSettings?.isEmpty == false || spec.removedEnvironmentKeys?.isEmpty == false {
                throw DevContainerError(
                    .unsupportedCapability,
                    message: "Execution settings and environment removal require descriptor-bound native creation"
                )
            }
            let result = try await command(containerCreateArguments(spec, optionSupport: optionSupport))
            try requireSuccess(result, operation: "container create")
            return nil
        }
        let store = try requireCreationStore()
        try await requireCompletedCreation(id: spec.name, forCreate: true)
        if let prior = try await metadataStore?.containerMetadata(id: spec.name),
           prior.spec.labels[Self.managedNetworkHostsLabel] != nil
        {
            throw DevContainerError(.conflict, message: "Prior managed container must be removed before recreation")
        }
        // Validate flags before preparing mounts or creating native resources.
        _ = try containerConfigurationArguments(spec, optionSupport: optionSupport)
        try Self.validateNativeMounts(spec.mounts)
        var configuration = try await containerCreateClient.prepare(spec: spec, image: image, context: context)
        let hostsIdentity = try prepareManagedNetworkHosts(configuration: &configuration, spec: spec)
        let hostsStore = managedNetworkHosts
        var mountOptions: [String] = []
        for mount in spec.mounts {
            mountOptions += try await mountArguments(mount)
        }
        return try await containerCreateClient.create(
            configuration: configuration, mountOptions: mountOptions, context: context
        ) { prepared in
            var journalSpec = spec
            if let hostsIdentity {
                journalSpec.labels[Self.managedNetworkHostsLabel] = hostsIdentity.operationID.uuidString
            }
            let creation = try RuntimeContainerCreation(
                operationID: hostsIdentity?.operationID ?? UUID(),
                runtimeID: prepared.id, nativeCreatedAt: prepared.creationDate,
                imageID: image.snapshot.id, spec: journalSpec,
                nativeConfiguration: JSONEncoder().encode(prepared)
            )
            try await store.beginContainerCreation(creation)
            if let hostsIdentity {
                try hostsStore.create(identity: hostsIdentity, contents: Self.initialNetworkHosts)
            }
            return creation
        }
    }

    private func containerCreateArguments(
        _ spec: ContainerSpec,
        optionSupport: CreateOptionSupport
    ) async throws -> [String] {
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
        arguments += spec.networks.flatMap { ["--network", $0.name] }
        arguments.append(spec.image)
        arguments += Array(spec.entrypoint.dropFirst())
            + (spec.inheritImageEntrypoint == false && spec.entrypoint.isEmpty
                ? Array(spec.command.dropFirst()) : spec.command)
        return arguments
    }

    static func configuredPortBinding(
        _ binding: PortBinding,
        nativePublishingSupported: Bool
    ) -> PortBinding {
        var binding = binding
        guard binding.published != false else {
            binding.hostForwarded = false
            return binding
        }
        binding.hostForwarded = !nativePublishingSupported || (binding.hostPort ?? 0) == 0
        return binding
    }

    static func nativePublishArguments(_ binding: PortBinding) -> [String]? {
        guard binding.published != false,
              binding.hostForwarded == false,
              let hostPort = binding.hostPort,
              hostPort > 0
        else {
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
        arguments += try Self.dnsArguments(spec, supported: optionSupport.dns)
        let executable = spec.entrypoint.first
            ?? (spec.inheritImageEntrypoint == false ? spec.command.first : nil)
        arguments += Self.optionalArgument("--entrypoint", value: executable)
        return arguments
    }

    private static func dnsArguments(_ spec: ContainerSpec, supported: Bool) throws -> [String] {
        try spec.dns?.validate()
        var arguments = (spec.dns?.nameservers ?? []).flatMap { ["--dns", $0] }
        arguments += (spec.dns?.searchDomains ?? []).flatMap { ["--dns-search", $0] }
        arguments += (spec.dns?.options ?? []).flatMap { ["--dns-option", $0] }
        if !supported, !arguments.isEmpty {
            throw DevContainerError(
                .unsupportedCapability, message: "This Apple container distribution cannot configure DNS"
            )
        }
        if supported, requiresHostDNS(spec) {
            arguments += hostBuildDNSArguments()
        }
        return arguments
    }

    static func requiresHostDNS(_ spec: ContainerSpec) -> Bool {
        spec.name.hasPrefix("buildx_buildkit_")
            && spec.image.contains("buildkit")
            && (spec.dns?.nameservers.isEmpty ?? true)
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
            try AppleBindSourcePolicy.prepare(mount)
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

    static func validateNativeMounts(_ mounts: [RuntimeMount]) throws {
        var destinations: Set<String> = []
        for mount in mounts {
            guard destinations.insert(mount.destination).inserted else {
                throw DevContainerError(.invalidRequest, message: "Duplicate mount destination")
            }
            if mount.type == .volume, mount.anonymous == true {
                // These remain on the container's private root filesystem.
                guard mount.destination.hasPrefix("/") else {
                    throw DevContainerError(.invalidRequest, message: "Mount destination must be absolute")
                }
                continue
            }
            if mount.type == .tmpfs {
                _ = try Parser.tmpfsMounts([mount.destination])
            } else {
                let source = try AppleBindSourcePolicy.validationSource(mount)
                _ = try Parser.mounts([mountValue(
                    mount, type: mount.type == .volume ? "volume" : "bind", source: source
                )])
            }
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

    func recordContainerMetadata(
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

    // swiftlint:disable:next function_body_length
    func copyArchiveFromContainer(
        id: String,
        path: String,
        context: RuntimeRequestContext
    ) async throws -> RuntimeArchive {
        let mutation = beginContainerLifecycleMutation(id: id)
        var mutationIdentifiers: Set<String> = [id]
        defer {
            finishContainerLifecycleMutation(
                identifiers: mutationIdentifiers,
                registration: mutation
            )
        }
        let snapshot = try await inspectContainer(id: id, context: context)
        let resolved = snapshot.runtimeID.rawValue
        mutationIdentifiers.formUnion([
            resolved,
            snapshot.dockerID.rawValue,
            snapshot.spec.name
        ])
        includeContainerLifecycleMutation(
            identifiers: mutationIdentifiers,
            registration: mutation
        )
        return try await withContainerRunningForArchiveTransfer(
            snapshot: snapshot,
            context: context
        ) {
            let temporary = try TemporaryDirectory(base: transferRoot)
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
        let mutation = beginContainerLifecycleMutation(id: id)
        var mutationIdentifiers: Set<String> = [id]
        defer {
            finishContainerLifecycleMutation(
                identifiers: mutationIdentifiers,
                registration: mutation
            )
        }
        guard archive.count <= 1_073_741_824 else {
            throw DevContainerError(.invalidRequest, message: "archive exceeds the 1 GiB request limit")
        }
        let extractionInput = try TarArchiveValidator.validatedForExtraction(archive)
        let snapshot = try await inspectContainer(id: id, context: context)
        let resolved = snapshot.runtimeID.rawValue
        mutationIdentifiers.formUnion([
            resolved,
            snapshot.dockerID.rawValue,
            snapshot.spec.name
        ])
        includeContainerLifecycleMutation(
            identifiers: mutationIdentifiers,
            registration: mutation
        )
        try await withContainerRunningForArchiveTransfer(
            snapshot: snapshot,
            context: context
        ) {
            let temporary = try TemporaryDirectory(base: transferRoot)
            defer { temporary.remove() }
            // An archive may give its "." directory broad permissions. Keep
            // an untouched private parent outside the extraction namespace.
            let contents = try TemporaryDirectory(base: temporary.url)
            defer { contents.remove() }
            let extractResult = try await AppleCommandRunner.run(
                executable: URL(fileURLWithPath: "/usr/bin/tar"),
                // Preserve the validated archive's permissions, not the
                // service's restrictive umask. The staging root stays 0700.
                arguments: ["-xpf", "-", "-C", contents.url.path],
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
                            source: contents.url.path,
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
                        contents.url.path,
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
        try await requireCompletedCreation(id: resolved)
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

    func listImages(context: RuntimeRequestContext) async throws -> [ImageSnapshot] {
        var snapshots: [ImageSnapshot] = []
        for image in try await resolvedImages(context: context) {
            if let index = snapshots.firstIndex(where: { $0.id == image.snapshot.id }) {
                let references = snapshots[index].references + image.snapshot.references
                snapshots[index].references = Array(Set(references)).sorted()
            } else {
                snapshots.append(image.snapshot)
            }
        }
        return snapshots
    }

    func inspectImage(
        reference: String,
        context: RuntimeRequestContext
    ) async throws -> ImageSnapshot {
        try await resolvedImage(reference: reference, context: context).snapshot
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
        try AtomicFile.write(archive, to: archiveURL)
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
        return imageBuildResultStream(result)
    }

    private func imageBuildResultStream(
        _ result: AppleCommandResult
    ) -> AsyncThrowingStream<Data, any Error> {
        AsyncThrowingStream { continuation in
            if !result.standardOutput.isEmpty {
                continuation.yield(result.standardOutput)
            }
            if !result.standardError.isEmpty {
                continuation.yield(result.standardError)
            }
            do {
                try requireSuccess(result, operation: "image build")
                continuation.finish()
            } catch {
                // Once the builder has run, its failure belongs to the stream.
                continuation.finish(throwing: error)
            }
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

    static func hostBuildDNSArguments() -> [String] {
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
        try AtomicFile.write(
            Data("FROM scratch\nADD context.tar /tmp/build-features/\n".utf8),
            to: preparedDockerfile
        )
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
        try Self.requireNamedImageMutation(source)
        try await requireSuccess(
            command(["image", "tag", source, target]),
            operation: "image tag"
        )
    }

    func removeImage(
        reference: String,
        force: Bool,
        context: RuntimeRequestContext
    ) async throws {
        try Self.requireNamedImageMutation(reference)
        if useDirectContainerAPI {
            try await removeNamedImage(reference: reference, force: force, context: context)
            return
        }
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
            return try await directNetwork(
                id: id,
                context: context,
                operation: "network inspect"
            )
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
        context: RuntimeRequestContext
    ) async throws {
        if useDirectContainerAPI {
            let network = try await directNetwork(
                id: id,
                context: context,
                operation: "network delete"
            )
            do {
                try await networkClient.delete(id: network.id)
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

    private func directNetwork(
        id: String,
        context: RuntimeRequestContext,
        operation: String
    ) async throws -> NetworkSnapshot {
        do {
            try context.checkActive()
            let network = try await networkClient.get(id: id)
            try context.checkActive()
            return network
        } catch {
            let directError = directAPIError(error, operation: operation)
            guard directError.code == .notFound else {
                throw directError
            }
        }

        do {
            try context.checkActive()
            let networks = try await networkClient.list()
            try context.checkActive()
            guard let network = networks.first(where: {
                $0.id == id || $0.spec.name == id
            }) else {
                throw DevContainerError(
                    .notFound,
                    message: "network \(id) was not found"
                )
            }
            return network
        } catch {
            throw directAPIError(error, operation: operation)
        }
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
