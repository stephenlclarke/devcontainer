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

import DevContainerRuntimeSPI
import Foundation

final class RuntimeSessionCancellation: @unchecked Sendable {
    private let session: any RuntimeProcessSession
    private let lock = NSLock()
    private var task: Task<Void, Never>?

    init(session: any RuntimeProcessSession) {
        self.session = session
    }

    func cancel() {
        lock.withLock {
            guard task == nil else {
                return
            }
            task = Task {
                await session.cancel()
            }
        }
    }

    func wait() async {
        let cancellationTask: Task<Void, Never>? = lock.withLock { task }
        await cancellationTask?.value
    }
}

final class EngineResourceBudget: @unchecked Sendable {
    private let maximumConnections: Int
    private let maximumBodyBytes: Int
    private let lock = NSLock()
    private var connections = 0
    private var bodyBytes = 0

    init(limits: EngineServerLimits) {
        maximumConnections = limits.maximumActiveConnections
        maximumBodyBytes = limits.maximumProcessBufferedRequestBodyBytes
    }

    func openConnection() -> Bool {
        lock.withLock {
            guard connections < maximumConnections else {
                return false
            }
            connections += 1
            return true
        }
    }

    func closeConnection() {
        lock.withLock {
            connections = max(0, connections - 1)
        }
    }

    func reserveBodyBytes(_ count: Int) -> Bool {
        guard count >= 0 else {
            return false
        }
        return lock.withLock {
            let (next, overflow) = bodyBytes.addingReportingOverflow(count)
            guard !overflow, next <= maximumBodyBytes else {
                return false
            }
            bodyBytes = next
            return true
        }
    }

    func releaseBodyBytes(_ count: Int) {
        lock.withLock {
            bodyBytes = max(0, bodyBytes - count)
        }
    }
}

/// Serializes bytes and EOF from a hijacked Docker connection before forwarding
/// them to the runtime process. NIO invokes the synchronous enqueue methods in
/// channel order; the single consumer task preserves that order across the
/// asynchronous runtime boundary.
final class OrderedRuntimeInputPump: @unchecked Sendable {
    private enum Event: Sendable {
        case data(Data)
        case close
    }

    private let continuation: AsyncStream<Event>.Continuation
    private let worker: Task<Void, Never>
    private let onFailure: @Sendable () -> Void
    private let stateLock = NSLock()
    private var finished = false

    init(
        session: any RuntimeProcessSession,
        onFailure: @escaping @Sendable () -> Void = {}
    ) {
        let (stream, continuation) = AsyncStream<Event>.makeStream()
        self.continuation = continuation
        self.onFailure = onFailure
        worker = Task {
            do {
                for await event in stream {
                    try Task.checkCancellation()
                    switch event {
                    case let .data(data):
                        try await session.write(data)
                    case .close:
                        try await session.closeStandardInput()
                    }
                }
            } catch {
                onFailure()
            }
        }
    }

    func write(_ data: Data) {
        guard !data.isEmpty else {
            return
        }
        stateLock.withLock {
            guard !finished else {
                return
            }
            continuation.yield(.data(data))
        }
    }

    func close() {
        stateLock.withLock {
            guard !finished else {
                return
            }
            finished = true
            continuation.yield(.close)
            continuation.finish()
        }
    }

    func finish() {
        stateLock.withLock {
            guard !finished else {
                return
            }
            finished = true
            continuation.finish()
        }
    }

    func cancel() {
        stateLock.withLock {
            guard !finished else {
                return
            }
            finished = true
            continuation.finish()
            worker.cancel()
        }
    }

    func wait() async {
        await worker.value
    }
}
