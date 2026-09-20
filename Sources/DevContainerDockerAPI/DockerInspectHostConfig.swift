// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

struct DockerInspectHostConfig: Encodable {
    let binds: [String]
    var portBindings: [String: [DockerNetworkPortBinding]] = [:]
    let networkMode = "default"
    var dns: [String] = []
    var dnsSearch: [String] = []
    var dnsOptions: [String] = []
    var memory: UInt64 = 0
    var shmSize: UInt64 = 64 * 1024 * 1024
    var readOnlyRootFilesystem = false
    var sysctls: [String: String] = [:]
    var logConfig: DockerInspectLogConfig?

    enum CodingKeys: String, CodingKey {
        case binds = "Binds"
        case networkMode = "NetworkMode"
        case portBindings = "PortBindings"
        case dns = "Dns"
        case dnsSearch = "DnsSearch"
        case dnsOptions = "DnsOptions"
        case memory = "Memory"
        case shmSize = "ShmSize"
        case readOnlyRootFilesystem = "ReadonlyRootfs"
        case sysctls = "Sysctls"
        case logConfig = "LogConfig"
    }
}

struct DockerInspectLogConfig: Encodable {
    let type = "json-file"
    let config: [String: String] = [:]
    enum CodingKeys: String, CodingKey {
        case type = "Type"
        case config = "Config"
    }
}
