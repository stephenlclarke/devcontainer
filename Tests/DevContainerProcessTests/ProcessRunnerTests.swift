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
import DevContainerModel
@testable import DevContainerProcess
import DevContainerTestStorage
import Foundation
import Testing

@Suite(.serialized)
struct ProcessRunnerTests {
    @Test(arguments: [0, 3, 6, 4096])
    func `bounded capture reports exact omitted bytes below and at the limit`(limit: Int) async throws {
        let result = try await ProcessRunner.captured(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: ["-c", "printf abcdef; printf xy >&2"],
            environment: [:],
            maximumOutputBytes: limit
        )
        #expect(result.exitCode == 0)
        #expect(result.standardOutput == Data("abcdef".utf8.prefix(limit)))
        #expect(result.standardError == Data("xy".utf8.prefix(limit)))
        #expect(result.omittedStandardOutputBytes == max(0, 6 - limit))
        #expect(result.omittedStandardErrorBytes == max(0, 2 - limit))
    }

    @Test
    func `cancelling a synchronous caller cancels and reaps its running child`() async throws {
        let marker = TestStorage.temporaryDirectory.appendingPathComponent("sync-cancel-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: marker) }
        let task = Task.detached {
            try ProcessRunner.capturedSync(
                executable: URL(fileURLWithPath: "/bin/sh"),
                arguments: ["-c", "printf '%s' $$ > \"$1\"; exec /bin/sleep 2", "sh", marker.path],
                environment: [:]
            )
        }
        for _ in 0 ..< 200 where !FileManager.default.fileExists(atPath: marker.path) {
            try await Task.sleep(for: .milliseconds(5))
        }
        let started = ContinuousClock.now
        task.cancel()
        await #expect(throws: CancellationError.self) { try await task.value }
        #expect(started.duration(to: .now) < .milliseconds(1500))
        let pid = try #require(pid_t(String(contentsOf: marker, encoding: .utf8)))
        errno = 0
        #expect(Darwin.kill(pid, 0) == -1 && errno == ESRCH)
    }

    @Test
    func `synchronous capture preserves expired caller deadline before launch`() throws {
        let marker = TestStorage.temporaryDirectory.appendingPathComponent("deadline-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: marker) }
        let context = RuntimeRequestContext(correlationID: "sync-expired", deadline: .distantPast)
        try RuntimeRequestScope.$context.withValue(context) {
            do {
                _ = try ProcessRunner.capturedSync(
                    executable: URL(fileURLWithPath: "/bin/sh"),
                    arguments: ["-c", "printf unsafe > \"$1\"", "sh", marker.path],
                    environment: [:]
                )
                Issue.record("Expired synchronous request must fail before launch")
            } catch let error as DevContainerError {
                #expect(error.code == .deadlineExceeded)
                #expect(error.correlationID == "sync-expired")
            }
        }
        #expect(!FileManager.default.fileExists(atPath: marker.path))
    }

    @Test
    func `synchronous capture enforces deadline and reaps its child`() throws {
        let marker = TestStorage.temporaryDirectory.appendingPathComponent("sync-pid-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: marker) }
        let context = RuntimeRequestContext(correlationID: "sync-running", deadline: Date().addingTimeInterval(0.3))
        let started = ContinuousClock.now
        try RuntimeRequestScope.$context.withValue(context) {
            do {
                // Finite sleep bounds the unfixed regression without an outer retry.
                _ = try ProcessRunner.capturedSync(
                    executable: URL(fileURLWithPath: "/bin/sh"),
                    arguments: ["-c", "printf '%s' $$ > \"$1\"; exec /bin/sleep 2", "sh", marker.path],
                    environment: [:]
                )
                Issue.record("Running synchronous request must respect its deadline")
            } catch let error as DevContainerError {
                #expect(error.code == .deadlineExceeded)
                #expect(error.correlationID == "sync-running")
            }
        }
        #expect(started.duration(to: .now) < .milliseconds(1500))
        let pid = try #require(pid_t(String(contentsOf: marker, encoding: .utf8)))
        errno = 0
        #expect(Darwin.kill(pid, 0) == -1 && errno == ESRCH)
    }

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

    @Test(arguments: [false, true])
    func `inherited runner forwards exact signals and restores prior dispositions`(signalBeforeSpawnReturns: Bool) async throws {
        let result = try await runSignalRelayProbe(signalBeforeSpawnReturns: signalBeforeSpawnReturns)
        #expect(result.readyObserved)
        #expect(result.signalObserved)
        #expect(result.exited)
        #expect(result.exitCode == 23)
        #expect(result.stdout == Data("usr1\nterm\ndispositions-restored\n".utf8))
        #expect(result.stderr == Data("child-stderr\n".utf8))
        #expect(result.termMarkerExists)
        #expect(result.productionWaitCompleted)
        #expect(!result.fixtureRootPreserved)
        #expect(result.competingRequestRejected, "Contender result: \(result.competingStatus)")
        #expect(!result.competingChildStarted)
    }

    @Test
    func `exact signal forwarding stops when the child has been reaped`() throws {
        let command = ProcessCommand("/bin/sleep", arguments: ["10"], environment: [], directory: nil)
        try command.start()
        #expect(try command.forwardSignal(SIGTERM))
        #expect(try command.wait() == 128 + SIGTERM)
        #expect(try !command.forwardSignal(SIGTERM))
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

private func processProbeURL() throws -> URL {
    let environment = ProcessInfo.processInfo.environment
    if environment["BAZEL_TEST"] == "1" {
        guard let root = environment["TEST_SRCDIR"],
              let workspace = environment["TEST_WORKSPACE"],
              let runfile = environment["DEVCONTAINER_PROCESS_TEST_RUNFILE"]
        else {
            throw POSIXError(.ENOENT)
        }
        return URL(fileURLWithPath: root).appendingPathComponent(workspace).appendingPathComponent(runfile)
    }
    return Bundle(for: ProcessProbeBundle.self).bundleURL.deletingLastPathComponent()
        .appendingPathComponent("DevContainerProcessProbe")
}

private func waitForSignalMarker(_ url: URL) async throws -> Bool {
    for _ in 0 ..< 400 {
        if FileManager.default.fileExists(atPath: url.path) {
            return true
        }
        try await Task.sleep(for: .milliseconds(5))
    }
    return false
}

private func waitForProcessExit(_ process: Process, checks: Int = 400) async throws -> Bool {
    for _ in 0 ..< checks {
        if !process.isRunning {
            return true
        }
        try await Task.sleep(for: .milliseconds(5))
    }
    return !process.isRunning
}

private struct SignalRelayProbeResult {
    var readyObserved: Bool
    var signalObserved: Bool
    var exited: Bool
    var exitCode: Int32
    var stdout: Data
    var stderr: Data
    var termMarkerExists: Bool
    var productionWaitCompleted: Bool
    var fixtureRootPreserved: Bool
    var competingRequestRejected: Bool
    var competingChildStarted: Bool
    var competingStatus: String
}

private func runSignalRelayProbe(signalBeforeSpawnReturns: Bool) async throws -> SignalRelayProbeResult {
    let probe = try SignalRelayProbe(signalBeforeSpawnReturns: signalBeforeSpawnReturns)
    try probe.process.run()
    var result = try await probe.observe()
    result.fixtureRootPreserved = !result.productionWaitCompleted
    if result.productionWaitCompleted {
        try? FileManager.default.removeItem(at: probe.root)
    }
    return result
}

private struct SignalRelayProbe {
    let root: URL
    let ready: URL
    let pidMarker: URL
    let userMarker: URL
    let termMarker: URL
    let competingMarker: URL
    let competingRejectedMarker: URL
    let competingStatusMarker: URL
    let process: Process
    let signalBeforeSpawnReturns: Bool
    let output = Pipe()
    let error = Pipe()

    init(signalBeforeSpawnReturns: Bool) throws {
        root = TestStorage.temporaryDirectory.appendingPathComponent("signal-relay-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        ready = root.appendingPathComponent("ready")
        pidMarker = root.appendingPathComponent("pid")
        userMarker = root.appendingPathComponent("usr1")
        termMarker = root.appendingPathComponent("term")
        competingMarker = root.appendingPathComponent("competing")
        competingRejectedMarker = root.appendingPathComponent("competing-rejected")
        competingStatusMarker = root.appendingPathComponent("competing-status")
        process = Process()
        self.signalBeforeSpawnReturns = signalBeforeSpawnReturns
        process.executableURL = try processProbeURL()
        process.arguments = ["--forward-signals"]
        process.environment = [
            "RELAY_READY_MARKER": ready.path,
            "RELAY_PID_MARKER": pidMarker.path,
            "RELAY_USR1_MARKER": userMarker.path,
            "RELAY_TERM_MARKER": termMarker.path,
            "RELAY_COMPETING_MARKER": competingMarker.path,
            "RELAY_COMPETING_REJECTED_MARKER": competingRejectedMarker.path,
            "RELAY_COMPETING_STATUS_MARKER": competingStatusMarker.path,
            "RELAY_EARLY_SIGNAL": signalBeforeSpawnReturns ? "1" : "0"
        ]
        process.standardOutput = output
        process.standardError = error
    }

    func observe() async throws -> SignalRelayProbeResult {
        let readyFile = try await waitForSignalMarker(ready)
        let pidFile = try await waitForSignalMarker(pidMarker)
        let readyObserved = readyFile && pidFile
        let competingRequestRejected = try await waitForSignalMarker(competingRejectedMarker)
        if !signalBeforeSpawnReturns, readyObserved, process.isRunning {
            _ = Darwin.kill(process.processIdentifier, SIGUSR1)
        }
        let signalObserved = try await waitForSignalMarker(userMarker)
        if process.isRunning {
            _ = Darwin.kill(process.processIdentifier, SIGTERM)
        }
        var exited = try await waitForProcessExit(process)
        if !exited, process.isRunning {
            // Cleanup only addresses the owned wrapper; its production signal relay
            // forwards TERM to the child while preserving exact-child ownership.
            _ = Darwin.kill(process.processIdentifier, SIGTERM)
            exited = try await waitForProcessExit(process, checks: 1400)
        }
        if exited {
            process.waitUntilExit()
        }
        let exitCode = exited ? process.terminationStatus : -1
        let termMarkerExists = FileManager.default.fileExists(atPath: termMarker.path)
        let productionWaitCompleted = exited && exitCode == 23 && termMarkerExists
        return SignalRelayProbeResult(
            readyObserved: readyObserved,
            signalObserved: signalObserved,
            exited: exited,
            exitCode: exitCode,
            stdout: exited ? output.fileHandleForReading.readDataToEndOfFile() : Data(),
            stderr: exited ? error.fileHandleForReading.readDataToEndOfFile() : Data(),
            termMarkerExists: termMarkerExists,
            productionWaitCompleted: productionWaitCompleted,
            fixtureRootPreserved: false,
            competingRequestRejected: competingRequestRejected,
            competingChildStarted: FileManager.default.fileExists(atPath: competingMarker.path),
            competingStatus: (try? String(contentsOf: competingStatusMarker, encoding: .utf8)) ?? "missing"
        )
    }
}

private final class ProcessProbeBundle: NSObject {}
