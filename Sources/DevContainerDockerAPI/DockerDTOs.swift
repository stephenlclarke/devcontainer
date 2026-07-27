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

struct DockerErrorEnvelope: Encodable {
    let message: String
}

struct DockerVersionResponse: Encodable {
    let platform: DockerVersionPlatform
    let components: [DockerVersionComponent]
    let version: String
    let apiVersion: String
    let minAPIVersion: String
    let gitCommit: String
    let goVersion = ""
    let operatingSystem: String
    let arch = "arm64"
    let kernelVersion = ""
    let buildTime: String

    enum CodingKeys: String, CodingKey {
        case apiVersion = "ApiVersion"
        case arch = "Arch"
        case buildTime = "BuildTime"
        case components = "Components"
        case gitCommit = "GitCommit"
        case goVersion = "GoVersion"
        case kernelVersion = "KernelVersion"
        case minAPIVersion = "MinAPIVersion"
        case operatingSystem = "Os"
        case platform = "Platform"
        case version = "Version"
    }
}

struct DockerVersionPlatform: Encodable {
    let name: String

    enum CodingKeys: String, CodingKey {
        case name = "Name"
    }
}

struct DockerVersionComponent: Encodable {
    let name: String
    let version: String
    let details: [String: String]

    enum CodingKeys: String, CodingKey {
        case details = "Details"
        case name = "Name"
        case version = "Version"
    }
}

struct DockerInfoResponse: Encodable {
    let id = "devcontainer"
    let containers: Int
    let containersRunning: Int
    let containersPaused = 0
    let containersStopped: Int
    let images: Int
    let driver = "apple-container"
    let memoryLimit = true
    let swapLimit = false
    let cpuCfsPeriod = false
    let cpuCfsQuota = false
    let cpuShares = false
    let cpuSet = false
    let pidsLimit = false
    let oomKillDisable = false
    let operatingSystem = "Apple container Linux virtual machines"
    let osType = "linux"
    let architecture = "aarch64"
    let name = "devcontainer"
    let serverVersion: String

    enum CodingKeys: String, CodingKey {
        case architecture = "Architecture"
        case containers = "Containers"
        case containersPaused = "ContainersPaused"
        case containersRunning = "ContainersRunning"
        case containersStopped = "ContainersStopped"
        case cpuCfsPeriod = "CpuCfsPeriod"
        case cpuCfsQuota = "CpuCfsQuota"
        case cpuSet = "CPUSet"
        case cpuShares = "CPUShares"
        case driver = "Driver"
        case id = "ID"
        case images = "Images"
        case memoryLimit = "MemoryLimit"
        case name = "Name"
        case oomKillDisable = "OomKillDisable"
        case operatingSystem = "OperatingSystem"
        case osType = "OSType"
        case pidsLimit = "PidsLimit"
        case serverVersion = "ServerVersion"
        case swapLimit = "SwapLimit"
    }
}

struct DockerCreateContainerRequest: Decodable {
    var hostname: String?
    var user: String?
    var attachStdin: Bool?
    var attachStdout: Bool?
    var attachStderr: Bool?
    var tty: Bool?
    var openStdin: Bool?
    var env: [String]?
    var cmd: [String]?
    var image: String
    var volumes: [String: EmptyObject]?
    var workingDir: String?
    var entrypoint: StringOrArray?
    var labels: [String: String]?
    var healthcheck: DockerHealthcheck?
    var hostConfig: DockerHostConfig?
    var mounts: [DockerMountRequest]?
    var networkingConfig: DockerNetworkingConfig?

    struct EmptyObject: Decodable {}

    enum CodingKeys: String, CodingKey {
        case attachStderr = "AttachStderr"
        case attachStdin = "AttachStdin"
        case attachStdout = "AttachStdout"
        case cmd = "Cmd"
        case entrypoint = "Entrypoint"
        case env = "Env"
        case hostConfig = "HostConfig"
        case healthcheck = "Healthcheck"
        case hostname = "Hostname"
        case image = "Image"
        case labels = "Labels"
        case mounts = "Mounts"
        case networkingConfig = "NetworkingConfig"
        case openStdin = "OpenStdin"
        case tty = "Tty"
        case user = "User"
        case volumes = "Volumes"
        case workingDir = "WorkingDir"
    }
}

struct DockerHealthcheck: Codable, Equatable, Sendable {
    var test: [String]?
    var interval: Int64?
    var timeout: Int64?
    var retries: Int?
    var startPeriod: Int64?

    enum CodingKeys: String, CodingKey {
        case interval = "Interval"
        case retries = "Retries"
        case startPeriod = "StartPeriod"
        case test = "Test"
        case timeout = "Timeout"
    }
}

enum StringOrArray: Decodable {
    case string(String)
    case array([String])

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let value = try? container.decode(String.self) {
            self = .string(value)
        } else {
            self = try .array(container.decode([String].self))
        }
    }

    var values: [String] {
        switch self {
        case let .string(value):
            [value]
        case let .array(values):
            values
        }
    }
}

struct DockerHostConfig: Decodable {
    var binds: [String]?
    var mounts: [DockerMountRequest]?
    var portBindings: [String: [DockerPortBindingRequest]]?
    var privileged: Bool?
    var autoRemove: Bool?
    var initProcess: Bool?
    var capabilitiesToAdd: [String]?
    var capabilitiesToDrop: [String]?
    var securityOptions: [String]?
    var networkMode: String?

    enum CodingKeys: String, CodingKey {
        case autoRemove = "AutoRemove"
        case binds = "Binds"
        case capabilitiesToAdd = "CapAdd"
        case capabilitiesToDrop = "CapDrop"
        case initProcess = "Init"
        case mounts = "Mounts"
        case networkMode = "NetworkMode"
        case portBindings = "PortBindings"
        case privileged = "Privileged"
        case securityOptions = "SecurityOpt"
    }
}

struct DockerNetworkingConfig: Decodable {
    var endpointsConfig: [String: DockerEndpointConfig]?

    enum CodingKeys: String, CodingKey {
        case endpointsConfig = "EndpointsConfig"
    }
}

struct DockerEndpointConfig: Decodable {
    var aliases: [String]?

    enum CodingKeys: String, CodingKey {
        case aliases = "Aliases"
    }
}

struct DockerPortBindingRequest: Decodable {
    var hostIP: String?
    var hostPort: String?

    enum CodingKeys: String, CodingKey {
        case hostIP = "HostIp"
        case hostPort = "HostPort"
    }
}

struct DockerMountRequest: Decodable {
    var type: String
    var source: String?
    var target: String
    var readOnly: Bool?

    enum CodingKeys: String, CodingKey {
        case readOnly = "ReadOnly"
        case source = "Source"
        case target = "Target"
        case type = "Type"
    }
}

struct DockerCreateContainerResponse: Encodable {
    let id: String
    let warnings: [String]

    enum CodingKeys: String, CodingKey {
        case id = "Id"
        case warnings = "Warnings"
    }
}

struct DockerContainerSummary: Encodable {
    let id: String
    let names: [String]
    let image: String
    let imageID: String
    let command: String
    let created: Int64
    let state: String
    let status: String
    let ports: [DockerPortSummary]
    let labels: [String: String]
    let mounts: [DockerMountSummary]

    enum CodingKeys: String, CodingKey {
        case command = "Command"
        case created = "Created"
        case id = "Id"
        case image = "Image"
        case imageID = "ImageID"
        case labels = "Labels"
        case mounts = "Mounts"
        case names = "Names"
        case ports = "Ports"
        case state = "State"
        case status = "Status"
    }
}

struct DockerPortSummary: Encodable {
    let address: String
    let privatePort: UInt16
    let publicPort: UInt16?
    let type: String

    enum CodingKeys: String, CodingKey {
        case address = "IP"
        case privatePort = "PrivatePort"
        case publicPort = "PublicPort"
        case type = "Type"
    }
}

struct DockerMountSummary: Encodable {
    let type: String
    let name: String
    let source: String
    let destination: String
    let driver: String
    let mode: String
    let readWrite: Bool
    let propagation: String

    enum CodingKeys: String, CodingKey {
        case destination = "Destination"
        case driver = "Driver"
        case mode = "Mode"
        case name = "Name"
        case propagation = "Propagation"
        case readWrite = "RW"
        case source = "Source"
        case type = "Type"
    }
}

struct DockerContainerInspect: Encodable {
    let id: String
    let created: String
    let path: String
    let args: [String]
    let name: String
    let state: DockerContainerState
    let image: String
    let config: DockerContainerConfig
    let hostConfig: DockerInspectHostConfig
    let mounts: [DockerMountSummary]
    let networkSettings: DockerNetworkSettings

    enum CodingKeys: String, CodingKey {
        case args = "Args"
        case config = "Config"
        case created = "Created"
        case hostConfig = "HostConfig"
        case id = "Id"
        case image = "Image"
        case mounts = "Mounts"
        case name = "Name"
        case networkSettings = "NetworkSettings"
        case path = "Path"
        case state = "State"
    }
}

struct DockerContainerState: Encodable {
    let status: String
    let running: Bool
    let paused = false
    let restarting = false
    let oomKilled = false
    let dead = false
    let pid: Int
    let exitCode: Int32
    let error = ""
    let startedAt: String
    let finishedAt: String
    let health: DockerContainerHealth?

    enum CodingKeys: String, CodingKey {
        case dead = "Dead"
        case error = "Error"
        case exitCode = "ExitCode"
        case finishedAt = "FinishedAt"
        case health = "Health"
        case oomKilled = "OOMKilled"
        case paused = "Paused"
        case pid = "Pid"
        case restarting = "Restarting"
        case running = "Running"
        case startedAt = "StartedAt"
        case status = "Status"
    }
}

struct DockerContainerHealth: Encodable, Equatable, Sendable {
    let status: String
    let failingStreak: Int
    let log: [DockerHealthLog]

    enum CodingKeys: String, CodingKey {
        case failingStreak = "FailingStreak"
        case log = "Log"
        case status = "Status"
    }
}

struct DockerHealthLog: Encodable, Equatable, Sendable {
    let start: String
    let end: String
    let exitCode: Int32
    let output: String

    enum CodingKeys: String, CodingKey {
        case end = "End"
        case exitCode = "ExitCode"
        case output = "Output"
        case start = "Start"
    }
}

struct DockerContainerConfig: Encodable {
    let hostname: String
    let user: String
    let attachStdin: Bool
    let attachStdout: Bool
    let attachStderr: Bool
    let tty: Bool
    let openStdin: Bool
    let env: [String]
    let cmd: [String]
    let image: String
    let volumes: [String: [String: String]]
    let workingDir: String
    let entrypoint: [String]
    let labels: [String: String]
    let healthcheck: DockerHealthcheck?

    enum CodingKeys: String, CodingKey {
        case attachStderr = "AttachStderr"
        case attachStdin = "AttachStdin"
        case attachStdout = "AttachStdout"
        case cmd = "Cmd"
        case entrypoint = "Entrypoint"
        case env = "Env"
        case healthcheck = "Healthcheck"
        case hostname = "Hostname"
        case image = "Image"
        case labels = "Labels"
        case openStdin = "OpenStdin"
        case tty = "Tty"
        case user = "User"
        case volumes = "Volumes"
        case workingDir = "WorkingDir"
    }
}

struct DockerInspectHostConfig: Encodable {
    let binds: [String]
    let networkMode = "default"

    enum CodingKeys: String, CodingKey {
        case binds = "Binds"
        case networkMode = "NetworkMode"
    }
}

struct DockerNetworkSettings: Encodable {
    let ports: [String: [DockerNetworkPortBinding]?]
    let networks: [String: DockerEndpointSettings]

    enum CodingKeys: String, CodingKey {
        case networks = "Networks"
        case ports = "Ports"
    }
}

struct DockerNetworkPortBinding: Encodable {
    let hostIP: String
    let hostPort: String

    enum CodingKeys: String, CodingKey {
        case hostIP = "HostIp"
        case hostPort = "HostPort"
    }
}

struct DockerEndpointSettings: Encodable {
    let aliases: [String]
    let networkID: String
    let endpointID: String
    let gateway: String
    let ipAddress: String
    let ipPrefixLen: Int
    let macAddress: String

    enum CodingKeys: String, CodingKey {
        case aliases = "Aliases"
        case endpointID = "EndpointID"
        case gateway = "Gateway"
        case ipAddress = "IPAddress"
        case ipPrefixLen = "IPPrefixLen"
        case macAddress = "MacAddress"
        case networkID = "NetworkID"
    }
}

struct DockerWaitResponse: Encodable {
    let statusCode: Int32

    enum CodingKeys: String, CodingKey {
        case statusCode = "StatusCode"
    }
}

struct DockerCreateExecRequest: Decodable {
    var attachStdin: Bool?
    var attachStdout: Bool?
    var attachStderr: Bool?
    var detachKeys: String?
    var tty: Bool?
    var env: [String]?
    var cmd: [String]
    var privileged: Bool?
    var user: String?
    var workingDir: String?

    enum CodingKeys: String, CodingKey {
        case attachStderr = "AttachStderr"
        case attachStdin = "AttachStdin"
        case attachStdout = "AttachStdout"
        case cmd = "Cmd"
        case detachKeys = "DetachKeys"
        case env = "Env"
        case privileged = "Privileged"
        case tty = "Tty"
        case user = "User"
        case workingDir = "WorkingDir"
    }
}

struct DockerCreateExecResponse: Encodable {
    let id: String

    enum CodingKeys: String, CodingKey {
        case id = "Id"
    }
}

struct DockerStartExecRequest: Decodable {
    var detach: Bool?
    var tty: Bool?

    enum CodingKeys: String, CodingKey {
        case detach = "Detach"
        case tty = "Tty"
    }
}

struct DockerExecInspect: Encodable {
    let id: String
    let running: Bool
    let exitCode: Int32
    let processConfig: DockerExecProcessConfig
    let containerID: String

    enum CodingKeys: String, CodingKey {
        case containerID = "ContainerID"
        case exitCode = "ExitCode"
        case id = "ID"
        case processConfig = "ProcessConfig"
        case running = "Running"
    }
}

struct DockerExecProcessConfig: Encodable {
    let tty: Bool
    let entrypoint: String
    let arguments: [String]
    let privileged = false
    let user: String

    enum CodingKeys: String, CodingKey {
        case arguments
        case entrypoint
        case privileged
        case tty
        case user
    }
}

struct DockerImageSummary: Encodable {
    let containers: Int = -1
    let created: Int64
    let id: String
    let labels: [String: String] = [:]
    let parentID = ""
    let repoDigests: [String]
    let repoTags: [String]
    let sharedSize: Int = -1
    let size: UInt64
    let virtualSize: UInt64

    enum CodingKeys: String, CodingKey {
        case containers = "Containers"
        case created = "Created"
        case id = "Id"
        case labels = "Labels"
        case parentID = "ParentId"
        case repoDigests = "RepoDigests"
        case repoTags = "RepoTags"
        case sharedSize = "SharedSize"
        case size = "Size"
        case virtualSize = "VirtualSize"
    }
}

struct DockerImageInspect: Encodable {
    let id: String
    let repoTags: [String]
    let repoDigests: [String]
    let created: String
    let size: UInt64
    let virtualSize: UInt64
    let architecture: String
    let variant: String
    let operatingSystem: String
    let config: DockerImageConfig

    enum CodingKeys: String, CodingKey {
        case architecture = "Architecture"
        case config = "Config"
        case created = "Created"
        case id = "Id"
        case operatingSystem = "Os"
        case repoDigests = "RepoDigests"
        case repoTags = "RepoTags"
        case size = "Size"
        case variant = "Variant"
        case virtualSize = "VirtualSize"
    }
}

struct DockerImageConfig: Encodable {
    let user: String
    let environment: [String]
    let entrypoint: [String]?
    let command: [String]?
    let labels: [String: String]
    let workingDirectory = ""

    enum CodingKeys: String, CodingKey {
        case command = "Cmd"
        case entrypoint = "Entrypoint"
        case environment = "Env"
        case labels = "Labels"
        case user = "User"
        case workingDirectory = "WorkingDir"
    }
}

struct DockerImageDeleteResponse: Encodable {
    let deleted: String?
    let untagged: String?

    enum CodingKeys: String, CodingKey {
        case deleted = "Deleted"
        case untagged = "Untagged"
    }
}

struct DockerNetworkCreateRequest: Decodable {
    var name: String
    var driver: String?
    var internalNetwork: Bool?
    var labels: [String: String]?

    enum CodingKeys: String, CodingKey {
        case driver = "Driver"
        case internalNetwork = "Internal"
        case labels = "Labels"
        case name = "Name"
    }
}

struct DockerNetworkCreateResponse: Encodable {
    let id: String
    let warning: String

    enum CodingKeys: String, CodingKey {
        case id = "Id"
        case warning = "Warning"
    }
}

struct DockerNetworkConnectRequest: Decodable {
    let container: String
    let endpointConfig: DockerNetworkEndpointConfig?

    enum CodingKeys: String, CodingKey {
        case container = "Container"
        case endpointConfig = "EndpointConfig"
    }
}

struct DockerNetworkEndpointConfig: Decodable {
    var aliases: [String]?

    enum CodingKeys: String, CodingKey {
        case aliases = "Aliases"
    }
}

struct DockerNetworkDisconnectRequest: Decodable {
    let container: String
    let force: Bool?

    enum CodingKeys: String, CodingKey {
        case container = "Container"
        case force = "Force"
    }
}

struct DockerNetworkInspect: Encodable {
    let name: String
    let id: String
    let created: String
    let scope = "local"
    let driver: String
    let enableIPv6 = true
    let internalNetwork: Bool
    let attachable = true
    let ingress = false
    let containers: [String: DockerNetworkContainer]
    let options: [String: String] = [:]
    let labels: [String: String]

    enum CodingKeys: String, CodingKey {
        case attachable = "Attachable"
        case containers = "Containers"
        case created = "Created"
        case driver = "Driver"
        case enableIPv6 = "EnableIPv6"
        case id = "Id"
        case ingress = "Ingress"
        case internalNetwork = "Internal"
        case labels = "Labels"
        case name = "Name"
        case options = "Options"
        case scope = "Scope"
    }
}

struct DockerNetworkContainer: Encodable {
    let name: String
    let endpointID = ""
    let macAddress = ""
    let ipv4Address: String
    let ipv6Address = ""

    enum CodingKeys: String, CodingKey {
        case endpointID = "EndpointID"
        case ipv4Address = "IPv4Address"
        case ipv6Address = "IPv6Address"
        case macAddress = "MacAddress"
        case name = "Name"
    }
}

struct DockerVolumeCreateRequest: Decodable {
    var name: String?
    var driver: String?
    var labels: [String: String]?

    enum CodingKeys: String, CodingKey {
        case driver = "Driver"
        case labels = "Labels"
        case name = "Name"
    }
}

struct DockerVolumeListResponse: Encodable {
    let volumes: [DockerVolumeInspect]
    let warnings: [String]

    enum CodingKeys: String, CodingKey {
        case volumes = "Volumes"
        case warnings = "Warnings"
    }
}

struct DockerVolumeInspect: Encodable {
    let createdAt: String
    let driver: String
    let labels: [String: String]
    let mountpoint: String
    let name: String
    let options: [String: String] = [:]
    let scope = "local"

    enum CodingKeys: String, CodingKey {
        case createdAt = "CreatedAt"
        case driver = "Driver"
        case labels = "Labels"
        case mountpoint = "Mountpoint"
        case name = "Name"
        case options = "Options"
        case scope = "Scope"
    }
}

struct DockerArchivePathStat: Encodable {
    let name: String
    let size: Int64
    let mode: Int64
    let modificationTime: String
    let linkTarget: String

    enum CodingKeys: String, CodingKey {
        case linkTarget
        case mode
        case modificationTime = "mtime"
        case name
        case size
    }
}

struct DockerEventMessage: Encodable {
    let status: String
    let id: String
    let type: String
    let action: String
    let actor: DockerEventActor
    let time: Int64
    let timeNano: Int64

    enum CodingKeys: String, CodingKey {
        case action = "Action"
        case actor = "Actor"
        case id
        case status
        case time
        case timeNano
        case type = "Type"
    }
}

struct DockerEventActor: Encodable {
    let id: String
    let attributes: [String: String]

    enum CodingKeys: String, CodingKey {
        case attributes = "Attributes"
        case id = "ID"
    }
}
