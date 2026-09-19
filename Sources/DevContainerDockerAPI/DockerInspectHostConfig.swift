// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

struct DockerInspectHostConfig: Encodable {
    let binds: [String]
    var portBindings: [String: [DockerNetworkPortBinding]] = [:]
    let networkMode = "default"

    enum CodingKeys: String, CodingKey {
        case binds = "Binds"
        case networkMode = "NetworkMode"
        case portBindings = "PortBindings"
    }
}
