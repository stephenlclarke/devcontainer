// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerResource
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

extension AppleContainerRuntime {
    func preciseContainerRecord(
        _ value: [String: Any], context: RuntimeRequestContext
    ) async throws -> AppleContainerRecord {
        guard let id = value["id"] as? String,
              let configuration = value["configuration"] as? [String: Any]
        else {
            throw DevContainerError(.providerProtocolMismatch, message: "invalid Apple container record")
        }
        let labels = configuration["labels"] as? [String: String] ?? [:]
        let dockerID = labels[Self.dockerIDLabel] ?? id
        guard useDirectContainerAPI,
              metadataStore != nil || requestedContainers[id] != nil
              || requestedContainers[dockerID] != nil
        else {
            return try await verifiedContainerRecord(value, context: context)
        }
        try context.checkActive()
        let native: AppleContainerIdentity
        do {
            native = try await inventoryClient.identity(id: id)
        } catch {
            throw directAPIError(error, operation: "precise container identity")
        }
        try context.checkActive()
        let image = configuration["image"] as? [String: Any]
        let status = value["status"] as? [String: Any]
        guard native.id == id,
              native.labels == labels,
              image?["reference"] as? String == native.image.reference,
              (image?["descriptor"] as? [String: Any])?["digest"] as? String == native.image.descriptor.digest,
              let encodedDate = configuration["creationDate"] as? String,
              Self.matchesEncodedDate(encodedDate, native: native.creationDate),
              Self.matchesEncodedStartDate(status?["startedDate"], native: native.startedDate)
        else {
            throw DevContainerError(.conflict, message: "Container identity changed during CLI inventory")
        }
        let configurationDigest = try await preciseConfigurationDigest(
            id: id, labels: labels, native: native, context: context
        )
        var record = try containerRecord(value, configurationDigest: configurationDigest)
        // Never loosen incarnation matching to a one-second tolerance: two
        // replacements or process generations can occupy the same encoded
        // second. Preserve CLI-only enhanced fields while restoring both
        // precise native timestamps used by attachment ownership checks.
        record.createdAt = native.creationDate
        record.startedAt = native.startedDate
        return record
    }

    private func preciseConfigurationDigest(
        id: String, labels: [String: String], native: AppleContainerIdentity,
        context: RuntimeRequestContext
    ) async throws -> String? {
        guard let original = labels[Self.composeImageReferenceLabel], Self.validImageDigest(original) else {
            return nil
        }
        let authority: AppleContainerImageAuthority
        do {
            authority = try await inventoryClient.imageAuthority(id: id)
        } catch {
            throw directAPIError(error, operation: "precise Compose image identity")
        }
        guard authority.identity.id == native.id,
              authority.identity.creationDate == native.creationDate,
              authority.identity.labels == native.labels,
              authority.identity.image.reference == native.image.reference,
              authority.identity.image.descriptor.digest == native.image.descriptor.digest,
              authority.identity.startedDate == native.startedDate
        else {
            throw DevContainerError(.conflict, message: "Container identity changed during Compose image verification")
        }
        try context.checkActive()
        let proved = try await imageIdentityClient.configurationIdentity(
            reference: authority.identity.image.reference,
            descriptor: authority.descriptor, platform: authority.platform
        )
        try context.checkActive()
        guard Self.validImageDigest(proved.digest) else {
            throw DevContainerError(.providerProtocolMismatch, message: "OCI configuration has invalid digest")
        }
        let current = try await inventoryClient.imageAuthority(id: id)
        try context.checkActive()
        guard authority.matches(current) else {
            throw DevContainerError(.conflict, message: "Compose image verification observed a new incarnation")
        }
        return proved.digest
    }

    /// Resolve a bare Compose config ID only from this native container's
    /// descriptor and selected platform, before metadata can project an image.
    func verifiedContainerRecord(
        _ value: ContainerResource.ContainerSnapshot,
        context: RuntimeRequestContext
    ) async throws -> AppleContainerRecord {
        let configuration = value.configuration
        guard let original = configuration.labels[Self.composeImageReferenceLabel],
              Self.validImageDigest(original)
        else { return try containerRecord(value) }
        let digest = try await nativeConfigurationDigest(value, context: context)
        let current: ContainerResource.ContainerSnapshot
        do {
            current = try await inventoryClient.get(id: value.id)
        } catch {
            throw directAPIError(error, operation: "current Compose container identity")
        }
        try context.checkActive()
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let sameDescriptor = try encoder.encode(current.configuration.image.descriptor)
            == encoder.encode(configuration.image.descriptor)
        let samePlatform = try encoder.encode(current.configuration.platform)
            == encoder.encode(configuration.platform)
        guard current.id == value.id,
              current.configuration.creationDate == configuration.creationDate,
              current.configuration.labels == configuration.labels,
              current.configuration.image.reference == configuration.image.reference,
              sameDescriptor, samePlatform,
              current.startedDate == value.startedDate
        else {
            throw DevContainerError(.conflict, message: "Compose image verification observed a new incarnation")
        }
        return try containerRecord(value, configurationDigest: digest)
    }

    /// A planned configuration has no native incarnation to re-read. Its image
    /// projection is used only for pre-process hosts preparation, not adoption.
    func plannedContainerRecord(
        _ value: ContainerResource.ContainerSnapshot,
        context: RuntimeRequestContext
    ) async throws -> AppleContainerRecord {
        guard let original = value.configuration.labels[Self.composeImageReferenceLabel],
              Self.validImageDigest(original)
        else { return try containerRecord(value) }
        let digest = try await nativeConfigurationDigest(value, context: context)
        return try containerRecord(value, configurationDigest: digest)
    }

    private func nativeConfigurationDigest(
        _ value: ContainerResource.ContainerSnapshot, context: RuntimeRequestContext
    ) async throws -> String {
        try context.checkActive()
        let configuration = value.configuration
        let identity = try await imageIdentityClient.configurationIdentity(
            reference: configuration.image.reference,
            descriptor: JSONEncoder().encode(configuration.image.descriptor),
            platform: JSONEncoder().encode(configuration.platform)
        )
        try context.checkActive()
        guard Self.validImageDigest(identity.digest) else {
            throw DevContainerError(.providerProtocolMismatch, message: "OCI configuration has invalid digest")
        }
        return identity.digest
    }

    func verifiedContainerRecord(
        _ value: [String: Any], context: RuntimeRequestContext
    ) async throws -> AppleContainerRecord {
        let configuration = value["configuration"] as? [String: Any]
        let labels = configuration?["labels"] as? [String: String] ?? [:]
        guard let original = labels[Self.composeImageReferenceLabel],
              Self.validImageDigest(original)
        else { return try containerRecord(value) }
        guard let image = configuration?["image"] as? [String: Any],
              let reference = image["reference"] as? String,
              let descriptor = image["descriptor"] as? [String: Any],
              let platform = configuration?["platform"] as? [String: Any]
        else {
            throw DevContainerError(.providerProtocolMismatch, message: "Compose image lacks native OCI identity")
        }
        try context.checkActive()
        let identity = try await imageIdentityClient.configurationIdentity(
            reference: reference,
            descriptor: JSONSerialization.data(withJSONObject: descriptor),
            platform: JSONSerialization.data(withJSONObject: platform)
        )
        try context.checkActive()
        return try containerRecord(value, configurationDigest: identity.digest)
    }

    private static func matchesEncodedStartDate(_ value: Any?, native: Date?) -> Bool {
        guard let native else {
            return value == nil || value is NSNull
        }
        guard let value = value as? String else { return false }
        return matchesEncodedDate(value, native: native)
    }

    private static func matchesEncodedDate(_ value: String, native: Date) -> Bool {
        if value.contains(".") {
            guard let observed = date(value) else { return false }
            return sameContainerIncarnation(metadataCreatedAt: native, observedCreatedAt: observed)
        }
        return ISO8601DateFormatter().string(from: native) == value
    }
}
