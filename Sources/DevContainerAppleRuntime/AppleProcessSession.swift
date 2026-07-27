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
import DevContainerRuntimeSPI
import Foundation

final class AppleProcessSession: RuntimeProcessSession, @unchecked Sendable {
    let frames: AsyncThrowingStream<RuntimeIOFrame, any Error>

    private let process: Process
    private let standardInput: Pipe
    private let completion: Task<Int32, any Error>

    init(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        workingDirectory: URL? = nil,
        input: Data? = nil
    ) throws {
        let process = Process()
        let standardInput = Pipe()
        let standardOutput = Pipe()
        let standardError = Pipe()
        var continuationValue: AsyncThrowingStream<RuntimeIOFrame, any Error>.Continuation?
        let stream = AsyncThrowingStream<RuntimeIOFrame, any Error> { continuation in
            continuationValue = continuation
        }
        var terminationContinuationValue: AsyncStream<Int32>.Continuation?
        let termination = AsyncStream<Int32> { continuation in
            terminationContinuationValue = continuation
        }
        var outputEndContinuationValue: AsyncStream<Void>.Continuation?
        let outputEnd = AsyncStream<Void> { continuation in
            outputEndContinuationValue = continuation
        }
        var errorEndContinuationValue: AsyncStream<Void>.Continuation?
        let errorEnd = AsyncStream<Void> { continuation in
            errorEndContinuationValue = continuation
        }
        guard let continuation = continuationValue else {
            preconditionFailure("process output continuation was not created")
        }
        guard let terminationContinuation = terminationContinuationValue else {
            preconditionFailure("process termination continuation was not created")
        }
        guard
            let outputEndContinuation = outputEndContinuationValue,
            let errorEndContinuation = errorEndContinuationValue
        else {
            preconditionFailure("process pipe continuations were not created")
        }

        process.executableURL = executable
        process.arguments = arguments
        process.environment = environment
        process.currentDirectoryURL = workingDirectory
        process.standardInput = standardInput
        process.standardOutput = standardOutput
        process.standardError = standardError
        process.terminationHandler = { process in
            terminationContinuation.yield(process.terminationStatus)
            terminationContinuation.finish()
            Task {
                try? await Task.sleep(for: .milliseconds(250))
                standardOutput.fileHandleForReading.readabilityHandler = nil
                standardError.fileHandleForReading.readabilityHandler = nil
                outputEndContinuation.finish()
                errorEndContinuation.finish()
            }
        }
        standardOutput.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
                outputEndContinuation.finish()
            } else {
                continuation.yield(RuntimeIOFrame(channel: .standardOutput, data: data))
            }
        }
        standardError.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
                errorEndContinuation.finish()
            } else {
                continuation.yield(RuntimeIOFrame(channel: .standardError, data: data))
            }
        }

        do {
            try process.run()
        } catch {
            standardOutput.fileHandleForReading.readabilityHandler = nil
            standardError.fileHandleForReading.readabilityHandler = nil
            continuation.finish(throwing: error)
            terminationContinuation.finish()
            outputEndContinuation.finish()
            errorEndContinuation.finish()
            throw error
        }

        if let input {
            standardInput.fileHandleForWriting.write(input)
            try? standardInput.fileHandleForWriting.close()
        }

        completion = Task.detached {
            var exitCode: Int32 = 255
            for await status in termination {
                exitCode = status
                break
            }
            for await _ in outputEnd {}
            for await _ in errorEnd {}
            continuation.finish()
            return exitCode
        }
        self.process = process
        self.standardInput = standardInput
        frames = stream
    }

    func write(_ data: Data) throws {
        guard process.isRunning else {
            throw DevContainerError(.conflict, message: "process is no longer running")
        }
        standardInput.fileHandleForWriting.write(data)
    }

    func closeStandardInput() throws {
        try standardInput.fileHandleForWriting.close()
    }

    func resize(width _: UInt16, height _: UInt16) throws {
        throw DevContainerError(
            .unsupportedCapability,
            message: "Apple CLI process sessions do not expose PTY resize; the direct API session is required"
        )
    }

    func wait() async throws -> Int32 {
        try await completion.value
    }

    func cancel() {
        guard process.isRunning else {
            return
        }
        process.terminate()
    }
}

final class DeferredAppleProcessSession: RuntimeProcessSession, @unchecked Sendable {
    let frames: AsyncThrowingStream<RuntimeIOFrame, any Error>

    private let state: DeferredProcessState
    private let completion: Task<Int32, any Error>

    init(
        launch: @escaping @Sendable () async throws -> any RuntimeProcessSession
    ) {
        let state = DeferredProcessState()
        var continuationValue: AsyncThrowingStream<RuntimeIOFrame, any Error>.Continuation?
        let stream = AsyncThrowingStream<RuntimeIOFrame, any Error> { continuation in
            continuationValue = continuation
        }
        guard let continuation = continuationValue else {
            preconditionFailure("deferred process continuation was not created")
        }
        completion = Task {
            do {
                let session = try await launch()
                let pending = state.activate(session)
                if pending.cancelled {
                    await session.cancel()
                    throw CancellationError()
                }
                if !pending.input.isEmpty {
                    try await session.write(pending.input)
                }
                if let size = pending.size {
                    try await session.resize(width: size.width, height: size.height)
                }
                if pending.inputClosed {
                    try await session.closeStandardInput()
                }
                for try await frame in session.frames {
                    continuation.yield(frame)
                }
                let exitCode = try await session.wait()
                continuation.finish()
                return exitCode
            } catch {
                continuation.finish(throwing: error)
                throw error
            }
        }
        self.state = state
        frames = stream
    }

    func write(_ data: Data) async throws {
        if let session = state.bufferInputOrSession(data) {
            try await session.write(data)
        }
    }

    func closeStandardInput() async throws {
        if let session = state.closeInputOrSession() {
            try await session.closeStandardInput()
        }
    }

    func resize(width: UInt16, height: UInt16) async throws {
        if let session = state.resizeOrSession(width: width, height: height) {
            try await session.resize(width: width, height: height)
        }
    }

    func wait() async throws -> Int32 {
        try await completion.value
    }

    func cancel() async {
        completion.cancel()
        if let session = state.cancelAndReturnSession() {
            await session.cancel()
        }
    }
}

final class MonitoredAppleProcessSession: RuntimeProcessSession, @unchecked Sendable {
    let frames: AsyncThrowingStream<RuntimeIOFrame, any Error>

    private let process: AppleProcessSession
    private let completion: Task<Int32, any Error>
    private let monitor: Task<Void, Never>

    init(
        process: AppleProcessSession,
        waitUntilFinished: @escaping @Sendable () async -> Void
    ) {
        let state = MonitoredProcessState()
        let monitor = Task {
            await waitUntilFinished()
            guard !Task.isCancelled else {
                return
            }
            state.markMonitorTermination()
            process.cancel()
        }
        completion = Task {
            let exitCode = try await process.wait()
            monitor.cancel()
            return state.wasTerminatedByMonitor ? 0 : exitCode
        }
        self.process = process
        self.monitor = monitor
        frames = process.frames
    }

    func write(_ data: Data) throws {
        try process.write(data)
    }

    func closeStandardInput() throws {
        try process.closeStandardInput()
    }

    func resize(width: UInt16, height: UInt16) throws {
        try process.resize(width: width, height: height)
    }

    func wait() async throws -> Int32 {
        try await completion.value
    }

    func cancel() {
        monitor.cancel()
        completion.cancel()
        process.cancel()
    }
}

private final class MonitoredProcessState: @unchecked Sendable {
    private let lock = NSLock()
    private var terminatedByMonitor = false

    var wasTerminatedByMonitor: Bool {
        lock.withLock { terminatedByMonitor }
    }

    func markMonitorTermination() {
        lock.withLock {
            terminatedByMonitor = true
        }
    }
}

private final class DeferredProcessState: @unchecked Sendable {
    struct Pending: Sendable {
        let input: Data
        let inputClosed: Bool
        let cancelled: Bool
        let size: (width: UInt16, height: UInt16)?
    }

    private let lock = NSLock()
    private var session: (any RuntimeProcessSession)?
    private var pendingInput = Data()
    private var inputClosed = false
    private var cancelled = false
    private var size: (width: UInt16, height: UInt16)?

    func activate(_ value: any RuntimeProcessSession) -> Pending {
        lock.withLock {
            session = value
            let pending = Pending(
                input: pendingInput,
                inputClosed: inputClosed,
                cancelled: cancelled,
                size: size
            )
            pendingInput.removeAll(keepingCapacity: false)
            return pending
        }
    }

    func bufferInputOrSession(_ data: Data) -> (any RuntimeProcessSession)? {
        lock.withLock {
            if let session {
                return session
            }
            pendingInput.append(data)
            return nil
        }
    }

    func closeInputOrSession() -> (any RuntimeProcessSession)? {
        lock.withLock {
            inputClosed = true
            return session
        }
    }

    func resizeOrSession(
        width: UInt16,
        height: UInt16
    ) -> (any RuntimeProcessSession)? {
        lock.withLock {
            size = (width, height)
            return session
        }
    }

    func cancelAndReturnSession() -> (any RuntimeProcessSession)? {
        lock.withLock {
            cancelled = true
            return session
        }
    }
}

struct AppleCommandResult: Sendable {
    let standardOutput: Data
    let standardError: Data
    let exitCode: Int32
}

struct AppleLogPoll: Sendable {
    let standardOutput: Data
    let standardError: Data
    let finished: Bool
    let exitCode: Int32
}

final class ApplePollingLogSession: RuntimeProcessSession, @unchecked Sendable {
    let frames: AsyncThrowingStream<RuntimeIOFrame, any Error>

    private let completion: Task<Int32, any Error>

    init(
        poll: @escaping @Sendable () async throws -> AppleLogPoll
    ) {
        let (stream, continuation) = AsyncThrowingStream<
            RuntimeIOFrame,
            any Error
        >.makeStream()
        completion = Task {
            var outputOffset = 0
            var errorOffset = 0
            do {
                while !Task.isCancelled {
                    let value = try await poll()
                    if value.standardOutput.count < outputOffset {
                        outputOffset = 0
                    }
                    if value.standardError.count < errorOffset {
                        errorOffset = 0
                    }
                    if value.standardOutput.count > outputOffset {
                        continuation.yield(
                            RuntimeIOFrame(
                                channel: .standardOutput,
                                data: value.standardOutput.subdata(
                                    in: outputOffset ..< value.standardOutput.count
                                )
                            )
                        )
                        outputOffset = value.standardOutput.count
                    }
                    if value.standardError.count > errorOffset {
                        continuation.yield(
                            RuntimeIOFrame(
                                channel: .standardError,
                                data: value.standardError.subdata(
                                    in: errorOffset ..< value.standardError.count
                                )
                            )
                        )
                        errorOffset = value.standardError.count
                    }
                    if value.finished {
                        continuation.finish()
                        return value.exitCode
                    }
                    try await Task.sleep(for: .milliseconds(100))
                }
                throw CancellationError()
            } catch {
                continuation.finish(throwing: error)
                throw error
            }
        }
        frames = stream
    }

    func write(_: Data) throws {
        throw DevContainerError(
            .unsupportedCapability,
            message: "stock Apple container does not expose attach stdin"
        )
    }

    func closeStandardInput() {}

    func resize(width _: UInt16, height _: UInt16) throws {
        throw DevContainerError(
            .unsupportedCapability,
            message: "stock Apple container does not expose attach resize"
        )
    }

    func wait() async throws -> Int32 {
        try await completion.value
    }

    func cancel() {
        completion.cancel()
    }
}

enum AppleCommandRunner {
    static func run(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        workingDirectory: URL? = nil,
        input: Data? = nil
    ) async throws -> AppleCommandResult {
        let session: AppleProcessSession
        do {
            session = try AppleProcessSession(
                executable: executable,
                arguments: arguments,
                environment: environment,
                workingDirectory: workingDirectory,
                input: input
            )
            if input == nil {
                try session.closeStandardInput()
            }
        } catch {
            throw DevContainerError(
                .runtimeUnavailable,
                message: "cannot launch Apple container CLI: \(error)"
            )
        }

        var standardOutput = Data()
        var standardError = Data()
        for try await frame in session.frames {
            switch frame.channel {
            case .standardOutput:
                standardOutput.append(frame.data)
            case .standardError:
                standardError.append(frame.data)
            case .standardInput:
                break
            }
        }
        let exitCode = try await session.wait()
        return AppleCommandResult(
            standardOutput: standardOutput,
            standardError: standardError,
            exitCode: exitCode
        )
    }
}
