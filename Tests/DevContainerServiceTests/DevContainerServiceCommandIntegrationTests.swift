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

import ContainerEngineProviderSession
import ContainerEngineWire
import Darwin
import Foundation
import Testing

@Suite(.serialized)
struct ServiceCommandIntegrationTests {
    @Test
    func `engine executable starts serves and terminates cleanly`() async throws {
        let root = URL(fileURLWithPath: "/tmp", isDirectory: true)
            .appendingPathComponent(
                "dcs-\(UUID().uuidString.prefix(8))",
                isDirectory: true
            )
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700]
        )
        defer { try? FileManager.default.removeItem(at: root) }

        let container = root.appendingPathComponent("container")
        try Data(fakeContainerCLI.utf8).write(to: container)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o700],
            ofItemAtPath: container.path
        )
        let socket = root.appendingPathComponent("docker.sock").path
        let state = root.appendingPathComponent("state.sqlite").path
        let executable = try engineExecutable()
        let process = Process()
        let log = root.appendingPathComponent("engine.log")
        #expect(FileManager.default.createFile(atPath: log.path, contents: nil))
        let output = try FileHandle(forWritingTo: log)
        defer { try? output.close() }
        process.executableURL = executable
        process.arguments = [
            "--socket",
            socket,
            "--state",
            state,
            "--container",
            container.path
        ]
        process.environment = try engineEnvironment(executable: executable)
        process.standardOutput = output
        process.standardError = output
        try process.run()

        try await exerciseEngineProcess(
            process,
            socket: socket,
            log: log,
            providerSelection: root.appendingPathComponent("engine-provider.json")
        )
    }

    @Test
    func `engine executable serves a private provider session`() async throws {
        let root = URL(fileURLWithPath: "/tmp", isDirectory: true)
            .appendingPathComponent(
                "dcs-provider-\(UUID().uuidString.prefix(8))",
                isDirectory: true
            )
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700]
        )
        defer { try? FileManager.default.removeItem(at: root) }

        let container = root.appendingPathComponent("container")
        try Data(fakeContainerCLI.utf8).write(to: container)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o700],
            ofItemAtPath: container.path
        )
        let providerSocket = root.appendingPathComponent("provider.sock").path
        let state = root.appendingPathComponent("state.sqlite").path
        let executable = try engineExecutable()
        let process = Process()
        let log = root.appendingPathComponent("provider.log")
        #expect(FileManager.default.createFile(atPath: log.path, contents: nil))
        let output = try FileHandle(forWritingTo: log)
        defer { try? output.close() }
        process.executableURL = executable
        process.arguments = [
            "--provider-socket",
            providerSocket,
            "--state",
            state,
            "--container",
            container.path
        ]
        process.environment = try engineEnvironment(executable: executable)
        process.standardOutput = output
        process.standardError = output
        try process.run()

        try await exerciseProviderProcess(
            process,
            socket: providerSocket,
            root: root,
            log: log
        )
    }
}

private func engineEnvironment(executable: URL) throws -> [String: String] {
    var environment = ProcessInfo.processInfo.environment
    guard environment["LLVM_PROFILE_FILE"] != nil else {
        return environment
    }

    let profileDirectory = executable
        .deletingLastPathComponent()
        .appendingPathComponent("codecov", isDirectory: true)
    try FileManager.default.createDirectory(
        at: profileDirectory,
        withIntermediateDirectories: true
    )
    environment["LLVM_PROFILE_FILE"] = profileDirectory
        .appendingPathComponent("devcontainer-engine-%m-%p.profraw")
        .path
    return environment
}

private func exerciseEngineProcess(
    _ process: Process,
    socket: String,
    log: URL,
    providerSelection: URL
) async throws {
    do {
        try await waitForSocket(socket, process: process)
        let ping = try runCurl(socket: socket, path: "/_ping")
        #expect(ping == "OK")
        let version = try runCurl(socket: socket, path: "/version")
        #expect(version.contains("\"Version\":\"1.1.0\""))
        let selectionData = try Data(contentsOf: providerSelection)
        let selection = try #require(
            JSONSerialization.jsonObject(with: selectionData) as? [String: Any]
        )
        #expect((selection["digest"] as? String)?.hasPrefix("sha256:") == true)
        #expect(selection["stateRootUUID"] is String)
        var selectionStatus = stat()
        #expect(lstat(providerSelection.path, &selectionStatus) == 0)
        #expect(selectionStatus.st_mode & (S_IRWXG | S_IRWXO) == 0)

        #expect(kill(process.processIdentifier, SIGTERM) == 0)
        try await waitForExit(process, log: log)
        #expect(process.terminationStatus == 0)
        #expect(!FileManager.default.fileExists(atPath: socket))
    } catch {
        if process.isRunning {
            _ = kill(process.processIdentifier, SIGKILL)
            try? await waitForExit(process, log: log, timeout: .seconds(2))
        }
        throw error
    }
}

private func exerciseProviderProcess(
    _ process: Process,
    socket: String,
    root: URL,
    log: URL
) async throws {
    do {
        try await waitForSocket(socket, process: process, log: log)
        let descriptor = try await ContainerEngineProviderSessionClient.probe(
            socketPath: socket
        )
        #expect(descriptor.fingerprint.declaration.profile == .stock)
        #expect(descriptor.fingerprint.declaration.kind == .devcontainerStock)
        #expect(descriptor.fingerprint.declaration.capabilities.contains {
            $0.identifier == "engine.route.SystemPing" && $0.status == .native
        })
        let client = ContainerEngineProviderSessionClient(
            socketPath: socket,
            expectedFingerprint: descriptor.fingerprint
        )
        let response = await client.respond(
            to: DockerHTTPRequest(method: .get, target: "/_ping")
        )
        #expect(response.status == 200)
        guard case let .bytes(body) = response.body else {
            throw ServiceIntegrationError("provider ping did not return a byte response")
        }
        #expect(String(data: body, encoding: .utf8) == "OK")

        let selection = root.appendingPathComponent("engine-provider.json")
        #expect(!FileManager.default.fileExists(atPath: selection.path))
        let stateRootIdentity = root.appendingPathComponent("engine-state-root-id")
        var identityStatus = stat()
        #expect(lstat(stateRootIdentity.path, &identityStatus) == 0)
        #expect(identityStatus.st_mode & (S_IRWXG | S_IRWXO) == 0)
        var socketStatus = stat()
        #expect(lstat(socket, &socketStatus) == 0)
        #expect(socketStatus.st_mode & (S_IRWXG | S_IRWXO) == 0)

        #expect(kill(process.processIdentifier, SIGTERM) == 0)
        try await waitForExit(process, log: log)
        #expect(process.terminationStatus == 0)
        #expect(!FileManager.default.fileExists(atPath: socket))
    } catch {
        if process.isRunning {
            _ = kill(process.processIdentifier, SIGKILL)
            try? await waitForExit(process, log: log, timeout: .seconds(2))
        }
        throw error
    }
}

private func waitForExit(
    _ process: Process,
    log: URL,
    timeout: Duration = .seconds(10)
) async throws {
    let deadline = ContinuousClock.now + timeout
    while process.isRunning, ContinuousClock.now < deadline {
        try await Task.sleep(for: .milliseconds(20))
    }
    guard !process.isRunning else {
        let diagnostic =
            (try? String(contentsOf: log, encoding: .utf8))?
                .trimmingCharacters(in: .whitespacesAndNewlines)
                ?? "no engine output"
        throw ServiceIntegrationError(
            "devcontainer-engine did not exit within \(timeout): \(diagnostic)"
        )
    }
}

private func engineExecutable() throws -> URL {
    if let executable = try configuredEngineExecutable() {
        return executable
    }

    var startingPoints = [URL(fileURLWithPath: CommandLine.arguments[0])]
    startingPoints += Bundle.allBundles.compactMap(\.executableURL)
    if let profile = ProcessInfo.processInfo.environment["LLVM_PROFILE_FILE"] {
        startingPoints.append(URL(fileURLWithPath: profile))
    }
    for startingPoint in startingPoints {
        var candidate = startingPoint
        for _ in 0 ..< 12 {
            let sibling = candidate
                .deletingLastPathComponent()
                .appendingPathComponent("devcontainer-engine")
            if FileManager.default.isExecutableFile(atPath: sibling.path) {
                return sibling
            }
            candidate.deleteLastPathComponent()
        }
    }

    let repository = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()
        .deletingLastPathComponent()
        .deletingLastPathComponent()
    let build = repository.appendingPathComponent(".build", isDirectory: true)
    let enumerator = FileManager.default.enumerator(
        at: build,
        includingPropertiesForKeys: [.isExecutableKey],
        options: [.skipsHiddenFiles, .skipsPackageDescendants]
    )
    let expectsCoverageBuild = startingPoints.contains {
        $0.path.contains("/coverage/")
    }
    let matches = (enumerator?.allObjects as? [URL] ?? [])
        .filter {
            $0.lastPathComponent == "devcontainer-engine"
                && FileManager.default.isExecutableFile(atPath: $0.path)
        }
        .sorted {
            executableCandidatePrecedes(
                $0,
                $1,
                expectsCoverageBuild: expectsCoverageBuild
            )
        }
    if let match = matches.first {
        return match
    }
    throw ServiceIntegrationError("could not locate the built devcontainer-engine")
}

private func executableCandidatePrecedes(
    _ left: URL,
    _ right: URL,
    expectsCoverageBuild: Bool
) -> Bool {
    let leftMatchesBuild = left.path.contains("/coverage/") == expectsCoverageBuild
    let rightMatchesBuild = right.path.contains("/coverage/") == expectsCoverageBuild
    return leftMatchesBuild == rightMatchesBuild
        ? left.path < right.path
        : leftMatchesBuild && !rightMatchesBuild
}

private func configuredEngineExecutable() throws -> URL? {
    guard let configured = ProcessInfo.processInfo.environment[
        "DEVCONTAINER_ENGINE_TEST_EXECUTABLE"
    ] else {
        return nil
    }
    guard configured.hasPrefix("/") else {
        throw ServiceIntegrationError(
            "DEVCONTAINER_ENGINE_TEST_EXECUTABLE must be absolute: \(configured)"
        )
    }
    let executable = URL(fileURLWithPath: configured)
    guard FileManager.default.isExecutableFile(atPath: executable.path) else {
        throw ServiceIntegrationError(
            "configured devcontainer-engine is not executable: \(configured)"
        )
    }
    return executable
}

private func waitForSocket(
    _ path: String,
    process: Process,
    log: URL? = nil
) async throws {
    let deadline = ContinuousClock.now + .seconds(10)
    while ContinuousClock.now < deadline {
        if FileManager.default.fileExists(atPath: path) {
            return
        }
        if !process.isRunning {
            let diagnostic = log.flatMap {
                try? String(contentsOf: $0, encoding: .utf8)
            }?.trimmingCharacters(in: .whitespacesAndNewlines)
                ?? "no engine output"
            throw ServiceIntegrationError(
                "devcontainer-engine exited \(process.terminationStatus) before creating its socket: \(diagnostic)"
            )
        }
        try await Task.sleep(for: .milliseconds(20))
    }
    throw ServiceIntegrationError("devcontainer-engine did not create its socket")
}

private func runCurl(socket: String, path: String) throws -> String {
    let process = Process()
    let output = Pipe()
    let error = Pipe()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/curl")
    process.arguments = [
        "--fail-with-body",
        "--silent",
        "--show-error",
        "--unix-socket",
        socket,
        "http://localhost\(path)"
    ]
    process.standardOutput = output
    process.standardError = error
    try process.run()
    process.waitUntilExit()
    let data = try output.fileHandleForReading.readToEnd() ?? Data()
    let diagnostic = try error.fileHandleForReading.readToEnd() ?? Data()
    guard process.terminationStatus == 0 else {
        let response = String(data: data, encoding: .utf8) ?? "non-UTF-8 response body"
        let curlDiagnostic =
            String(data: diagnostic, encoding: .utf8)
                ?? "non-UTF-8 service diagnostic"
        throw ServiceIntegrationError(
            "\(curlDiagnostic.trimmingCharacters(in: .whitespacesAndNewlines)): \(response)"
        )
    }
    return String(data: data, encoding: .utf8)
        ?? "non-UTF-8 service response"
}

private let fakeContainerCLI = """
#!/bin/sh
set -eu
if [ "$*" = "system version --format json" ]; then
  printf '%s\\n' '[{"appName":"container","version":"1.1.0","commit":"fixture","distribution":"fixture"}]'
  exit 0
fi
if [ "$*" = "list --all --format json" ]; then
  printf '%s\\n' '[]'
  exit 0
fi
printf 'unexpected fake container invocation: %s\\n' "$*" >&2
exit 64
"""

private struct ServiceIntegrationError: Error {
    let message: String

    init(_ message: String) {
        self.message = message
    }
}
