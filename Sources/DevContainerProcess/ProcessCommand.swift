// Copyright 2026 container-compose project authors.
// Copyright 2026 devcontainer project authors.
// SPDX-License-Identifier: Apache-2.0
// Adapted from container-compose's ComposeProcessCommand (a28bb458).
// Changes: Darwin-only, exact executable path, null default streams, EINTR-safe wait.

import Darwin
import Foundation

/// POSIX launch avoids running inherited libc teardown after fork/exec failure.
/// Configure and start on one task; only the launched PID is shared with waiters.
final class ProcessCommand: @unchecked Sendable {
    struct Attributes {
        var setProcessGroup = false
        var setForegroundProcessGroup = false
    }

    let executable: String
    let arguments: [String]
    let environment: [String]
    let directory: String?
    var stdin: FileHandle?
    var stdout: FileHandle?
    var stderr: FileHandle?
    var attributes = Attributes()

    private let lock = NSLock()
    private var processIdentifier: pid_t = -1
    private let transferForeground: @Sendable (pid_t) throws -> Void

    var pid: pid_t {
        lock.withLock { processIdentifier }
    }

    init(
        _ executable: String,
        arguments: [String],
        environment: [String],
        directory: String?,
        transferForeground: @escaping @Sendable (pid_t) throws -> Void = { child in
            guard tcsetpgrp(STDIN_FILENO, child) == 0 else {
                throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
            }
        }
    ) {
        self.executable = executable
        self.arguments = arguments
        self.environment = environment
        self.directory = directory
        self.transferForeground = transferForeground
    }

    // Keep POSIX resources and their matching cleanup visible in launch order.
    // swiftlint:disable:next function_body_length
    func start() throws {
        guard pid == -1 else { throw POSIXError(.EBUSY) }
        let nullInput = try FileHandle(forReadingFrom: URL(fileURLWithPath: "/dev/null"))
        let nullOutput = try FileHandle(forWritingTo: URL(fileURLWithPath: "/dev/null"))
        defer {
            try? nullInput.close()
            try? nullOutput.close()
        }
        var actions: posix_spawn_file_actions_t?
        try Self.check(posix_spawn_file_actions_init(&actions))
        defer { posix_spawn_file_actions_destroy(&actions) }
        try Self.duplicate(stdin ?? nullInput, to: STDIN_FILENO, actions: &actions)
        try Self.duplicate(stdout ?? nullOutput, to: STDOUT_FILENO, actions: &actions)
        try Self.duplicate(stderr ?? nullOutput, to: STDERR_FILENO, actions: &actions)
        if let directory {
            if #available(macOS 26, *) {
                try Self.check(posix_spawn_file_actions_addchdir(&actions, directory))
            } else {
                try Self.check(posix_spawn_file_actions_addchdir_np(&actions, directory))
            }
        }
        var spawnAttributes: posix_spawnattr_t?
        try Self.check(posix_spawnattr_init(&spawnAttributes))
        defer { posix_spawnattr_destroy(&spawnAttributes) }
        var flags = Int16(POSIX_SPAWN_SETSIGDEF | POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_CLOEXEC_DEFAULT)
        if attributes.setProcessGroup {
            flags |= Int16(POSIX_SPAWN_SETPGROUP)
            try Self.check(posix_spawnattr_setpgroup(&spawnAttributes, 0))
        }
        if attributes.setForegroundProcessGroup {
            // Do not let a child read the TTY before the foreground handoff.
            flags |= Int16(POSIX_SPAWN_START_SUSPENDED)
        }
        try Self.check(posix_spawnattr_setflags(&spawnAttributes, flags))
        var defaultSignals = sigset_t()
        sigemptyset(&defaultSignals)
        for signal in 1 ..< NSIG where signal != SIGKILL && signal != SIGSTOP {
            sigaddset(&defaultSignals, signal)
        }
        try Self.check(posix_spawnattr_setsigdefault(&spawnAttributes, &defaultSignals))
        var mask = sigset_t()
        sigemptyset(&mask)
        try Self.check(posix_spawnattr_setsigmask(&spawnAttributes, &mask))
        var cArguments = ([executable] + arguments).map { strdup($0) } + [nil]
        defer { for value in cArguments {
            free(value)
        } }
        var cEnvironment = environment.map { strdup($0) } + [nil]
        defer { for value in cEnvironment {
            free(value)
        } }
        var child: pid_t = 0
        let result = cArguments.withUnsafeMutableBufferPointer { argumentBuffer in
            cEnvironment.withUnsafeMutableBufferPointer { environmentBuffer in
                posix_spawn(
                    &child, executable, &actions, &spawnAttributes,
                    argumentBuffer.baseAddress, environmentBuffer.baseAddress
                )
            }
        }
        try Self.check(result)
        lock.withLock { processIdentifier = child }
        if attributes.setForegroundProcessGroup {
            try resumeInForeground(child)
        }
    }

    private func resumeInForeground(_ child: pid_t) throws {
        do {
            try transferForeground(child)
            guard Darwin.kill(child, SIGCONT) == 0 else {
                throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
            }
        } catch {
            // A suspended child cannot clean itself up on handoff failure.
            _ = Darwin.kill(child, SIGKILL)
            _ = try? wait()
            throw error
        }
    }

    /// Observe exit without releasing the leader PID while descendants drain.
    func waitUntilExit() throws {
        let identifier = pid
        guard identifier > 0 else { throw POSIXError(.ECHILD) }
        var information = siginfo_t()
        while waitid(P_PID, id_t(identifier), &information, WEXITED | WNOWAIT) < 0 {
            if errno != EINTR {
                throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
            }
        }
    }

    func wait() throws -> Int32 {
        try lock.withLock {
            let identifier = processIdentifier
            guard identifier > 0 else { throw POSIXError(.ECHILD) }
            // Consumed is distinct from never started: neither a second wait
            // nor restarting this command may act on a recycled PID.
            defer { processIdentifier = -2 }
            var status: Int32 = 0
            while waitpid(identifier, &status, 0) < 0 {
                if errno != EINTR {
                    throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
                }
            }
            // Darwin's WIFEXITED/WEXITSTATUS/WTERMSIG are C function-like macros.
            let signal = status & 0x7F
            return signal == 0 ? (status >> 8) & 0xFF : 128 + signal
        }
    }

    private static func duplicate(
        _ handle: FileHandle, to destination: Int32, actions: inout posix_spawn_file_actions_t?
    ) throws {
        try check(posix_spawn_file_actions_adddup2(&actions, handle.fileDescriptor, destination))
    }

    private static func check(_ result: Int32) throws {
        guard result == 0 else { throw POSIXError(POSIXErrorCode(rawValue: result) ?? .EIO) }
    }
}
