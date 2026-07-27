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
@testable import DevContainerAppleRuntime
import DevContainerModel
import Foundation
import Testing

struct AppleDirectProcessSessionTests {
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

        try session.write(Data("request".utf8))
        try session.closeStandardInput()
        try session.closeStandardInput()
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
        #expect(String(decoding: standardOutput, as: UTF8.self) == "stdout")
        #expect(String(decoding: standardError, as: UTF8.self) == "stderr")
        #expect(process.input == Data("request".utf8))
        #expect(process.lastSize?.width == 132)
        #expect(process.lastSize?.height == 48)
        #expect(throws: DevContainerError.self) {
            try session.write(Data("late".utf8))
        }
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

        #expect(throws: DevContainerError.self) {
            try session.write(Data("not-attached".utf8))
        }
        try session.closeStandardInput()
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
                ["Z=last", "A=old", "EMPTY"],
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

private final class MockClientProcess: ClientProcess, @unchecked Sendable {
    let id = "mock-process"

    private let output: Pipe?
    private let error: Pipe?
    private let standardInput: Pipe?
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
        standardInput = input
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
        let value = try standardInput?.fileHandleForReading.readToEnd() ?? Data()
        lock.withLock {
            recordedInput = value
        }
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
        exitCode
    }
}
