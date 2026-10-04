// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerizationError
import ContainerResource
import Foundation

/// Decode identity only: enhanced runtime attachments need not fit stock schemas.
struct AppleContainerIdentity: Decodable, Sendable {
    let id: String
    let creationDate: Date
    let labels: [String: String]
    let image: ImageIdentity
    var startedDate: Date?

    struct ImageIdentity: Decodable, Sendable {
        let reference: String
        let descriptor: Descriptor
    }

    struct Descriptor: Decodable, Sendable {
        let digest: String
    }

    private struct Record: Decodable {
        let configuration: AppleContainerIdentity
        let startedDate: Date?
    }

    init(_ configuration: ContainerConfiguration, startedDate: Date? = nil) {
        id = configuration.id
        creationDate = configuration.creationDate
        labels = configuration.labels
        image = ImageIdentity(
            reference: configuration.image.reference,
            descriptor: Descriptor(digest: configuration.image.descriptor.digest)
        )
        self.startedDate = startedDate
    }

    static func decode(_ data: Data, id: String) throws -> Self {
        let records = try JSONDecoder().decode([Record].self, from: data)
        guard !records.isEmpty else {
            throw ContainerizationError(.notFound, message: "Container identity is absent")
        }
        guard records.count == 1, let record = records.first, record.configuration.id == id else {
            throw ContainerizationError(.invalidState, message: "Ambiguous container identity response")
        }
        var identity = record.configuration
        identity.startedDate = record.startedDate
        return identity
    }
}

/// The native image proof fields without decoding provider-specific network attachments.
struct AppleContainerImageAuthority: Sendable {
    let identity: AppleContainerIdentity
    let descriptor: Data
    let platform: Data

    init(_ configuration: ContainerConfiguration, startedDate: Date? = nil) throws {
        identity = AppleContainerIdentity(configuration, startedDate: startedDate)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        descriptor = try encoder.encode(configuration.image.descriptor)
        platform = try encoder.encode(configuration.platform)
    }

    static func decode(_ data: Data, id: String) throws -> Self {
        let identity = try AppleContainerIdentity.decode(data, id: id)
        guard let records = try JSONSerialization.jsonObject(with: data) as? [[String: Any]],
              records.count == 1,
              let configuration = records[0]["configuration"] as? [String: Any],
              let image = configuration["image"] as? [String: Any],
              let descriptor = image["descriptor"] as? [String: Any],
              let platform = configuration["platform"] as? [String: Any]
        else {
            throw ContainerizationError(.invalidState, message: "Container image authority is incomplete")
        }
        return try Self(
            identity: identity,
            descriptor: JSONSerialization.data(withJSONObject: descriptor, options: [.sortedKeys]),
            platform: JSONSerialization.data(withJSONObject: platform, options: [.sortedKeys])
        )
    }

    private init(identity: AppleContainerIdentity, descriptor: Data, platform: Data) {
        self.identity = identity
        self.descriptor = descriptor
        self.platform = platform
    }

    func matches(_ other: Self) -> Bool {
        identity.id == other.identity.id
            && identity.creationDate == other.identity.creationDate
            && identity.labels == other.identity.labels
            && identity.image.reference == other.identity.image.reference
            && identity.image.descriptor.digest == other.identity.image.descriptor.digest
            && identity.startedDate == other.identity.startedDate
            && descriptor == other.descriptor
            && platform == other.platform
    }
}
