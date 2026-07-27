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

import ContainerAPIClient
import ContainerizationOS
import ContainerResource
import Darwin
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

private struct DirectProcessStreams: @unchecked Sendable {
    let standardInput: Pipe?
    let standardOutput: Pipe?
    let standardError: Pipe?
    let frames: AsyncThrowingStream<RuntimeIOFrame, any Error>
    let frameContinuation: AsyncThrowingStream<RuntimeIOFrame, any Error>.Continuation
    let outputEnd: AsyncStream<Void>
    let outputEndContinuation: AsyncStream<Void>.Continuation
    let errorEnd: AsyncStream<Void>
    let errorEndContinuation: AsyncStream<Void>.Continuation
    let drainState: PipeDrainState

    init(
        standardInput: Pipe?,
        standardOutput: Pipe?,
        standardError: Pipe?
    ) {
        self.standardInput = standardInput
        self.standardOutput = standardOutput
        self.standardError = standardError
        (frames, frameContinuation) = AsyncThrowingStream.makeStream()
        (outputEnd, outputEndContinuation) = AsyncStream.makeStream()
        (errorEnd, errorEndContinuation) = AsyncStream.makeStream()
        drainState = PipeDrainState(
            outputFinished: standardOutput == nil,
            errorFinished: standardError == nil
        )
    }
}

/// A Docker-style process session backed directly by Apple's public container
/// API client. Direct process handles are necessary for lossless duplex I/O and
/// terminal resizing; the `container exec` command does not expose its process
/// identifier to callers.
final class AppleDirectProcessSession: RuntimeProcessSession, @unchecked Sendable {
    let frames: AsyncThrowingStream<RuntimeIOFrame, any Error>

    private let process: any ClientProcess
    private let standardInput: Pipe?
    private let outputMonitor: DirectPipeMonitor?
    private let errorMonitor: DirectPipeMonitor?
    private let startup: Task<Void, any Error>
    private let completion: Task<Int32, any Error>
    private let stateLock = NSLock()
    private var inputClosed = false

    init(
        process: any ClientProcess,
        standardInput: Pipe?,
        standardOutput: Pipe?,
        standardError: Pipe?
    ) {
        self.process = process
        self.standardInput = standardInput
        let streams = DirectProcessStreams(
            standardInput: standardInput,
            standardOutput: standardOutput,
            standardError: standardError
        )
        frames = streams.frames
        outputMonitor = Self.monitor(
            streams.standardOutput,
            channel: .standardOutput,
            streams: streams
        )
        errorMonitor = Self.monitor(
            streams.standardError,
            channel: .standardError,
            streams: streams
        )
        let startup = Task {
            Self.trace("starting process \(process.id)")
            try await process.start()
            Self.trace("started process \(process.id)")
        }
        self.startup = startup
        completion = Self.completionTask(
            process: process,
            startup: startup,
            streams: streams,
            outputMonitor: outputMonitor,
            errorMonitor: errorMonitor
        )
    }

    private static func monitor(
        _ pipe: Pipe?,
        channel: RuntimeIOChannel,
        streams: DirectProcessStreams
    ) -> DirectPipeMonitor? {
        guard let pipe else {
            if channel == .standardOutput {
                streams.outputEndContinuation.finish()
            } else {
                streams.errorEndContinuation.finish()
            }
            return nil
        }
        return DirectPipeMonitor(
            pipe: pipe,
            channel: channel,
            streams: streams
        )
    }

    private static func completionTask(
        process: any ClientProcess,
        startup: Task<Void, any Error>,
        streams: DirectProcessStreams,
        outputMonitor: DirectPipeMonitor?,
        errorMonitor: DirectPipeMonitor?
    ) -> Task<Int32, any Error> {
        Task {
            do {
                try await startup.value
                try? streams.standardInput?.fileHandleForReading.close()
                try? streams.standardOutput?.fileHandleForWriting.close()
                try? streams.standardError?.fileHandleForWriting.close()
                let exitCode = try await process.wait()
                Self.trace("wait completed for \(process.id) with \(exitCode)")
                streams.drainState.markActivity()
                let deadline = ContinuousClock.now + .seconds(30)
                while !streams.drainState.isDrained(idleNanoseconds: 250_000_000),
                      ContinuousClock.now < deadline
                {
                    try await Task.sleep(for: .milliseconds(20))
                }
                outputMonitor?.cancel()
                errorMonitor?.cancel()
                for await _ in streams.outputEnd { /* Completion latch for stdout. */ }
                for await _ in streams.errorEnd { /* Completion latch for stderr. */ }
                Self.trace("I/O drained for \(process.id)")
                streams.frameContinuation.finish()
                return exitCode
            } catch {
                Self.trace("process \(process.id) failed: \(error)")
                outputMonitor?.cancel()
                errorMonitor?.cancel()
                for await _ in streams.outputEnd { /* Completion latch for stdout. */ }
                for await _ in streams.errorEnd { /* Completion latch for stderr. */ }
                streams.frameContinuation.finish(throwing: error)
                throw error
            }
        }
    }

    private static func validatedExecutable(for spec: ExecSpec) throws -> String {
        guard let executable = spec.command.first, !executable.isEmpty else {
            throw DevContainerError(.invalidRequest, message: "exec command is empty")
        }
        return executable
    }

    static func create(
        containerID: String,
        spec: ExecSpec,
        client: ContainerClient = ContainerClient()
    ) async throws -> AppleDirectProcessSession {
        _ = try validatedExecutable(for: spec)
        let container = try await client.get(id: containerID)
        return try await create(
            containerID: containerID,
            spec: spec,
            inheritedConfiguration: container.configuration.initProcess
        ) { containerID, processID, configuration, standardIO in
            try await client.createProcess(
                containerId: containerID,
                processId: processID,
                configuration: configuration,
                stdio: standardIO
            )
        }
    }

    static func create(
        containerID: String,
        spec: ExecSpec,
        inheritedConfiguration: ProcessConfiguration,
        createProcess: (
            String,
            String,
            ProcessConfiguration,
            [FileHandle?]
        ) async throws -> any ClientProcess
    ) async throws -> AppleDirectProcessSession {
        let executable = try validatedExecutable(for: spec)
        trace(
            "creating process terminal=\(spec.terminal) "
                + "stdin=\(spec.attachStandardInput) "
                + "stdout=\(spec.attachStandardOutput) "
                + "stderr=\(spec.attachStandardError)"
        )

        var configuration = inheritedConfiguration
        configuration.executable = executable
        configuration.arguments = Array(spec.command.dropFirst())
        configuration.environment = mergedEnvironment(
            configuration.environment,
            overrides: spec.environment
        )
        if let workingDirectory = spec.workingDirectory, !workingDirectory.isEmpty {
            configuration.workingDirectory = workingDirectory
        }
        if let user = spec.user, !user.isEmpty {
            configuration.user = .raw(userString: user)
        }
        configuration.terminal = spec.terminal

        let standardInput = spec.attachStandardInput ? Pipe() : nil
        let attachTerminalOutput =
            spec.terminal && (spec.attachStandardOutput || spec.attachStandardError)
        let standardOutput =
            (spec.attachStandardOutput || attachTerminalOutput) ? Pipe() : nil
        let standardError =
            (!spec.terminal && spec.attachStandardError) ? Pipe() : nil
        let standardIO = try AppleXPCFileHandleTransfer.copies(
            of: [
                standardInput?.fileHandleForReading,
                standardOutput?.fileHandleForWriting,
                standardError?.fileHandleForWriting
            ]
        )
        let process = try await createProcess(
            containerID,
            UUID().uuidString.lowercased(),
            configuration,
            standardIO
        )
        let session = AppleDirectProcessSession(
            process: process,
            standardInput: standardInput,
            standardOutput: standardOutput,
            standardError: standardError
        )
        try await session.startup.value
        return session
    }

    func write(_ data: Data) async throws {
        try await startup.value
        guard let standardInput else {
            throw DevContainerError(
                .conflict,
                message: "standard input was not attached to this exec session"
            )
        }
        Self.trace("writing \(data.count) stdin bytes to \(process.id)")
        do {
            try stateLock.withLock {
                guard !inputClosed else {
                    throw DevContainerError(.conflict, message: "standard input is closed")
                }
                do {
                    try standardInput.fileHandleForWriting.write(contentsOf: data)
                } catch {
                    inputClosed = true
                    try? standardInput.fileHandleForWriting.close()
                    throw error
                }
            }
            Self.trace("wrote \(data.count) stdin bytes to \(process.id)")
        } catch {
            Self.trace("stdin write failed for \(process.id): \(error)")
            throw error
        }
    }

    func closeStandardInput() async throws {
        try await startup.value
        guard let standardInput else {
            return
        }
        try stateLock.withLock {
            guard !inputClosed else {
                return
            }
            inputClosed = true
            Self.trace("closing stdin for \(process.id)")
            try standardInput.fileHandleForWriting.close()
        }
    }

    func resize(width: UInt16, height: UInt16) async throws {
        try await startup.value
        try await process.resize(Terminal.Size(width: width, height: height))
    }

    func wait() async throws -> Int32 {
        try await completion.value
    }

    func cancel() async {
        startup.cancel()
        completion.cancel()
        try? await process.kill(SIGKILL)
        try? await closeStandardInput()
    }

    static func mergedEnvironment(
        _ inherited: [String],
        overrides: [String: String]
    ) -> [String] {
        var environment = Dictionary(
            uniqueKeysWithValues: inherited.map { value in
                let parts = value.split(
                    separator: "=",
                    maxSplits: 1,
                    omittingEmptySubsequences: false
                )
                return (
                    String(parts[0]),
                    parts.count == 2 ? String(parts[1]) : ""
                )
            }
        )
        environment.merge(overrides) { _, new in new }
        return environment.keys.sorted().map { "\($0)=\(environment[$0] ?? "")" }
    }

    fileprivate static func trace(_ message: String) {
        guard ProcessInfo.processInfo.environment["DEVCONTAINER_TRACE_PROCESS"] == "1" else {
            return
        }
        try? FileHandle.standardError.write(
            contentsOf: Data("devcontainer-engine: process trace: \(message)\n".utf8)
        )
    }
}

/// Apple's stock ContainerAPIClient consumes a transferred descriptor with
/// `Darwin.close` while the caller's `FileHandle` still believes it owns that
/// descriptor. Pass non-owning duplicates across the XPC boundary so delayed
/// Foundation deallocation cannot close an unrelated descriptor that reused
/// the same integer value.
enum AppleXPCFileHandleTransfer {
    static func copies(of handles: [FileHandle?]) throws -> [FileHandle?] {
        var copies: [FileHandle?] = []
        copies.reserveCapacity(handles.count)
        do {
            for handle in handles {
                guard let handle else {
                    copies.append(nil)
                    continue
                }
                let descriptor = fcntl(handle.fileDescriptor, F_DUPFD_CLOEXEC, 0)
                guard descriptor >= 0 else {
                    throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
                }
                copies.append(
                    FileHandle(
                        fileDescriptor: descriptor,
                        closeOnDealloc: false
                    )
                )
            }
            return copies
        } catch {
            for case let handle? in copies {
                Darwin.close(handle.fileDescriptor)
            }
            throw error
        }
    }
}

private final class DirectPipeMonitor: @unchecked Sendable {
    private let source: DispatchSourceRead
    private let descriptor: Int32
    private let channel: RuntimeIOChannel
    private let streams: DirectProcessStreams
    private let stateLock = NSLock()
    private var cancelled = false

    init(
        pipe: Pipe,
        channel: RuntimeIOChannel,
        streams: DirectProcessStreams
    ) {
        descriptor = pipe.fileHandleForReading.fileDescriptor
        self.channel = channel
        self.streams = streams
        source = DispatchSource.makeReadSource(
            fileDescriptor: descriptor,
            queue: DispatchQueue(
                label: "io.github.stephenlclarke.devcontainer.direct-process-\(channel)",
                qos: .userInitiated
            )
        )
        let flags = fcntl(descriptor, F_GETFL)
        if flags >= 0 {
            _ = fcntl(descriptor, F_SETFL, flags | O_NONBLOCK)
        }
        source.setEventHandler { [weak self] in
            self?.readAvailable()
        }
        source.setCancelHandler { [streams, channel] in
            if channel == .standardOutput {
                streams.drainState.finishOutput()
                AppleDirectProcessSession.trace("stdout EOF")
                streams.outputEndContinuation.finish()
            } else {
                streams.drainState.finishError()
                AppleDirectProcessSession.trace("stderr EOF")
                streams.errorEndContinuation.finish()
            }
        }
        source.resume()
    }

    func cancel() {
        let shouldCancel = stateLock.withLock {
            guard !cancelled else {
                return false
            }
            cancelled = true
            return true
        }
        if shouldCancel {
            source.cancel()
        }
    }

    private func readAvailable() {
        var buffer = [UInt8](repeating: 0, count: 64 * 1024)
        while true {
            let count = buffer.withUnsafeMutableBytes { bytes in
                Darwin.read(descriptor, bytes.baseAddress, bytes.count)
            }
            if count > 0 {
                let data = Data(buffer.prefix(count))
                streams.drainState.markActivity()
                AppleDirectProcessSession.trace(
                    "\(channel == .standardOutput ? "stdout" : "stderr") produced \(count) bytes"
                )
                streams.frameContinuation.yield(
                    RuntimeIOFrame(channel: channel, data: data)
                )
                continue
            }
            if count == 0 {
                cancel()
                return
            }
            if errno == EINTR {
                continue
            }
            if errno == EAGAIN || errno == EWOULDBLOCK {
                return
            }
            let code = POSIXErrorCode(rawValue: errno) ?? .EIO
            streams.frameContinuation.finish(throwing: POSIXError(code))
            cancel()
            return
        }
    }
}

private final class PipeDrainState: @unchecked Sendable {
    private let lock = NSLock()
    private var lastActivity = DispatchTime.now().uptimeNanoseconds
    private var outputFinished: Bool
    private var errorFinished: Bool

    init(outputFinished: Bool, errorFinished: Bool) {
        self.outputFinished = outputFinished
        self.errorFinished = errorFinished
    }

    func markActivity() {
        lock.withLock {
            lastActivity = DispatchTime.now().uptimeNanoseconds
        }
    }

    func finishOutput() {
        lock.withLock {
            outputFinished = true
            lastActivity = DispatchTime.now().uptimeNanoseconds
        }
    }

    func finishError() {
        lock.withLock {
            errorFinished = true
            lastActivity = DispatchTime.now().uptimeNanoseconds
        }
    }

    func isDrained(idleNanoseconds: UInt64) -> Bool {
        lock.withLock {
            if outputFinished, errorFinished {
                return true
            }
            let now = DispatchTime.now().uptimeNanoseconds
            return now >= lastActivity
                && now - lastActivity >= idleNanoseconds
        }
    }
}
