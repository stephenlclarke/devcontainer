// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerAPIClient
import ContainerizationOCI
import ContainerResource
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

protocol AppleImageIdentityClient: Sendable {
    func configurationDigest(reference: String, descriptor: Data, platform: Data) async throws -> String
}

struct LiveAppleImageIdentityClient: AppleImageIdentityClient {
    func configurationDigest(reference: String, descriptor: Data, platform: Data) async throws -> String {
        let decoder = JSONDecoder()
        let image = try ClientImage(description: ImageDescription(
            reference: reference,
            descriptor: decoder.decode(Descriptor.self, from: descriptor)
        ))
        // Hashing re-encoded config JSON would produce the wrong Docker ID.
        // Read the digest of the original config blob from its OCI manifest.
        let manifest = try await image.manifest(for: decoder.decode(Platform.self, from: platform))
        return manifest.config.digest
    }
}

struct ResolvedAppleImage {
    var snapshot: ImageSnapshot
    let nativeReference: String
    let nativeDigest: String
    var manifestDigest: String?

    func matches(_ reference: String) -> Bool {
        if snapshot.id == reference || nativeDigest == reference
            || snapshot.references.contains(where: {
                AppleContainerRuntime.equivalentImageReference($0, reference)
            })
        {
            return true
        }
        guard let digest = AppleContainerRuntime.imageDigest(reference),
              digest == nativeDigest || digest == manifestDigest else { return false }
        return AppleContainerRuntime.imageRepository(reference)
            == AppleContainerRuntime.imageRepository(nativeReference)
    }
}

extension AppleContainerRuntime {
    func resolvedImages(context: RuntimeRequestContext) async throws -> [ResolvedAppleImage] {
        try context.checkActive()
        let result = try await command(["image", "list", "--format", "json"])
        try requireSuccess(result, operation: "image list")
        var images: [ResolvedAppleImage] = []
        for value in try parseJSONObjectArray(result.standardOutput) {
            guard var snapshot = imageSnapshot(value),
                  let configuration = value["configuration"] as? [String: Any],
                  let reference = configuration["name"] as? String,
                  let descriptor = configuration["descriptor"] as? [String: Any],
                  let nativeDigest = descriptor["digest"] as? String,
                  let variants = value["variants"] as? [[String: Any]],
                  let variant = variants.first(where: {
                      let platform = $0["platform"] as? [String: Any]
                      return platform?["architecture"] as? String == snapshot.architecture
                          && platform?["os"] as? String == snapshot.operatingSystem
                  }), let platform = variant["platform"] as? [String: Any]
            else {
                throw DevContainerError(
                    .providerProtocolMismatch, message: "Image inventory is missing identity metadata"
                )
            }
            let configDigest = try await imageIdentityClient.configurationDigest(
                reference: reference,
                descriptor: JSONSerialization.data(withJSONObject: descriptor),
                platform: JSONSerialization.data(withJSONObject: platform)
            )
            guard Self.validImageDigest(configDigest), Self.validImageDigest(nativeDigest) else {
                throw DevContainerError(
                    .providerProtocolMismatch, message: "Image inventory returned an invalid digest"
                )
            }
            snapshot.id = configDigest
            images.append(ResolvedAppleImage(
                snapshot: snapshot, nativeReference: reference, nativeDigest: nativeDigest,
                manifestDigest: variant["digest"] as? String
            ))
        }
        try context.checkActive()
        return images
    }

    static func validImageDigest(_ value: String) -> Bool {
        value.hasPrefix("sha256:") && value.count == 71
            && value.dropFirst(7).allSatisfy { "0123456789abcdef".contains($0) }
    }

    static func requireNamedImageMutation(_ reference: String) throws {
        guard !reference.hasPrefix("sha256:"), !reference.contains("@") else {
            // Stock CLI treats a bare config digest as a repository/tag and
            // may fetch it. Do not open that path merely because inspect now
            // resolves Docker IDs. Replace this guard with descriptor-bound
            // native operations, never a mutable-tag substitution.
            throw DevContainerError(
                .unsupportedCapability,
                message: "Digest-addressed image mutations require descriptor-bound native operations"
            )
        }
    }

    static func imageRepository(_ reference: String) -> String {
        var repository = String(reference.split(
            separator: "@", maxSplits: 1, omittingEmptySubsequences: false
        )[0])
        if let colon = repository.lastIndex(of: ":"),
           colon > (repository.lastIndex(of: "/") ?? repository.startIndex)
        {
            repository.removeSubrange(colon...)
        }
        return normalizedImageReference(repository)
    }

    func resolvedImage(reference: String, context: RuntimeRequestContext) async throws -> ResolvedAppleImage {
        let images = try await resolvedImages(context: context)
        guard var image = images.first(where: { $0.matches(reference) }) else {
            throw DevContainerError(.notFound, message: "image \(reference) was not found")
        }
        image.snapshot.references = Array(Set(images.filter { $0.snapshot.id == image.snapshot.id }
                .flatMap(\.snapshot.references))).sorted()
        return image
    }
}
