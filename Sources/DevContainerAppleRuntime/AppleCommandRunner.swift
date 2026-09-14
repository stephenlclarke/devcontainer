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
import DevContainerProcess
import Foundation

enum AppleCommandRunner {
    struct Options: Sendable {
        var workingDirectory: URL?
        var input: Data?
        var maximumStandardOutputBytes: Int?
        var maximumStandardErrorBytes: Int?
        var standardOutputFile: URL?

        init(
            workingDirectory: URL? = nil,
            input: Data? = nil,
            maximumStandardOutputBytes: Int? = nil,
            maximumStandardErrorBytes: Int? = nil,
            standardOutputFile: URL? = nil
        ) {
            self.workingDirectory = workingDirectory
            self.input = input
            self.maximumStandardOutputBytes = maximumStandardOutputBytes
            self.maximumStandardErrorBytes = maximumStandardErrorBytes
            self.standardOutputFile = standardOutputFile
        }
    }

    static func run(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        options: Options = Options()
    ) async throws -> AppleCommandResult {
        try await RuntimeRequestScope.withDeadline {
            try await runWithoutDeadline(
                executable: executable,
                arguments: arguments,
                environment: environment,
                options: options
            )
        }
    }

    private static func runWithoutDeadline(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        options: Options
    ) async throws -> AppleCommandResult {
        if let standardOutputFile = options.standardOutputFile {
            let result = try await ProcessRunner.captured(
                executable: executable,
                arguments: arguments,
                environment: environment,
                workingDirectory: options.workingDirectory,
                input: options.input,
                maximumOutputBytes: options.maximumStandardErrorBytes,
                standardOutputFile: standardOutputFile
            )
            return AppleCommandResult(
                standardOutput: Data(),
                standardError: result.standardError,
                exitCode: result.exitCode
            )
        }
        let session: AppleProcessSession
        do {
            session = try AppleProcessSession(
                executable: executable,
                arguments: arguments,
                environment: environment,
                workingDirectory: options.workingDirectory
            )
        } catch {
            throw DevContainerError(
                .runtimeUnavailable,
                message: "cannot launch Apple container CLI: \(error)"
            )
        }

        return try await capture(session: session, options: options)
    }

    private static func capture(
        session: AppleProcessSession,
        options: Options
    ) async throws -> AppleCommandResult {
        try await withTaskCancellationHandler {
            try await captureFrames(session: session, options: options)
        } onCancel: {
            session.cancel()
        }
    }

    private static func captureFrames(
        session: AppleProcessSession,
        options: Options
    ) async throws -> AppleCommandResult {
        if let input = options.input {
            try await session.write(input)
        }
        try await session.closeStandardInput()
        var standardOutput = Data()
        var standardError = Data()
        for try await frame in session.frames {
            try Task.checkCancellation()
            switch frame.channel {
            case .standardOutput:
                try append(
                    frame.data,
                    to: &standardOutput,
                    maximumBytes: options.maximumStandardOutputBytes,
                    stream: "standard output",
                    session: session
                )
            case .standardError:
                try append(
                    frame.data,
                    to: &standardError,
                    maximumBytes: options.maximumStandardErrorBytes,
                    stream: "standard error",
                    session: session
                )
            case .standardInput:
                break
            }
        }
        return try await AppleCommandResult(
            standardOutput: standardOutput,
            standardError: standardError,
            exitCode: session.wait()
        )
    }

    private static func append(
        _ incoming: Data,
        to accumulated: inout Data,
        maximumBytes: Int?,
        stream: String,
        session: AppleProcessSession
    ) throws {
        if let maximumBytes {
            let remaining = maximumBytes >= accumulated.count
                ? maximumBytes - accumulated.count
                : -1
            let exceedsLimit = remaining < 0 || incoming.count > remaining
            guard !exceedsLimit else {
                session.cancel()
                throw DevContainerError(
                    .providerProtocolMismatch,
                    message: "Apple container command \(stream) exceeds the \(max(0, maximumBytes))-byte safety limit"
                )
            }
        }
        accumulated.append(incoming)
    }
}
