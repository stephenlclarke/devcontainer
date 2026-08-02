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
@testable import DevContainerAppleRuntime
import DevContainerModel
import Foundation
import Testing

struct AppleDirectProcessSessionTests {
    @Test
    func `XPC transfer copies cannot close reused descriptors`() throws {
        let pipe = Pipe()
        var copies: [FileHandle?]? = try AppleXPCFileHandleTransfer.copies(
            of: [pipe.fileHandleForReading, nil]
        )
        let copiedDescriptor = try #require(copies?[0]?.fileDescriptor)
        #expect(copiedDescriptor != pipe.fileHandleForReading.fileDescriptor)
        #expect(fcntl(pipe.fileHandleForReading.fileDescriptor, F_GETFD) >= 0)

        Darwin.close(copiedDescriptor)
        let nullDescriptor = open("/dev/null", O_RDONLY | O_CLOEXEC)
        #expect(nullDescriptor >= 0)
        if nullDescriptor != copiedDescriptor {
            #expect(dup2(nullDescriptor, copiedDescriptor) == copiedDescriptor)
            Darwin.close(nullDescriptor)
        }

        copies = nil
        #expect(fcntl(copiedDescriptor, F_GETFD) >= 0)
        Darwin.close(copiedDescriptor)
        #expect(fcntl(pipe.fileHandleForReading.fileDescriptor, F_GETFD) >= 0)
    }

    @Test
    func `XPC transfer closes earlier copies when a later descriptor is invalid`() throws {
        let valid = Pipe()
        let nullDescriptor = open("/dev/null", O_RDONLY | O_CLOEXEC)
        #expect(nullDescriptor >= 0)
        let invalidDescriptor = fcntl(nullDescriptor, F_DUPFD_CLOEXEC, 1024)
        #expect(invalidDescriptor >= 0)
        Darwin.close(nullDescriptor)
        let invalid = FileHandle(
            fileDescriptor: invalidDescriptor,
            closeOnDealloc: false
        )
        Darwin.close(invalidDescriptor)

        #expect(throws: POSIXError.self) {
            _ = try AppleXPCFileHandleTransfer.copies(
                of: [
                    valid.fileHandleForReading,
                    invalid
                ]
            )
        }
        #expect(fcntl(valid.fileHandleForReading.fileDescriptor, F_GETFD) >= 0)
    }

    @Test
    func `direct process creation applies exec overrides and standard IO`() async throws {
        let inherited = ProcessConfiguration(
            executable: "/bin/inherited",
            arguments: ["old"],
            environment: ["A=old", "Z=last"],
            workingDirectory: "/inherited",
            terminal: false,
            user: .id(uid: 501, gid: 20)
        )
        let capture = DirectProcessCreationCapture()
        let process = MockClientProcess(exitCode: 0)

        let session = try await AppleDirectProcessSession.create(
            containerID: "example",
            spec: ExecSpec(
                command: ["/bin/echo", "hello"],
                environment: ["A": "new", "B": "second"],
                workingDirectory: "/workspace",
                user: "1000:1000",
                terminal: false,
                attachStandardInput: true,
                attachStandardOutput: true,
                attachStandardError: true
            ),
            inheritedConfiguration: inherited
        ) { containerID, processID, configuration, standardIO in
            await capture.record(
                containerID: containerID,
                processID: processID,
                configuration: configuration,
                standardIO: standardIO
            )
            for case let handle? in standardIO {
                Darwin.close(handle.fileDescriptor)
            }
            return process
        }

        try await session.closeStandardInput()
        #expect(try await session.wait() == 0)
        let created = await capture.value
        #expect(created?.containerID == "example")
        #expect(created?.processID.count == 36)
        #expect(created?.configuration.executable == "/bin/echo")
        #expect(created?.configuration.arguments == ["hello"])
        #expect(created?.configuration.environment == ["A=new", "B=second", "Z=last"])
        #expect(created?.configuration.workingDirectory == "/workspace")
        #expect(created?.configuration.user == .raw(userString: "1000:1000"))
        #expect(created?.configuration.terminal == false)
        #expect(created?.standardIO == [true, true, true])
    }

    @Test
    func `direct session preserves duplex streams exit and terminal resize`() async throws {
        let input = Pipe()
        let output = Pipe()
        let error = Pipe()
        let process = MockClientProcess(
            input: input,
            output: output,
            error: error,
            exitCode: 23
        )
        let session = AppleDirectProcessSession(
            process: process,
            standardInput: input,
            standardOutput: output,
            standardError: error
        )

        try await session.write(Data("request".utf8))
        try await session.closeStandardInput()
        try await session.closeStandardInput()
        try await session.resize(width: 132, height: 48)

        var standardOutput = Data()
        var standardError = Data()
        for try await frame in session.frames {
            switch frame.channel {
            case .standardOutput:
                standardOutput.append(frame.data)
            case .standardError:
                standardError.append(frame.data)
            case .standardInput:
                Issue.record("direct sessions must not echo stdin as an output frame")
            }
        }

        #expect(try await session.wait() == 23)
        #expect(String(data: standardOutput, encoding: .utf8) == "stdout")
        #expect(String(data: standardError, encoding: .utf8) == "stderr")
        #expect(process.input == Data("request".utf8))
        #expect(process.lastSize?.width == 132)
        #expect(process.lastSize?.height == 48)
        await #expect(throws: DevContainerError.self) {
            try await session.write(Data("late".utf8))
        }
    }

    @Test
    func `direct session uses nonblocking input and blocking output drains`() async throws {
        let input = Pipe()
        let output = Pipe()
        let process = EchoClientProcess(input: input, output: output)
        let session = AppleDirectProcessSession(
            process: process,
            standardInput: input,
            standardOutput: output,
            standardError: nil
        )

        let inputFlags = fcntl(input.fileHandleForWriting.fileDescriptor, F_GETFL)
        #expect(inputFlags >= 0)
        #expect(inputFlags & O_NONBLOCK == O_NONBLOCK)
        let outputFlags = fcntl(output.fileHandleForReading.fileDescriptor, F_GETFL)
        #expect(outputFlags >= 0)
        #expect(outputFlags & O_NONBLOCK == 0)

        try await session.closeStandardInput()
        #expect(try await session.wait() == 0)
    }

    @Test
    func `socket input half close signals EOF while a descriptor copy remains`() async throws {
        let channel = try AppleProcessInputChannel.socketPair()
        let duplicate = dup(channel.hostEnd.fileDescriptor)
        #expect(duplicate >= 0)
        defer { Darwin.close(duplicate) }
        let writer = ProcessInputWriter(
            channel: channel,
            label: "io.github.stephenlclarke.devcontainer.test-process-input"
        )

        try await writer.close()
        var byte: UInt8 = 0
        #expect(Darwin.read(channel.processEnd.fileDescriptor, &byte, 1) == 0)
    }

    @Test
    func `direct session preserves four mebibytes of duplex backpressure`() async throws {
        let input = Pipe()
        let output = Pipe()
        let process = EchoClientProcess(input: input, output: output)
        let session = AppleDirectProcessSession(
            process: process,
            standardInput: input,
            standardOutput: output,
            standardError: nil
        )
        let payload = Data((0 ..< 4 * 1024 * 1024).lazy.map { UInt8($0 & 0xFF) })

        try await session.write(payload)
        try await session.closeStandardInput()

        var echoed = Data()
        for try await frame in session.frames {
            #expect(frame.channel == .standardOutput)
            echoed.append(frame.data)
        }

        #expect(try await session.wait() == 0)
        #expect(echoed == payload)
    }

    @Test
    func `direct session supports detached channels and cancellation`() async throws {
        let process = MockClientProcess(exitCode: 0)
        let session = AppleDirectProcessSession(
            process: process,
            standardInput: nil,
            standardOutput: nil,
            standardError: nil
        )

        await #expect(throws: DevContainerError.self) {
            try await session.write(Data("not-attached".utf8))
        }
        try await session.closeStandardInput()
        await session.cancel()
        #expect(process.killedSignal == SIGKILL)
    }

    @Test
    func `direct session validates commands and merges process environments`() async throws {
        await #expect(throws: DevContainerError.self) {
            _ = try await AppleDirectProcessSession.create(
                containerID: "unused",
                spec: ExecSpec(command: [])
            )
        }
        await #expect(throws: DevContainerError.self) {
            _ = try await AppleDirectProcessSession.create(
                containerID: "unused",
                spec: ExecSpec(command: [""])
            )
        }

        #expect(
            AppleDirectProcessSession.mergedEnvironment(
                ["Z=last", "A=old", "EMPTY", "A=inherited-last"],
                overrides: ["A": "new", "B": "second"]
            ) == ["A=new", "B=second", "EMPTY=", "Z=last"]
        )
    }

    @Test
    func `direct session reports process start failures to frames and waiters`() async throws {
        let session = AppleDirectProcessSession(
            process: MockClientProcess(
                exitCode: 0,
                startError: DirectProcessTestError.startFailed
            ),
            standardInput: nil,
            standardOutput: nil,
            standardError: nil
        )

        await #expect(throws: DirectProcessTestError.self) {
            for try await _ in session.frames {}
        }
        await #expect(throws: DirectProcessTestError.self) {
            _ = try await session.wait()
        }
    }
}

private enum DirectProcessTestError: Error {
    case startFailed
}

private struct CapturedDirectProcess: Sendable {
    let containerID: String
    let processID: String
    let configuration: ProcessConfiguration
    let standardIO: [Bool]
}

private actor DirectProcessCreationCapture {
    private(set) var value: CapturedDirectProcess?

    func record(
        containerID: String,
        processID: String,
        configuration: ProcessConfiguration,
        standardIO: [FileHandle?]
    ) {
        value = CapturedDirectProcess(
            containerID: containerID,
            processID: processID,
            configuration: configuration,
            standardIO: standardIO.map { $0 != nil }
        )
    }
}

private final class MockClientProcess: ClientProcess, @unchecked Sendable {
    let id = "mock-process"

    private let output: Pipe?
    private let error: Pipe?
    private let inputReader: FileHandle?
    private let exitCode: Int32
    private let startError: (any Error)?
    private let lock = NSLock()
    private var recordedSize: Terminal.Size?
    private var recordedSignal: Int32?
    private var recordedInput = Data()

    init(
        input: Pipe? = nil,
        output: Pipe? = nil,
        error: Pipe? = nil,
        exitCode: Int32,
        startError: (any Error)? = nil
    ) {
        inputReader = input.map {
            FileHandle(
                fileDescriptor: Darwin.dup($0.fileHandleForReading.fileDescriptor),
                closeOnDealloc: true
            )
        }
        self.output = output
        self.error = error
        self.exitCode = exitCode
        self.startError = startError
    }

    var lastSize: Terminal.Size? {
        lock.withLock { recordedSize }
    }

    var killedSignal: Int32? {
        lock.withLock { recordedSignal }
    }

    var input: Data {
        lock.withLock { recordedInput }
    }

    func start() async throws {
        if let startError {
            throw startError
        }
        try output?.fileHandleForWriting.write(contentsOf: Data("stdout".utf8))
        try error?.fileHandleForWriting.write(contentsOf: Data("stderr".utf8))
        try output?.fileHandleForWriting.close()
        try error?.fileHandleForWriting.close()
    }

    func resize(_ size: Terminal.Size) async throws {
        lock.withLock {
            recordedSize = size
        }
    }

    func kill(_ signal: Int32) async throws {
        lock.withLock {
            recordedSignal = signal
        }
    }

    func wait() async throws -> Int32 {
        let inputReader = inputReader
        let value = try await Task.detached {
            try inputReader?.readToEnd() ?? Data()
        }.value
        lock.withLock {
            recordedInput = value
        }
        return exitCode
    }
}

private final class EchoClientProcess: ClientProcess, @unchecked Sendable {
    let id = "echo-process"

    private let input: FileHandle
    private let output: FileHandle

    init(input: Pipe, output: Pipe) {
        self.input = FileHandle(
            fileDescriptor: Darwin.dup(input.fileHandleForReading.fileDescriptor),
            closeOnDealloc: true
        )
        self.output = FileHandle(
            fileDescriptor: Darwin.dup(output.fileHandleForWriting.fileDescriptor),
            closeOnDealloc: true
        )
    }

    func start() async throws {}

    func resize(_: Terminal.Size) async throws {}

    func kill(_: Int32) async throws {
        try? input.close()
        try? output.close()
    }

    func wait() async throws -> Int32 {
        let input = input
        let output = output
        return try await Task.detached {
            defer {
                try? input.close()
                try? output.close()
            }
            while let data = try input.read(upToCount: 16 * 1024), !data.isEmpty {
                try output.write(contentsOf: data)
            }
            return 0
        }.value
    }
}
