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
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

private struct ProcessLaunchConfiguration {
    let executable: URL
    let arguments: [String]
    let environment: [String: String]
    let workingDirectory: URL?
}

private struct ProcessSessionIO: @unchecked Sendable {
    let standardInput = Pipe()
    let standardOutput = Pipe()
    let standardError = Pipe()
    let frames: AsyncThrowingStream<RuntimeIOFrame, any Error>
    let frameContinuation: AsyncThrowingStream<RuntimeIOFrame, any Error>.Continuation
    let termination: AsyncStream<Int32>
    let terminationContinuation: AsyncStream<Int32>.Continuation
    let outputEnd: AsyncStream<Void>
    let outputEndContinuation: AsyncStream<Void>.Continuation
    let errorEnd: AsyncStream<Void>
    let errorEndContinuation: AsyncStream<Void>.Continuation

    init() {
        (frames, frameContinuation) = AsyncThrowingStream.makeStream()
        (termination, terminationContinuation) = AsyncStream.makeStream()
        (outputEnd, outputEndContinuation) = AsyncStream.makeStream()
        (errorEnd, errorEndContinuation) = AsyncStream.makeStream()
    }
}

final class AppleProcessSession: RuntimeProcessSession, @unchecked Sendable {
    let frames: AsyncThrowingStream<RuntimeIOFrame, any Error>

    private let process: Process
    private let inputWriter: ProcessInputWriter
    private let outputMonitor: ProcessPipeMonitor
    private let errorMonitor: ProcessPipeMonitor
    private let completion: Task<Int32, any Error>

    init(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        workingDirectory: URL? = nil
    ) throws {
        let process = Process()
        let streams = ProcessSessionIO()
        let inputWriter = ProcessInputWriter(pipe: streams.standardInput)
        let monitors = Self.configure(
            process,
            configuration: ProcessLaunchConfiguration(
                executable: executable,
                arguments: arguments,
                environment: environment,
                workingDirectory: workingDirectory
            ),
            streams: streams
        )
        try Self.start(process, streams: streams)
        completion = Self.completionTask(
            streams,
            inputWriter: inputWriter
        )
        self.process = process
        self.inputWriter = inputWriter
        outputMonitor = monitors.output
        errorMonitor = monitors.error
        frames = streams.frames
    }

    private static func configure(
        _ process: Process,
        configuration: ProcessLaunchConfiguration,
        streams: ProcessSessionIO
    ) -> (output: ProcessPipeMonitor, error: ProcessPipeMonitor) {
        process.executableURL = configuration.executable
        process.arguments = configuration.arguments
        process.environment = configuration.environment
        process.currentDirectoryURL = configuration.workingDirectory
        process.standardInput = streams.standardInput
        process.standardOutput = streams.standardOutput
        process.standardError = streams.standardError
        process.terminationHandler = { process in
            streams.terminationContinuation.yield(process.terminationStatus)
            streams.terminationContinuation.finish()
        }
        let output = drain(
            streams.standardOutput,
            channel: .standardOutput,
            end: streams.outputEndContinuation,
            frames: streams.frameContinuation
        )
        let error = drain(
            streams.standardError,
            channel: .standardError,
            end: streams.errorEndContinuation,
            frames: streams.frameContinuation
        )
        return (output, error)
    }

    private static func drain(
        _ pipe: Pipe,
        channel: RuntimeIOChannel,
        end: AsyncStream<Void>.Continuation,
        frames: AsyncThrowingStream<RuntimeIOFrame, any Error>.Continuation
    ) -> ProcessPipeMonitor {
        ProcessPipeMonitor(
            pipe: pipe,
            channel: channel,
            end: end,
            frames: frames
        )
    }

    private static func start(
        _ process: Process,
        streams: ProcessSessionIO
    ) throws {
        do {
            try process.run()
            try? streams.standardInput.fileHandleForReading.close()
            try? streams.standardOutput.fileHandleForWriting.close()
            try? streams.standardError.fileHandleForWriting.close()
        } catch {
            try? streams.standardInput.fileHandleForReading.close()
            try? streams.standardInput.fileHandleForWriting.close()
            try? streams.standardOutput.fileHandleForWriting.close()
            try? streams.standardError.fileHandleForWriting.close()
            streams.frameContinuation.finish(throwing: error)
            streams.terminationContinuation.finish()
            streams.outputEndContinuation.finish()
            streams.errorEndContinuation.finish()
            throw error
        }
    }

    private static func completionTask(
        _ streams: ProcessSessionIO,
        inputWriter: ProcessInputWriter
    ) -> Task<Int32, any Error> {
        Task {
            var exitCode: Int32 = 255
            for await status in streams.termination {
                exitCode = status
                break
            }
            inputWriter.cancel()
            for await _ in streams.outputEnd { /* Completion latch for stdout. */ }
            for await _ in streams.errorEnd { /* Completion latch for stderr. */ }
            streams.frameContinuation.finish()
            return exitCode
        }
    }

    func write(_ data: Data) async throws {
        guard process.isRunning else {
            throw DevContainerError(.conflict, message: "process is no longer running")
        }
        try await inputWriter.write(data)
    }

    func closeStandardInput() async throws {
        try await inputWriter.close()
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
        inputWriter.cancel()
        guard process.isRunning else {
            return
        }
        process.terminate()
        outputMonitor.cancel()
        errorMonitor.cancel()
    }
}

/// Serializes potentially blocking writes away from Swift's cooperative
/// executor. The queue also orders EOF after every accepted input chunk.
private final class ProcessInputWriter: @unchecked Sendable {
    private let handle: FileHandle
    private let descriptor: Int32
    private let queue = DispatchQueue(
        label: "io.github.stephenlclarke.devcontainer.cli-process-input",
        qos: .userInitiated
    )
    private var closed = false

    init(pipe: Pipe) {
        handle = pipe.fileHandleForWriting
        descriptor = handle.fileDescriptor
        let flags = fcntl(descriptor, F_GETFL)
        if flags >= 0 {
            _ = fcntl(descriptor, F_SETFL, flags | O_NONBLOCK)
        }
        _ = fcntl(descriptor, F_SETNOSIGPIPE, 1)
    }

    func write(_ data: Data) async throws {
        guard !data.isEmpty else {
            return
        }
        let _: Void = try await withCheckedThrowingContinuation { continuation in
            queue.async { [self] in
                guard !closed else {
                    continuation.resume(
                        throwing: DevContainerError(
                            .conflict,
                            message: "process standard input is closed"
                        )
                    )
                    return
                }
                do {
                    try writeAll(data)
                    continuation.resume()
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    private func writeAll(_ data: Data) throws {
        try data.withUnsafeBytes { bytes in
            var offset = 0
            while offset < bytes.count {
                let count = min(16 * 1024, bytes.count - offset)
                let written = Darwin.write(
                    descriptor,
                    bytes.baseAddress?.advanced(by: offset),
                    count
                )
                if written > 0 {
                    offset += written
                    continue
                }
                if written < 0, errno == EINTR {
                    continue
                }
                if written < 0, errno == EAGAIN || errno == EWOULDBLOCK {
                    try waitUntilWritable()
                    continue
                }
                throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
            }
        }
    }

    private func waitUntilWritable() throws {
        var event = pollfd(fd: descriptor, events: Int16(POLLOUT), revents: 0)
        while true {
            let result = Darwin.poll(&event, 1, -1)
            if result > 0 {
                if event.revents & Int16(POLLOUT) != 0 {
                    return
                }
                throw POSIXError(.EPIPE)
            }
            if result < 0, errno == EINTR {
                continue
            }
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
    }

    func close() async throws {
        let _: Void = try await withCheckedThrowingContinuation { continuation in
            queue.async { [self] in
                guard !closed else {
                    continuation.resume()
                    return
                }
                closed = true
                do {
                    try handle.close()
                    continuation.resume()
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    func cancel() {
        queue.async { [self] in
            guard !closed else {
                return
            }
            closed = true
            try? handle.close()
        }
    }
}

/// Drains a launched CLI process on dedicated OS threads. stdout and stderr can
/// block independently without occupying Swift's cooperative executor or
/// relying on a one-shot readiness edge while a child performs duplex I/O.
private final class ProcessPipeMonitor: @unchecked Sendable {
    private let handle: FileHandle
    private let descriptor: Int32
    private let channel: RuntimeIOChannel
    private let end: AsyncStream<Void>.Continuation
    private let frames: AsyncThrowingStream<
        RuntimeIOFrame,
        any Error
    >.Continuation
    private let stateLock = NSLock()
    private var finished = false

    init(
        pipe: Pipe,
        channel: RuntimeIOChannel,
        end: AsyncStream<Void>.Continuation,
        frames: AsyncThrowingStream<
            RuntimeIOFrame,
            any Error
        >.Continuation
    ) {
        handle = pipe.fileHandleForReading
        descriptor = handle.fileDescriptor
        self.channel = channel
        self.end = end
        self.frames = frames
        Thread.detachNewThread { [self] in
            Thread.current.name =
                "io.github.stephenlclarke.devcontainer.cli-process-\(channel)"
            drain()
        }
    }

    func cancel() {
        finish()
    }

    private func drain() {
        defer { finish() }
        var buffer = [UInt8](repeating: 0, count: 64 * 1024)
        while true {
            let count = buffer.withUnsafeMutableBytes { bytes in
                Darwin.read(descriptor, bytes.baseAddress, bytes.count)
            }
            if count > 0 {
                frames.yield(
                    RuntimeIOFrame(
                        channel: channel,
                        data: Data(buffer.prefix(count))
                    )
                )
                continue
            }
            if count == 0 {
                return
            }
            if errno == EINTR {
                continue
            }
            if stateLock.withLock({ finished }) {
                return
            }
            let code = POSIXErrorCode(rawValue: errno) ?? .EIO
            frames.finish(throwing: POSIXError(code))
            return
        }
    }

    private func finish() {
        let shouldFinish = stateLock.withLock {
            guard !finished else {
                return false
            }
            finished = true
            return true
        }
        guard shouldFinish else {
            return
        }
        try? handle.close()
        end.finish()
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

    func write(_ data: Data) async throws {
        try await process.write(data)
    }

    func closeStandardInput() async throws {
        try await process.closeStandardInput()
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
        completion = Self.pollingTask(poll: poll, continuation: continuation)
        frames = stream
    }

    private static func pollingTask(
        poll: @escaping @Sendable () async throws -> AppleLogPoll,
        continuation: AsyncThrowingStream<RuntimeIOFrame, any Error>.Continuation
    ) -> Task<Int32, any Error> {
        Task {
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
    }

    func write(_: Data) throws {
        throw DevContainerError(
            .unsupportedCapability,
            message: "stock Apple container does not expose attach stdin"
        )
    }

    func closeStandardInput() {
        // Attach sessions are output-only because stock container has no stdin API.
    }

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
                workingDirectory: workingDirectory
            )
            if let input {
                try await session.write(input)
            }
            try await session.closeStandardInput()
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
