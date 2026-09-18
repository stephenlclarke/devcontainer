// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerAPIClient
import ContainerizationOCI
import ContainerResource
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

protocol AppleImageIdentityClient: Sendable {
    func configurationDigest(reference: String, descriptor: Data, platform: Data) async throws -> String
    func deleteNamedReference(_ reference: String) async throws
}

struct LiveAppleImageIdentityClient: AppleImageIdentityClient {
    func deleteNamedReference(_ reference: String) async throws {
        // Named-reference removal is not a compare-and-delete by config ID.
        // Keep unreferenced content for explicit, separately authorized GC.
        try await ClientImage.delete(reference: reference, garbageCollect: false)
    }

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
    var descriptor: Data?
    var platform: Data?

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
    func removeNamedImage(reference: String, force: Bool, context: RuntimeRequestContext) async throws {
        do {
            // The normal visible inventory excludes the provider's protected
            // infrastructure images. Resolve its exact native name without
            // substituting a mutable alias for a digest-addressed request.
            let image = try await resolvedImage(reference: reference, context: context)
            try context.checkActive()
            try await imageIdentityClient.deleteNamedReference(image.nativeReference)
        } catch {
            let mapped = directAPIError(error, operation: "image delete")
            if force, mapped.code == .notFound {
                return
            }
            throw mapped
        }
    }

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
            if let manifestDigest = variant["digest"] as? String {
                // The native inventory exposes the selected manifest separately
                // from its index and config. Preserve that repository-qualified
                // identity in Docker inspection rather than reporting only tags.
                let repositoryDigest = try Self.repositoryDigestReference(reference, digest: manifestDigest)
                snapshot.references = Array(Set(snapshot.references + [repositoryDigest])).sorted()
            }
            try images.append(ResolvedAppleImage(
                snapshot: snapshot, nativeReference: reference, nativeDigest: nativeDigest,
                manifestDigest: variant["digest"] as? String,
                descriptor: JSONSerialization.data(withJSONObject: descriptor),
                platform: JSONSerialization.data(withJSONObject: platform)
            ))
        }
        try context.checkActive()
        return images
    }

    static func validImageDigest(_ value: String) -> Bool {
        value.hasPrefix("sha256:") && value.count == 71
            && value.dropFirst(7).allSatisfy { "0123456789abcdef".contains($0) }
    }

    static func repositoryDigestReference(_ reference: String, digest: String) throws -> String {
        var repository = String(reference.split(
            separator: "@", maxSplits: 1, omittingEmptySubsequences: false
        )[0])
        if let colon = repository.lastIndex(of: ":"),
           colon > (repository.lastIndex(of: "/") ?? repository.startIndex)
        {
            repository.removeSubrange(colon...)
        }
        guard !repository.isEmpty, validImageDigest(digest) else {
            throw DevContainerError(
                .providerProtocolMismatch, message: "Image repository digest is invalid"
            )
        }
        return repository + "@" + digest
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
