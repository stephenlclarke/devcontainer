// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

/// Commit source records before publication, and capture history plus live
/// registration atomically under the same output ordering boundary.
final class AppleContainerAttachmentState: @unchecked Sendable {
    typealias Frames = AsyncThrowingStream<RuntimeIOFrame, any Error>
    private let lock = NSLock()
    private var subscribers: [UUID: AppleContainerSubscription] = [:]
    private var result: Result<Int32, any Error>?
    private var journal: (any RuntimeContainerOutputJournal)?
    private let requiresJournal: Bool

    init(requiresJournal: Bool = false) {
        self.requiresJournal = requiresJournal
    }

    func installJournal(_ journal: any RuntimeContainerOutputJournal) throws {
        try lock.withLock {
            guard result == nil, self.journal == nil else {
                try journal.finish(complete: false)
                throw CancellationError()
            }
            self.journal = journal
        }
    }

    func requireCaptureReady() throws {
        try lock.withLock {
            guard !requiresJournal || journal != nil else {
                throw DevContainerError(.conflict, message: "Container output capture is not prepared")
            }
        }
    }

    func prepare(
        history: Bool, live: Bool, context: RuntimeRequestContext
    ) throws -> (Frames?, AppleContainerSubscription?) {
        let subscription = live ? AppleContainerSubscription() : nil
        let saved: Frames? = try lock.withLock {
            try context.checkActive()
            let saved: Frames?
            if history {
                guard let journal else {
                    throw DevContainerError(
                        .unsupportedCapability,
                        message: "Source-aware output capture is unavailable"
                    )
                }
                saved = try journal.captureLogHistory(context: context)
            } else {
                saved = nil
            }
            if let subscription, result == nil {
                subscribers[subscription.id] = subscription
            }
            return saved
        }
        // Complete outside the lock: cancellation callbacks may reenter controls.
        if let subscription, let completed = lock.withLock({ result }) {
            subscription.finish(completed)
        }
        return (saved, subscription)
    }

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
        var failed: [(AppleContainerSubscription, any Error)] = []
        lock.withLock {
            guard result == nil else { return }
            do {
                try journal?.append(frame)
                for subscription in subscribers.values {
                    switch subscription.continuation.yield(frame) {
                    case .enqueued: break
                    case .terminated: failed.append((subscription, CancellationError()))
                    default:
                        failed.append((subscription, DevContainerError(
                            .runtimeUnavailable, message: "Container attachment output buffer exhausted"
                        )))
                    }
                }
                for (subscription, _) in failed {
                    subscribers.removeValue(forKey: subscription.id)
                }
            } catch {
                result = .failure(error)
                try? journal?.finish(complete: false)
                failed = subscribers.values.map { ($0, error) }
                subscribers.removeAll()
            }
        }
        for (subscription, error) in failed {
            subscription.finish(.failure(error))
        }
    }

    func requireActive(_ id: UUID) throws {
        guard isActive(id) else {
            throw DevContainerError(.conflict, message: "Container attachment is closed")
        }
    }

    func endSource(_ channel: RuntimeIOChannel) {
        var failures: [(AppleContainerSubscription, any Error)] = []
        lock.withLock {
            guard result == nil else { return }
            do {
                try journal?.endSource(channel)
            } catch {
                result = .failure(error)
                try? journal?.finish(complete: false)
                failures = subscribers.values.map { ($0, error) }
                subscribers.removeAll()
            }
        }
        for (subscription, error) in failures {
            subscription.finish(.failure(error))
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
        var completion = result
        let completed: [AppleContainerSubscription] = lock.withLock {
            guard self.result == nil else { return [] }
            do {
                if case .success = result {
                    try journal?.finish(complete: true)
                } else {
                    try journal?.finish(complete: false)
                }
            } catch { completion = .failure(error) }
            self.result = completion
            defer { subscribers.removeAll() }
            return Array(subscribers.values)
        }
        for subscriber in completed {
            subscriber.finish(completion)
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
