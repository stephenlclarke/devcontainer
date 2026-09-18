// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerService
import Testing

struct ServiceLifecycleTests {
    enum Failure: Error, Equatable {
        case startup
        case wait
        case shutdown
    }

    @Test
    func `startup failure releases runtime resources without repeating server rollback`() async {
        let events = LifecycleEvents()
        let signals = AsyncStream<Int32>.makeStream()
        await #expect(throws: Failure.startup) {
            try await ServiceLifecycle(
                start: { throw Failure.startup },
                wait: { await events.record("wait") },
                shutdownRuntime: { await events.record("runtime") },
                shutdownServer: { await events.record("server") }
            ).run(
                signals: signals.stream,
                onSignal: { _ in Issue.record("unexpected signal") }
            )
        }
        #expect(await events.values == ["runtime"])
    }

    @Test(arguments: [false, true])
    func `wait failure always shuts down and preserves the original error`(shutdownFails: Bool) async {
        let events = LifecycleEvents()
        let signals = AsyncStream<Int32>.makeStream()
        await #expect(throws: Failure.wait) {
            try await ServiceLifecycle(
                start: { await events.record("start") },
                wait: { throw Failure.wait },
                shutdownRuntime: { await events.record("runtime") },
                shutdownServer: {
                    await events.record("server")
                    if shutdownFails {
                        throw Failure.shutdown
                    }
                }
            ).run(
                signals: signals.stream,
                onSignal: { _ in Issue.record("unexpected signal") }
            )
        }
        #expect(await events.values == ["start", "runtime", "server"])
    }

    @Test(arguments: [false, true])
    func `server closure drains signal listener and reports shutdown failures`(shutdownFails: Bool) async throws {
        let events = LifecycleEvents()
        let signals = AsyncStream<Int32>.makeStream()
        do {
            try await ServiceLifecycle(
                start: { await events.record("start") },
                wait: {}, // The fixture server has already closed.
                shutdownRuntime: { await events.record("runtime") },
                shutdownServer: {
                    await events.record("server")
                    if shutdownFails {
                        throw Failure.shutdown
                    }
                }
            ).run(
                signals: signals.stream,
                onSignal: { _ in Issue.record("unexpected signal") }
            )
            #expect(!shutdownFails)
        } catch {
            #expect(shutdownFails && error as? Failure == .shutdown)
        }
        #expect(await events.values == ["start", "runtime", "server"])
        if case .terminated = signals.continuation.yield(15) {
            // Cancellation has closed the signal stream before returning.
        } else {
            Issue.record("signal listener not drained")
        }
    }

    @Test
    func `signal shuts down runtime before server and drains server wait`() async throws {
        let events = LifecycleEvents()
        let signals = AsyncStream<Int32>.makeStream()
        let closed = AsyncStream<Void>.makeStream()
        signals.continuation.yield(15)
        try await ServiceLifecycle(
            start: { await events.record("start") },
            wait: {
                for await _ in closed.stream {}
                await events.record("wait-ended")
            },
            shutdownRuntime: { await events.record("runtime") },
            shutdownServer: {
                await events.record("server")
                closed.continuation.finish()
            }
        ).run(
            signals: signals.stream,
            onSignal: { #expect($0 == 15) }
        )
        #expect(await events.values == ["start", "runtime", "server", "wait-ended"])
    }

    @Test
    func `parent cancellation still drains both resource owners`() async throws {
        let events = LifecycleEvents()
        let signals = AsyncStream<Int32>.makeStream()
        let started = AsyncStream<Void>.makeStream()
        let closed = AsyncStream<Void>.makeStream()
        let task = Task {
            try await ServiceLifecycle(
                start: { started.continuation.yield(()) },
                wait: {
                    for await _ in closed.stream {}
                    try Task.checkCancellation()
                },
                shutdownRuntime: { await events.record("runtime") },
                shutdownServer: {
                    await events.record("server")
                    closed.continuation.finish()
                }
            ).run(
                signals: signals.stream,
                onSignal: { _ in Issue.record("unexpected signal") }
            )
        }
        for await _ in started.stream {
            break
        }
        task.cancel()
        await #expect(throws: CancellationError.self) { try await task.value }
        #expect(await events.values == ["runtime", "server"])
    }
}

private actor LifecycleEvents {
    var values: [String] = []

    func record(_ value: String) {
        values.append(value)
    }
}
