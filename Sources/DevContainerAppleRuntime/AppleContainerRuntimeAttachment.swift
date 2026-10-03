// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

public extension AppleContainerRuntime {
    func prepareContainerAttachment(
        id: String, terminal: Bool, history: Bool, live: Bool, context: RuntimeRequestContext
    ) async throws -> RuntimeContainerAttachment {
        let snapshot = try await inspectContainer(id: id, context: context)
        guard snapshot.spec.terminal == terminal else {
            throw DevContainerError(.invalidRequest, message: "Attachment terminal mode does not match the container")
        }
        try await requireCompletedCreation(id: snapshot.runtimeID.rawValue)
        if live {
            guard useDirectProcessAPI else {
                throw DevContainerError(.unsupportedCapability, message: "Live attachment requires direct process APIs")
            }
            return try await prepareContainerIO(snapshot: snapshot, context: context)
                .prepareAttachment(history: history, live: true, context: context)
        }
        guard history else { return RuntimeContainerAttachment(history: nil, session: nil) }
        if let channel = containerIO[snapshot.runtimeID.rawValue], channel.createdAt == snapshot.createdAt {
            try await channel.prepareOutputCapture()
            return try channel.prepareAttachment(history: true, live: false, context: context)
        }
        guard let store = metadataStore as? any RuntimeContainerOutputStore else {
            throw DevContainerError(.unsupportedCapability, message: "Source-aware output history is unavailable")
        }
        return try await RuntimeContainerAttachment(
            history: store.containerLogHistory(snapshot: snapshot, context: context), session: nil
        )
    }
}
