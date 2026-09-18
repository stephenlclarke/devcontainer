// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

/// Owns shutdown ordering without changing the server's startup rollback contract.
struct ServiceLifecycle: Sendable {
    enum Completion: Sendable {
        case serverClosed
        case signal(Int32)
    }

    let start: @Sendable () async throws -> Void
    let wait: @Sendable () async throws -> Void
    let shutdownRuntime: @Sendable () async -> Void
    let shutdownServer: @Sendable () async throws -> Void

    func run(
        signals: AsyncStream<Int32>,
        onSignal: (Int32) -> Void
    ) async throws {
        do {
            try await start()
        } catch {
            // Server startup owns its partial-start rollback; the runtime has
            // already restored resources and must be released separately.
            await shutdownRuntime()
            throw error
        }
        try await withThrowingTaskGroup(of: Completion.self) { group in
            group.addTask {
                try await wait()
                return .serverClosed
            }
            group.addTask {
                for await signal in signals {
                    return .signal(signal)
                }
                return .serverClosed
            }
            var failure: (any Error)?
            do {
                if case let .signal(signal) = try await group.next() {
                    onSignal(signal)
                }
            } catch {
                failure = error
            }
            // Cleanup must happen inside the group before waiting for its
            // children: the server waiter may need shutdown to finish.
            await shutdownRuntime()
            do {
                try await shutdownServer()
            } catch {
                failure = failure ?? error
            }
            group.cancelAll()
            while !group.isEmpty {
                do {
                    _ = try await group.next()
                } catch is CancellationError {
                    // Cancellation is expected after the first completion.
                } catch {
                    failure = failure ?? error
                }
            }
            if let failure {
                throw failure
            }
            try Task.checkCancellation()
        }
    }
}
