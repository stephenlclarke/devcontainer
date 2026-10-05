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

struct AppleEventSnapshotConflictTests {
    @Test
    func `initial events inventory retries only a coherent complete observation`() async throws {
        let source = ConflictSnapshotSource(steps: [.conflict, .snapshot(false)])
        let poller = AppleEventPoller { _ in try await source.snapshot() }
        let stream = AsyncThrowingStream<RuntimeEvent, any Error>.makeStream()
        _ = try await poller.subscribe(
            continuation: stream.continuation, since: nil, until: nil,
            labels: [:], context: RuntimeRequestContext()
        )
        #expect(await source.callCount() >= 2)
        await poller.shutdown()
    }

    @Test
    func `a conflicted poll retries before publishing the next start event`() async throws {
        let source = ConflictSnapshotSource(steps: [.snapshot(false), .conflict, .snapshot(true)])
        let poller = AppleEventPoller { _ in try await source.snapshot() }
        let stream = AsyncThrowingStream<RuntimeEvent, any Error>.makeStream()
        _ = try await poller.subscribe(
            continuation: stream.continuation, since: nil,
            until: Date().addingTimeInterval(2), labels: [:], context: RuntimeRequestContext()
        )
        await poller.notifyChanged()
        var iterator = stream.stream.makeAsyncIterator()
        let event = try await iterator.next()
        #expect(event?.action == .start)
        #expect(event?.resourceID == "docker-fixture")
        await poller.shutdown()
        #expect(try await iterator.next() == nil)
    }

    @Test
    func `persistent initial inventory conflict fails after exactly three reads`() async {
        let source = ConflictSnapshotSource(steps: [.conflict])
        let poller = AppleEventPoller { _ in try await source.snapshot() }
        let stream = AsyncThrowingStream<RuntimeEvent, any Error>.makeStream()
        await #expect(throws: Self.conflict) {
            _ = try await poller.subscribe(
                continuation: stream.continuation, since: nil, until: nil,
                labels: [:], context: RuntimeRequestContext()
            )
        }
        #expect(await source.callCount() == 3)
        await poller.shutdown()
    }

    @Test
    func `persistent polling conflict fails without publishing a partial event`() async throws {
        let source = ConflictSnapshotSource(steps: [.snapshot(false), .conflict])
        let poller = AppleEventPoller { _ in try await source.snapshot() }
        let stream = AsyncThrowingStream<RuntimeEvent, any Error>.makeStream()
        _ = try await poller.subscribe(
            continuation: stream.continuation, since: nil,
            until: Date().addingTimeInterval(2), labels: [:], context: RuntimeRequestContext()
        )
        await poller.notifyChanged()
        var iterator = stream.stream.makeAsyncIterator()
        await #expect(throws: Self.conflict) { _ = try await iterator.next() }
        #expect(await source.callCount() == 4)
        await poller.shutdown()
    }

    @Test
    func `a subscriber expiring during a held retry receives no late event`() async throws {
        let source = ExpiringRetrySnapshotSource()
        let poller = AppleEventPoller { _ in try await source.snapshot() }
        let stream = AsyncThrowingStream<RuntimeEvent, any Error>.makeStream()
        let expiry = Date().addingTimeInterval(2)
        _ = try await poller.subscribe(
            continuation: stream.continuation, since: nil, until: expiry,
            labels: [:], context: RuntimeRequestContext()
        )
        await poller.notifyChanged()
        // The source's third read is held only after the conflicted second read.
        // Releasing it after the absolute expiry makes the publication race exact.
        try await source.waitUntilHeld(deadline: expiry)
        let remaining = expiry.timeIntervalSinceNow
        if remaining > 0 {
            let clock = ContinuousClock()
            try await clock.sleep(until: clock.now.advanced(by: .seconds(remaining)))
        }
        #expect(Date() >= expiry)
        await source.release()
        var iterator = stream.stream.makeAsyncIterator()
        #expect(try await iterator.next() == nil)
        #expect(await source.callCount() == 3)
        await poller.shutdown()
    }

    @Test(arguments: [
        DevContainerError(.authentication, message: "denied"),
        DevContainerError(.providerProtocolMismatch, message: "malformed inventory"),
        DevContainerError(.conflict, message: "different incarnation proof failure")
    ])
    func `unrelated snapshot errors are never retried`(error: DevContainerError) async {
        let source = ConflictSnapshotSource(steps: [.failure(error)])
        let poller = AppleEventPoller { _ in try await source.snapshot() }
        let stream = AsyncThrowingStream<RuntimeEvent, any Error>.makeStream()
        await #expect(throws: error) {
            _ = try await poller.subscribe(
                continuation: stream.continuation, since: nil, until: nil,
                labels: [:], context: RuntimeRequestContext()
            )
        }
        #expect(await source.callCount() == 1)
        await poller.shutdown()
    }

    @Test
    func `snapshot cancellation prevents another identity read`() async {
        let source = ConflictSnapshotSource(steps: [.cancelledConflict])
        let poller = AppleEventPoller { _ in try await source.snapshot() }
        let stream = AsyncThrowingStream<RuntimeEvent, any Error>.makeStream()
        await #expect(throws: CancellationError.self) {
            _ = try await poller.subscribe(
                continuation: stream.continuation, since: nil, until: nil,
                labels: [:], context: RuntimeRequestContext()
            )
        }
        #expect(await source.callCount() == 1)
        await poller.shutdown()
    }

    @Test
    func `expired context rejects snapshot acquisition before its first read`() async {
        let source = ConflictSnapshotSource(steps: [.snapshot(false)])
        let poller = AppleEventPoller { _ in try await source.snapshot() }
        let stream = AsyncThrowingStream<RuntimeEvent, any Error>.makeStream()
        await #expect(throws: DevContainerError.self) {
            _ = try await poller.subscribe(
                continuation: stream.continuation, since: nil, until: nil,
                labels: [:], context: RuntimeRequestContext(deadline: .distantPast)
            )
        }
        #expect(await source.callCount() == 0)
        await poller.shutdown()
    }

    static let conflict = DevContainerError(
        .conflict, message: "Container identity changed during CLI inventory"
    )
}

private actor ExpiringRetrySnapshotSource {
    private var calls = 0
    private var held = false
    private var releaseWaiter: CheckedContinuation<Void, Never>?

    func callCount() -> Int {
        calls
    }

    func snapshot() async throws -> [String: ContainerSnapshot] {
        calls += 1
        if calls == 1 {
            return [:]
        }
        if calls == 2 {
            throw AppleEventSnapshotConflictTests.conflict
        }
        held = true
        await withCheckedContinuation { releaseWaiter = $0 }
        return ["fixture": ContainerSnapshot(
            runtimeID: RuntimeID(rawValue: "fixture"),
            dockerID: DockerID(rawValue: "docker-fixture"),
            spec: ContainerSpec(name: "fixture", image: "fixture:latest"),
            state: .running, createdAt: Date(timeIntervalSince1970: 1)
        )]
    }

    func waitUntilHeld(deadline: Date) async throws {
        while !held {
            guard Date() < deadline else {
                throw DevContainerError(.deadlineExceeded, message: "retry never reached the test barrier")
            }
            let clock = ContinuousClock()
            try await clock.sleep(until: clock.now.advanced(by: .milliseconds(10)))
        }
    }

    func release() {
        releaseWaiter?.resume()
        releaseWaiter = nil
    }
}

private actor ConflictSnapshotSource {
    enum Step: Sendable {
        case snapshot(Bool)
        case conflict
        case failure(DevContainerError)
        case cancelledConflict
    }

    private let steps: [Step]
    private var calls = 0

    init(steps: [Step]) {
        self.steps = steps
    }

    func callCount() -> Int {
        calls
    }

    func snapshot() throws -> [String: ContainerSnapshot] {
        let step = steps[min(calls, steps.count - 1)]
        calls += 1
        switch step {
        case let .snapshot(running):
            return ["fixture": ContainerSnapshot(
                runtimeID: RuntimeID(rawValue: "fixture"),
                dockerID: DockerID(rawValue: "docker-fixture"),
                spec: ContainerSpec(name: "fixture", image: "fixture:latest"),
                state: running ? .running : .created,
                createdAt: Date(timeIntervalSince1970: 1)
            )]
        case .conflict:
            throw AppleEventSnapshotConflictTests.conflict
        case let .failure(error):
            throw error
        case .cancelledConflict:
            withUnsafeCurrentTask { $0?.cancel() }
            throw AppleEventSnapshotConflictTests.conflict
        }
    }
}
