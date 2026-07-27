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

import ArgumentParser
import Darwin
import DevContainerCore
import DevContainerModel
import Foundation

struct DoctorCommand: AsyncParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "doctor",
        abstract: "Validate the local Apple runtime and compatibility endpoint"
    )

    @Option(name: .long, help: "Apple container executable.")
    var container = CLIPaths.containerExecutable

    @Option(name: .long, help: "Engine Unix socket.")
    var socket = CLIPaths.socket

    @Option(name: .long, help: "Optional container-compose executable.")
    var compose: String?

    @Option(name: .long, help: "Output format: pretty or json.")
    var format = "pretty"

    mutating func run() async throws {
        var checks: [DoctorCheck] = []
        checks.append(
            DoctorCheck(
                name: "architecture",
                status: machineArchitecture() == "arm64" ? .pass : .fail,
                detail: machineArchitecture()
            )
        )

        let containerURL = URL(fileURLWithPath: container).standardizedFileURL
        let executable = FileManager.default.isExecutableFile(atPath: containerURL.path)
        checks.append(
            DoctorCheck(
                name: "container-executable",
                status: executable ? .pass : .fail,
                detail: containerURL.path
            )
        )
        if executable {
            await checks.append(commandCheck(
                name: "container-version",
                executable: containerURL,
                arguments: ["system", "version", "--format", "json"]
            ))
            await checks.append(commandCheck(
                name: "container-service",
                executable: containerURL,
                arguments: ["system", "status"]
            ))
        }

        checks.append(socketCheck(path: socket))

        if let compose {
            let composeURL = URL(fileURLWithPath: compose).standardizedFileURL
            await checks.append(commandCheck(
                name: "container-compose",
                executable: composeURL,
                arguments: ["version", "--format", "json"]
            ))
        }

        let report = DoctorReport(
            build: DevContainerProject.buildInfo,
            checks: checks,
            ready: checks.allSatisfy { $0.status != .fail }
        )
        switch format {
        case "pretty":
            for check in report.checks {
                print("[\(check.status.rawValue.uppercased())] \(check.name): \(check.detail)")
            }
            print(report.ready ? "devcontainer is ready" : "devcontainer is not ready")
        case "json":
            try FileHandle.standardOutput.write(JSONEncoder.pretty.encode(report))
            FileHandle.standardOutput.write(Data("\n".utf8))
        default:
            throw ValidationError("unsupported format \(format); expected pretty or json")
        }
        if !report.ready {
            throw ExitCode.failure
        }
    }

    private func commandCheck(
        name: String,
        executable: URL,
        arguments: [String]
    ) async -> DoctorCheck {
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            return DoctorCheck(name: name, status: .fail, detail: "not executable at \(executable.path)")
        }
        do {
            let result = try await runProcess(executable: executable, arguments: arguments)
            if result.status == 0 {
                let output = String(
                    bytes: result.standardOutput.prefix(1024),
                    encoding: .utf8
                )?.trimmingCharacters(in: .whitespacesAndNewlines)
                    ?? "non-UTF-8 output"
                return DoctorCheck(
                    name: name,
                    status: .pass,
                    detail: output.isEmpty ? "command completed" : output
                )
            }
            let error = String(
                bytes: result.standardError.prefix(1024),
                encoding: .utf8
            )?.trimmingCharacters(in: .whitespacesAndNewlines)
                ?? "non-UTF-8 diagnostic output"
            return DoctorCheck(
                name: name,
                status: .fail,
                detail: "exit \(result.status): \(error)"
            )
        } catch {
            return DoctorCheck(name: name, status: .fail, detail: String(describing: error))
        }
    }

    private func socketCheck(path: String) -> DoctorCheck {
        var status = stat()
        guard lstat(path, &status) == 0 else {
            return DoctorCheck(name: "engine-socket", status: .warning, detail: "not running at \(path)")
        }
        guard status.st_uid == getuid(), status.st_mode & S_IFMT == S_IFSOCK else {
            return DoctorCheck(name: "engine-socket", status: .fail, detail: "unsafe socket ownership or type")
        }
        let permissions = status.st_mode & 0o777
        return DoctorCheck(
            name: "engine-socket",
            status: permissions & 0o077 == 0 ? .pass : .fail,
            detail: "\(path) mode \(String(permissions, radix: 8))"
        )
    }

    private func machineArchitecture() -> String {
        var system = utsname()
        uname(&system)
        return withUnsafePointer(to: &system.machine) {
            $0.withMemoryRebound(to: CChar.self, capacity: 1) {
                String(cString: $0)
            }
        }
    }

    private func runProcess(
        executable: URL,
        arguments: [String]
    ) async throws -> (standardOutput: Data, standardError: Data, status: Int32) {
        let process = Process()
        let standardOutput = Pipe()
        let standardError = Pipe()
        process.executableURL = executable
        process.arguments = arguments
        process.environment = CLIPaths.safeEnvironment
        process.standardOutput = standardOutput
        process.standardError = standardError
        let (termination, terminationContinuation) = AsyncStream<Int32>.makeStream()
        process.terminationHandler = { process in
            terminationContinuation.yield(process.terminationStatus)
            terminationContinuation.finish()
        }
        try process.run()
        let outputTask = Task.detached {
            standardOutput.fileHandleForReading.readDataToEndOfFile()
        }
        let errorTask = Task.detached {
            standardError.fileHandleForReading.readDataToEndOfFile()
        }
        let exitCode = await withTaskCancellationHandler {
            for await status in termination {
                return status
            }
            return 255
        } onCancel: {
            if process.isRunning {
                process.terminate()
            }
        }
        return await (outputTask.value, errorTask.value, exitCode)
    }
}

private enum DoctorStatus: String, Codable, Sendable {
    case pass
    case warning
    case fail
}

private struct DoctorCheck: Codable, Sendable {
    let name: String
    let status: DoctorStatus
    let detail: String
}

private struct DoctorReport: Codable, Sendable {
    let build: BuildInfo
    let checks: [DoctorCheck]
    let ready: Bool
}
