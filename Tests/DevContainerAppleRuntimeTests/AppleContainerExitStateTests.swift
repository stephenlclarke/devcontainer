// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Darwin
@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

@Suite(.timeLimit(.minutes(1)))
struct AppleContainerExitStateTests {
    @Test func `pre cancelled waiter releases its registration before native exit`() async throws {
        let state = AppleContainerExitState()
        let reference = try await cancelBeforeWaiting(state)
        #expect(reference.value == nil)
        // The still-live generation can accept and satisfy an independent waiter.
        let survivor = try #require(state.subscribe(snapshot: snapshot()))
        state.complete(.success(31))
        #expect(try await survivor.wait() == 31)
    }

    @Test func `cancellation does not consume another waiters exit`() async throws {
        let state = AppleContainerExitState()
        let first = try #require(state.subscribe(snapshot: snapshot()))
        let second = try #require(state.subscribe(snapshot: snapshot()))
        let task = Task { try await first.wait() }
        task.cancel()
        await #expect(throws: CancellationError.self) { try await task.value }
        state.complete(.success(37))
        #expect(try await second.wait() == 37)
        state.complete(.success(0))
        #expect(try await second.wait() == 37)
        #expect(state.subscribe(snapshot: snapshot()) == nil)
    }

    @Test func `registered result survives authority release`() async throws {
        var state: AppleContainerExitState? = AppleContainerExitState()
        let wait = try #require(state?.subscribe(snapshot: snapshot()))
        state?.complete(.success(42))
        state = nil
        #expect(try await wait.wait() == 42)
        await wait.cancel()
        #expect(try await wait.wait() == 42)
    }

    @Test func `shutdown before exit is failure not zero status`() async throws {
        let channel = try AppleContainerIO(createdAt: Date(), terminal: false, openStandardInput: false)
        let wait = try #require(channel.prepareExitWait(snapshot: snapshot()))
        await channel.shutdown()
        await #expect(throws: CancellationError.self) { try await wait.wait() }
    }

    @Test func `missing output EOF does not replace native exit`() async throws {
        let channel = try AppleContainerIO(createdAt: Date(), terminal: false, openStandardInput: false)
        let waiter = try #require(channel.prepareExitWait(snapshot: snapshot()))
        let output = channel.attach()
        let handles: [FileHandle?] = try channel.bootstrapHandles().map { handle in
            guard let handle else { return nil }
            let descriptor = dup(handle.fileDescriptor)
            guard descriptor >= 0 else { throw POSIXError(.EMFILE) }
            return FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        }
        defer { for handle in handles {
            try? handle?.close()
        } }
        await channel.finish(exitCode: 29, drainTimeout: .milliseconds(20))
        #expect(try await waiter.wait() == 29)
        await #expect(throws: DevContainerError.self) { try await output.wait() }
        await channel.shutdown()
        #expect(try await waiter.wait() == 29)
    }

    private func snapshot() -> ContainerSnapshot {
        .init(
            runtimeID: RuntimeID(rawValue: "fixture"),
            dockerID: DockerID(rawValue: String(repeating: "a", count: 64)),
            spec: ContainerSpec(name: "fixture", image: "fixture:latest"),
            state: .created,
            createdAt: Date()
        )
    }

    private func cancelBeforeWaiting(_ state: AppleContainerExitState) async throws -> WeakExitRegistration {
        let registration = try #require(state.subscribe(snapshot: snapshot()))
        let reference = WeakExitRegistration(registration as AnyObject)
        let task = Task {
            withUnsafeCurrentTask { $0?.cancel() }
            return try await registration.wait()
        }
        await #expect(throws: CancellationError.self) { try await task.value }
        return reference
    }
}

private final class WeakExitRegistration: @unchecked Sendable {
    weak var value: AnyObject?
    init(_ value: AnyObject) {
        self.value = value
    }
}
