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
import DevContainerTestStorage
import Foundation
import Testing

@Suite(.serialized)
struct ProcessRunnerTests {
    @Test
    func `interactive child owns the terminal before reading and restores its parent`() async throws {
        let environment = ProcessInfo.processInfo.environment
        let probe: URL
        if environment["BAZEL_TEST"] == "1" {
            let root = try #require(environment["TEST_SRCDIR"])
            let workspace = try #require(environment["TEST_WORKSPACE"])
            let runfile = try #require(environment["DEVCONTAINER_PROCESS_TEST_RUNFILE"])
            probe = URL(fileURLWithPath: root).appendingPathComponent(workspace).appendingPathComponent(runfile)
        } else {
            probe = Bundle(for: ProcessProbeBundle.self).bundleURL.deletingLastPathComponent()
                .appendingPathComponent("DevContainerProcessProbe")
        }
        try #require(FileManager.default.isExecutableFile(atPath: probe.path))
        var childEnvironment = ["TERM": "dumb"]
        var profileDirectory: URL?
        let profilePrefix = "terminal-probe-\(UUID().uuidString)-"
        if environment["COVERAGE"] == "1" {
            let path = try #require(environment["COVERAGE_DIR"])
            let directory = URL(fileURLWithPath: path).resolvingSymlinksInPath()
            try #require(directory.path.hasPrefix("/Volumes/SSD/cf/bazel/"))
            childEnvironment["LLVM_PROFILE_FILE"] = directory
                .appendingPathComponent(profilePrefix + "%p-%m.profraw").path
            profileDirectory = directory
        }
        // Keep script's stdin open briefly so its synthetic EOF does not race
        // the queued PTY input. The probe itself has a separate ten-second alarm.
        let result = try await ProcessRunner.captured(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: [
                "-c", "{ printf '%s' \"$2\"; /bin/sleep 1; } | /usr/bin/script -q /dev/null \"$1\"",
                "sh", probe.path, String(repeating: "fixture\n", count: 12)
            ],
            environment: childEnvironment
        )
        let output = String(bytes: result.standardOutput, encoding: .utf8)
        #expect(result.exitCode == 0, "TTY probe output: \(output ?? "invalid UTF-8")")
        #expect(output?.components(separatedBy: "tty-ok").count == 13)
        if let profileDirectory {
            let profiles = try FileManager.default.contentsOfDirectory(
                at: profileDirectory, includingPropertiesForKeys: [.fileSizeKey]
            ).filter { $0.lastPathComponent.hasPrefix(profilePrefix) && $0.pathExtension == "profraw" }
            try #require(!profiles.isEmpty, "The native terminal child must emit its own coverage")
            for profile in profiles {
                #expect(try profile.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0 > 0)
            }
        }
    }

    @Test
    func `failed terminal handoff reaps the suspended child before execution`() throws {
        let marker = TestStorage.temporaryDirectory.appendingPathComponent("tty-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: marker) }
        let command = ProcessCommand(
            "/bin/sh", arguments: ["-c", "printf unsafe > '\(marker.path)'"], environment: [], directory: nil,
            transferForeground: { _ in throw POSIXError(.ENOTTY) }
        )
        let foreground = tcgetpgrp(STDIN_FILENO)
        command.attributes.setProcessGroup = true
        command.attributes.setForegroundProcessGroup = true
        #expect(throws: POSIXError(.ENOTTY)) { try command.start() }
        #expect(tcgetpgrp(STDIN_FILENO) == foreground)
        #expect(!FileManager.default.fileExists(atPath: marker.path))
        #expect(throws: POSIXError(.ECHILD)) { try command.wait() }
    }

    @Test
    func `cancellation after reap cannot wait again on a recycled identifier`() throws {
        let ownership = OwnedProcessTermination()
        // No signal is sent: cancellation occurs only after ownership clears.
        ownership.didLaunch(processGroup: getpid())
        var waits = 0
        #expect(try ownership.reap { waits += 1; return 7 } == 7)
        ownership.cancel()
        #expect(throws: POSIXError(.ECHILD)) {
            try ownership.reap { waits += 1; return 9 }
        }
        #expect(waits == 1)
        #expect(!ownership.isRunning)
    }

    @Test(arguments: [false, true])
    func `cancellation owns output descendants after the leader exits`(exitImmediately: Bool) async throws {
        let marker = TestStorage.temporaryDirectory.appendingPathComponent("drain-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: marker) }
        // Finite child bounds the unfixed regression without concealing a hang.
        let task = Task {
            try await ProcessRunner.captured(
                executable: URL(fileURLWithPath: "/bin/sh"),
                arguments: [
                    "-c", "(trap '' TERM; printf ready > '\(marker.path)'; /bin/sleep 5) & "
                        + (exitImmediately ? "exit 0" : "wait")
                ],
                environment: [:]
            )
        }
        for _ in 0 ..< 200 where !FileManager.default.fileExists(atPath: marker.path) {
            try await Task.sleep(for: .milliseconds(5))
        }
        #expect(FileManager.default.fileExists(atPath: marker.path))
        // Let the immediate-exit leader reach wait before cancellation.
        try await Task.sleep(for: .milliseconds(30))
        let clock = ContinuousClock()
        let started = clock.now
        task.cancel()
        await #expect(throws: CancellationError.self) { try await task.value }
        // This is a bounded-cleanup assertion, not a comparative benchmark.
        #expect(started.duration(to: clock.now) < .seconds(3))
    }

    @Test
    func `spawn preserves input arguments environment directory and exit status`() async throws {
        let directory = TestStorage.temporaryDirectory
        let result = try await ProcessRunner.captured(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: [
                "-c", "read -r line; printf '%s|%s|%s' \"$line\" \"$VALUE\" \"$1\"; pwd >&2; exit 7", "sh", "a b"
            ],
            environment: ["VALUE": "fixture"],
            workingDirectory: directory,
            input: Data("payload\n".utf8)
        )
        #expect(result.exitCode == 7)
        #expect(String(bytes: result.standardOutput, encoding: .utf8) == "payload|fixture|a b")
        let observed = try #require(String(bytes: result.standardError, encoding: .utf8))
            .trimmingCharacters(in: .whitespacesAndNewlines)
        var actual = stat()
        var expected = stat()
        try #require(stat(observed, &actual) == 0)
        try #require(stat(directory.path, &expected) == 0)
        #expect(actual.st_dev == expected.st_dev && actual.st_ino == expected.st_ino)
    }

    @Test
    func `missing interpreter rejects concurrent launches without fork teardown`() async throws {
        let script = TestStorage.temporaryDirectory.appendingPathComponent("bad-exec-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: script) }
        try Data("#!/missing-process-interpreter\n".utf8).write(to: script)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: script.path)
        await withTaskGroup(of: Void.self) { group in
            for _ in 0 ..< 20 {
                group.addTask {
                    await #expect(throws: POSIXError(.ENOENT)) {
                        try await ProcessRunner.captured(executable: script, arguments: [], environment: [:])
                    }
                }
            }
        }
    }

    @Test
    func `spawn rejects missing working directory without running command`() async {
        await #expect(throws: POSIXError(.ENOENT)) {
            try await ProcessRunner.captured(
                executable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", "exit 0"], environment: [:],
                workingDirectory: TestStorage.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            )
        }
    }

    @Test
    func `null input closes immediately and signal exit is shell compatible`() async throws {
        let result = try await ProcessRunner.captured(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: ["-c", "if read -r line; then exit 9; fi; kill -TERM $$"], environment: [:]
        )
        #expect(result.exitCode == 128 + SIGTERM)
        #expect(result.standardOutput.isEmpty && result.standardError.isEmpty)
    }

    @Test
    func `inherited runner preserves exit status without a terminal`() async throws {
        let status = try await ProcessRunner.inherited(
            executable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", "exit 19"], environment: [:]
        )
        #expect(status == 19)
    }

    @Test
    func `process primitive rejects start twice and wait before launch`() throws {
        let command = ProcessCommand("/bin/sh", arguments: ["-c", "exit 0"], environment: [], directory: nil)
        #expect(throws: POSIXError(.ECHILD)) { try command.wait() }
        try command.start()
        #expect(throws: POSIXError(.EBUSY)) { try command.start() }
        #expect(try command.wait() == 0)
        #expect(throws: POSIXError(.ECHILD)) { try command.wait() }
        #expect(throws: POSIXError(.ECHILD)) { try command.waitUntilExit() }
        #expect(throws: POSIXError(.EBUSY)) { try command.start() }
    }

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
    func `captured runner cancels and reaps a TERM ignoring process tree`() async throws {
        let pidFile = TestStorage.temporaryDirectory
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

    private static func processExists(_ identifier: pid_t) -> Bool {
        errno = 0
        return Darwin.kill(identifier, 0) == 0 || errno != ESRCH
    }
}

private final class ProcessProbeBundle: NSObject {}
