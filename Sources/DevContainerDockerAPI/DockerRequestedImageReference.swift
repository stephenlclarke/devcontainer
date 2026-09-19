// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel

extension DockerRouter {
    func validateRequestedImageReference(
        _ request: DockerCreateContainerRequest, context: RuntimeRequestContext
    ) async throws {
        guard let reference = request.containerImageReference else { return }
        // This is historical caller spelling, not an instruction to resolve a
        // mutable tag or proof that the tag currently names the selected image.
        let allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/:@[]"
        guard !reference.isEmpty, reference.utf8.count <= 1024,
              reference.allSatisfy({ allowed.contains($0) }),
              request.image.hasPrefix("sha256:"), request.image.utf8.count == 71,
              request.image.dropFirst(7).allSatisfy({ "0123456789abcdef".contains($0) }) else {
            throw DevContainerError(.invalidRequest, message: "Image reference display requires an immutable image ID")
        }
        let image = try await runtime.inspectImage(reference: request.image, context: context)
        guard image.id == request.image else {
            throw DevContainerError(
                .providerProtocolMismatch, message: "Resolved image identity changed before creation"
            )
        }
    }
}
