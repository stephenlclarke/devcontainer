// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

extension DockerRouter {
    /// Returning this stream acknowledges a completed registration, not merely
    /// a scheduled task. Clients may start an auto-removed init after headers.
    func preparedContainerWaitStream(
        id: String, condition: String?, context: RuntimeRequestContext
    ) async throws -> AsyncThrowingStream<Data, any Error> {
        guard condition == "next-exit" else {
            return containerWaitStream(id: id, condition: condition, context: context)
        }
        let registration = try await runtime.prepareContainerExitWait(id: id, context: context)
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let snapshot = registration.snapshot
                    let code = try await registration.wait()
                    if snapshot.spec.autoRemove {
                        try await waitForContainerRemoval(id: snapshot.dockerID.rawValue, context: context)
                    }
                    try await reconcileAutomaticRemoval(snapshot)
                    try continuation.yield(DockerJSON.encoder.encode(DockerWaitResponse(statusCode: code)))
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
                await registration.cancel()
            }
            continuation.onTermination = { _ in
                task.cancel()
                Task { await registration.cancel() }
            }
        }
    }
}
