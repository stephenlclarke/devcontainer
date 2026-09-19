// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
import Foundation

/// Both halves are prepared at one provider-owned output ordering boundary.
/// History ends immediately before live output begins; neither half may overlap.
public struct RuntimeContainerAttachment: Sendable {
    public let history: AsyncThrowingStream<RuntimeIOFrame, any Error>?
    public let session: (any RuntimeProcessSession)?

    public init(
        history: AsyncThrowingStream<RuntimeIOFrame, any Error>?, session: (any RuntimeProcessSession)?
    ) {
        self.history = history
        self.session = session
    }
}
