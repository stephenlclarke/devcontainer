// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

/// An explicit handshake, not a sleep, keeps recovery/health overlap deterministic.
actor RecoveryProcessSession: RuntimeProcessSession {
    private enum ProbeFailure: Error { case unobservedCompletion }
    nonisolated let frames = AsyncThrowingStream<RuntimeIOFrame, any Error> { $0.finish() }
    private let failAfterRelease: Bool
    private var started = false
    private var released = false
    private var startedWaiters: [CheckedContinuation<Void, Never>] = []
    private var waiters: [CheckedContinuation<Int32, Never>] = []
    private(set) var waitCount = 0

    init(failAfterRelease: Bool = false) {
        self.failAfterRelease = failAfterRelease
    }

    func waitForStart() async {
        if started { return }
        await withCheckedContinuation { startedWaiters.append($0) }
    }

    func wait() async throws -> Int32 {
        waitCount += 1
        started = true
        startedWaiters.forEach { $0.resume() }
        startedWaiters.removeAll()
        let result = released ? 0 : await withCheckedContinuation { waiters.append($0) }
        if failAfterRelease { throw ProbeFailure.unobservedCompletion }
        return result
    }

    func release() {
        released = true
        waiters.forEach { $0.resume(returning: 0) }
        waiters.removeAll()
    }

    func write(_: Data) { /* This probe has no input channel. */ }
    func closeStandardInput() { /* This probe has no input channel. */ }
    func resize(width _: UInt16, height _: UInt16) { /* This probe has no terminal. */ }
    func cancel() { release() }
}
