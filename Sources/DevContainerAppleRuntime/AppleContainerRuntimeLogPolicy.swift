// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
import DevContainerRuntimeSPI

extension AppleContainerRuntime {
    private var supportsOutputLogPolicy: Bool {
        useDirectContainerAPI && useDirectProcessAPI && metadataStore is any RuntimeContainerOutputStore
    }

    func effectiveOutputLogFormat(_ requested: ContainerOutputLogFormat?) throws -> ContainerOutputLogFormat? {
        guard requested == nil || supportsOutputLogPolicy else {
            throw DevContainerError(
                .unsupportedCapability,
                message: "json-file logging requires native descriptor capture and durable output storage"
            )
        }
        return supportsOutputLogPolicy ? .jsonFileV1 : nil
    }

    func applyingOutputLogPolicy(to spec: ContainerSpec) throws -> ContainerSpec {
        var spec = spec
        spec.outputLogFormat = try effectiveOutputLogFormat(spec.outputLogFormat)
        return spec
    }

    func requireRetainedOutputLogPolicy(id: String) async throws {
        if let metadata = try await metadataStore?.containerMetadata(id: id), metadata.spec.outputLogFormat != nil {
            _ = try effectiveOutputLogFormat(metadata.spec.outputLogFormat)
        }
    }
}
