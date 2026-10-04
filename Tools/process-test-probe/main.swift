// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Darwin
import DevContainerProcess
import Foundation

if CommandLine.arguments.dropFirst().first == "--forward-signals" {
    let environment = ProcessInfo.processInfo.environment
    guard let readyMarker = environment["RELAY_READY_MARKER"],
          environment["RELAY_PID_MARKER"] != nil,
          environment["RELAY_USR1_MARKER"] != nil,
          environment["RELAY_TERM_MARKER"] != nil,
          let phaseMarker = environment["RELAY_PHASE_MARKER"],
          let competingMarker = environment["RELAY_COMPETING_MARKER"],
          let competingRejectedMarker = environment["RELAY_COMPETING_REJECTED_MARKER"],
          let competingStatusMarker = environment["RELAY_COMPETING_STATUS_MARKER"]
    else {
        exit(89)
    }
    _ = recordRelayMarker("checking-disposition-restoration", at: phaseMarker)
    _ = Darwin.signal(SIGUSR1, SIG_IGN)
    do {
        _ = try await ProcessRunner.inherited(
            executable: URL(fileURLWithPath: "/missing-signal-relay-probe"),
            arguments: [],
            environment: [:]
        )
        exit(87)
    } catch let error as POSIXError where error.code == .ENOENT {
        var previousAction = sigaction()
        guard sigaction(SIGUSR1, nil, &previousAction) == 0,
              handlerAddress(previousAction.__sigaction_u.__sa_handler) == unsafeBitCast(SIG_IGN, to: UInt.self)
        else {
            exit(86)
        }
    }
    _ = recordRelayMarker("disposition-restored-before-main-call", at: phaseMarker)
    let competing = Task.detached { () -> Bool in
        _ = recordRelayMarker("started", at: competingStatusMarker)
        var readyObserved = false
        for _ in 0 ..< 2500 {
            if FileManager.default.fileExists(atPath: readyMarker) {
                readyObserved = true
                break
            }
            try? await Task.sleep(for: .milliseconds(2))
        }
        guard readyObserved else {
            _ = recordRelayMarker("ready-timeout", at: competingStatusMarker)
            return false
        }
        _ = recordRelayMarker("ready", at: competingStatusMarker)
        let status: String
        do {
            _ = try await ProcessRunner.inherited(
                executable: URL(fileURLWithPath: "/bin/sh"),
                arguments: ["-c", ": > \"$RELAY_COMPETING_MARKER\""],
                environment: ["RELAY_COMPETING_MARKER": competingMarker]
            )
            status = "launched"
        } catch let error as POSIXError {
            status = "posix-\(error.code.rawValue)"
        } catch {
            status = "other-error"
        }
        _ = recordRelayMarker(status, at: competingStatusMarker)
        return status == "posix-\(POSIXErrorCode.EBUSY.rawValue)"
            && recordRelayMarker("rejected", at: competingRejectedMarker)
    }
    var competingReady = false
    for _ in 0 ..< 250 {
        if (try? String(contentsOfFile: competingStatusMarker, encoding: .utf8)) == "started" {
            competingReady = true
            break
        }
        try await Task.sleep(for: .milliseconds(2))
    }
    guard competingReady else { exit(84) }
    _ = recordRelayMarker("awaiting-inherited-return", at: phaseMarker)
    let status = try await ProcessRunner.inherited(
        executable: URL(fileURLWithPath: "/bin/sh"),
        arguments: [
            "-c",
            "trap 'printf \"usr1\\n\"; : > \"$RELAY_USR1_MARKER\"' USR1; "
                + "trap 'printf \"term\\n\"; : > \"$RELAY_TERM_MARKER\"; exit 23' TERM; "
                + "printf \"child-stderr\\n\" >&2; "
                + "if [ \"$RELAY_EARLY_SIGNAL\" = 1 ]; then kill -USR1 \"$PPID\"; fi; "
                + "if [ \"$RELAY_DELAY_PID_MARKER\" = 1 ]; then /bin/sleep 1; fi; "
                + "printf '%s\\n' \"$$\" > \"$RELAY_PID_MARKER\"; "
                + ": > \"$RELAY_READY_MARKER\"; "
                + "i=0; while [ \"$i\" -lt 250 ]; do /bin/sleep 0.02; i=$((i + 1)); done; exit 84"
        ],
        environment: environment
    )
    if let releaseMarker = environment["RELAY_EXIT_RELEASE_MARKER"] {
        _ = recordRelayMarker("holding-after-inherited-return", at: phaseMarker)
        var released = false
        for _ in 0 ..< 2000 {
            if FileManager.default.fileExists(atPath: releaseMarker) {
                released = true
                break
            }
            try await Task.sleep(for: .milliseconds(5))
        }
        guard released else { exit(86) }
    }
    _ = recordRelayMarker("inherited-returned", at: phaseMarker)
    _ = recordRelayMarker("awaiting-contender-result", at: phaseMarker)
    guard await competing.value else {
        exit(85)
    }
    _ = recordRelayMarker("contender-returned", at: phaseMarker)
    var userAction = sigaction()
    var termAction = sigaction()
    guard sigaction(SIGUSR1, nil, &userAction) == 0,
          sigaction(SIGTERM, nil, &termAction) == 0,
          handlerAddress(userAction.__sigaction_u.__sa_handler) == unsafeBitCast(SIG_IGN, to: UInt.self),
          handlerAddress(termAction.__sigaction_u.__sa_handler) == unsafeBitCast(SIG_DFL, to: UInt.self),
          status == 23
    else {
        exit(88)
    }
    print("dispositions-restored")
    _ = recordRelayMarker("probe-complete", at: phaseMarker)
    exit(status)
}

// A separate process lets tests exercise real controlling-TTY ownership without
// changing descriptors or signal handlers in the concurrent Swift test host.
guard isatty(STDIN_FILENO) == 1 else { exit(90) }
alarm(10)
for _ in 0 ..< 12 {
    let status = try await ProcessRunner.inherited(
        executable: URL(fileURLWithPath: "/bin/sh"),
        arguments: ["-c", "read -r line; test \"$line\" = fixture || exit 91; printf 'tty-ok\\n'; exit 3"],
        environment: [:]
    )
    guard status == 3, tcgetpgrp(STDIN_FILENO) == getpgrp() else {
        print("tty-failure status=\(status) foreground=\(tcgetpgrp(STDIN_FILENO)) parent=\(getpgrp())")
        exit(92)
    }
}

alarm(0)

private func handlerAddress(_ handler: (@convention(c) (Int32) -> Void)?) -> UInt {
    unsafeBitCast(handler, to: UInt.self)
}

@discardableResult
private func recordRelayMarker(_ value: String, at path: String) -> Bool {
    do {
        try value.write(toFile: path, atomically: false, encoding: .utf8)
        return true
    } catch {
        let failure = error as NSError
        "relay marker write failed: \(failure.domain) \(failure.code)\n".withCString {
            _ = fputs($0, stderr)
        }
        return false
    }
}
