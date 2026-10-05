//===----------------------------------------------------------------------===//
// Copyright 2026 devcontainer project authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//===----------------------------------------------------------------------===//

import DevContainerModel
import Foundation

actor AppleEventPoller {
    typealias SnapshotProvider = @Sendable (RuntimeRequestContext) async throws
        -> [String: ContainerSnapshot]

    private struct Subscription {
        let continuation: AsyncThrowingStream<RuntimeEvent, any Error>.Continuation
        let since: Date?
        let until: Date?
        let labels: [String: String]
        var previous: [String: ContainerSnapshot]
        var sequence: Int64
    }

    private let snapshotProvider: SnapshotProvider
    private var latestSnapshot: [String: ContainerSnapshot]?
    private var initialSnapshotTask: Task<[String: ContainerSnapshot], Error>?
    private var subscriptions: [UUID: Subscription] = [:]
    private var pollingTask: Task<Void, Never>?
    private var pollingID: UUID?
    private var changeGeneration: UInt64 = 0
    private var changeWaiters: [UUID: CheckedContinuation<Void, Never>] = [:]

    init(snapshotProvider: @escaping SnapshotProvider) {
        self.snapshotProvider = snapshotProvider
    }

    func subscribe(
        continuation: AsyncThrowingStream<RuntimeEvent, any Error>.Continuation,
        since: Date?,
        until: Date?,
        labels: [String: String],
        context: RuntimeRequestContext
    ) async throws -> UUID {
        let identifier = UUID()
        let initial = try await snapshot(context: context)
        subscriptions[identifier] = Subscription(
            continuation: continuation,
            since: since,
            until: until,
            labels: labels,
            previous: Self.filtered(initial, labels: labels),
            sequence: Int64(Date().timeIntervalSince1970 * 1_000_000)
        )
        startPollingIfNeeded()
        return identifier
    }

    func unsubscribe(_ identifier: UUID) {
        subscriptions.removeValue(forKey: identifier)
        if subscriptions.isEmpty {
            stopPolling()
        }
    }

    func shutdown() {
        for subscription in subscriptions.values {
            subscription.continuation.finish()
        }
        subscriptions.removeAll()
        stopPolling()
    }

    func notifyChanged() {
        changeGeneration &+= 1
        let waiters = changeWaiters.values
        changeWaiters.removeAll(keepingCapacity: true)
        for waiter in waiters {
            waiter.resume()
        }
    }

    private func snapshot(
        context: RuntimeRequestContext
    ) async throws -> [String: ContainerSnapshot] {
        if let latestSnapshot {
            return latestSnapshot
        }
        if let initialSnapshotTask {
            return try await initialSnapshotTask.value
        }
        let provider = snapshotProvider
        let task = Task {
            try await Self.coherentSnapshot(using: provider, context: context)
        }
        initialSnapshotTask = task
        do {
            let snapshot = try await task.value
            latestSnapshot = snapshot
            initialSnapshotTask = nil
            return snapshot
        } catch {
            initialSnapshotTask = nil
            throw error
        }
    }

    /// Inventory spans CLI and native reads. A concurrent lifecycle transition
    /// invalidates the whole observation; retry it without accepting partial rows.
    private static func coherentSnapshot(
        using provider: SnapshotProvider, context: RuntimeRequestContext
    ) async throws -> [String: ContainerSnapshot] {
        for attempt in 1 ... 3 {
            try Task.checkCancellation()
            try context.checkActive()
            do {
                let snapshot = try await provider(context)
                try Task.checkCancellation()
                try context.checkActive()
                return snapshot
            } catch let error as DevContainerError
                where error.code == .conflict
                && error.message == "Container identity changed during CLI inventory"
                && attempt < 3
            {
                try Task.checkCancellation()
                try context.checkActive()
                try await Task.sleep(nanoseconds: 20_000_000)
            }
        }
        preconditionFailure("The final snapshot attempt must return or throw")
    }

    private func startPollingIfNeeded() {
        guard pollingTask == nil else {
            return
        }
        let identifier = UUID()
        pollingID = identifier
        let observedGeneration = changeGeneration
        pollingTask = Task { [weak self] in
            await self?.poll(
                identifier: identifier,
                observedGeneration: observedGeneration
            )
        }
    }

    private func stopPolling() {
        pollingTask?.cancel()
        pollingTask = nil
        pollingID = nil
        latestSnapshot = nil
    }

    private func poll(
        identifier: UUID,
        observedGeneration initialGeneration: UInt64
    ) async {
        var observedGeneration = initialGeneration
        defer {
            if pollingID == identifier {
                pollingTask = nil
                pollingID = nil
                if subscriptions.isEmpty {
                    latestSnapshot = nil
                } else {
                    startPollingIfNeeded()
                }
            }
        }
        do {
            while !Task.isCancelled {
                guard !activeSubscriptionIDs(at: Date()).isEmpty else {
                    return
                }
                await waitForChange(after: observedGeneration)
                guard
                    !Task.isCancelled,
                    !subscriptions.isEmpty
                else {
                    continue
                }
                // The subscription set can change while the poller sleeps.
                // Re-evaluate immediately before reading the inventory so a
                // late subscriber sees this snapshot and an expired one never
                // receives an event beyond its requested deadline.
                let active = activeSubscriptionIDs(at: Date())
                guard !active.isEmpty else {
                    return
                }
                let current = try await Self.coherentSnapshot(
                    using: snapshotProvider, context: RuntimeRequestContext()
                )
                let timestamp = Date()
                let currentActive = activeSubscriptionIDs(at: timestamp)
                guard !currentActive.isEmpty else {
                    return
                }
                latestSnapshot = current
                publish(current, to: currentActive, timestamp: timestamp)
                observedGeneration = changeGeneration
            }
        } catch is CancellationError {
            return
        } catch {
            for subscription in subscriptions.values {
                subscription.continuation.finish(throwing: error)
            }
            subscriptions.removeAll()
        }
    }

    private func waitForChange(after observedGeneration: UInt64) async {
        guard changeGeneration == observedGeneration else {
            return
        }
        let identifier = UUID()
        await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                guard changeGeneration == observedGeneration else {
                    continuation.resume()
                    return
                }
                changeWaiters[identifier] = continuation
                scheduleChangeWaiterTimeout(identifier)
            }
        } onCancel: {
            Task {
                await self.resumeChangeWaiter(identifier)
            }
        }
    }

    private func scheduleChangeWaiterTimeout(_ identifier: UUID) {
        Task {
            try? await Task.sleep(for: .milliseconds(200))
            self.resumeChangeWaiter(identifier)
        }
    }

    private func resumeChangeWaiter(_ identifier: UUID) {
        changeWaiters.removeValue(forKey: identifier)?.resume()
    }

    private func activeSubscriptionIDs(at timestamp: Date) -> [UUID] {
        var active: [UUID] = []
        var expired: [UUID] = []
        for (identifier, subscription) in subscriptions {
            guard subscription.until.map({ timestamp < $0 }) ?? true else {
                expired.append(identifier)
                continue
            }
            active.append(identifier)
        }
        for identifier in expired {
            subscriptions.removeValue(forKey: identifier)?.continuation.finish()
        }
        return active
    }

    private func publish(
        _ snapshot: [String: ContainerSnapshot],
        to identifiers: [UUID],
        timestamp: Date
    ) {
        for identifier in identifiers {
            guard var subscription = subscriptions[identifier] else {
                continue
            }
            let current = Self.filtered(snapshot, labels: subscription.labels)
            let events = AppleContainerRuntime.lifecycleEvents(
                previous: subscription.previous,
                current: current,
                timestamp: timestamp,
                since: subscription.since,
                sequence: &subscription.sequence
            )
            for event in events {
                subscription.continuation.yield(event)
            }
            subscription.previous = current
            subscriptions[identifier] = subscription
        }
    }

    private static func filtered(
        _ snapshots: [String: ContainerSnapshot],
        labels: [String: String]
    ) -> [String: ContainerSnapshot] {
        var filtered: [String: ContainerSnapshot] = [:]
        filtered.reserveCapacity(snapshots.count)
        for (identifier, snapshot) in snapshots {
            guard labels.allSatisfy({ key, expected in
                guard let actual = snapshot.spec.labels[key] else {
                    return false
                }
                return expected.isEmpty || actual == expected
            }) else {
                continue
            }
            filtered[identifier] = snapshot
        }
        return filtered
    }
}
