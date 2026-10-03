// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerizationOCI
import DevContainerModel

extension AppleContainerRuntime {
    static let composeImageReferenceLabel = "com.apple.container.compose.image-reference"

    /// Restore only a spelling alias proved by the native creation descriptor.
    /// Do not resolve a mutable tag or replace native platform/config identity.
    static func composeImageReference(
        observed: String, descriptorDigest: String?, labels: [String: String]
    ) throws -> String {
        guard let original = labels[composeImageReferenceLabel] else { return observed }
        guard let parsed = try? Reference.parse(original),
              let digest = parsed.digest, validImageDigest(digest),
              original.filter({ $0 == "@" }).count == 1,
              original.hasSuffix("@" + digest),
              digest == descriptorDigest,
              (try? Reference.parse(observed)) != nil,
              imageRepository(original) == imageRepository(observed)
        else {
            throw DevContainerError(
                .providerProtocolMismatch,
                message: "Compose image reference does not match the native image descriptor"
            )
        }
        return original
    }
}
