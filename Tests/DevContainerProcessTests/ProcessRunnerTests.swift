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
@testable import DevContainerProcess
import Foundation
import Testing

@Suite(.serialized)
struct ProcessRunnerTests {
    @Test
    func `captured runner bounds output and preserves exact omission counts`() async throws {
        let result = try await ProcessRunner.captured(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: [
                "-c",
                "yes o | head -c 100000; yes e | head -c 50000 >&2"
            ],
            environment: [:],
            maximumOutputBytes: 4096
        )

        #expect(result.exitCode == 0)
        #expect(result.standardOutput.count == 4096)
        #expect(result.standardError.count == 4096)
        #expect(result.omittedStandardOutputBytes == 100_000 - 4096)
        #expect(result.omittedStandardErrorBytes == 50000 - 4096)
    }

    @Test
    func `captured runner writes standard output directly to a file`() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("devcontainer-runner-output-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let output = root.appendingPathComponent("output.bin")
        try Data().write(to: output, options: .withoutOverwriting)

        let result = try await ProcessRunner.captured(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: ["-c", "yes o | head -c 2097152; printf error >&2"],
            environment: [:],
            maximumOutputBytes: 4096,
            standardOutputFile: output
        )

        #expect(result.exitCode == 0)
        #expect(result.standardOutput.isEmpty)
        #expect(result.omittedStandardOutputBytes == 0)
        #expect(result.standardError == Data("error".utf8))
        #expect(try Data(contentsOf: output).count == 2_097_152)
    }

    @Test
    func `captured runner cancels and reaps a TERM ignoring process tree`() async throws {
        let pidFile = FileManager.default.temporaryDirectory
            .appendingPathComponent("devcontainer-runner-group-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: pidFile) }
        let task = Task {
            try await ProcessRunner.captured(
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
        }
        for _ in 0 ..< 100 where !FileManager.default.fileExists(atPath: pidFile.path) {
            try await Task.sleep(for: .milliseconds(10))
        }
        let identifiers = try String(contentsOf: pidFile, encoding: .utf8)
            .split(separator: " ")
            .compactMap { pid_t($0) }

        task.cancel()
        await #expect(throws: CancellationError.self) {
            _ = try await task.value
        }
        for _ in 0 ..< 100 where identifiers.contains(where: Self.processExists) {
            try await Task.sleep(for: .milliseconds(10))
        }
        #expect(identifiers.count == 2)
        #expect(!identifiers.contains(where: Self.processExists))
    }

    @Test
    func `process escalation cannot outlive exit publication`() {
        let killEntered = DispatchSemaphore(value: 0)
        let allowKill = DispatchSemaphore(value: 0)
        let exitEntered = DispatchSemaphore(value: 0)
        let exitReturned = DispatchSemaphore(value: 0)
        let termination = OwnedProcessTermination(
            gracePeriod: .milliseconds(0),
            signalProcessGroup: { _, signal in
                guard signal == SIGKILL else {
                    return
                }
                killEntered.signal()
                allowKill.wait()
            }
        )
        termination.didLaunch(processGroup: 4242)
        termination.cancel()
        #expect(killEntered.wait(timeout: .now() + 1) == .success)

        DispatchQueue.global(qos: .utility).async {
            exitEntered.signal()
            termination.didExit()
            exitReturned.signal()
        }
        #expect(exitEntered.wait(timeout: .now() + 1) == .success)
        #expect(
            exitReturned.wait(timeout: .now() + .milliseconds(50))
                == .timedOut
        )
        allowKill.signal()
        #expect(exitReturned.wait(timeout: .now() + 1) == .success)
    }

    @Test
    func `shared runner rejects Docker family executable aliases before launch`() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("devcontainer-runner-policy-\(UUID().uuidString)")
        let executable = root.appendingPathComponent("docker")
        let marker = root.appendingPathComponent("launched")
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700]
        )
        defer { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.createSymbolicLink(
            at: executable,
            withDestinationURL: URL(fileURLWithPath: "/bin/sh")
        )

        await #expect(throws: Error.self) {
            _ = try await ProcessRunner.captured(
                executable: executable,
                arguments: ["-c", "touch \(marker.path)"],
                environment: [:]
            )
        }
        #expect(!FileManager.default.fileExists(atPath: marker.path))
    }

    private static func processExists(_ identifier: pid_t) -> Bool {
        errno = 0
        return Darwin.kill(identifier, 0) == 0 || errno != ESRCH
    }
}
