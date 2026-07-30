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

indirect enum DockerRequestSchema: Sendable {
    case value
    case array(DockerRequestSchema)
    case dictionary(DockerRequestSchema)
    case object([String: DockerRequestSchema])

    static let createContainer: DockerRequestSchema = .object([
        "AttachStderr": .value,
        "AttachStdin": .value,
        "AttachStdout": .value,
        "ArgsEscaped": .value,
        "Cmd": .value,
        "Entrypoint": .value,
        "Env": .value,
        "ExposedPorts": .dictionary(.object([:])),
        "Healthcheck": .object([
            "Interval": .value,
            "Retries": .value,
            "StartPeriod": .value,
            "Test": .value,
            "Timeout": .value
        ]),
        "HostConfig": .object([
            "Annotations": .dictionary(.value),
            "AutoRemove": .value,
            "Binds": .value,
            "BlkioDeviceReadBps": .array(.value),
            "BlkioDeviceReadIOps": .array(.value),
            "BlkioDeviceWriteBps": .array(.value),
            "BlkioDeviceWriteIOps": .array(.value),
            "BlkioWeight": .value,
            "BlkioWeightDevice": .array(.value),
            "CapAdd": .value,
            "CapDrop": .value,
            "Cgroup": .value,
            "CgroupParent": .value,
            "CgroupnsMode": .value,
            "ConsoleSize": .array(.value),
            "ContainerIDFile": .value,
            "CpuCount": .value,
            "CpuPeriod": .value,
            "CpuPercent": .value,
            "CpuQuota": .value,
            "CpuRealtimePeriod": .value,
            "CpuRealtimeRuntime": .value,
            "CpuShares": .value,
            "CpusetCpus": .value,
            "CpusetMems": .value,
            "DeviceCgroupRules": .value,
            "DeviceRequests": .array(.deviceRequest),
            "Devices": .array(.deviceMapping),
            "Dns": .value,
            "DnsOptions": .value,
            "DnsSearch": .value,
            "ExtraHosts": .value,
            "GroupAdd": .value,
            "Init": .value,
            "IpcMode": .value,
            "IOMaximumBandwidth": .value,
            "IOMaximumIOps": .value,
            "Isolation": .value,
            "Links": .value,
            "LogConfig": .object([
                "Config": .dictionary(.value),
                "Type": .value
            ]),
            "MaskedPaths": .value,
            "Memory": .value,
            "MemoryReservation": .value,
            "MemorySwap": .value,
            "MemorySwappiness": .value,
            "Mounts": .array(.mount),
            "NanoCpus": .value,
            "NetworkMode": .value,
            "OomKillDisable": .value,
            "OomScoreAdj": .value,
            "PidMode": .value,
            "PidsLimit": .value,
            "PortBindings": .dictionary(.array(.portBinding)),
            "Privileged": .value,
            "PublishAllPorts": .value,
            "ReadonlyPaths": .value,
            "ReadonlyRootfs": .value,
            "RestartPolicy": .object([
                "MaximumRetryCount": .value,
                "Name": .value
            ]),
            "Runtime": .value,
            "SecurityOpt": .value,
            "ShmSize": .value,
            "StorageOpt": .dictionary(.value),
            "Sysctls": .dictionary(.value),
            "Tmpfs": .dictionary(.value),
            "Ulimits": .array(.object([
                "Hard": .value,
                "Name": .value,
                "Soft": .value
            ])),
            "UsernsMode": .value,
            "UTSMode": .value,
            "VolumeDriver": .value,
            "VolumesFrom": .value
        ]),
        "Domainname": .value,
        "Hostname": .value,
        "Image": .value,
        "Labels": .dictionary(.value),
        "MacAddress": .value,
        "Mounts": .array(.mount),
        "NetworkDisabled": .value,
        "NetworkingConfig": .object([
            "EndpointsConfig": .dictionary(.object([
                "Aliases": .value,
                "DNSNames": .value,
                "DriverOpts": .dictionary(.value),
                "EndpointID": .value,
                "Gateway": .value,
                "GlobalIPv6Address": .value,
                "GlobalIPv6PrefixLen": .value,
                "GwPriority": .value,
                "IPAMConfig": .endpointIPAM,
                "IPAddress": .value,
                "IPPrefixLen": .value,
                "IPv6Gateway": .value,
                "Links": .value,
                "MacAddress": .value,
                "NetworkID": .value
            ]))
        ]),
        "OnBuild": .value,
        "OpenStdin": .value,
        "Shell": .value,
        "StdinOnce": .value,
        "StopSignal": .value,
        "StopTimeout": .value,
        "Tty": .value,
        "User": .value,
        "Volumes": .dictionary(.object([:])),
        "WorkingDir": .value
    ])

    static let createExec: DockerRequestSchema = .object([
        "AttachStderr": .value,
        "AttachStdin": .value,
        "AttachStdout": .value,
        "Cmd": .value,
        "ConsoleSize": .array(.value),
        "DetachKeys": .value,
        "Env": .value,
        "Privileged": .value,
        "Tty": .value,
        "User": .value,
        "WorkingDir": .value
    ])

    static let startExec: DockerRequestSchema = .object([
        "ConsoleSize": .array(.value),
        "Detach": .value,
        "Tty": .value
    ])

    static let createNetwork: DockerRequestSchema = .object([
        "Attachable": .value,
        "ConfigFrom": .object([
            "Network": .value
        ]),
        "ConfigOnly": .value,
        "Driver": .value,
        "EnableIPv4": .value,
        "EnableIPv6": .value,
        "Ingress": .value,
        "Internal": .value,
        "IPAM": .object([
            "Config": .array(.object([
                "AuxiliaryAddresses": .dictionary(.value),
                "Gateway": .value,
                "IPRange": .value,
                "Subnet": .value
            ])),
            "Driver": .value,
            "Options": .dictionary(.value)
        ]),
        "Labels": .dictionary(.value),
        "Name": .value,
        "Options": .dictionary(.value),
        "Scope": .value
    ])

    static let connectNetwork: DockerRequestSchema = .object([
        "Container": .value,
        "EndpointConfig": .object([
            "Aliases": .value,
            "DNSNames": .value,
            "DriverOpts": .dictionary(.value),
            "EndpointID": .value,
            "Gateway": .value,
            "GlobalIPv6Address": .value,
            "GlobalIPv6PrefixLen": .value,
            "GwPriority": .value,
            "IPAMConfig": .endpointIPAM,
            "IPAddress": .value,
            "IPPrefixLen": .value,
            "IPv6Gateway": .value,
            "Links": .value,
            "MacAddress": .value,
            "NetworkID": .value
        ])
    ])

    static let disconnectNetwork: DockerRequestSchema = .object([
        "Container": .value,
        "Force": .value
    ])

    static let createVolume: DockerRequestSchema = .object([
        "ClusterVolumeSpec": .object([:]),
        "Driver": .value,
        "DriverOpts": .dictionary(.value),
        "Labels": .dictionary(.value),
        "Name": .value
    ])

    private static let mount: DockerRequestSchema = .object([
        "BindOptions": .object([
            "CreateMountpoint": .value,
            "NonRecursive": .value,
            "Propagation": .value,
            "ReadOnlyForceRecursive": .value,
            "ReadOnlyNonRecursive": .value
        ]),
        "Consistency": .value,
        "ReadOnly": .value,
        "Source": .value,
        "Target": .value,
        "TmpfsOptions": .object([
            "Mode": .value,
            "SizeBytes": .value
        ]),
        "Type": .value,
        "VolumeOptions": .object([
            "DriverConfig": .object([
                "Name": .value,
                "Options": .dictionary(.value)
            ]),
            "Labels": .dictionary(.value),
            "NoCopy": .value,
            "Subpath": .value
        ])
    ])

    private static let deviceRequest: DockerRequestSchema = .object([
        "Capabilities": .array(.array(.value)),
        "Count": .value,
        "DeviceIDs": .value,
        "Driver": .value,
        "Options": .dictionary(.value)
    ])

    private static let deviceMapping: DockerRequestSchema = .object([
        "CgroupPermissions": .value,
        "PathInContainer": .value,
        "PathOnHost": .value
    ])

    private static let endpointIPAM: DockerRequestSchema = .object([
        "IPv4Address": .value,
        "IPv6Address": .value,
        "LinkLocalIPs": .value
    ])

    private static let portBinding: DockerRequestSchema = .object([
        "HostIp": .value,
        "HostPort": .value
    ])

    // The exhaustive schema-node switch deliberately keeps each recursive
    // shape visible in one place.
    // swiftlint:disable:next cyclomatic_complexity
    func validate(_ value: Any, path: [String] = []) throws {
        switch self {
        case .value:
            return
        case let .array(itemSchema):
            guard let values = value as? [Any] else {
                return
            }
            for (index, item) in values.enumerated() {
                try itemSchema.validate(item, path: path + ["[\(index)]"])
            }
        case let .dictionary(valueSchema):
            guard let values = value as? [String: Any] else {
                return
            }
            for (key, item) in values {
                try valueSchema.validate(item, path: path + [key])
            }
        case let .object(fields):
            guard let values = value as? [String: Any] else {
                return
            }
            for (key, item) in values {
                guard let fieldSchema = fields[key] else {
                    throw DecodingError.dataCorrupted(
                        DecodingError.Context(
                            codingPath: [],
                            debugDescription: "unsupported Docker request field "
                                + (path + [key]).joined(separator: ".")
                        )
                    )
                }
                try fieldSchema.validate(item, path: path + [key])
            }
        }
    }
}

extension DockerJSON {
    static func decode<Value: Decodable>(
        _ type: Value.Type,
        from data: Data,
        schema: DockerRequestSchema
    ) throws -> Value {
        let value: Any
        do {
            value = try JSONSerialization.jsonObject(
                with: data,
                options: [.fragmentsAllowed]
            )
        } catch {
            throw DecodingError.dataCorrupted(
                DecodingError.Context(
                    codingPath: [],
                    debugDescription: "invalid JSON: \(error.localizedDescription)",
                    underlyingError: error
                )
            )
        }
        try schema.validate(value)
        return try decoder.decode(type, from: data)
    }
}
