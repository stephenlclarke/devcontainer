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

import Darwin
@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

struct AppleProcessSessionSupportTests {
    @Test
    func `deferred session replays ordered operations after launch`() async throws {
        let launched = RecordingRuntimeSession()
        let deferred = DeferredAppleProcessSession {
            try await Task.sleep(for: .milliseconds(30))
            return launched
        }

        try await deferred.write(Data("before".utf8))
        try await deferred.resize(width: 120, height: 44)
        try await deferred.closeStandardInput()

        var output = Data()
        for try await frame in deferred.frames {
            output.append(frame.data)
        }
        #expect(try await deferred.wait() == 9)
        #expect(String(data: output, encoding: .utf8) == "deferred-output")
        #expect(
            launched.operations == [
                .write("before"),
                .resize(width: 120, height: 44),
                .close
            ]
        )

        try await deferred.write(Data("after".utf8))
        #expect(launched.operations.last == .write("after"))
    }

    @Test
    func `deferred cancellation before activation fails the session`() async {
        let deferred = DeferredAppleProcessSession {
            try await Task.sleep(for: .seconds(1))
            return RecordingRuntimeSession()
        }
        await deferred.cancel()
        await #expect(throws: (any Error).self) {
            try await deferred.wait()
        }
    }

    @Test
    func `polling logs emit deltas resets and typed unsupported operations`() async throws {
        let poller = PollSequence()
        let session = ApplePollingLogSession {
            await poller.next()
        }

        #expect(throws: DevContainerError.self) {
            try session.write(Data("input".utf8))
        }
        session.closeStandardInput()
        #expect(throws: DevContainerError.self) {
            try session.resize(width: 80, height: 24)
        }

        var output = Data()
        var error = Data()
        for try await frame in session.frames {
            if frame.channel == .standardOutput {
                output.append(frame.data)
            } else if frame.channel == .standardError {
                error.append(frame.data)
            }
        }
        #expect(try await session.wait() == 5)
        #expect(String(data: output, encoding: .utf8) == "abc")
        #expect(String(data: error, encoding: .utf8) == "ewarn")
    }

    @Test
    func `log snapshots never wait for an open writer`() throws {
        var descriptors = [Int32](repeating: -1, count: 2)
        #expect(pipe(&descriptors) == 0)
        let reader = FileHandle(fileDescriptor: descriptors[0], closeOnDealloc: true)
        let writer = FileHandle(fileDescriptor: descriptors[1], closeOnDealloc: true)
        defer {
            try? reader.close()
            try? writer.close()
        }
        try writer.write(contentsOf: Data("available".utf8))

        let started = ContinuousClock.now
        let data = try AppleContainerRuntime.readAvailableLogData(from: reader)

        #expect(String(data: data, encoding: .utf8) == "available")
        #expect(ContinuousClock.now - started < .milliseconds(100))
    }

    @Test
    func `monitor maps runtime disappearance and preserves normal exit`() async throws {
        let monitoredProcess = try AppleProcessSession(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: [
                "-c",
                "trap 'exit 9' TERM; while :; do sleep 1; done"
            ],
            environment: [:]
        )
        let monitored = MonitoredAppleProcessSession(
            process: monitoredProcess
        ) {
            try? await Task.sleep(for: .milliseconds(30))
        }
        #expect(try await monitored.wait() == 0)

        let normalProcess = try AppleProcessSession(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: ["-c", "exit 7"],
            environment: [:]
        )
        let normal = MonitoredAppleProcessSession(process: normalProcess) {
            try? await Task.sleep(for: .seconds(2))
        }
        #expect(try await normal.wait() == 7)
        normal.cancel()
    }
}

private final class RecordingRuntimeSession: RuntimeProcessSession, @unchecked Sendable {
    enum Operation: Equatable {
        case write(String)
        case close
        case resize(width: UInt16, height: UInt16)
        case cancel
    }

    let frames = AsyncThrowingStream<RuntimeIOFrame, any Error> { continuation in
        continuation.yield(
            RuntimeIOFrame(
                channel: .standardOutput,
                data: Data("deferred-output".utf8)
            )
        )
        continuation.finish()
    }

    private let lock = NSLock()
    private var recorded: [Operation] = []

    var operations: [Operation] {
        lock.withLock { recorded }
    }

    func write(_ data: Data) async {
        lock.withLock {
            recorded.append(
                .write(String(data: data, encoding: .utf8) ?? "non-UTF-8 input")
            )
        }
    }

    func closeStandardInput() async {
        lock.withLock {
            recorded.append(.close)
        }
    }

    func resize(width: UInt16, height: UInt16) async {
        lock.withLock {
            recorded.append(.resize(width: width, height: height))
        }
    }

    func wait() async -> Int32 {
        9
    }

    func cancel() async {
        lock.withLock {
            recorded.append(.cancel)
        }
    }
}

private actor PollSequence {
    private var index = 0

    func next() -> AppleLogPoll {
        defer { index += 1 }
        switch index {
        case 0:
            return AppleLogPoll(
                standardOutput: Data("a".utf8),
                standardError: Data("e".utf8),
                finished: false,
                exitCode: 0
            )
        case 1:
            return AppleLogPoll(
                standardOutput: Data("abc".utf8),
                standardError: Data(),
                finished: false,
                exitCode: 0
            )
        default:
            return AppleLogPoll(
                standardOutput: Data("abc".utf8),
                standardError: Data("warn".utf8),
                finished: true,
                exitCode: 5
            )
        }
    }
}
