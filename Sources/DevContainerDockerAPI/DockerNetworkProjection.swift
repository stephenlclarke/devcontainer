// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerCore
import DevContainerModel
import Foundation

extension DockerRouter {
    func resolveNetworkAttachments(
        _ attachments: [NetworkAttachment], context: RuntimeRequestContext
    ) async throws -> [NetworkAttachment] {
        var resolved: [NetworkAttachment] = []
        for attachment in attachments {
            // Built-in modes retain their existing provider handling. Custom
            // network wire IDs must not escape into the native runtime request.
            if ["bridge", "default", "host", "none"].contains(attachment.name) {
                resolved.append(attachment)
            } else {
                let network = try await resolveNetwork(attachment.name, context: context)
                resolved.append(NetworkAttachment(name: network.spec.name, aliases: attachment.aliases))
            }
        }
        return resolved
    }

    /// Resolve the persisted wire identity anew, including after router restart.
    /// A deleted generation must never resolve to a replacement with the same name.
    func resolveNetwork(_ reference: String, context: RuntimeRequestContext) async throws -> NetworkSnapshot {
        let matches = try await runtime.listNetworks(context: context).filter {
            try RuntimeLabels.networkDockerID($0) == reference || $0.id == reference || $0.spec.name == reference
        }
        guard matches.count == 1, let network = matches.first else {
            throw DevContainerError(
                matches.isEmpty ? .notFound : .conflict,
                message: "network reference is missing or ambiguous"
            )
        }
        return network
    }

    func networkInspect(_ network: NetworkSnapshot, containers: [ContainerSnapshot]) throws -> DockerNetworkInspect {
        var endpoints: [String: DockerNetworkContainer] = [:]
        let runtimeIDs = Set(containers.map(\.runtimeID))
        guard Set(network.containers.keys).isSubset(of: runtimeIDs) else {
            throw DevContainerError(.providerProtocolMismatch, message: "network contains an unobserved container")
        }
        for container in containers {
            // Native Container reports live attachments on containers rather than
            // NetworkResource. Do not infer membership from requested networks.
            let address = network.containers[container.runtimeID]
                ?? container.networkAddresses[network.spec.name]
                ?? container.networkAddresses[network.id]
            guard let address else { continue }
            guard endpoints[container.dockerID.rawValue] == nil else {
                throw DevContainerError(.providerProtocolMismatch, message: "duplicate network container identity")
            }
            endpoints[container.dockerID.rawValue] = DockerNetworkContainer(
                name: container.spec.name,
                ipv4Address: address.contains(":") ? "" : address,
                ipv6Address: address.contains(":") ? address : ""
            )
        }
        return try DockerNetworkInspect(
            name: network.spec.name,
            id: RuntimeLabels.networkDockerID(network),
            created: ISO8601DateFormatter().string(from: network.createdAt),
            driver: network.spec.driver,
            internalNetwork: network.spec.internalNetwork,
            containers: endpoints,
            labels: RuntimeLabels.projectComposeLabels(network.spec.labels)
        )
    }
}
