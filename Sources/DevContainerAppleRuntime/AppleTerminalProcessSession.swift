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

import ContainerizationOS
import Darwin
import DevContainerModel
import DevContainerProcess
import DevContainerRuntimeSPI
import Foundation

private struct AppleTerminalSessionIO: @unchecked Sendable {
    let controllerOutput: FileHandle
    let controllerInput: FileHandle
    let terminal: FileHandle
    let frames: AsyncThrowingStream<RuntimeIOFrame, any Error>
    let frameContinuation: AsyncThrowingStream<RuntimeIOFrame, any Error>.Continuation
    let termination: AsyncStream<Int32>
    let terminationContinuation: AsyncStream<Int32>.Continuation
    let outputEnd: AsyncStream<Void>
    let outputEndContinuation: AsyncStream<Void>.Continuation

    init() throws {
        var controllerDescriptor: Int32 = -1
        var terminalDescriptor: Int32 = -1
        var size = winsize(
            ws_row: 24,
            ws_col: 80,
            ws_xpixel: 0,
            ws_ypixel: 0
        )
        guard openpty(
            &controllerDescriptor,
            &terminalDescriptor,
            nil,
            nil,
            &size
        ) == 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        let inputDescriptor = fcntl(
            controllerDescriptor,
            F_DUPFD_CLOEXEC,
            0
        )
        guard inputDescriptor >= 0 else {
            Darwin.close(controllerDescriptor)
            Darwin.close(terminalDescriptor)
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        controllerOutput = FileHandle(
            fileDescriptor: controllerDescriptor,
            closeOnDealloc: true
        )
        controllerInput = FileHandle(
            fileDescriptor: inputDescriptor,
            closeOnDealloc: true
        )
        terminal = FileHandle(
            fileDescriptor: terminalDescriptor,
            closeOnDealloc: true
        )
        (frames, frameContinuation) = AsyncThrowingStream.makeStream()
        (termination, terminationContinuation) = AsyncStream.makeStream()
        (outputEnd, outputEndContinuation) = AsyncStream.makeStream()
    }
}

/// Runs Apple's CLI on a real host pseudo-terminal. This lets the CLI own the
/// guest process descriptor transfer while retaining Docker-compatible TTY
/// input, merged output, and resize behaviour.
final class AppleTerminalProcessSession: RuntimeProcessSession, @unchecked Sendable {
    let frames: AsyncThrowingStream<RuntimeIOFrame, any Error>

    private let command: Command
    private let termination: OwnedProcessTermination
    private let inputWriter: ProcessInputWriter
    private let outputMonitor: ProcessPipeMonitor
    private let controllerDescriptor: Int32
    private let completion: Task<Int32, any Error>

    init(
        executable: URL,
        arguments: [String],
        environment: [String: String]
    ) throws {
        let streams = try AppleTerminalSessionIO()
        let inputWriter = ProcessInputWriter(
            handle: streams.controllerInput,
            label: "io.github.stephenlclarke.devcontainer.terminal-process-input",
            nonBlocking: false
        )
        let outputMonitor = Self.outputMonitor(streams)
        let command = Self.command(
            executable: executable,
            arguments: arguments,
            environment: environment,
            terminal: streams.terminal
        )
        let termination = OwnedProcessTermination()
        try Self.start(
            command,
            streams: streams,
            termination: termination,
            inputWriter: inputWriter,
            outputMonitor: outputMonitor
        )
        completion = Self.completionTask(
            streams,
            inputWriter: inputWriter
        )
        self.command = command
        self.termination = termination
        self.inputWriter = inputWriter
        self.outputMonitor = outputMonitor
        controllerDescriptor = streams.controllerOutput.fileDescriptor
        frames = streams.frames
    }

    private static func outputMonitor(
        _ streams: AppleTerminalSessionIO
    ) -> ProcessPipeMonitor {
        ProcessPipeMonitor(
            handle: streams.controllerOutput,
            channel: .standardOutput,
            end: streams.outputEndContinuation,
            frames: streams.frameContinuation,
            endOnEIO: true,
            closeHandleOnFinish: false
        )
    }

    private static func command(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        terminal: FileHandle
    ) -> Command {
        var command = Command(
            executable.path,
            arguments: arguments,
            environment: environment.sorted { $0.key < $1.key }
                .map { "\($0.key)=\($0.value)" }
        )
        command.stdin = terminal
        command.stdout = terminal
        command.stderr = terminal
        command.attrs.setsid = true
        command.attrs.setctty = true
        return command
    }

    private static func start(
        _ command: Command,
        streams: AppleTerminalSessionIO,
        termination: OwnedProcessTermination,
        inputWriter: ProcessInputWriter,
        outputMonitor: ProcessPipeMonitor
    ) throws {
        do {
            try command.start()
            termination.didLaunch(processGroup: command.pid)
            try streams.terminal.close()
        } catch {
            try? streams.terminal.close()
            inputWriter.cancel()
            outputMonitor.cancel()
            streams.frameContinuation.finish(throwing: error)
            streams.terminationContinuation.finish()
            throw error
        }
        let launchedCommand = command
        Thread.detachNewThread {
            let status: Int32
            do {
                status = try launchedCommand.wait()
            } catch {
                status = 255
            }
            termination.didExit()
            streams.terminationContinuation.yield(status)
            streams.terminationContinuation.finish()
        }
    }

    private static func completionTask(
        _ streams: AppleTerminalSessionIO,
        inputWriter: ProcessInputWriter
    ) -> Task<Int32, any Error> {
        Task {
            var exitCode: Int32 = 255
            for await status in streams.termination {
                exitCode = status
                break
            }
            inputWriter.cancel()
            for await _ in streams.outputEnd { /* Completion latch for PTY output. */ }
            streams.frameContinuation.finish()
            return exitCode
        }
    }

    func write(_ data: Data) async throws {
        guard termination.isRunning else {
            throw DevContainerError(
                .conflict,
                message: "process is no longer running"
            )
        }
        try await inputWriter.write(data)
    }

    func closeStandardInput() async throws {
        guard termination.isRunning else {
            return
        }
        // A pseudo-terminal has one duplex controller endpoint rather than an
        // independently closeable write side. EOT provides terminal EOF.
        try await inputWriter.write(Data([0x04]))
    }

    func resize(width: UInt16, height: UInt16) throws {
        guard width > 0, height > 0 else {
            return
        }
        var size = winsize(
            ws_row: height,
            ws_col: width,
            ws_xpixel: 0,
            ws_ypixel: 0
        )
        guard Darwin.ioctl(controllerDescriptor, TIOCSWINSZ, &size) == 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
    }

    func wait() async throws -> Int32 {
        try await completion.value
    }

    func cancel() {
        completion.cancel()
        inputWriter.cancel()
        outputMonitor.cancel()
        termination.cancel()
    }
}
