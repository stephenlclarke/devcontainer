// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerResource
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

extension AppleContainerRuntime {
    static let managedNetworkHostsLabel = "io.devcontainer.network-hosts-operation"
    static let initialNetworkHosts = "127.0.0.1 localhost\n::1 localhost ip6-localhost ip6-loopback\n"

    func networkHostsTargetIsCurrent(
        _ target: DevContainerModel.ContainerSnapshot, context: RuntimeRequestContext
    ) async throws -> Bool {
        do {
            let current = try await inspectContainer(id: target.runtimeID.rawValue, context: context)
            return current.state == .running && current.createdAt == target.createdAt
                && current.startedAt == target.startedAt
        } catch let error as DevContainerError where error.code == .notFound {
            return false
        }
    }

    func managedContainerIsStopped(id: String) async throws -> Bool {
        guard let configuration = try await managedHostsConfiguration(id: id) else { return false }
        let native = try await inventoryClient.get(id: id)
        guard native.configuration.creationDate == configuration.creationDate else {
            throw DevContainerError(.conflict, message: "Managed container changed before lifecycle operation")
        }
        return native.status == .stopped
    }

    func managedHostsConfiguration(id: String) async throws -> ContainerConfiguration? {
        let labels: [String: String]
        let createdAt: Date?
        if let cached = requestedContainers[id] {
            labels = cached.spec.labels
            createdAt = cached.createdAt
        } else if let metadata = try await metadataStore?.containerMetadata(id: id) {
            labels = metadata.spec.labels
            createdAt = metadata.createdAt
        } else {
            return nil
        }
        guard let ownership = labels[Self.managedNetworkHostsLabel] else { return nil }
        let native = try await inventoryClient.get(id: id)
        guard native.id == id, native.configuration.creationDate == createdAt,
              native.configuration.labels[Self.managedNetworkHostsLabel] == ownership
        else {
            throw DevContainerError(.conflict, message: "Managed hosts container incarnation changed")
        }
        guard let identity = try managedNetworkHostsIdentity(configuration: native.configuration) else {
            throw DevContainerError(.stateCorruption, message: "Managed hosts ownership is missing")
        }
        try managedNetworkHosts.update(identity: identity) { $0 }
        return native.configuration
    }

    func populateManagedHostsBeforeProcess(
        configuration: ContainerConfiguration, includeAllocatedSelf: Bool,
        context: RuntimeRequestContext
    ) async throws {
        try context.checkActive()
        guard let identity = try managedNetworkHostsIdentity(configuration: configuration) else { return }
        let inventory = try await listContainers(all: true, labels: [:], context: context)
        var target = try containerSnapshot(containerRecord(.init(
            configuration: configuration, status: .stopped, networks: []
        )))
        if includeAllocatedSelf {
            var attachments: [Attachment] = []
            for network in configuration.networks {
                let resource = try await networkClient.get(id: network.network)
                let hostname = network.options.hostname
                guard let allocated = try await networkAllocationClient.lookup(
                    network: network.network, plugin: resource.spec.driver, hostname: hostname
                ), allocated.network == network.network, allocated.hostname == hostname
                else {
                    throw DevContainerError(
                        .runtimeUnavailable,
                        message: "Bootstrapped network allocation is unavailable"
                    )
                }
                attachments.append(allocated)
            }
            target.networkAddresses = Self.networkAddresses(attachments)
        }
        let peers = inventory.filter { $0.state == .running && $0.runtimeID.rawValue != configuration.id }
        let hosts = Self.managedHosts(target: target, containers: peers + [target])
        let current = try await inventoryClient.get(id: configuration.id)
        guard current.configuration.creationDate == identity.createdAt,
              try managedNetworkHostsIdentity(configuration: current.configuration) == identity
        else { throw DevContainerError(.conflict, message: "Managed hosts container changed during preparation") }
        try context.checkActive()
        try managedNetworkHosts.update(identity: identity) { Self.replacingManagedHosts(in: $0, with: hosts) }
    }

    func updateMountedNetworkHosts(
        target: DevContainerModel.ContainerSnapshot, hosts: String, context: RuntimeRequestContext
    ) async throws -> Bool {
        guard target.spec.labels[Self.managedNetworkHostsLabel] != nil else { return false }
        let native = try await inventoryClient.get(id: target.runtimeID.rawValue)
        guard native.status == .running, native.startedDate == target.startedAt,
              native.configuration.creationDate == target.createdAt,
              native.configuration.labels[Self.managedNetworkHostsLabel] == target.spec
              .labels[Self.managedNetworkHostsLabel],
              let identity = try managedNetworkHostsIdentity(configuration: native.configuration)
        else { throw DevContainerError(.conflict, message: "Managed hosts target incarnation changed") }
        try context.checkActive()
        try managedNetworkHosts.update(identity: identity) { Self.replacingManagedHosts(in: $0, with: hosts) }
        return true
    }

    func removeManagedHostsAfterNativeDeletion(configuration: ContainerConfiguration) async throws {
        guard let identity = try managedNetworkHostsIdentity(configuration: configuration) else { return }
        try await removeManagedHostsAfterNativeDeletion(identity: identity)
    }

    private func removeManagedHostsAfterNativeDeletion(identity: ManagedNetworkHostsStore.Identity) async throws {
        if let store = metadataStore as? any RuntimeCreationStore,
           try await store.pendingContainerCreation(id: identity.runtimeID) != nil
        {
            // A late native create may still need this exact backing file.
            throw DevContainerError(.conflict, message: "Native creation remains unresolved; hosts retained")
        }
        do {
            _ = try await inventoryClient.get(id: identity.runtimeID)
        } catch {
            guard directAPIError(error, operation: "verify native removal").code == .notFound else { throw error }
            try managedNetworkHosts.remove(identity: identity)
            return
        }
        throw DevContainerError(.conflict, message: "Native identity is still present after deletion; hosts retained")
    }

    func recoverRemovedManagedContainer(id: String, context: RuntimeRequestContext) async throws -> Bool {
        try context.checkActive()
        guard let metadataStore else { return false }
        let records = try await metadataStore.listContainerMetadata()
        let matches = records.filter {
            $0.runtimeID.rawValue == id || $0.dockerID.rawValue == id || $0.spec.name == id
        }
        guard matches.count == 1, let metadata = matches.first,
              let value = metadata.spec.labels[Self.managedNetworkHostsLabel]
        else { return false }
        guard let operationID = UUID(uuidString: value) else {
            throw DevContainerError(.stateCorruption, message: "Retained hosts ownership is invalid")
        }
        let identity = ManagedNetworkHostsStore.Identity(
            operationID: operationID, runtimeID: metadata.runtimeID.rawValue, createdAt: metadata.createdAt
        )
        try await removeManagedHostsAfterNativeDeletion(identity: identity)
        try context.checkActive()
        await portForwarding.stop(containerID: identity.runtimeID)
        try await metadataStore.removeContainerMetadata(id: identity.runtimeID)
        discardContainerState(id: identity.runtimeID, dockerID: metadata.dockerID.rawValue, name: metadata.spec.name)
        await signalEventPollers()
        return true
    }

    /// Plan before the journal callback; allocate only after its durable intent.
    func prepareManagedNetworkHosts(
        configuration: inout ContainerConfiguration, spec: ContainerSpec
    ) throws -> ManagedNetworkHostsStore.Identity? {
        guard spec.labels[Self.managedNetworkHostsLabel] == nil,
              configuration.labels[Self.managedNetworkHostsLabel] == nil
        else {
            throw DevContainerError(.invalidRequest, message: "Managed network hosts label is runtime-owned")
        }
        guard Self.nativeComposeServiceName(labels: spec.labels) != nil else { return nil }
        let identity = ManagedNetworkHostsStore.Identity(
            operationID: UUID(), runtimeID: configuration.id, createdAt: configuration.creationDate
        )
        configuration.labels[Self.managedNetworkHostsLabel] = identity.operationID.uuidString
        configuration.mounts.append(.virtiofs(
            source: managedNetworkHosts.fileURL(for: identity).path,
            destination: "/etc/hosts", options: ["ro"]
        ))
        return identity
    }

    func managedNetworkHostsIdentity(
        configuration: ContainerConfiguration
    ) throws -> ManagedNetworkHostsStore.Identity? {
        guard let value = configuration.labels[Self.managedNetworkHostsLabel] else { return nil }
        guard let operationID = UUID(uuidString: value) else {
            throw DevContainerError(.stateCorruption, message: "Managed network hosts operation identity is invalid")
        }
        let identity = ManagedNetworkHostsStore.Identity(
            operationID: operationID, runtimeID: configuration.id, createdAt: configuration.creationDate
        )
        let mounts = configuration.mounts.filter { $0.destination == "/etc/hosts" }
        guard mounts.count == 1, let mount = mounts.first,
              mount.isVirtiofs, mount.options.readonly,
              mount.source == managedNetworkHosts.fileURL(for: identity).path
        else {
            throw DevContainerError(.stateCorruption, message: "Managed network hosts native mount provenance changed")
        }
        return identity
    }
}
