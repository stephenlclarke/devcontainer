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

import Foundation

public enum BackendProvider: String, Codable, CaseIterable, Sendable {
    case stock
    case containerCompose = "container-compose"
}

public enum RuntimeCapability: String, Codable, CaseIterable, Hashable, Sendable {
    case archive
    case attach
    case build
    case containers
    case events
    case exec
    case images
    case networks
    case portForwarding
    case registryAuthentication
    case volumes
}

public enum CapabilityStatus: String, Codable, Sendable {
    case native
    case emulated
    case unsupported
}

public struct ProtocolDescriptor: Codable, Equatable, Sendable {
    public var provider: BackendProvider
    public var providerVersion: String
    public var providerCommit: String
    public var distribution: String
    public var dockerAPIMinimum: String
    public var dockerAPIMaximum: String
    public var capabilities: [RuntimeCapability: CapabilityStatus]

    public init(
        provider: BackendProvider,
        providerVersion: String,
        providerCommit: String,
        distribution: String,
        dockerAPIMinimum: String = "1.44",
        dockerAPIMaximum: String = "1.53",
        capabilities: [RuntimeCapability: CapabilityStatus]
    ) {
        self.provider = provider
        self.providerVersion = providerVersion
        self.providerCommit = providerCommit
        self.distribution = distribution
        self.dockerAPIMinimum = dockerAPIMinimum
        self.dockerAPIMaximum = dockerAPIMaximum
        self.capabilities = capabilities
    }
}

public struct RuntimeRequestContext: Codable, Equatable, Sendable {
    public var operationID: OperationID
    public var correlationID: String
    public var project: ProjectKey?
    public var generation: Int64?
    public var deadline: Date?
    public var providerFingerprint: String?
    public var configurationHash: String?

    public init(
        operationID: OperationID = .random(),
        correlationID: String = UUID().uuidString.lowercased(),
        project: ProjectKey? = nil,
        generation: Int64? = nil,
        deadline: Date? = nil,
        providerFingerprint: String? = nil,
        configurationHash: String? = nil
    ) {
        self.operationID = operationID
        self.correlationID = correlationID
        self.project = project
        self.generation = generation
        self.deadline = deadline
        self.providerFingerprint = providerFingerprint
        self.configurationHash = configurationHash
    }

    public func checkActive(now: Date = Date()) throws {
        if Task.isCancelled {
            throw DevContainerError(
                .cancelled,
                message: "runtime request \(correlationID) was cancelled",
                correlationID: correlationID
            )
        }
        if let deadline, deadline <= now {
            throw DevContainerError(
                .deadlineExceeded,
                message: "runtime request \(correlationID) exceeded its deadline",
                correlationID: correlationID
            )
        }
    }
}

public enum RuntimeRequestScope {
    @TaskLocal public static var context: RuntimeRequestContext?

    public static func checkActive() throws {
        try context?.checkActive()
    }

    public static func withDeadline<Result: Sendable>(
        _ operation: @escaping @Sendable () async throws -> Result
    ) async throws -> Result {
        try checkActive()
        guard let context, let deadline = context.deadline else {
            let result = try await operation()
            try checkActive()
            return result
        }
        let remaining = deadline.timeIntervalSinceNow
        guard remaining > 0 else {
            try context.checkActive()
            throw DevContainerError(
                .deadlineExceeded,
                message: "runtime request \(context.correlationID) exceeded its deadline",
                correlationID: context.correlationID
            )
        }
        return try await withThrowingTaskGroup(of: Result.self) { group in
            group.addTask(operation: operation)
            group.addTask {
                let nanoseconds = UInt64(
                    min(remaining, TimeInterval(UInt64.max) / 1_000_000_000)
                        * 1_000_000_000
                )
                try await Task.sleep(nanoseconds: nanoseconds)
                throw DevContainerError(
                    .deadlineExceeded,
                    message: "runtime request \(context.correlationID) exceeded its deadline",
                    correlationID: context.correlationID
                )
            }
            do {
                guard let result = try await group.next() else {
                    throw DevContainerError(
                        .cancelled,
                        message: "runtime request \(context.correlationID) was cancelled",
                        correlationID: context.correlationID
                    )
                }
                group.cancelAll()
                while await (try? group.next()) != nil {}
                try context.checkActive()
                return result
            } catch {
                group.cancelAll()
                while await (try? group.next()) != nil {}
                throw error
            }
        }
    }
}

public enum RuntimeContainerState: String, Codable, Sendable {
    case created
    case running
    case stopped
    case removing
    case unknown
}

public struct PortBinding: Codable, Equatable, Hashable, Sendable {
    public var containerPort: UInt16
    public var hostPort: UInt16?
    public var protocolName: String
    public var hostAddress: String

    public init(
        containerPort: UInt16,
        hostPort: UInt16? = nil,
        protocolName: String = "tcp",
        hostAddress: String = "0.0.0.0"
    ) {
        self.containerPort = containerPort
        self.hostPort = hostPort
        self.protocolName = protocolName
        self.hostAddress = hostAddress
    }
}

public enum RuntimeMountType: String, Codable, Sendable {
    case bind
    case volume
    case tmpfs
}

public struct RuntimeMount: Codable, Equatable, Sendable {
    public var type: RuntimeMountType
    public var source: String
    public var destination: String
    public var readOnly: Bool
    /// `true` for a Docker-managed anonymous volume declared without a
    /// source. Apple containers already retain their writable root
    /// filesystem for the container lifetime, so the Apple adapter can keep
    /// these paths on native EXT4 storage instead of projecting a host
    /// directory through VirtioFS.
    public var anonymous: Bool?

    public init(
        type: RuntimeMountType,
        source: String,
        destination: String,
        readOnly: Bool = false,
        anonymous: Bool? = nil
    ) {
        self.type = type
        self.source = source
        self.destination = destination
        self.readOnly = readOnly
        self.anonymous = anonymous
    }
}

public struct NetworkAttachment: Codable, Equatable, Sendable {
    public var name: String
    public var aliases: [String]

    public init(name: String, aliases: [String] = []) {
        self.name = name
        self.aliases = aliases
    }
}

/// Docker-compatible container health-check configuration.
///
/// Apple container does not currently expose a native health-check scheduler,
/// so the Docker API bridge evaluates this specification through exec.
public struct ContainerHealthcheck: Codable, Equatable, Sendable {
    public var test: [String]
    public var intervalNanoseconds: Int64
    public var timeoutNanoseconds: Int64
    public var retries: Int
    public var startPeriodNanoseconds: Int64

    public init(
        test: [String],
        intervalNanoseconds: Int64 = 30_000_000_000,
        timeoutNanoseconds: Int64 = 30_000_000_000,
        retries: Int = 3,
        startPeriodNanoseconds: Int64 = 0
    ) {
        self.test = test
        self.intervalNanoseconds = intervalNanoseconds
        self.timeoutNanoseconds = timeoutNanoseconds
        self.retries = retries
        self.startPeriodNanoseconds = startPeriodNanoseconds
    }
}

public struct ContainerSpec: Codable, Equatable, Sendable {
    public var name: String
    public var image: String
    public var command: [String]
    public var entrypoint: [String]
    public var environment: [String: String]
    public var labels: [String: String]
    public var workingDirectory: String?
    public var user: String?
    public var hostname: String?
    public var mounts: [RuntimeMount]
    public var ports: [PortBinding]
    public var networks: [NetworkAttachment]
    public var terminal: Bool
    public var openStandardInput: Bool
    public var privileged: Bool
    public var initProcess: Bool
    public var autoRemove: Bool
    public var capabilitiesToAdd: [String]
    public var capabilitiesToDrop: [String]
    public var securityOptions: [String]
    public var healthcheck: ContainerHealthcheck?

    public init(
        name: String,
        image: String,
        command: [String] = [],
        entrypoint: [String] = [],
        environment: [String: String] = [:],
        labels: [String: String] = [:],
        workingDirectory: String? = nil,
        user: String? = nil,
        hostname: String? = nil,
        mounts: [RuntimeMount] = [],
        ports: [PortBinding] = [],
        networks: [NetworkAttachment] = [],
        terminal: Bool = false,
        openStandardInput: Bool = false,
        privileged: Bool = false,
        initProcess: Bool = false,
        autoRemove: Bool = false,
        capabilitiesToAdd: [String] = [],
        capabilitiesToDrop: [String] = [],
        securityOptions: [String] = [],
        healthcheck: ContainerHealthcheck? = nil
    ) {
        self.name = name
        self.image = image
        self.command = command
        self.entrypoint = entrypoint
        self.environment = environment
        self.labels = labels
        self.workingDirectory = workingDirectory
        self.user = user
        self.hostname = hostname
        self.mounts = mounts
        self.ports = ports
        self.networks = networks
        self.terminal = terminal
        self.openStandardInput = openStandardInput
        self.privileged = privileged
        self.initProcess = initProcess
        self.autoRemove = autoRemove
        self.capabilitiesToAdd = capabilitiesToAdd
        self.capabilitiesToDrop = capabilitiesToDrop
        self.securityOptions = securityOptions
        self.healthcheck = healthcheck
    }
}

public struct ContainerSnapshot: Codable, Equatable, Sendable {
    public var runtimeID: RuntimeID
    public var dockerID: DockerID
    public var imageID: String?
    public var spec: ContainerSpec
    public var state: RuntimeContainerState
    public var createdAt: Date
    public var startedAt: Date?
    public var finishedAt: Date?
    public var exitCode: Int32?
    public var networkAddresses: [String: String]

    public init(
        runtimeID: RuntimeID,
        dockerID: DockerID,
        imageID: String? = nil,
        spec: ContainerSpec,
        state: RuntimeContainerState,
        createdAt: Date,
        startedAt: Date? = nil,
        finishedAt: Date? = nil,
        exitCode: Int32? = nil,
        networkAddresses: [String: String] = [:]
    ) {
        self.runtimeID = runtimeID
        self.dockerID = dockerID
        self.imageID = imageID
        self.spec = spec
        self.state = state
        self.createdAt = createdAt
        self.startedAt = startedAt
        self.finishedAt = finishedAt
        self.exitCode = exitCode
        self.networkAddresses = networkAddresses
    }
}

public struct ArchivePathStat: Codable, Equatable, Sendable {
    public var name: String
    public var size: Int64
    public var mode: UInt32
    public var modificationTime: Date
    public var linkTarget: String

    public init(
        name: String,
        size: Int64,
        mode: UInt32,
        modificationTime: Date,
        linkTarget: String = ""
    ) {
        self.name = name
        self.size = size
        self.mode = mode
        self.modificationTime = modificationTime
        self.linkTarget = linkTarget
    }
}

public struct RuntimeArchive: Equatable, Sendable {
    public var data: Data
    public var stat: ArchivePathStat

    public init(data: Data, stat: ArchivePathStat) {
        self.data = data
        self.stat = stat
    }
}

public struct ImageSnapshot: Codable, Equatable, Sendable {
    public var id: String
    public var references: [String]
    public var createdAt: Date
    public var size: UInt64
    public var architecture: String
    public var operatingSystem: String
    public var user: String
    public var environment: [String]
    public var entrypoint: [String]
    public var command: [String]
    public var labels: [String: String]

    public init(
        id: String,
        references: [String],
        createdAt: Date,
        size: UInt64,
        architecture: String = "arm64",
        operatingSystem: String = "linux",
        user: String = "",
        environment: [String] = [],
        entrypoint: [String] = [],
        command: [String] = [],
        labels: [String: String] = [:]
    ) {
        self.id = id
        self.references = references
        self.createdAt = createdAt
        self.size = size
        self.architecture = architecture
        self.operatingSystem = operatingSystem
        self.user = user
        self.environment = environment
        self.entrypoint = entrypoint
        self.command = command
        self.labels = labels
    }
}

public struct ImageBuildRequest: Codable, Equatable, Sendable {
    public var context: Data
    public var dockerfile: String
    public var tags: [String]
    public var buildArguments: [String: String]
    public var target: String?
    public var labels: [String: String]

    public init(
        context: Data,
        dockerfile: String = "Dockerfile",
        tags: [String] = [],
        buildArguments: [String: String] = [:],
        target: String? = nil,
        labels: [String: String] = [:]
    ) {
        self.context = context
        self.dockerfile = dockerfile
        self.tags = tags
        self.buildArguments = buildArguments
        self.target = target
        self.labels = labels
    }
}

public struct ExecSpec: Codable, Equatable, Sendable {
    public var command: [String]
    public var environment: [String: String]
    public var workingDirectory: String?
    public var user: String?
    public var terminal: Bool
    public var attachStandardInput: Bool
    public var attachStandardOutput: Bool
    public var attachStandardError: Bool

    public init(
        command: [String],
        environment: [String: String] = [:],
        workingDirectory: String? = nil,
        user: String? = nil,
        terminal: Bool = false,
        attachStandardInput: Bool = false,
        attachStandardOutput: Bool = true,
        attachStandardError: Bool = true
    ) {
        self.command = command
        self.environment = environment
        self.workingDirectory = workingDirectory
        self.user = user
        self.terminal = terminal
        self.attachStandardInput = attachStandardInput
        self.attachStandardOutput = attachStandardOutput
        self.attachStandardError = attachStandardError
    }
}

public struct ExecSnapshot: Codable, Equatable, Sendable {
    public var id: ExecID
    public var containerID: RuntimeID
    public var spec: ExecSpec
    public var running: Bool
    public var exitCode: Int32?

    public init(
        id: ExecID,
        containerID: RuntimeID,
        spec: ExecSpec,
        running: Bool = false,
        exitCode: Int32? = nil
    ) {
        self.id = id
        self.containerID = containerID
        self.spec = spec
        self.running = running
        self.exitCode = exitCode
    }
}

public enum RuntimeIOChannel: UInt8, Codable, Sendable {
    case standardInput = 0
    case standardOutput = 1
    case standardError = 2
}

public struct RuntimeIOFrame: Codable, Equatable, Sendable {
    public var channel: RuntimeIOChannel
    public var data: Data

    public init(channel: RuntimeIOChannel, data: Data) {
        self.channel = channel
        self.data = data
    }
}

public struct NetworkSpec: Codable, Equatable, Sendable {
    public var name: String
    public var labels: [String: String]
    public var driver: String
    public var internalNetwork: Bool

    public init(
        name: String,
        labels: [String: String] = [:],
        driver: String = "bridge",
        internalNetwork: Bool = false
    ) {
        self.name = name
        self.labels = labels
        self.driver = driver
        self.internalNetwork = internalNetwork
    }
}

public struct NetworkSnapshot: Codable, Equatable, Sendable {
    public var id: String
    public var spec: NetworkSpec
    public var createdAt: Date
    public var containers: [RuntimeID: String]

    public init(
        id: String,
        spec: NetworkSpec,
        createdAt: Date,
        containers: [RuntimeID: String] = [:]
    ) {
        self.id = id
        self.spec = spec
        self.createdAt = createdAt
        self.containers = containers
    }
}

public struct VolumeSpec: Codable, Equatable, Sendable {
    public var name: String
    public var labels: [String: String]
    public var driver: String

    public init(name: String, labels: [String: String] = [:], driver: String = "local") {
        self.name = name
        self.labels = labels
        self.driver = driver
    }
}

public struct VolumeSnapshot: Codable, Equatable, Sendable {
    public var name: String
    public var spec: VolumeSpec
    public var mountpoint: String
    public var createdAt: Date

    public init(name: String, spec: VolumeSpec, mountpoint: String, createdAt: Date) {
        self.name = name
        self.spec = spec
        self.mountpoint = mountpoint
        self.createdAt = createdAt
    }
}

public enum RuntimeEventAction: String, Codable, Sendable {
    case create
    case start
    case stop
    case destroy
    case execCreate = "exec_create"
    case execStart = "exec_start"
    case execDie = "exec_die"
}

public struct RuntimeEvent: Codable, Equatable, Sendable {
    public var sequence: Int64
    public var timestamp: Date
    public var resourceID: String
    public var resourceType: String
    public var action: RuntimeEventAction
    public var attributes: [String: String]

    public init(
        sequence: Int64,
        timestamp: Date,
        resourceID: String,
        resourceType: String = "container",
        action: RuntimeEventAction,
        attributes: [String: String] = [:]
    ) {
        self.sequence = sequence
        self.timestamp = timestamp
        self.resourceID = resourceID
        self.resourceType = resourceType
        self.action = action
        self.attributes = attributes
    }
}
