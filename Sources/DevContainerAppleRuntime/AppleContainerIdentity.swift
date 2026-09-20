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
