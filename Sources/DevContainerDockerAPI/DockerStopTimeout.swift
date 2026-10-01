// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
import DevContainerRuntimeSPI

extension DockerRouter {
    static func validateStopTimeout(_ seconds: Int64) throws {
        // Apple's signed CLI value is not an indefinite-wait primitive: a
        // negative sleep can proceed to SIGKILL. Never pass it through.
        guard seconds >= 0, seconds <= Int64(Int32.max) else {
            throw DevContainerError(
                .unsupportedCapability,
                message: "Stop timeout requires zero through \(Int32.max) seconds; indefinite waits are not supported"
            )
        }
    }

    func stopTimeout(
        id: String, requested: String?, context: RuntimeRequestContext
    ) async throws -> Duration {
        let seconds: Int64
        if let requested {
            guard let value = Int64(requested) else {
                throw DevContainerError(.invalidRequest, message: "Invalid stop timeout")
            }
            seconds = value
        } else {
            let snapshot = try await runtime.inspectContainer(id: id, context: context)
            seconds = Int64(snapshot.spec.stopTimeoutSeconds ?? 10)
        }
        try Self.validateStopTimeout(seconds)
        return .seconds(seconds)
    }
}
