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
import Foundation

public struct CapturedProcessResult: Equatable, Sendable {
    public var standardOutput: Data
    public var standardError: Data
    public var exitCode: Int32
    public var omittedStandardOutputBytes: Int
    public var omittedStandardErrorBytes: Int

    public init(
        standardOutput: Data,
        standardError: Data,
        exitCode: Int32,
        omittedStandardOutputBytes: Int = 0,
        omittedStandardErrorBytes: Int = 0
    ) {
        self.standardOutput = standardOutput
        self.standardError = standardError
        self.exitCode = exitCode
        self.omittedStandardOutputBytes = omittedStandardOutputBytes
        self.omittedStandardErrorBytes = omittedStandardErrorBytes
    }
}

public enum ProcessRunner {
    public static func capturedSync(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        workingDirectory: URL? = nil,
        input: Data? = nil,
        maximumOutputBytes: Int? = nil
    ) throws -> CapturedProcessResult {
        try Task.checkCancellation()
        let context = RuntimeRequestScope.context
        let semaphore = DispatchSemaphore(value: 0)
        let result = SynchronousProcessResult()
        let operation = Task.detached {
            do {
                // Detached work avoids blocking the caller's executor, but must
                // explicitly retain its request identity and bounded lifetime.
                let capturedResult = try await RuntimeRequestScope.$context.withValue(context) {
                    try await RuntimeRequestScope.withDeadline {
                        try await captured(
                            executable: executable,
                            arguments: arguments,
                            environment: environment,
                            workingDirectory: workingDirectory,
                            input: input,
                            maximumOutputBytes: maximumOutputBytes
                        )
                    }
                }
                result.store(.success(capturedResult))
            } catch {
                result.store(.failure(error))
            }
            semaphore.signal()
        }
        // Synchronous callers cannot install an async cancellation handler.
        // Forward their cancellation while still waiting for owned cleanup.
        while semaphore.wait(timeout: .now() + .milliseconds(50)) == .timedOut {
            if Task.isCancelled {
                operation.cancel()
            }
        }
        try Task.checkCancellation()
        return try result.load().get()
    }

    // Launch, drain, cancellation, escalation, and reap form one ownership
    // transaction and must preserve their ordering.
    // swiftlint:disable:next function_body_length
    public static func captured(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        workingDirectory: URL? = nil,
        input: Data? = nil,
        maximumOutputBytes: Int? = nil
    ) async throws -> CapturedProcessResult {
        if let maximumOutputBytes {
            precondition(maximumOutputBytes >= 0)
        }
        try Task.checkCancellation()
        try RuntimeRequestScope.checkActive()
        let standardInput = input.map { _ in Pipe() }
        let standardOutput = Pipe()
        let standardError = Pipe()
        let command = configuredCommand(
            executable: executable,
            arguments: arguments,
            environment: environment,
            workingDirectory: workingDirectory
        )
        command.stdin = standardInput?.fileHandleForReading
        command.stdout = standardOutput.fileHandleForWriting
        command.stderr = standardError.fileHandleForWriting
        let termination = OwnedProcessTermination()
        let outputTask = drain(
            standardOutput.fileHandleForReading,
            maximumBytes: maximumOutputBytes
        )
        let errorTask = drain(
            standardError.fileHandleForReading,
            maximumBytes: maximumOutputBytes
        )
        do {
            try command.start()
            termination.didLaunch(processGroup: command.pid)
            try? standardInput?.fileHandleForReading.close()
            try? standardOutput.fileHandleForWriting.close()
            try? standardError.fileHandleForWriting.close()
        } catch {
            try? standardInput?.fileHandleForReading.close()
            try? standardInput?.fileHandleForWriting.close()
            try? standardOutput.fileHandleForWriting.close()
            try? standardError.fileHandleForWriting.close()
            _ = await outputTask.value
            _ = await errorTask.value
            throw error
        }
        let inputTask = Task.detached {
            guard let input, let standardInput else {
                return
            }
            try await performThrowingBlocking {
                defer { try? standardInput.fileHandleForWriting.close() }
                try standardInput.fileHandleForWriting.write(contentsOf: input)
            }
        }
        let runningCommand = command
        let waitTask = Task.detached {
            try await performThrowingBlocking {
                try runningCommand.waitUntilExit()
            }
        }
        return try await withTaskCancellationHandler {
            do {
                try await inputTask.value
                try await waitTask.value
                let output = await outputTask.value
                let error = await errorTask.value
                let exitCode = try termination.reap { try runningCommand.wait() }
                try Task.checkCancellation()
                try RuntimeRequestScope.checkActive()
                return CapturedProcessResult(
                    standardOutput: output.data,
                    standardError: error.data,
                    exitCode: exitCode,
                    omittedStandardOutputBytes: output.omitted,
                    omittedStandardErrorBytes: error.omitted
                )
            } catch {
                termination.cancel()
                _ = try? await waitTask.value
                _ = await outputTask.value
                _ = await errorTask.value
                _ = try? termination.reap { try runningCommand.wait() }
                throw error
            }
        } onCancel: {
            termination.cancel()
        }
    }

    public static func inherited(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        workingDirectory: URL? = nil
    ) async throws -> Int32 {
        try Task.checkCancellation()
        try RuntimeRequestScope.checkActive()
        let command = configuredCommand(
            executable: executable,
            arguments: arguments,
            environment: environment,
            workingDirectory: workingDirectory
        )
        command.stdin = FileHandle.standardInput
        command.stdout = FileHandle.standardOutput
        command.stderr = FileHandle.standardError
        let ownsTerminal = isatty(STDIN_FILENO) == 1
        let parentProcessGroup = ownsTerminal ? getpgrp() : nil
        defer { restoreForegroundProcessGroup(parentProcessGroup) }
        command.attributes.setForegroundProcessGroup = ownsTerminal
        let termination = OwnedProcessTermination()
        try command.start()
        termination.didLaunch(processGroup: command.pid)
        let runningCommand = command
        let waitTask = Task.detached {
            try await performThrowingBlocking {
                try runningCommand.waitUntilExit()
            }
        }
        return try await withTaskCancellationHandler {
            do {
                try await waitTask.value
                let exitCode = try termination.reap { try runningCommand.wait() }
                try Task.checkCancellation()
                try RuntimeRequestScope.checkActive()
                return exitCode
            } catch {
                termination.cancel()
                _ = try? await waitTask.value
                _ = try? termination.reap { try runningCommand.wait() }
                throw error
            }
        } onCancel: {
            termination.cancel()
        }
    }

    private static func configuredCommand(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        workingDirectory: URL?
    ) -> ProcessCommand {
        let command = ProcessCommand(
            executable.path,
            arguments: arguments,
            environment: environment.sorted { $0.key < $1.key }
                .map { "\($0.key)=\($0.value)" },
            directory: workingDirectory?.path
        )
        command.attributes.setProcessGroup = true
        return command
    }

    private static func drain(
        _ handle: FileHandle,
        maximumBytes: Int?
    ) -> Task<(data: Data, omitted: Int), Never> {
        Task.detached {
            await performBlocking {
                defer { try? handle.close() }
                var retained = Data()
                var omitted = 0
                while let chunk = try? handle.read(upToCount: 64 * 1024),
                      !chunk.isEmpty
                {
                    let available = retainedByteCount(
                        maximumBytes: maximumBytes,
                        retainedBytes: retained.count,
                        chunkBytes: chunk.count
                    )
                    retained.append(chunk.prefix(available))
                    omitted += chunk.count - available
                }
                return (retained, omitted)
            }
        }
    }

    private static func retainedByteCount(
        maximumBytes: Int?,
        retainedBytes: Int,
        chunkBytes: Int
    ) -> Int {
        guard let maximumBytes else {
            return chunkBytes
        }
        return min(chunkBytes, max(0, maximumBytes - retainedBytes))
    }

    private static func performBlocking<Value: Sendable>(
        _ operation: @escaping @Sendable () -> Value
    ) async -> Value {
        // FileHandle reads and wait4 can block indefinitely. Keep them off the
        // cooperative executor so a small runner can still deliver cancellation.
        await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .utility).async {
                continuation.resume(returning: operation())
            }
        }
    }

    private static func performThrowingBlocking<Value: Sendable>(
        _ operation: @escaping @Sendable () throws -> Value
    ) async throws -> Value {
        try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .utility).async {
                do {
                    try continuation.resume(returning: operation())
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    private static func restoreForegroundProcessGroup(_ processGroup: pid_t?) {
        guard let processGroup, isatty(STDIN_FILENO) == 1 else {
            return
        }
        let previous = Darwin.signal(SIGTTOU, SIG_IGN)
        _ = tcsetpgrp(STDIN_FILENO, processGroup)
        _ = Darwin.signal(SIGTTOU, previous)
    }
}

private final class SynchronousProcessResult: @unchecked Sendable {
    private let lock = NSLock()
    private var result: Result<CapturedProcessResult, any Error>?

    func store(_ result: Result<CapturedProcessResult, any Error>) {
        lock.withLock {
            self.result = result
        }
    }

    func load() -> Result<CapturedProcessResult, any Error> {
        lock.withLock {
            result ?? .failure(
                DevContainerError(
                    .stateCorruption,
                    message: "synchronous process completed without a result"
                )
            )
        }
    }
}

public final class OwnedProcessTermination: @unchecked Sendable {
    private static let gracePeriod = DispatchTimeInterval.milliseconds(500)

    private let lock = NSLock()
    private var processGroup: pid_t?
    private var running = false
    private var cancellationRequested = false
    private var escalation: DispatchWorkItem?

    public init() {
        // Mutable termination state is initialized by the property defaults.
    }

    public var isRunning: Bool {
        lock.withLock { running }
    }

    public func didLaunch(processGroup: pid_t) {
        lock.withLock {
            self.processGroup = processGroup
            running = true
            if cancellationRequested {
                beginTermination(processGroup)
            }
        }
    }

    public func didExit() {
        lock.withLock { clearOwnership() }
    }

    /// Caller first observes WNOWAIT exit and finishes draining inherited pipes.
    /// Reaping and clearing ownership are atomic relative to group signalling.
    func reap(_ operation: () throws -> Int32) throws -> Int32 {
        try lock.withLock {
            guard running else { throw POSIXError(.ECHILD) }
            defer { clearOwnership() }
            return try operation()
        }
    }

    private func clearOwnership() {
        running = false
        escalation?.cancel()
        escalation = nil
        processGroup = nil
    }

    public func cancel() {
        lock.withLock {
            guard !cancellationRequested else {
                return
            }
            cancellationRequested = true
            if running, let processGroup {
                beginTermination(processGroup)
            }
        }
    }

    /// Called only while ownership is locked; the leader has not been reaped.
    private func beginTermination(_ processGroup: pid_t) {
        _ = Darwin.kill(-processGroup, SIGTERM)
        let work = DispatchWorkItem { [weak self] in
            self?.forceTerminate(processGroup)
        }
        escalation = work
        DispatchQueue.global(qos: .utility).asyncAfter(
            deadline: .now() + Self.gracePeriod,
            execute: work
        )
    }

    private func forceTerminate(_ processGroup: pid_t) {
        lock.withLock {
            guard running, self.processGroup == processGroup else { return }
            _ = Darwin.kill(-processGroup, SIGKILL)
        }
    }
}
