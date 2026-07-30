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

struct DockerNetworkCreateRequest: Decodable {
    var name: String
    var driver: String?
    var internalNetwork: Bool?
    var labels: [String: String]?
    var options: [String: String]?
    var ipam: DockerNetworkIPAM?
    var enableIPv4: Bool?
    var enableIPv6: Bool?
    var attachable: Bool?
    var ingress: Bool?
    var configOnly: Bool?
    var configFrom: DockerNetworkConfigReference?
    var scope: String?

    enum CodingKeys: String, CodingKey {
        case attachable = "Attachable"
        case configFrom = "ConfigFrom"
        case configOnly = "ConfigOnly"
        case driver = "Driver"
        case enableIPv4 = "EnableIPv4"
        case enableIPv6 = "EnableIPv6"
        case ingress = "Ingress"
        case internalNetwork = "Internal"
        case ipam = "IPAM"
        case labels = "Labels"
        case name = "Name"
        case options = "Options"
        case scope = "Scope"
    }
}

struct DockerNetworkIPAM: Decodable {
    var driver: String?
    var config: [DockerNetworkIPAMConfiguration]?
    var options: [String: String]?

    enum CodingKeys: String, CodingKey {
        case config = "Config"
        case driver = "Driver"
        case options = "Options"
    }
}

struct DockerNetworkIPAMConfiguration: Decodable {
    var subnet: String?
    var ipRange: String?
    var gateway: String?
    var auxiliaryAddresses: [String: String]?

    enum CodingKeys: String, CodingKey {
        case auxiliaryAddresses = "AuxiliaryAddresses"
        case gateway = "Gateway"
        case ipRange = "IPRange"
        case subnet = "Subnet"
    }
}

struct DockerNetworkConfigReference: Decodable {
    var network: String?

    enum CodingKeys: String, CodingKey {
        case network = "Network"
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
    var dnsNames: [String]?
    var links: [String]?
    var ipamConfig: DockerEndpointIPAMConfig?
    var macAddress: String?
    var driverOptions: [String: String]?
    var endpointID: String?
    var gateway: String?
    var gatewayPriority: Int?
    var globalIPv6Address: String?
    var globalIPv6PrefixLength: Int?
    var ipAddress: String?
    var ipPrefixLength: Int?
    var ipv6Gateway: String?
    var networkID: String?

    enum CodingKeys: String, CodingKey {
        case aliases = "Aliases"
        case dnsNames = "DNSNames"
        case driverOptions = "DriverOpts"
        case endpointID = "EndpointID"
        case gateway = "Gateway"
        case gatewayPriority = "GwPriority"
        case globalIPv6Address = "GlobalIPv6Address"
        case globalIPv6PrefixLength = "GlobalIPv6PrefixLen"
        case ipamConfig = "IPAMConfig"
        case ipAddress = "IPAddress"
        case ipPrefixLength = "IPPrefixLen"
        case ipv6Gateway = "IPv6Gateway"
        case links = "Links"
        case macAddress = "MacAddress"
        case networkID = "NetworkID"
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
    var driverOptions: [String: String]?
    var clusterVolumeSpecification: DockerClusterVolumeSpecification?

    enum CodingKeys: String, CodingKey {
        case clusterVolumeSpecification = "ClusterVolumeSpec"
        case driver = "Driver"
        case driverOptions = "DriverOpts"
        case labels = "Labels"
        case name = "Name"
    }
}

struct DockerClusterVolumeSpecification: Decodable {}

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
