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
        Self.monitor(streams.standardOutput, channel: .standardOutput, streams: streams)
        Self.monitor(streams.standardError, channel: .standardError, streams: streams)
        completion = Self.completionTask(process: process, streams: streams)
    }

    private static func monitor(
        _ pipe: Pipe?,
        channel: RuntimeIOChannel,
        streams: DirectProcessStreams
    ) {
        guard let pipe else {
            if channel == .standardOutput {
                streams.outputEndContinuation.finish()
            } else {
                streams.errorEndContinuation.finish()
            }
            return
        }
        pipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
                if channel == .standardOutput {
                    streams.drainState.finishOutput()
                    Self.trace("stdout EOF")
                    streams.outputEndContinuation.finish()
                } else {
                    streams.drainState.finishError()
                    Self.trace("stderr EOF")
                    streams.errorEndContinuation.finish()
                }
            } else {
                streams.drainState.markActivity()
                streams.frameContinuation.yield(RuntimeIOFrame(channel: channel, data: data))
            }
        }
    }

    private static func completionTask(
        process: any ClientProcess,
        streams: DirectProcessStreams
    ) -> Task<Int32, any Error> {
        Task {
            do {
                Self.trace("starting process \(process.id)")
                try await process.start()
                Self.trace("started process \(process.id)")
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
                streams.standardOutput?.fileHandleForReading.readabilityHandler = nil
                streams.standardError?.fileHandleForReading.readabilityHandler = nil
                try? streams.standardOutput?.fileHandleForReading.close()
                try? streams.standardError?.fileHandleForReading.close()
                streams.outputEndContinuation.finish()
                streams.errorEndContinuation.finish()
                for await _ in streams.outputEnd { /* Completion latch for stdout. */ }
                for await _ in streams.errorEnd { /* Completion latch for stderr. */ }
                Self.trace("I/O drained for \(process.id)")
                streams.frameContinuation.finish()
                return exitCode
            } catch {
                Self.trace("process \(process.id) failed: \(error)")
                streams.frameContinuation.finish(throwing: error)
                throw error
            }
        }
    }

    static func create(
        containerID: String,
        spec: ExecSpec
    ) async throws -> AppleDirectProcessSession {
        guard let executable = spec.command.first, !executable.isEmpty else {
            throw DevContainerError(.invalidRequest, message: "exec command is empty")
        }

        let client = ContainerClient()
        let container = try await client.get(id: containerID)
        var configuration = container.configuration.initProcess
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
        let process = try await client.createProcess(
            containerId: containerID,
            processId: UUID().uuidString.lowercased(),
            configuration: configuration,
            stdio: [
                standardInput?.fileHandleForReading,
                standardOutput?.fileHandleForWriting,
                standardError?.fileHandleForWriting
            ]
        )
        return AppleDirectProcessSession(
            process: process,
            standardInput: standardInput,
            standardOutput: standardOutput,
            standardError: standardError
        )
    }

    func write(_ data: Data) throws {
        guard let standardInput else {
            throw DevContainerError(
                .conflict,
                message: "standard input was not attached to this exec session"
            )
        }
        let closed = stateLock.withLock { inputClosed }
        guard !closed else {
            throw DevContainerError(.conflict, message: "standard input is closed")
        }
        try standardInput.fileHandleForWriting.write(contentsOf: data)
    }

    func closeStandardInput() throws {
        guard let standardInput else {
            return
        }
        let shouldClose = stateLock.withLock {
            guard !inputClosed else {
                return false
            }
            inputClosed = true
            return true
        }
        if shouldClose {
            try standardInput.fileHandleForWriting.close()
        }
    }

    func resize(width: UInt16, height: UInt16) async throws {
        try await process.resize(Terminal.Size(width: width, height: height))
    }

    func wait() async throws -> Int32 {
        try await completion.value
    }

    func cancel() async {
        try? await process.kill(SIGKILL)
        completion.cancel()
        try? closeStandardInput()
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

    private static func trace(_ message: String) {
        guard ProcessInfo.processInfo.environment["DEVCONTAINER_TRACE_PROCESS"] == "1" else {
            return
        }
        try? FileHandle.standardError.write(
            contentsOf: Data("devcontainer-engine: process trace: \(message)\n".utf8)
        )
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
