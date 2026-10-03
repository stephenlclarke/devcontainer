// Copyright 2026 container-engine-api project authors. SPDX-License-Identifier: Apache-2.0

// Adapted from container-engine-api 40436017e1e93012b8dab7cfc3c79783538065c3 for the Devcontainer Docker frontend.
import Foundation

/// Dispatch timers permit cancellation from any thread; the event handler only
/// accesses the lifetime's locked state and never retains a duplex connection.
final class DockerFrontendRequestDeadline: @unchecked Sendable {
    private let timer: any DispatchSourceTimer

    init(lifetime: DockerFrontendRequestLifetime, timeoutSeconds: Int) {
        timer = DispatchSource.makeTimerSource(queue: .global())
        timer.schedule(deadline: .now() + .seconds(timeoutSeconds))
        timer.setEventHandler { lifetime.interrupt(.deadlineExceeded) }
        timer.resume()
    }

    func cancel() {
        timer.cancel()
    }

    deinit { cancel() }
}
