// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Darwin
@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

struct AppleContainerOutputHistoryTests {
    @Test func `generation cannot be replaced before durable completion is published`() async throws {
        let gate = OutputCompletionGate()
        defer { gate.release() }
        let journal = OutputHistoryProbe(onFinish: { gate.hold() })
        let channel = try AppleContainerIO(
            createdAt: Date(), terminal: false, openStandardInput: false, outputCapture: { journal }
        )
        try await channel.prepareOutputCapture()
        _ = try channel.bootstrapHandles()
        let completion = Task { await channel.finish(exitCode: 8) }
        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        while !gate.entered, ContinuousClock.now < deadline {
            await Task.yield()
        }
        #expect(gate.entered)
        #expect(!channel.hasExited)
        gate.release()
        await completion.value
        #expect(channel.hasExited)
        #expect(journal.completed == true)
        await channel.shutdown()
    }

    @Test func `concurrent publishers have identical durable and live order`() async throws {
        let journal = OutputHistoryProbe()
        let state = AppleContainerAttachmentState()
        try state.installJournal(journal)
        let subscriber = state.subscribe()
        DispatchQueue.concurrentPerform(iterations: 192) { index in state.publish(frame(index)) }
        state.complete(.success(0))
        #expect(try await collect(subscriber.frames) == journal.saved)
        #expect(journal.saved.count == 192)
        #expect(journal.completed == true)
    }

    @Test func `history cutoff and live registration cannot lose or duplicate a concurrent frame`() async throws {
        let journal = OutputHistoryProbe()
        let state = AppleContainerAttachmentState()
        try state.installJournal(journal)
        for index in 0 ..< 32 {
            state.publish(frame(index))
        }
        let writer = Task.detached {
            DispatchQueue.concurrentPerform(iterations: 192) { index in state.publish(frame(index + 32)) }
        }
        let (history, subscription) = try state.prepare(history: true, live: true, context: .init())
        await writer.value
        state.complete(.success(7))
        let saved = try await collect(#require(history))
        let live = try await collect(#require(subscription).frames)
        #expect(saved + live == journal.saved)
        #expect(journal.saved.count == 224)
        #expect(try await subscription?.wait() == 7)
    }

    @Test func `capture fails before bootstrap and native exit remains independent of output failure`() async throws {
        let journal = OutputHistoryProbe(failAppend: true)
        let channel = try AppleContainerIO(
            createdAt: Date(), terminal: false, openStandardInput: false, outputCapture: { journal }
        )
        try await channel.prepareOutputCapture()
        let prepared = try channel.prepareAttachment(history: true, live: true, context: .init())
        let session = try #require(prepared.session)
        let snapshot = ContainerSnapshot(
            runtimeID: .init(rawValue: "native"), dockerID: .init(rawValue: "docker"),
            spec: .init(name: "native", image: "fixture"), state: .created, createdAt: channel.createdAt
        )
        let exit = try #require(channel.prepareExitWait(snapshot: snapshot))
        let handles = try channel.bootstrapHandles()
        try handles[1]?.write(contentsOf: Data("source".utf8))
        try handles[1]?.close()
        try handles[2]?.close()
        await channel.finish(exitCode: 17)
        #expect(try await exit.wait() == 17)
        await #expect(throws: DevContainerError.self) { try await collect(session.frames) }
        await #expect(throws: DevContainerError.self) { try await session.wait() }
        #expect(journal.completed == false)
        await channel.shutdown()
    }

    @Test func `shutdown joins capture preparation and refuses late journal installation`() async throws {
        let gate = OutputPreparationGate()
        let journal = OutputHistoryProbe()
        let channel = try AppleContainerIO(
            createdAt: Date(), terminal: false, openStandardInput: false,
            outputCapture: { await gate.wait(); return journal }
        )
        #expect(throws: DevContainerError.self) { try channel.bootstrapHandles() }
        channel.cancel()
        await gate.release()
        await channel.shutdown()
        #expect(journal.completed == false)
        await #expect(throws: CancellationError.self) { try await channel.prepareOutputCapture() }
    }

    @Test func `cancelled preparation waiter does not close shared capture`() async throws {
        let journal = OutputHistoryProbe()
        let channel = try AppleContainerIO(
            createdAt: Date(), terminal: false, openStandardInput: false, outputCapture: { journal }
        )
        let waiter = Task {
            withUnsafeCurrentTask { $0?.cancel() }
            try await channel.prepareOutputCapture()
        }
        await #expect(throws: CancellationError.self) { try await waiter.value }
        #expect(!channel.isFinished)
        try await channel.prepareOutputCapture()
        _ = try channel.bootstrapHandles()
        await channel.shutdown()
    }

    private func frame(_ index: Int) -> RuntimeIOFrame {
        .init(channel: index.isMultiple(of: 2) ? .standardOutput : .standardError, data: Data("\(index)".utf8))
    }

    private func collect(_ frames: AsyncThrowingStream<RuntimeIOFrame, any Error>) async throws -> [RuntimeIOFrame] {
        var result: [RuntimeIOFrame] = []
        for try await frame in frames {
            result.append(frame)
        }
        return result
    }
}

private final class OutputHistoryProbe: RuntimeContainerOutputJournal, @unchecked Sendable {
    private let lock = NSLock()
    private var records: [RuntimeIOFrame] = []
    private var completion: Bool?
    private let failAppend: Bool
    private let onFinish: (@Sendable () -> Void)?

    init(failAppend: Bool = false, onFinish: (@Sendable () -> Void)? = nil) {
        self.failAppend = failAppend
        self.onFinish = onFinish
    }

    var saved: [RuntimeIOFrame] {
        lock.withLock { records }
    }

    var completed: Bool? {
        lock.withLock { completion }
    }

    func append(_ frame: RuntimeIOFrame) throws {
        try lock.withLock {
            guard !failAppend else { throw DevContainerError(.stateCorruption, message: "Injected output failure") }
            records.append(frame)
        }
    }

    func captureHistory(context: RuntimeRequestContext) throws -> AsyncThrowingStream<RuntimeIOFrame, any Error> {
        try context.checkActive()
        let snapshot = saved
        return AsyncThrowingStream { continuation in
            for frame in snapshot {
                continuation.yield(frame)
            }
            continuation.finish()
        }
    }

    func captureLogHistory(context: RuntimeRequestContext) throws -> AsyncThrowingStream<RuntimeIOFrame, any Error> {
        try captureHistory(context: context)
    }

    func endSource(_: RuntimeIOChannel) {
        // This ordering fake retains frames; SQLite tests prove the log encoding.
    }

    func finish(complete: Bool) {
        onFinish?()
        lock.withLock { completion = complete }
    }
}

private final class OutputCompletionGate: @unchecked Sendable {
    private let lock = NSLock()
    private let semaphore = DispatchSemaphore(value: 0)
    private var waiting = false
    var entered: Bool {
        lock.withLock { waiting }
    }

    func hold() {
        lock.withLock { waiting = true }
        _ = semaphore.wait(timeout: .now() + 10)
    }

    func release() {
        semaphore.signal()
    }
}

private actor OutputPreparationGate {
    private var released = false
    private var waiters: [CheckedContinuation<Void, Never>] = []
    func wait() async {
        guard !released else { return }
        await withCheckedContinuation { waiters.append($0) }
    }

    func release() {
        released = true
        let pending = waiters
        waiters.removeAll()
        for waiter in pending {
            waiter.resume()
        }
    }
}
