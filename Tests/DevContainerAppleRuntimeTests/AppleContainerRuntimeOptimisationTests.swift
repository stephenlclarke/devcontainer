//===----------------------------------------------------------------------===//
// Copyright 2026 devcontainer project authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//===----------------------------------------------------------------------===//

@testable import DevContainerAppleRuntime
import DevContainerModel
import Foundation
import Testing

struct AppleContainerRuntimeOptimisationTests {
    @Test
    func `event subscribers share an in-flight initial inventory`() async throws {
        let source = DelayedEventSnapshotSource()
        let poller = AppleEventPoller { _ in
            await source.snapshot()
        }
        let first = AsyncThrowingStream<RuntimeEvent, any Error>.makeStream()
        async let firstIdentifier = poller.subscribe(
            continuation: first.continuation,
            since: nil,
            until: nil,
            labels: [:],
            context: RuntimeRequestContext()
        )

        try await Task.sleep(for: .milliseconds(10))
        let second = AsyncThrowingStream<RuntimeEvent, any Error>.makeStream()
        async let secondIdentifier = poller.subscribe(
            continuation: second.continuation,
            since: nil,
            until: nil,
            labels: [:],
            context: RuntimeRequestContext()
        )

        _ = try await (firstIdentifier, secondIdentifier)
        #expect(await source.snapshotCount() == 1)
        await poller.shutdown()
    }

    @Test
    func `event subscriber startup clears a failed initial inventory`() async {
        let poller = AppleEventPoller { _ in
            throw EventSnapshotError.failed
        }
        let stream = AsyncThrowingStream<RuntimeEvent, any Error>.makeStream()

        await #expect(throws: EventSnapshotError.self) {
            _ = try await poller.subscribe(
                continuation: stream.continuation,
                since: nil,
                until: nil,
                labels: [:],
                context: RuntimeRequestContext()
            )
        }
        await poller.shutdown()
    }

    @Test
    func `event polling filters nonmatching labels across snapshots`() async throws {
        let source = EventSnapshotSource()
        await source.set(snapshot: .fixture)
        let poller = AppleEventPoller { _ in
            await source.snapshot()
        }
        let valueMismatch = AsyncThrowingStream<RuntimeEvent, any Error>.makeStream()
        _ = try await poller.subscribe(
            continuation: valueMismatch.continuation,
            since: nil,
            until: nil,
            labels: ["fixture": "no"],
            context: RuntimeRequestContext()
        )
        let missingLabel = AsyncThrowingStream<RuntimeEvent, any Error>.makeStream()
        _ = try await poller.subscribe(
            continuation: missingLabel.continuation,
            since: nil,
            until: nil,
            labels: ["missing": "yes"],
            context: RuntimeRequestContext()
        )

        #expect(await source.waitForSnapshotCount(atLeast: 2))
        await poller.shutdown()
    }

    @Test
    func `event polling drops subscriptions already past their deadline`() async throws {
        let source = EventSnapshotSource()
        let poller = AppleEventPoller { _ in
            await source.snapshot()
        }
        let stream = AsyncThrowingStream<RuntimeEvent, any Error>.makeStream()
        _ = try await poller.subscribe(
            continuation: stream.continuation,
            since: nil,
            until: .distantPast,
            labels: [:],
            context: RuntimeRequestContext()
        )

        try await Task.sleep(for: .milliseconds(50))

        await poller.shutdown()
    }

    @Test
    func `event subscribers share one inventory poller`() async throws {
        let source = EventSnapshotSource()
        let poller = AppleEventPoller { _ in
            await source.snapshot()
        }
        let first = AsyncThrowingStream<RuntimeEvent, any Error>.makeStream()
        let firstIdentifier = try await poller.subscribe(
            continuation: first.continuation,
            since: nil,
            until: nil,
            labels: ["fixture": "yes"],
            context: RuntimeRequestContext()
        )
        first.continuation.onTermination = { _ in
            Task {
                await poller.unsubscribe(firstIdentifier)
            }
        }
        let second = AsyncThrowingStream<RuntimeEvent, any Error>.makeStream()
        let secondIdentifier = try await poller.subscribe(
            continuation: second.continuation,
            since: nil,
            until: nil,
            labels: ["fixture": "yes"],
            context: RuntimeRequestContext()
        )
        second.continuation.onTermination = { _ in
            Task {
                await poller.unsubscribe(secondIdentifier)
            }
        }

        let firstEvent = Task { () throws -> RuntimeEvent? in
            for try await event in first.stream {
                return event
            }
            return nil
        }
        let secondEvent = Task { () throws -> RuntimeEvent? in
            for try await event in second.stream {
                return event
            }
            return nil
        }
        await source.set(snapshot: .fixture)

        #expect(try await firstEvent.value?.action == .create)
        #expect(try await secondEvent.value?.action == .create)
        await poller.shutdown()
        #expect(await source.snapshotCount() < 4)
    }

    @Test
    func `event poller includes subscribers added during its sleep`() async throws {
        let source = EventSnapshotSource()
        let poller = AppleEventPoller { _ in
            await source.snapshot()
        }
        let first = AsyncThrowingStream<RuntimeEvent, any Error>.makeStream()
        let firstIdentifier = try await poller.subscribe(
            continuation: first.continuation,
            since: nil,
            until: nil,
            labels: ["fixture": "yes"],
            context: RuntimeRequestContext()
        )
        first.continuation.onTermination = { _ in
            Task {
                await poller.unsubscribe(firstIdentifier)
            }
        }

        try await Task.sleep(for: .milliseconds(25))

        let second = AsyncThrowingStream<RuntimeEvent, any Error>.makeStream()
        let secondIdentifier = try await poller.subscribe(
            continuation: second.continuation,
            since: nil,
            until: nil,
            labels: ["fixture": "yes"],
            context: RuntimeRequestContext()
        )
        second.continuation.onTermination = { _ in
            Task {
                await poller.unsubscribe(secondIdentifier)
            }
        }

        let firstEvent = Task { () throws -> RuntimeEvent? in
            for try await event in first.stream {
                return event
            }
            return nil
        }
        let secondEvent = Task { () throws -> RuntimeEvent? in
            for try await event in second.stream {
                return event
            }
            return nil
        }
        await source.set(snapshot: .fixture)

        #expect(try await firstEvent.value?.action == .create)
        #expect(try await secondEvent.value?.action == .create)
        await poller.shutdown()
    }

    @Test
    func `event poller does not emit after a subscription deadline`() async throws {
        let source = EventSnapshotSource()
        let poller = AppleEventPoller { _ in
            await source.snapshot()
        }
        let stream = AsyncThrowingStream<RuntimeEvent, any Error>.makeStream()
        let identifier = try await poller.subscribe(
            continuation: stream.continuation,
            since: nil,
            until: Date().addingTimeInterval(0.05),
            labels: ["fixture": "yes"],
            context: RuntimeRequestContext()
        )
        stream.continuation.onTermination = { _ in
            Task {
                await poller.unsubscribe(identifier)
            }
        }
        let nextEvent = Task { () throws -> RuntimeEvent? in
            for try await event in stream.stream {
                return event
            }
            return nil
        }

        try await Task.sleep(for: .milliseconds(100))
        await source.set(snapshot: .fixture)

        #expect(try await nextEvent.value == nil)
        await poller.shutdown()
    }

    @Test
    func `start reuses its refreshed inventory for host synchronisation`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()

        try await runtime.startContainer(
            id: "fixture",
            context: RuntimeRequestContext()
        )

        let inventoryReads = try fixture.log()
            .split(separator: "\n")
            .filter { $0 == "list --all --format json" }
        #expect(inventoryReads.count == 2)
    }
}

private actor EventSnapshotSource {
    private var current: [String: ContainerSnapshot] = [:]
    private var count = 0

    func snapshot() -> [String: ContainerSnapshot] {
        count += 1
        return current
    }

    func set(snapshot: ContainerSnapshot) {
        current[snapshot.runtimeID.rawValue] = snapshot
    }

    func snapshotCount() -> Int {
        count
    }

    func waitForSnapshotCount(atLeast expected: Int) async -> Bool {
        for _ in 0 ..< 100 {
            if count >= expected {
                return true
            }
            try? await Task.sleep(for: .milliseconds(10))
        }
        return count >= expected
    }
}

private actor DelayedEventSnapshotSource {
    private var count = 0

    func snapshot() async -> [String: ContainerSnapshot] {
        count += 1
        try? await Task.sleep(for: .milliseconds(100))
        return [:]
    }

    func snapshotCount() -> Int {
        count
    }
}

private enum EventSnapshotError: Error {
    case failed
}

private extension ContainerSnapshot {
    static let fixture = ContainerSnapshot(
        runtimeID: RuntimeID(rawValue: "fixture"),
        dockerID: DockerID(rawValue: "docker-fixture"),
        spec: ContainerSpec(
            name: "fixture",
            image: "fixture:latest",
            labels: ["fixture": "yes"]
        ),
        state: .running,
        createdAt: Date(timeIntervalSince1970: 1)
    )
}
