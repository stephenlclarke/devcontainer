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

import Darwin
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
                while await (try? group.next()) != nil {
                    // Drain cancelled children before returning their winner.
                }
                try context.checkActive()
                return result
            } catch {
                group.cancelAll()
                while await (try? group.next()) != nil {
                    // Drain cancelled children before propagating the failure.
                }
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
    /// `false` when the port is declarative container metadata and must not
    /// open a host listener. A missing value preserves compatibility with
    /// metadata written before publication ownership was explicit.
    public var published: Bool?
    /// `true` when the compatibility adapter, rather than the native runtime,
    /// owns the host listener. This survives engine restarts so forwarding can
    /// be reconciled without guessing from a resolved ephemeral port.
    public var hostForwarded: Bool?

    public init(
        containerPort: UInt16,
        hostPort: UInt16? = nil,
        protocolName: String = "tcp",
        hostAddress: String = "0.0.0.0",
        published: Bool? = nil,
        hostForwarded: Bool? = nil
    ) {
        self.containerPort = containerPort
        self.hostPort = hostPort
        self.protocolName = protocolName
        self.hostAddress = hostAddress
        self.published = published
        self.hostForwarded = hostForwarded
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

public final class RuntimeArchiveFile: @unchecked Sendable, Equatable {
    public let url: URL

    private let lock = NSLock()
    private var descriptor: Int32
    private let device: dev_t
    private let inode: ino_t
    private var removalPending = true

    public init(baseDirectory: URL) throws {
        try FileManager.default.createDirectory(
            at: baseDirectory,
            withIntermediateDirectories: true,
            attributes: nil
        )
        let candidate = baseDirectory.appendingPathComponent(
            "devcontainer-archive-\(UUID().uuidString.lowercased()).tar"
        )
        let opened = Darwin.open(
            candidate.path,
            O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC,
            mode_t(0o600)
        )
        guard opened >= 0 else {
            throw Self.posixError()
        }
        var status = Darwin.stat()
        guard Darwin.unlink(candidate.path) == 0 else {
            let failure = errno
            Darwin.close(opened)
            errno = failure
            throw Self.posixError()
        }
        guard fchmod(opened, mode_t(0o600)) == 0,
              fstat(opened, &status) == 0,
              status.st_mode & S_IFMT == S_IFREG,
              status.st_uid == geteuid(),
              status.st_nlink == 0,
              status.st_mode & 0o777 == 0o600
        else {
            let failure = errno
            Darwin.close(opened)
            errno = failure == 0 ? EACCES : failure
            throw Self.posixError()
        }
        url = candidate
        descriptor = opened
        device = status.st_dev
        inode = status.st_ino
    }

    public static func == (lhs: RuntimeArchiveFile, rhs: RuntimeArchiveFile) -> Bool {
        lhs === rhs || lhs.url == rhs.url
    }

    public func makeWritingHandle() throws -> FileHandle {
        try duplicate(resetAndTruncate: true)
    }

    public func makeReadingHandle() throws -> FileHandle {
        try duplicate(resetAndTruncate: false)
    }

    public func remove() {
        let ownedDescriptor = lock.withLock {
            guard removalPending else {
                return Int32(-1)
            }
            removalPending = false
            let value = descriptor
            descriptor = -1
            return value
        }
        guard ownedDescriptor >= 0 else {
            return
        }
        var status = Darwin.stat()
        if lstat(url.path, &status) == 0,
           status.st_dev == device,
           status.st_ino == inode
        {
            Darwin.unlink(url.path)
        }
        Darwin.close(ownedDescriptor)
    }

    deinit {
        remove()
    }

    private func duplicate(resetAndTruncate: Bool) throws -> FileHandle {
        try lock.withLock {
            guard removalPending, descriptor >= 0 else {
                errno = EBADF
                throw Self.posixError()
            }
            let copied = fcntl(descriptor, F_DUPFD_CLOEXEC, 0)
            guard copied >= 0 else {
                throw Self.posixError()
            }
            if resetAndTruncate, ftruncate(copied, 0) != 0 {
                let failure = errno
                Darwin.close(copied)
                errno = failure
                throw Self.posixError()
            }
            guard lseek(copied, 0, SEEK_SET) == 0 else {
                let failure = errno
                Darwin.close(copied)
                errno = failure
                throw Self.posixError()
            }
            return FileHandle(fileDescriptor: copied, closeOnDealloc: true)
        }
    }

    private static func posixError() -> POSIXError {
        POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
    }
}

public enum RuntimeArchiveBody: Equatable, Sendable {
    case bytes(Data)
    case file(RuntimeArchiveFile)
}

public struct RuntimeArchive: Equatable, Sendable {
    public var body: RuntimeArchiveBody
    public var stat: ArchivePathStat

    public init(data: Data, stat: ArchivePathStat) {
        body = .bytes(data)
        self.stat = stat
    }

    public init(file: RuntimeArchiveFile, stat: ArchivePathStat) {
        body = .file(file)
        self.stat = stat
    }

    @available(*, deprecated, message: "Use body so file-backed archives remain streaming")
    public var data: Data {
        switch body {
        case let .bytes(data):
            return data
        case let .file(file):
            guard let handle = try? file.makeReadingHandle() else {
                return Data()
            }
            defer { try? handle.close() }
            return (try? handle.readToEnd()) ?? Data()
        }
    }
}

public struct ImageSnapshot: Codable, Equatable, Sendable {
    public var id: String
    public var references: [String]
    public var createdAt: Date
    public var size: UInt64
    public var architecture: String
    public var variant: String?
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
        variant: String? = nil,
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
        self.variant = variant
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
    public var noCache: Bool
    public var pull: Bool
    public var platform: String?

    public init(
        context: Data,
        dockerfile: String = "Dockerfile",
        tags: [String] = [],
        buildArguments: [String: String] = [:],
        target: String? = nil,
        labels: [String: String] = [:],
        noCache: Bool = false,
        pull: Bool = false,
        platform: String? = nil
    ) {
        self.context = context
        self.dockerfile = dockerfile
        self.tags = tags
        self.buildArguments = buildArguments
        self.target = target
        self.labels = labels
        self.noCache = noCache
        self.pull = pull
        self.platform = platform
    }

    private enum CodingKeys: String, CodingKey {
        case context
        case dockerfile
        case tags
        case buildArguments
        case target
        case labels
        case noCache
        case pull
        case platform
    }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        context = try values.decode(Data.self, forKey: .context)
        dockerfile = try values.decodeIfPresent(String.self, forKey: .dockerfile) ?? "Dockerfile"
        tags = try values.decodeIfPresent([String].self, forKey: .tags) ?? []
        buildArguments = try values.decodeIfPresent(
            [String: String].self,
            forKey: .buildArguments
        ) ?? [:]
        target = try values.decodeIfPresent(String.self, forKey: .target)
        labels = try values.decodeIfPresent([String: String].self, forKey: .labels) ?? [:]
        noCache = try values.decodeIfPresent(Bool.self, forKey: .noCache) ?? false
        pull = try values.decodeIfPresent(Bool.self, forKey: .pull) ?? false
        platform = try values.decodeIfPresent(String.self, forKey: .platform)
    }
}

public struct ExecSpec: Codable, Equatable, Sendable {
    public var command: [String]
    public var environment: [String: String]
    public var workingDirectory: String?
    public var user: String?
    public var terminal: Bool
    public var terminalWidth: UInt16?
    public var terminalHeight: UInt16?
    public var attachStandardInput: Bool
    public var attachStandardOutput: Bool
    public var attachStandardError: Bool

    public init(
        command: [String],
        environment: [String: String] = [:],
        workingDirectory: String? = nil,
        user: String? = nil,
        terminal: Bool = false,
        terminalWidth: UInt16? = nil,
        terminalHeight: UInt16? = nil,
        attachStandardInput: Bool = false,
        attachStandardOutput: Bool = true,
        attachStandardError: Bool = true
    ) {
        self.command = command
        self.environment = environment
        self.workingDirectory = workingDirectory
        self.user = user
        self.terminal = terminal
        self.terminalWidth = terminalWidth
        self.terminalHeight = terminalHeight
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
