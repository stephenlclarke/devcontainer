// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

/// Native exit authority is independent of output delivery and reader lifetime.
/// This state belongs to exactly one AppleContainerIO process generation.
final class AppleContainerExitState: @unchecked Sendable {
    private let lock = NSLock()
    private var result: Result<Int32, any Error>?
    private var subscribers: [UUID: AppleContainerExitWait] = [:]

    func subscribe(snapshot: ContainerSnapshot) -> (any RuntimeContainerExitWait)? {
        let id = UUID()
        let subscription = AppleContainerExitWait(snapshot: snapshot) { [weak self] in self?.detach(id) }
        return lock.withLock {
            // Registration linearizes against native exit. Later callers must
            // use the next generation rather than replay this result.
            guard result == nil else { return nil }
            subscribers[id] = subscription
            return subscription
        }
    }

    func complete(_ result: Result<Int32, any Error>) {
        let targets: [AppleContainerExitWait] = lock.withLock {
            guard self.result == nil else { return [] }
            self.result = result
            defer { subscribers.removeAll() }
            return Array(subscribers.values)
        }
        for target in targets {
            target.finish(result)
        }
    }

    private func detach(_ id: UUID) {
        let subscription = lock.withLock { subscribers.removeValue(forKey: id) }
        subscription?.finish(.failure(CancellationError()))
    }
}

private final class AppleContainerExitWait: RuntimeContainerExitWait, @unchecked Sendable {
    let snapshot: ContainerSnapshot
    private let lock = NSLock()
    private let detach: @Sendable () -> Void
    private var result: Result<Int32, any Error>?
    private var waiters: [CheckedContinuation<Int32, any Error>] = []

    init(snapshot: ContainerSnapshot, detach: @escaping @Sendable () -> Void) {
        self.snapshot = snapshot
        self.detach = detach
    }

    func wait() async throws -> Int32 {
        try await withTaskCancellationHandler {
            // Install cancellation ownership before checking the caller: even
            // a previously cancelled task must unregister from the generation.
            try Task.checkCancellation()
            return try await withCheckedThrowingContinuation { continuation in
                let completed: Result<Int32, any Error>? = lock.withLock {
                    if let result {
                        return result
                    }
                    waiters.append(continuation)
                    return nil
                }
                if let completed {
                    continuation.resume(with: completed)
                }
            }
        } onCancel: { self.cancel() }
    }

    func cancel() {
        // Completion and detach can race: each local promise resolves once.
        finish(.failure(CancellationError()))
        detach()
    }

    func finish(_ result: Result<Int32, any Error>) {
        let targets: [CheckedContinuation<Int32, any Error>] = lock.withLock {
            guard self.result == nil else { return [] }
            self.result = result
            defer { waiters.removeAll() }
            return waiters
        }
        for target in targets {
            target.resume(with: result)
        }
    }
}
