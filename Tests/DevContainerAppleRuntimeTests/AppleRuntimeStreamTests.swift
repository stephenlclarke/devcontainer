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

@testable import DevContainerAppleRuntime
import DevContainerModel
import Foundation
import Testing

@Suite(.serialized)
struct AppleRuntimeStreamTests {
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
    func `process session supports input closure cancellation and error output`() async throws {
        let fixture = try FakeAppleCLI()
        let session = try AppleProcessSession(
            executable: fixture.executable,
            arguments: ["echo-session"],
            environment: [:],
            input: Data("input".utf8)
        )
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
        #expect(throws: DevContainerError.self) {
            try session.write(Data())
        }
        session.cancel()

        let interactive = try AppleProcessSession(
            executable: fixture.executable,
            arguments: ["cat-session"],
            environment: [:]
        )
        try interactive.write(Data("interactive".utf8))
        try interactive.closeStandardInput()
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
        cancellable.cancel()
        #expect(try await cancellable.wait() != 0)
    }
}
