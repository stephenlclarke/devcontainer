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
import DevContainerRuntimeSPI
import Foundation

public struct ExecutableComposeProvider: ComposeProvider {
    public let provider = BackendProvider.containerCompose
    public let executable: URL
    private let baseEnvironment: [String: String]

    public init(
        executable: URL,
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) throws {
        let resolved = executable.standardizedFileURL
        guard resolved.isFileURL, FileManager.default.isExecutableFile(atPath: resolved.path) else {
            throw DevContainerError(
                .runtimeUnavailable,
                message: "container-compose is not executable at \(resolved.path)"
            )
        }
        self.executable = resolved
        baseEnvironment = Self.filteredEnvironment(environment)
    }

    public func descriptor(context _: RuntimeRequestContext) async throws -> ProtocolDescriptor {
        let result = try await execute(
            arguments: ["version", "--format", "json"],
            environment: baseEnvironment,
            workingDirectory: URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        )
        guard result.exitCode == 0 else {
            throw DevContainerError(
                .providerProtocolMismatch,
                message: "container-compose version probe failed: \(Self.boundedError(result.standardError))"
            )
        }
        let probe: VersionProbe
        do {
            probe = try JSONDecoder().decode(VersionProbe.self, from: result.standardOutput)
        } catch {
            throw DevContainerError(
                .providerProtocolMismatch,
                message: "container-compose returned invalid version JSON: \(error)"
            )
        }
        guard probe.source == "stephenlclarke/container-compose" else {
            throw DevContainerError(
                .providerProtocolMismatch,
                message: "unexpected container-compose source \(probe.source)"
            )
        }
        return ProtocolDescriptor(
            provider: .containerCompose,
            providerVersion: probe.version,
            providerCommit: probe.commit ?? "unspecified",
            distribution: probe.containerDistribution ?? "custom",
            capabilities: Dictionary(
                uniqueKeysWithValues: RuntimeCapability.allCases.map { capability in
                    let status: CapabilityStatus = switch capability {
                    case .containers, .images, .networks, .volumes, .build:
                        .native
                    case .archive, .attach, .events, .exec, .portForwarding:
                        .emulated
                    case .registryAuthentication:
                        .unsupported
                    }
                    return (capability, status)
                }
            )
        )
    }

    public func invoke(
        _ invocation: ComposeInvocation,
        context _: RuntimeRequestContext
    ) async throws -> ComposeResult {
        var environment = baseEnvironment
        for (key, value) in invocation.environment {
            guard Self.isSafeEnvironmentKey(key) else {
                throw DevContainerError(
                    .invalidRequest,
                    message: "environment override \(key) is unsafe for process execution"
                )
            }
            environment[key] = value
        }
        return try await execute(
            arguments: invocation.arguments,
            environment: environment,
            workingDirectory: invocation.workingDirectory
        )
    }

    private func execute(
        arguments: [String],
        environment: [String: String],
        workingDirectory: URL
    ) async throws -> ComposeResult {
        let result: CapturedProcessResult
        do {
            result = try await ProcessRunner.captured(
                executable: executable,
                arguments: arguments,
                environment: environment,
                workingDirectory: workingDirectory
            )
        } catch is CancellationError {
            throw CancellationError()
        } catch let error as DevContainerError {
            throw error
        } catch {
            throw DevContainerError(
                .runtimeUnavailable,
                message: "cannot launch container-compose: \(error)"
            )
        }
        return ComposeResult(
            standardOutput: result.standardOutput,
            standardError: result.standardError,
            exitCode: result.exitCode
        )
    }

    private static func filteredEnvironment(_ environment: [String: String]) -> [String: String] {
        environment.filter { isSafeEnvironmentKey($0.key) }
    }

    private static func isSafeEnvironmentKey(_ key: String) -> Bool {
        !key.hasPrefix("DYLD_")
            && !key.hasPrefix("LD_")
            && key != "BASH_ENV"
            && key != "ENV"
    }

    private static func boundedError(_ data: Data) -> String {
        let text = String(bytes: data.prefix(4096), encoding: .utf8)
            ?? "non-UTF-8 diagnostic output"
        return text.isEmpty ? "no diagnostic output" : text
    }
}

private struct VersionProbe: Decodable {
    let version: String
    let source: String
    let commit: String?
    let containerDistribution: String?
}
