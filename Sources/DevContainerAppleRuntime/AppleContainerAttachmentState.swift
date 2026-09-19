// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
import Foundation

/// Broadcast only live output. Historical log replay is a separate API operation.
/// A slow attachment fails explicitly rather than losing bytes or stopping init.
final class AppleContainerAttachmentState: @unchecked Sendable {
    typealias Frames = AsyncThrowingStream<RuntimeIOFrame, any Error>
    private let lock = NSLock()
    private var subscribers: [UUID: AppleContainerSubscription] = [:]
    private var result: Result<Int32, any Error>?

    var isFinished: Bool {
        lock.withLock { result != nil }
    }

    func subscribe() -> AppleContainerSubscription {
        let subscription = AppleContainerSubscription()
        let completed: Result<Int32, any Error>? = lock.withLock {
            if let result {
                return result
            }
            subscribers[subscription.id] = subscription
            return nil
        }
        if let completed {
            subscription.finish(completed)
        }
        return subscription
    }

    func publish(_ frame: RuntimeIOFrame) {
        let targets = lock.withLock { Array(subscribers.values) }
        for subscription in targets {
            let id = subscription.id
            switch subscription.continuation.yield(frame) {
            case .enqueued: break
            case .terminated: detach(id)
            case .dropped:
                detach(id, error: DevContainerError(
                    .runtimeUnavailable, message: "Container attachment output buffer exhausted"
                ))
            @unknown default:
                detach(id, error: DevContainerError(
                    .runtimeUnavailable, message: "Container attachment output delivery failed"
                ))
            }
        }
    }

    func requireActive(_ id: UUID) throws {
        guard isActive(id) else {
            throw DevContainerError(.conflict, message: "Container attachment is closed")
        }
    }

    func isActive(_ id: UUID) -> Bool {
        lock.withLock { result == nil && subscribers[id] != nil }
    }

    func detach(_ id: UUID, error: any Error = CancellationError()) {
        guard let subscriber = lock.withLock({ subscribers.removeValue(forKey: id) }) else { return }
        subscriber.finish(.failure(error))
    }

    func complete(_ result: Result<Int32, any Error>) {
        let completed: [AppleContainerSubscription] = lock.withLock {
            guard self.result == nil else { return [] }
            self.result = result
            defer { subscribers.removeAll() }
            return Array(subscribers.values)
        }
        for subscriber in completed {
            subscriber.finish(result)
        }
    }
}

/// Retains a client's own terminal result after it leaves the broadcast set.
final class AppleContainerSubscription: @unchecked Sendable {
    let id = UUID()
    let frames: AppleContainerAttachmentState.Frames
    let continuation: AppleContainerAttachmentState.Frames.Continuation
    private let lock = NSLock()
    private var result: Result<Int32, any Error>?
    private var waiters: [CheckedContinuation<Int32, any Error>] = []
    private var operations: [UUID: Task<Void, any Error>] = [:]

    init() {
        // Monitor frames are at most 64 KiB: each subscriber retains at most 16 MiB.
        (frames, continuation) = AppleContainerAttachmentState.Frames.makeStream(
            bufferingPolicy: .bufferingOldest(256)
        )
    }

    func wait() async throws -> Int32 {
        try await withCheckedThrowingContinuation { continuation in
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
    }

    func finish(_ result: Result<Int32, any Error>) {
        typealias Pending = (waiters: [CheckedContinuation<Int32, any Error>], operations: [Task<Void, any Error>])
        let pending: Pending? = lock.withLock {
            guard self.result == nil else { return nil }
            self.result = result
            defer { waiters.removeAll() }
            return (waiters, Array(operations.values))
        }
        guard let pending else { return }
        for operation in pending.operations {
            operation.cancel()
        }
        switch result {
        case .success: continuation.finish()
        case let .failure(error): continuation.finish(throwing: error)
        }
        for waiter in pending.waiters {
            waiter.resume(with: result)
        }
    }

    func perform(_ operation: @escaping @Sendable () async throws -> Void) async throws {
        let id = UUID()
        let task = try lock.withLock {
            guard result == nil else { throw CancellationError() }
            let task = Task { try await operation() }
            operations[id] = task
            return task
        }
        defer { _ = lock.withLock { operations.removeValue(forKey: id) } }
        try await withTaskCancellationHandler {
            try await task.value
        } onCancel: { task.cancel() }
    }
}
