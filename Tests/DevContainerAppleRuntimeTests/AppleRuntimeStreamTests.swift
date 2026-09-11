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

@Suite(.serialized)
struct AppleRuntimeStreamTests {
    @Test
    func `attached start carries stdin through the stock Apple CLI`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
        let session = try await runtime.startAttachedContainer(
            id: "fixture",
            terminal: false,
            context: RuntimeRequestContext()
        )

        try await session.write(Data("attached-input".utf8))
        try await session.closeStandardInput()
        var output = Data()
        for try await frame in session.frames where frame.channel == .standardOutput {
            output.append(frame.data)
        }

        #expect(try await session.wait() == 0)
        #expect(output == Data("attached-input".utf8))
        #expect(try fixture.log().contains("start --attach --interactive fixture"))
    }

    @Test
    func `superseded attached exits cannot overwrite the replacement generation`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
        let session = try await runtime.startAttachedContainer(
            id: "fixture",
            terminal: false,
            context: RuntimeRequestContext()
        )
        let attachedRegistration = try #require(await runtime.testExitRegistration(id: "fixture"))
        let replacementRegistration = await runtime.replaceTestExitRegistration(id: "fixture")
        #expect(attachedRegistration != replacementRegistration)

        try await session.closeStandardInput()
        for try await _ in session.frames {}
        #expect(try await session.wait() == 0)

        #expect(await runtime.testExitRegistration(id: "fixture") == replacementRegistration)
        #expect(await runtime.testExit(id: "fixture") == nil)
    }

    @Test
    func `failed attached startup tears down its forwarding generation`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
        let generation = UUID()
        await runtime.installTestExitRegistration(id: "fixture", registration: generation)
        _ = try await runtime.portForwarding.start(
            containerID: "fixture",
            bindings: [
                PortBinding(
                    containerPort: 65000,
                    hostPort: nil,
                    protocolName: "tcp",
                    hostAddress: "127.0.0.1"
                )
            ],
            networkAddresses: ["bridge": "127.0.0.1/8"],
            generation: generation
        )
        #expect(await runtime.portForwarding.hasListeners(containerID: "fixture"))

        await runtime.cleanupAttachedContainerStartFailure(
            runtimeID: "fixture",
            exitRegistration: generation
        )

        #expect(await !(runtime.portForwarding.hasListeners(containerID: "fixture")))
        #expect(await runtime.testExitRegistration(id: "fixture") == nil)
    }

    @Test
    func `failed attached startup keeps its operation fenced until session cancellation`() async throws {
        let fixture = try FakeAppleCLI()
        try fixture.setState("missing")
        let runtime = try fixture.runtime()
        let session = BlockingCancellationSession()
        let generation = UUID()
        let startup = Task {
            try await runtime.performAttachedContainerStart(
                requestedID: "fixture",
                runtimeID: "fixture",
                context: RuntimeRequestContext(),
                exitRegistration: generation,
                session: session
            )
        }

        await session.waitUntilCancellationStarts()
        #expect(await runtime.hasTestStartOperation(id: "fixture"))
        await session.finishCancellation()
        await #expect(throws: DevContainerError.self) {
            try await startup.value
        }
        #expect(await !runtime.hasTestStartOperation(id: "fixture"))
    }

    @Test
    func `cancelling a followed log stream terminates its owned process`() async throws {
        let fixture = try FakeAppleCLI()
        try fixture.setMode("follow-logs")
        let runtime = try fixture.runtime()
        var stream: AsyncThrowingStream<RuntimeIOFrame, any Error>? = try await runtime.containerLogs(
            id: "fixture",
            follow: true,
            standardOutput: true,
            standardError: true,
            context: RuntimeRequestContext()
        )
        let consumer = Task { [stream] in
            for try await _ in try #require(stream) {
                try await Task.sleep(for: .seconds(30))
            }
        }
        for _ in 0 ..< 100 where try !(fixture.log()).contains("logs --follow fixture") {
            try await Task.sleep(for: .milliseconds(10))
        }

        consumer.cancel()
        _ = try? await consumer.value
        stream = nil
        for _ in 0 ..< 200 where try !(fixture.log()).contains("logs-terminated") {
            try await Task.sleep(for: .milliseconds(10))
        }

        #expect(try fixture.log().contains("logs-terminated"))
    }

    @Test
    func `stream failures and invalid requests surface typed errors`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
        let context = RuntimeRequestContext()

        try fixture.setState("stopped")
        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.createExec(
                containerID: "fixture",
                spec: ExecSpec(command: ["true"]),
                context: context
            )
        }

        try fixture.setMode("failure")
        let pull = try await runtime.pullImage(reference: "fixture", context: context)
        await #expect(throws: DevContainerError.self) {
            for try await _ in pull {}
        }
        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.buildImage(
                request: ImageBuildRequest(context: Data("not-a-tar".utf8)),
                context: context
            )
        }
        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.descriptor(context: context)
        }
    }

    @Test
    func `process session captures error output and rejects late writes`() async throws {
        let fixture = try FakeAppleCLI()
        let session = try AppleProcessSession(
            executable: fixture.executable,
            arguments: ["echo-session"],
            environment: [:]
        )
        try await session.write(Data("input".utf8))
        try await session.closeStandardInput()
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
        #expect(try await session.wait() == 0)
        #expect(String(data: standardOutput, encoding: .utf8) == "input")
        #expect(String(data: standardError, encoding: .utf8) == "session-error")
        await #expect(throws: DevContainerError.self) {
            try await session.write(Data("late".utf8))
        }
        session.cancel()
    }

    @Test
    func `process session supports input closure and cancellation`() async throws {
        let fixture = try FakeAppleCLI()
        let interactive = try AppleProcessSession(
            executable: fixture.executable,
            arguments: ["cat-session"],
            environment: [:]
        )
        try await interactive.write(Data("interactive".utf8))
        try await interactive.closeStandardInput()
        var echoed = Data()
        for try await frame in interactive.frames where frame.channel == .standardOutput {
            echoed.append(frame.data)
        }
        #expect(try await interactive.wait() == 0)
        #expect(String(data: echoed, encoding: .utf8) == "interactive")

        let cancellable = try AppleProcessSession(
            executable: fixture.executable,
            arguments: ["sleep-session"],
            environment: [:]
        )
        try await cancellable.write(Data())
        try await cancellable.closeStandardInput()
        try await cancellable.closeStandardInput()
        await #expect(throws: DevContainerError.self) {
            try await cancellable.write(Data("closed".utf8))
        }
        cancellable.cancel()
        #expect(try await cancellable.wait() != 0)
    }

    @Test
    func `process cancellation escalates across the complete owned process group`() async throws {
        let pidFile = FileManager.default.temporaryDirectory
            .appendingPathComponent("devcontainer-process-group-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: pidFile) }
        let session = try AppleProcessSession(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: [
                "-c",
                """
                trap '' TERM
                (trap '' TERM; while :; do sleep 1; done) &
                printf '%s %s' "$$" "$!" > '\(pidFile.path)'
                wait
                """
            ],
            environment: [:]
        )
        for _ in 0 ..< 100 where !FileManager.default.fileExists(atPath: pidFile.path) {
            try await Task.sleep(for: .milliseconds(10))
        }
        let identifiers = try String(contentsOf: pidFile, encoding: .utf8)
            .split(separator: " ")
            .compactMap { pid_t($0) }
        #expect(identifiers.count == 2)

        session.cancel()
        #expect(try await session.wait() != 0)
        for _ in 0 ..< 100 where identifiers.contains(where: Self.processExists) {
            try await Task.sleep(for: .milliseconds(10))
        }
        #expect(!identifiers.contains(where: Self.processExists))
    }

    @Test
    func `process session preserves large duplex streams`() async throws {
        let payload = Data(
            (0 ..< (4 * 1024 * 1024)).lazy.map { UInt8($0 & 0xFF) }
        )
        let session = try AppleProcessSession(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: [
                "-c",
                "cat; printf large-stream-stderr >&2"
            ],
            environment: [:]
        )
        try await session.write(payload)
        try await session.closeStandardInput()
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

        #expect(try await session.wait() == 0)
        #expect(standardOutput == payload)
        #expect(standardError == Data("large-stream-stderr".utf8))
    }

    @Test
    func `terminal session survives immediate resize and preserves output`() async throws {
        let session = try AppleTerminalProcessSession(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: ["-c", "printf terminal-output"],
            environment: [:]
        )

        try session.resize(width: 132, height: 43)
        var output = Data()
        for try await frame in session.frames {
            #expect(frame.channel == .standardOutput)
            output.append(frame.data)
        }

        #expect(try await session.wait() == 0)
        let text = try #require(String(data: output, encoding: .utf8))
        #expect(text.contains("terminal-output"))
    }

    @Test
    func `terminal session supports duplex input closure and completed guards`() async throws {
        let session = try AppleTerminalProcessSession(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: [
                "-c",
                "IFS= read -r line; cat >/dev/null; printf 'received:%s' \"$line\""
            ],
            environment: [:]
        )

        try session.resize(width: 0, height: 43)
        try await session.write(Data("hello\n".utf8))
        try await session.closeStandardInput()
        var output = Data()
        for try await frame in session.frames {
            output.append(frame.data)
        }

        #expect(try await session.wait() == 0)
        let text = try #require(String(data: output, encoding: .utf8))
        #expect(text.contains("received:hello"))
        await #expect(throws: DevContainerError.self) {
            try await session.write(Data("late".utf8))
        }
        try await session.closeStandardInput()
        session.cancel()
    }

    private static func processExists(_ identifier: pid_t) -> Bool {
        errno = 0
        return Darwin.kill(identifier, 0) == 0 || errno != ESRCH
    }
}

private extension AppleContainerRuntime {
    func installTestExitRegistration(id: String, registration: UUID) {
        containerExitRegistrations[id] = registration
    }

    func testExitRegistration(id: String) -> UUID? {
        containerExitRegistrations[id]
    }

    func replaceTestExitRegistration(id: String) -> UUID {
        let registration = UUID()
        containerExitRegistrations[id] = registration
        return registration
    }

    func testExit(id: String) -> ContainerExit? {
        containerExits[id]
    }

    func hasTestStartOperation(id: String) -> Bool {
        containerStartOperations[id] != nil
    }
}

private actor BlockingCancellationSession: RuntimeProcessSession {
    nonisolated let frames = AsyncThrowingStream<RuntimeIOFrame, any Error> { continuation in
        continuation.finish()
    }

    private var cancellationStarted = false
    private var cancellationStartWaiters: [CheckedContinuation<Void, Never>] = []
    private var cancellationCompletion: CheckedContinuation<Void, Never>?

    func write(_: Data) async throws {}

    func closeStandardInput() async throws {}

    func resize(width _: UInt16, height _: UInt16) async throws {}

    func wait() async throws -> Int32 {
        0
    }

    func cancel() async {
        cancellationStarted = true
        let waiters = cancellationStartWaiters
        cancellationStartWaiters.removeAll()
        for waiter in waiters {
            waiter.resume()
        }
        await withCheckedContinuation { continuation in
            cancellationCompletion = continuation
        }
    }

    func waitUntilCancellationStarts() async {
        if cancellationStarted {
            return
        }
        await withCheckedContinuation { continuation in
            cancellationStartWaiters.append(continuation)
        }
    }

    func finishCancellation() {
        cancellationCompletion?.resume()
        cancellationCompletion = nil
    }
}
