// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerNetworkClient
import ContainerResource

protocol AppleNetworkAllocationClient: Sendable {
    func lookup(network: String, plugin: String, hostname: String) async throws -> Attachment?
}

struct LiveAppleNetworkAllocationClient: AppleNetworkAllocationClient {
    func lookup(network: String, plugin: String, hostname: String) async throws -> Attachment? {
        // Query this network's allocator, never the global cross-network DNS
        // service. Allocations exist after bootstrap, before the init process.
        try await ContainerNetworkClient.NetworkClient(id: network, plugin: plugin).lookup(hostname: hostname)
    }
}
