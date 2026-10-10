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
import DevContainerTestStorage
import Foundation

func waitForSignalMarker(_ url: URL) async throws -> Bool {
    for _ in 0 ..< 400 {
        if FileManager.default.fileExists(atPath: url.path) {
            return true
        }
        try await Task.sleep(for: .milliseconds(5))
    }
    return false
}

func waitForSignalPIDMarker(_ url: URL, checks: Int = 400) async throws -> pid_t? {
    for _ in 0 ..< checks {
        if let value = try? String(contentsOf: url, encoding: .utf8)
            .trimmingCharacters(in: .whitespacesAndNewlines),
            let pid = pid_t(value), pid > 1
        {
            return pid
        }
        try await Task.sleep(for: .milliseconds(5))
    }
    return nil
}

func waitForMarkerValue(_ url: URL, expected: String, checks: Int = 400) async throws -> Bool {
    for _ in 0 ..< checks {
        if (try? String(contentsOf: url, encoding: .utf8)) == expected {
            return true
        }
        try await Task.sleep(for: .milliseconds(5))
    }
    return false
}

func wrapperFromRootHasExited(_ root: URL, checks: Int = 400) async throws -> Bool {
    try await recordedProcessFromRootHasExited(root, markerName: "wrapper-identity.json", checks: checks)
}

func recordedProcessFromRootHasExited(_ root: URL, markerName: String, checks: Int = 400) async throws -> Bool {
    let marker = root.appendingPathComponent(markerName)
    guard let data = try? Data(contentsOf: marker),
          let identity = try? JSONDecoder().decode(SignalRelayProcessIdentity.self, from: data)
    else { return false }
    for _ in 0 ..< checks {
        if recordedSignalProcessHasExited(identity, current: liveSignalRelayProcessIdentity(identity.pid)) {
            return true
        }
        try await Task.sleep(for: .milliseconds(5))
    }
    return recordedSignalProcessHasExited(identity, current: liveSignalRelayProcessIdentity(identity.pid))
}

func recordedSignalProcessHasExited(
    _ expected: SignalRelayProcessIdentity,
    current: SignalRelayProcessIdentity?
) -> Bool {
    guard let current else { return true }
    return current.pid != expected.pid
        || current.startSeconds != expected.startSeconds
        || current.startMicroseconds != expected.startMicroseconds
}

func waitForProcessExit(_ process: Process, checks: Int = 400) async throws -> Bool {
    for _ in 0 ..< checks {
        if !process.isRunning {
            return true
        }
        try await Task.sleep(for: .milliseconds(5))
    }
    return !process.isRunning
}

struct SignalRelayPipeOutput {
    var data: Data
    var reachedEOF: Bool
    var omittedByteCount: Int
}

struct SignalRelayProcessIdentity: Codable, Equatable {
    var pid: pid_t
    var parentPID: pid_t
    var processGroupID: pid_t
    var startSeconds: UInt64
    var startMicroseconds: UInt64
    var executablePath: String
}

struct SignalRelayExpectedOwnership {
    var parentPID: pid_t
    var processGroupID: pid_t
    var executablePath: String
}

func admittedSignalProcessIdentity(
    _ candidate: SignalRelayProcessIdentity?,
    ownership: SignalRelayExpectedOwnership,
    allowedPaths: Set<String>
) -> SignalRelayProcessIdentity? {
    guard let candidate,
          allowedPaths.contains(candidate.executablePath),
          isOwnedSignalProcess(candidate, current: candidate, ownership: ownership)
    else { return nil }
    return candidate
}

func isOwnedSignalProcess(
    _ expected: SignalRelayProcessIdentity,
    current: SignalRelayProcessIdentity?,
    ownership: SignalRelayExpectedOwnership
) -> Bool {
    guard let current else { return false }
    return current == expected
        && current.parentPID == ownership.parentPID
        && current.processGroupID == ownership.processGroupID
        && current.executablePath == ownership.executablePath
}

func tamperedSignalRelayIdentities(_ identity: SignalRelayProcessIdentity) -> [SignalRelayProcessIdentity] {
    var pid = identity
    pid.pid += 1
    var seconds = identity
    seconds.startSeconds += 1
    var microseconds = identity
    microseconds.startMicroseconds += 1
    var parent = identity
    parent.parentPID += 1
    var group = identity
    group.processGroupID += 1
    var executable = identity
    executable.executablePath = "/usr/bin/other"
    return [pid, seconds, microseconds, parent, group, executable]
}

func liveSignalRelayProcessIdentity(_ pid: pid_t) -> SignalRelayProcessIdentity? {
    var info = proc_bsdinfo()
    let expectedSize = Int32(MemoryLayout<proc_bsdinfo>.size)
    guard proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, expectedSize) == expectedSize,
          pid_t(info.pbi_pid) == pid
    else { return nil }
    var path = [CChar](repeating: 0, count: 4096)
    let pathBytes = path.withUnsafeMutableBufferPointer { buffer in
        proc_pidpath(pid, buffer.baseAddress, UInt32(buffer.count))
    }
    guard pathBytes > 0 else { return nil }
    let executablePath = path.withUnsafeBufferPointer { buffer in
        String(cString: buffer.baseAddress!)
    }
    return SignalRelayProcessIdentity(
        pid: pid_t(info.pbi_pid),
        parentPID: pid_t(info.pbi_ppid),
        processGroupID: pid_t(info.pbi_pgid),
        startSeconds: info.pbi_start_tvsec,
        startMicroseconds: info.pbi_start_tvusec,
        executablePath: executablePath
    )
}

func readSignalRelayPipeChunk(_ descriptor: Int32, deadline: UInt64, buffer: inout [UInt8]) throws -> Int {
    let now = DispatchTime.now().uptimeNanoseconds
    guard now < deadline else { return -1 }
    let remainingMilliseconds = Int32(min((deadline - now + 999_999) / 1_000_000, 100))
    var pollDescriptor = pollfd(fd: descriptor, events: Int16(POLLIN | POLLHUP), revents: 0)
    let pollResult = Darwin.poll(&pollDescriptor, 1, remainingMilliseconds)
    if pollResult < 0 {
        if errno == EINTR {
            return -1
        }
        throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
    }
    guard pollResult > 0 else { return -1 }
    guard pollDescriptor.revents & Int16(POLLNVAL) == 0 else { throw POSIXError(.EBADF) }
    let readCount = buffer.withUnsafeMutableBytes { bytes in
        Darwin.read(descriptor, bytes.baseAddress, bytes.count)
    }
    if readCount < 0, errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK {
        return -1
    }
    if readCount < 0 {
        throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
    }
    return readCount
}

func drainSignalRelayPipe(_ pipe: Pipe, timeoutMilliseconds: Int32 = 2000) throws -> SignalRelayPipeOutput {
    let handle = pipe.fileHandleForReading
    let descriptor = handle.fileDescriptor
    let originalFlags = fcntl(descriptor, F_GETFL)
    guard originalFlags >= 0, fcntl(descriptor, F_SETFL, originalFlags | O_NONBLOCK) == 0 else {
        throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
    }
    defer {
        _ = fcntl(descriptor, F_SETFL, originalFlags)
        try? handle.close()
    }

    let deadline = DispatchTime.now().uptimeNanoseconds
        + UInt64(timeoutMilliseconds) * 1_000_000
    let maximumRetainedBytes = 64 * 1024
    var data = Data()
    var omittedByteCount = 0
    var buffer = [UInt8](repeating: 0, count: 16 * 1024)

    while true {
        let readCount = try readSignalRelayPipeChunk(descriptor, deadline: deadline, buffer: &buffer)
        if readCount < 0 {
            if DispatchTime.now().uptimeNanoseconds >= deadline {
                return SignalRelayPipeOutput(data: data, reachedEOF: false, omittedByteCount: omittedByteCount)
            }
            continue
        }
        if readCount == 0 {
            return SignalRelayPipeOutput(data: data, reachedEOF: true, omittedByteCount: omittedByteCount)
        }
        let count = readCount
        let available = max(0, maximumRetainedBytes - data.count)
        data.append(contentsOf: buffer.prefix(min(count, available)))
        omittedByteCount += count - min(count, available)
    }
}

struct SignalRelayProbeResult {
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
    var stdoutReachedEOF: Bool
    var stderrReachedEOF: Bool
    var phase: String
    var childPhase: String
    var forcedCleanup: Bool
    var cleanupAttempted: Bool
    var ownershipVerified: Bool
    var ownershipFailure: String

    var diagnostic: String {
        "phase=\(phase), childPhase=\(childPhase), exit=\(exitCode), "
            + "contender=\(competingStatus), stdoutEOF=\(stdoutReachedEOF), "
            + "stderrEOF=\(stderrReachedEOF), cleanupAttempted=\(cleanupAttempted), "
            + "forcedCleanup=\(forcedCleanup), ownership=\(ownershipVerified):\(ownershipFailure), "
            + "root=\(fixtureRootPath)"
    }

    var fixtureRootPath: String
}

struct SignalRelayProbeObservations {
    var readyObserved: Bool
    var signalObserved: Bool
    var competingRequestRejected: Bool
    var exited: Bool
    var exitCode: Int32
    var stdout: SignalRelayPipeOutput
    var stderr: SignalRelayPipeOutput
}

func runSignalRelayProbe(
    signalBeforeSpawnReturns: Bool,
    root: URL? = nil,
    delayPIDMarker: Bool = false,
    holdAfterInheritedReturn: Bool = false
) async throws -> SignalRelayProbeResult {
    var probe = try SignalRelayProbe(
        signalBeforeSpawnReturns: signalBeforeSpawnReturns,
        root: root,
        delayPIDMarker: delayPIDMarker,
        holdAfterInheritedReturn: holdAfterInheritedReturn
    )
    try probe.start()
    var result: SignalRelayProbeResult
    do {
        result = try await probe.observe()
    } catch {
        let interruptedPhase = probe.phase
        probe.cleanupAfterCancellation()
        probe.recordPhase("cancelled-\(interruptedPhase)")
        throw error
    }
    let passed = result.readyObserved
        && result.signalObserved
        && result.exited
        && result.exitCode == 23
        && result.stdout == Data("usr1\nterm\ndispositions-restored\n".utf8)
        && result.stderr == Data("child-stderr\n".utf8)
        && result.stdoutReachedEOF
        && result.stderrReachedEOF
        && result.termMarkerExists
        && result.productionWaitCompleted
        && result.competingRequestRejected
        && !result.competingChildStarted
        && result.ownershipVerified
        && !result.cleanupAttempted
        && !result.forcedCleanup
    result.fixtureRootPreserved = !passed
    if passed {
        try? FileManager.default.removeItem(at: probe.root)
    } else {
        probe.recordPhase("failed-\(result.phase)")
    }
    result.phase = probe.phase
    result.childPhase = probe.childPhase
    result.fixtureRootPath = probe.root.path
    return result
}

struct SignalRelayProbe {
    let root: URL
    let ready: URL
    let pidMarker: URL
    let userMarker: URL
    let termMarker: URL
    let competingMarker: URL
    let competingRejectedMarker: URL
    let competingStatusMarker: URL
    let parentPhaseMarker: URL
    let childPhaseMarker: URL
    let wrapperIdentityMarker: URL
    let childIdentityMarker: URL
    let exitReleaseMarker: URL?
    let process: Process
    let terminationSignal: DispatchSemaphore
    let signalBeforeSpawnReturns: Bool
    let output = Pipe()
    let error = Pipe()
    private(set) var phase = "created"
    private(set) var ownershipFailure = ""
    private(set) var forcedCleanup = false
    private(set) var cleanupAttempted = false
    private var wrapperIdentity: SignalRelayProcessIdentity?
    private var childIdentity: SignalRelayProcessIdentity?

    init(
        signalBeforeSpawnReturns: Bool,
        root suppliedRoot: URL? = nil,
        delayPIDMarker: Bool = false,
        holdAfterInheritedReturn: Bool = false
    ) throws {
        root = suppliedRoot ?? TestStorage.temporaryDirectory.appendingPathComponent("signal-relay-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        ready = root.appendingPathComponent("ready")
        pidMarker = root.appendingPathComponent("pid")
        userMarker = root.appendingPathComponent("usr1")
        termMarker = root.appendingPathComponent("term")
        competingMarker = root.appendingPathComponent("competing")
        competingRejectedMarker = root.appendingPathComponent("competing-rejected")
        competingStatusMarker = root.appendingPathComponent("competing-status")
        parentPhaseMarker = root.appendingPathComponent("parent-phase")
        childPhaseMarker = root.appendingPathComponent("child-phase")
        wrapperIdentityMarker = root.appendingPathComponent("wrapper-identity.json")
        childIdentityMarker = root.appendingPathComponent("child-identity.json")
        exitReleaseMarker = holdAfterInheritedReturn ? root.appendingPathComponent("release-exit-hold") : nil
        process = Process()
        let processTerminationSignal = DispatchSemaphore(value: 0)
        terminationSignal = processTerminationSignal
        self.signalBeforeSpawnReturns = signalBeforeSpawnReturns
        process.executableURL = try processProbeURL()
        process.arguments = ["--forward-signals"]
        var childEnvironment = [
            "RELAY_READY_MARKER": ready.path,
            "RELAY_PID_MARKER": pidMarker.path,
            "RELAY_USR1_MARKER": userMarker.path,
            "RELAY_TERM_MARKER": termMarker.path,
            "RELAY_COMPETING_MARKER": competingMarker.path,
            "RELAY_COMPETING_REJECTED_MARKER": competingRejectedMarker.path,
            "RELAY_COMPETING_STATUS_MARKER": competingStatusMarker.path,
            "RELAY_PHASE_MARKER": childPhaseMarker.path,
            "RELAY_EARLY_SIGNAL": signalBeforeSpawnReturns ? "1" : "0"
        ]
        if delayPIDMarker {
            childEnvironment["RELAY_DELAY_PID_MARKER"] = "1"
        }
        if let exitReleaseMarker {
            childEnvironment["RELAY_EXIT_RELEASE_MARKER"] = exitReleaseMarker.path
        }
        process.environment = childEnvironment
        process.standardOutput = output
        process.standardError = error
        process.terminationHandler = { _ in processTerminationSignal.signal() }
    }

    var childPhase: String {
        (try? String(contentsOf: childPhaseMarker, encoding: .utf8)) ?? "unrecorded"
    }

    mutating func start() throws {
        try process.run()
        try? output.fileHandleForWriting.close()
        try? error.fileHandleForWriting.close()
        let expected = SignalRelayExpectedOwnership(
            parentPID: getpid(), processGroupID: process.processIdentifier,
            executablePath: process.executableURL!.resolvingSymlinksInPath().path
        )
        wrapperIdentity = admittedSignalProcessIdentity(
            liveSignalRelayProcessIdentity(process.processIdentifier),
            ownership: expected, allowedPaths: [expected.executablePath]
        )
        guard let wrapperIdentity else {
            ownershipFailure = "could not bind wrapper pid/start/parent/group/executable"
            recordPhase("wrapper-identity-unavailable")
            return
        }
        do {
            try JSONEncoder().encode(wrapperIdentity).write(to: wrapperIdentityMarker)
        } catch {
            ownershipFailure = "could not persist wrapper identity: \(error)"
            recordPhase("wrapper-identity-record-failed")
            return
        }
        recordPhase("wrapper-started")
    }

    mutating func cleanupAfterCancellation() {
        cleanupAttempted = true
        recordPhase("cancellation-cleanup-signalling-wrapper")
        _ = sendSignalToWrapper(SIGTERM)
        var exited = waitForProcessExitUncancelled(milliseconds: 2000)
        if !exited {
            recordPhase("cancellation-cleanup-child-group")
            forceTerminateOwnedChildGroup()
            recordPhase("cancellation-cleanup-wrapper-kill")
            _ = sendSignalToWrapper(SIGKILL)
            exited = waitForProcessExitUncancelled(milliseconds: 2000)
        }
        if !exited {
            failOwnership("wrapper remained live after bounded cancellation cleanup")
        }
        let stdout = try? drainSignalRelayPipe(output, timeoutMilliseconds: 250)
        let stderr = try? drainSignalRelayPipe(error, timeoutMilliseconds: 250)
        if stdout?.reachedEOF != true || stderr?.reachedEOF != true {
            recordPhase("cancellation-cleanup-open-pipe")
            forceTerminateOwnedChildGroup()
        }
    }

    private func waitForProcessExitUncancelled(milliseconds: Int) -> Bool {
        guard process.isRunning else { return true }
        _ = terminationSignal.wait(timeout: .now() + .milliseconds(milliseconds))
        return !process.isRunning
    }

    mutating func observe() async throws -> SignalRelayProbeResult {
        recordPhase("waiting-child-pid")
        let childPID = try await waitForSignalPIDMarker(pidMarker)
        if let childPID {
            captureChildIdentity(pid: childPID)
        }
        recordPhase("waiting-ready-marker")
        let readyFile = try await waitForSignalMarker(ready)
        let readyObserved = readyFile && childPID != nil
        recordPhase("waiting-competing-call-rejection")
        let competingRequestRejected = try await waitForSignalMarker(competingRejectedMarker)
        sendUserSignalIfNeeded(readyObserved: readyObserved)
        recordPhase("waiting-user-signal-marker")
        let signalObserved = try await waitForSignalMarker(userMarker)
        let exit = try await waitForWrapperCompletion()
        let exited = exit.exited
        let exitCode = exit.exitCode
        let outputs = drainWrapperOutput()
        recordPhase(forcedCleanup ? "observation-complete-after-forced-cleanup" : "observation-complete")
        return makeResult(SignalRelayProbeObservations(
            readyObserved: readyObserved,
            signalObserved: signalObserved,
            competingRequestRejected: competingRequestRejected,
            exited: exited,
            exitCode: exitCode,
            stdout: outputs.stdout,
            stderr: outputs.stderr
        ))
    }

    private mutating func sendUserSignalIfNeeded(readyObserved: Bool) {
        guard !signalBeforeSpawnReturns, readyObserved else { return }
        recordPhase("sending-user-signal")
        _ = sendSignalToWrapper(SIGUSR1, requiresOwnedChild: true)
    }

    private mutating func waitForWrapperCompletion() async throws -> (exited: Bool, exitCode: Int32) {
        if process.isRunning {
            recordPhase("sending-termination-signal")
            _ = sendSignalToWrapper(SIGTERM, requiresOwnedChild: true)
        }
        recordPhase("waiting-wrapper-exit")
        var exited = try await waitForProcessExit(process)
        if !exited, process.isRunning {
            recordPhase("retrying-wrapper-termination")
            cleanupAttempted = true
            _ = sendSignalToWrapper(SIGTERM, requiresOwnedChild: true)
            exited = try await waitForProcessExit(process, checks: 1400)
        }
        if !exited {
            recordPhase("terminating-owned-child-group")
            forceTerminateOwnedChildGroup()
            recordPhase("killing-owned-wrapper")
            _ = sendSignalToWrapper(SIGKILL)
            exited = try await waitForProcessExit(process, checks: 400)
        }
        return (exited, exited ? process.terminationStatus : -1)
    }

    private mutating func captureChildIdentity(pid: pid_t) {
        guard let wrapperIdentity else {
            failOwnership("child pid marker or wrapper identity unavailable")
            return
        }
        let candidate = liveSignalRelayProcessIdentity(pid)
        let expected = SignalRelayExpectedOwnership(
            parentPID: wrapperIdentity.pid, processGroupID: pid,
            executablePath: candidate?.executablePath ?? ""
        )
        // macOS's /bin/sh shim can exec the system /bin/bash interpreter.
        // Bind the admitted kernel path exactly for every later signal.
        childIdentity = admittedSignalProcessIdentity(
            candidate, ownership: expected, allowedPaths: ["/bin/sh", "/bin/bash"]
        )
        guard let childIdentity else {
            failOwnership("could not bind child pid/start/parent/group/executable at pid marker")
            return
        }
        do {
            try JSONEncoder().encode(childIdentity).write(to: childIdentityMarker)
        } catch {
            failOwnership("could not persist child identity: \(error)")
        }
    }

    private mutating func sendSignalToWrapper(_ signal: Int32, requiresOwnedChild: Bool = false) -> Bool {
        guard process.isRunning,
              let expectedWrapper = wrapperIdentity,
              processIdentityIsCurrent(expectedWrapper, expectedParentPID: getpid())
        else {
            failOwnership("refused signal \(signal) to changed or unavailable wrapper identity")
            return false
        }
        if requiresOwnedChild {
            guard let expectedWrapper = wrapperIdentity, let expectedChild = childIdentity,
                  processIdentityIsCurrent(expectedChild, expectedParentPID: expectedWrapper.pid)
            else {
                failOwnership("refused signal \(signal) after child ownership changed")
                return false
            }
        }
        guard process.isRunning,
              let currentWrapper = wrapperIdentity,
              processIdentityIsCurrent(currentWrapper, expectedParentPID: getpid())
        else {
            failOwnership("refused signal \(signal) after wrapper changed during cleanup")
            return false
        }
        guard Darwin.kill(currentWrapper.pid, signal) == 0 else {
            failOwnership("wrapper signal \(signal) failed with errno \(errno)")
            return false
        }
        if signal == SIGKILL {
            forcedCleanup = true
        }
        return true
    }

    private mutating func sendSignalToOwnedChildGroup(_ signal: Int32) -> Bool {
        guard let wrapperIdentity,
              processIdentityIsCurrent(wrapperIdentity, expectedParentPID: getpid()),
              let childIdentity,
              processIdentityIsCurrent(childIdentity, expectedParentPID: wrapperIdentity.pid),
              childIdentity.processGroupID == childIdentity.pid
        else {
            failOwnership("refused signal \(signal) to changed, reparented, or unavailable child group")
            return false
        }
        guard Darwin.kill(-childIdentity.processGroupID, signal) == 0 else {
            failOwnership("child-group signal \(signal) failed with errno \(errno)")
            return false
        }
        if signal == SIGKILL {
            forcedCleanup = true
        }
        return true
    }

    private func processIdentityIsCurrent(_ expected: SignalRelayProcessIdentity, expectedParentPID: pid_t) -> Bool {
        guard let current = liveSignalRelayProcessIdentity(expected.pid) else { return false }
        let ownership = SignalRelayExpectedOwnership(
            parentPID: expectedParentPID,
            processGroupID: expected.processGroupID,
            executablePath: expected.executablePath
        )
        return isOwnedSignalProcess(expected, current: current, ownership: ownership)
    }

    private mutating func failOwnership(_ message: String) {
        if ownershipFailure.isEmpty {
            ownershipFailure = message
        }
    }

    private mutating func drainWrapperOutput() -> (stdout: SignalRelayPipeOutput, stderr: SignalRelayPipeOutput) {
        recordPhase("draining-wrapper-stdout")
        let stdout = (try? drainSignalRelayPipe(output)) ?? SignalRelayPipeOutput(
            data: Data(), reachedEOF: false, omittedByteCount: 0
        )
        recordPhase("draining-wrapper-stderr")
        let stderr = (try? drainSignalRelayPipe(error)) ?? SignalRelayPipeOutput(
            data: Data(), reachedEOF: false, omittedByteCount: 0
        )
        if !stdout.reachedEOF || !stderr.reachedEOF {
            recordPhase("terminating-owned-child-group-for-open-pipe")
            forceTerminateOwnedChildGroup()
        }
        return (stdout, stderr)
    }

    private func makeResult(_ observations: SignalRelayProbeObservations) -> SignalRelayProbeResult {
        let termMarkerExists = FileManager.default.fileExists(atPath: termMarker.path)
        let productionWaitCompleted = observations.exited && observations.exitCode == 23 && termMarkerExists
        return SignalRelayProbeResult(
            readyObserved: observations.readyObserved,
            signalObserved: observations.signalObserved,
            exited: observations.exited,
            exitCode: observations.exitCode,
            stdout: observations.stdout.data,
            stderr: observations.stderr.data,
            termMarkerExists: termMarkerExists,
            productionWaitCompleted: productionWaitCompleted,
            fixtureRootPreserved: true,
            competingRequestRejected: observations.competingRequestRejected,
            competingChildStarted: FileManager.default.fileExists(atPath: competingMarker.path),
            competingStatus: (try? String(contentsOf: competingStatusMarker, encoding: .utf8)) ?? "missing",
            stdoutReachedEOF: observations.stdout.reachedEOF && observations.stdout.omittedByteCount == 0,
            stderrReachedEOF: observations.stderr.reachedEOF && observations.stderr.omittedByteCount == 0,
            phase: phase,
            childPhase: childPhase,
            forcedCleanup: forcedCleanup,
            cleanupAttempted: cleanupAttempted,
            ownershipVerified: wrapperIdentity != nil && childIdentity != nil && ownershipFailure.isEmpty,
            ownershipFailure: ownershipFailure.isEmpty ? "none" : ownershipFailure,
            fixtureRootPath: root.path
        )
    }

    mutating func recordPhase(_ value: String) {
        phase = value
        do {
            try value.write(to: parentPhaseMarker, atomically: false, encoding: .utf8)
        } catch {
            failOwnership("could not persist phase marker: \(error)")
        }
    }

    private mutating func forceTerminateOwnedChildGroup() {
        cleanupAttempted = true
        _ = sendSignalToOwnedChildGroup(SIGKILL)
    }
}
