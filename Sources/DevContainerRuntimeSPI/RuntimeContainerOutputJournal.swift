// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
import Foundation

/// A process-generation writer. Call append and captureHistory under the same
/// broker ordering boundary as live publication/subscription. Implementations
/// commit before returning, preserve stream tags, and never silently truncate.
public protocol RuntimeContainerOutputJournal: Sendable {
    func append(_ frame: RuntimeIOFrame) throws
    func captureHistory(context: RuntimeRequestContext) throws -> AsyncThrowingStream<RuntimeIOFrame, any Error>
    func finish(complete: Bool) throws
}

/// Persistent output is keyed by immutable container and creation identity, not
/// by reusable names. An interrupted capture is not a complete history.
public protocol RuntimeContainerOutputStore: RuntimeMetadataStore {
    func beginContainerOutputCapture(snapshot: ContainerSnapshot) async throws -> any RuntimeContainerOutputJournal
    func containerOutputHistory(
        snapshot: ContainerSnapshot, context: RuntimeRequestContext
    ) async throws -> AsyncThrowingStream<RuntimeIOFrame, any Error>
}
