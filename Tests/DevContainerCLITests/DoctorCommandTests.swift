// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ArgumentParser
import Darwin
@testable import DevContainerCLI
import DevContainerModel
import DevContainerTestStorage
import Foundation
import Testing

struct DoctorCommandTests {
    @Test
    func `missing runtime reports failure without executing commands`() async throws {
        let fixture = try DoctorFixture()
        let report = try await fixture.command().report()
        #expect(!report.ready)
        #expect(report.checks.map(\.name) == ["architecture", "container-executable", "engine-socket"])
        #expect(try fixture.check("container-executable", report).status == .fail)
        #expect(try fixture.check("engine-socket", report).status == .warning)
        #expect(!FileManager.default.fileExists(atPath: fixture.calls.path))
    }

    @Test
    func `healthy fixture preserves exact runtime and compose probe arguments`() async throws {
        let fixture = try DoctorFixture()
        try fixture.script("printf 'fixture-ready\\n'")
        var command = try fixture.command()
        command.compose = fixture.runtime.path
        let report = try await command.report()
        #expect(report.ready)
        #expect(report.checks.map(\.name) == [
            "architecture", "container-executable", "container-version", "container-service",
            "engine-socket", "container-compose"
        ])
        #expect(try String(contentsOf: fixture.calls, encoding: .utf8).split(separator: "\n") == [
            "system version --format json", "system status", "version --format json"
        ])
        #expect(try fixture.check("container-compose", report).detail == "fixture-ready")
    }

    @Test(arguments: [false, true])
    func `regular files and symlinks cannot masquerade as an engine socket`(symlink: Bool) async throws {
        let fixture = try DoctorFixture()
        try fixture.script("exit 0")
        if symlink {
            try FileManager.default.createSymbolicLink(at: fixture.socket, withDestinationURL: fixture.runtime)
        } else {
            try Data("not a socket".utf8).write(to: fixture.socket)
        }
        let report = try await fixture.command().report()
        #expect(!report.ready)
        #expect(try fixture.check("engine-socket", report).detail == "unsafe socket ownership or type")
    }

    @Test(arguments: [0o600, 0o660, 0o666])
    func `socket metadata requires private permissions`(permissions: Int) async throws {
        let fixture = try DoctorFixture()
        try fixture.script("exit 0")
        let descriptor = try fixture.bindSocket(permissions: permissions)
        defer { Darwin.close(descriptor) }
        let report = try await fixture.command().report()
        #expect(report.ready == (permissions == 0o600))
        #expect(try fixture.check("engine-socket", report).status == (permissions == 0o600 ? .pass : .fail))
    }

    @Test(arguments: [false, true])
    func `failing probes report bounded text including invalid UTF8`(invalidUTF8: Bool) async throws {
        let fixture = try DoctorFixture()
        try fixture.script(invalidUTF8 ? "printf '\\377' >&2; exit 7" : "printf 'probe rejected\\n' >&2; exit 7")
        let report = try await fixture.command().report()
        #expect(!report.ready)
        let expected = invalidUTF8 ? "exit 7: non-UTF-8 diagnostic output" : "exit 7: probe rejected"
        #expect(try fixture.check("container-version", report).detail == expected)
        #expect(try fixture.check("container-service", report).detail == expected)
    }

    @Test(arguments: ["exit 0", "printf '\\377'", "printf '%02000d' 0"])
    func `successful output handles empty invalid and oversized diagnostics`(script: String) async throws {
        let fixture = try DoctorFixture()
        try fixture.script(script)
        let report = try await fixture.command().report()
        let detail = try fixture.check("container-version", report).detail
        #expect(report.ready)
        if script == "exit 0" {
            #expect(detail == "command completed")
        } else if script.contains("377") {
            #expect(detail == "non-UTF-8 output")
        } else {
            #expect(detail.count == 1024)
        }
    }

    @Test
    func `optional missing compose is a failure not a skipped success`() async throws {
        let fixture = try DoctorFixture()
        try fixture.script("exit 0")
        var command = try fixture.command()
        command.compose = fixture.root.appendingPathComponent("absent-compose").path
        let report = try await command.report()
        #expect(!report.ready)
        #expect(try fixture.check("container-compose", report).status == .fail)
    }

    @Test
    func `launch error is represented as a failed probe`() async throws {
        let fixture = try DoctorFixture()
        try Data("#!/missing-doctor-interpreter\n".utf8).write(to: fixture.runtime)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: fixture.runtime.path)
        let report = try await fixture.command().report()
        #expect(!report.ready)
        #expect(try fixture.check("container-version", report).status == .fail)
        #expect(!FileManager.default.fileExists(atPath: fixture.calls.path))
    }

    @Test
    func `probe deadlines report failure and reap the owned process`() async throws {
        let fixture = try DoctorFixture()
        // Finite sleep also bounds the red regression before deadline enforcement.
        try fixture.script("printf '%s' $$ > \"$0.pid\"; exec /bin/sleep 1")
        let report = try await fixture.command().report(probeTimeout: 0.1)
        #expect(!report.ready)
        for name in ["container-version", "container-service"] {
            let check = try fixture.check(name, report)
            #expect(check.status == .fail)
            #expect(check.detail.contains("deadline"))
        }
        let pidFile = fixture.runtime.appendingPathExtension("pid")
        let pid = try #require(pid_t(String(contentsOf: pidFile, encoding: .utf8)))
        errno = 0
        #expect(Darwin.kill(pid, 0) == -1 && errno == ESRCH)
    }

    @Test(arguments: [0.0, -1.0, Double.infinity, Double.nan])
    func `invalid probe timeout fails before executing diagnostics`(timeout: Double) async throws {
        let fixture = try DoctorFixture()
        try fixture.script("exit 0")
        await #expect(throws: ValidationError.self) {
            try await fixture.command().report(probeTimeout: timeout)
        }
        #expect(!FileManager.default.fileExists(atPath: fixture.calls.path))
    }

    @Test
    func `an expired caller deadline cannot be extended by a probe`() async throws {
        let fixture = try DoctorFixture()
        try fixture.script("exit 0")
        let command = try fixture.command()
        let report = try await RuntimeRequestScope.$context.withValue(
            RuntimeRequestContext(deadline: .distantPast)
        ) {
            try await command.report()
        }
        #expect(!report.ready)
        #expect(try fixture.check("container-version", report).detail.contains("deadline"))
        #expect(try fixture.check("container-service", report).detail.contains("deadline"))
        #expect(!FileManager.default.fileExists(atPath: fixture.calls.path))
    }

    @Test
    func `invalid format fails before any external diagnostic`() async throws {
        let fixture = try DoctorFixture()
        try fixture.script("exit 0")
        var command = try fixture.command(format: "invalid")
        await #expect(throws: (any Error).self) { try await command.run() }
        #expect(!FileManager.default.fileExists(atPath: fixture.calls.path))
    }

    @Test(arguments: ["pretty", "json"])
    func `command emits its selected report and fails when runtime is missing`(format: String) async throws {
        let fixture = try DoctorFixture()
        var missing = try fixture.command(format: format)
        await #expect(throws: ExitCode.failure) { try await missing.run() }
        try fixture.script("exit 0")
        var ready = try fixture.command(format: format)
        try await ready.run()
    }
}

private final class DoctorFixture {
    let root: URL
    var runtime: URL {
        root.appendingPathComponent("container")
    }

    var socket: URL {
        root.appendingPathComponent("engine.sock")
    }

    var calls: URL {
        runtime.appendingPathExtension("calls")
    }

    init() throws {
        root = TestStorage.temporaryDirectory.appendingPathComponent(String(UUID().uuidString.prefix(8)))
        try FileManager.default.createDirectory(
            at: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700]
        )
    }

    deinit { try? FileManager.default.removeItem(at: root) }

    func script(_ body: String) throws {
        try Data(("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$0.calls\"\n" + body + "\n").utf8).write(to: runtime)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: runtime.path)
    }

    func command(format: String = "pretty") throws -> DoctorCommand {
        try DoctorCommand.parse([
            "--config", root.appendingPathComponent("config.toml").path,
            "--container", runtime.path, "--socket", socket.path, "--format", format
        ])
    }

    func check(_ name: String, _ report: DoctorReport) throws -> DoctorCheck {
        try #require(report.checks.first { $0.name == name })
    }

    func bindSocket(permissions: Int) throws -> Int32 {
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
        try #require(socket.path.utf8.count < MemoryLayout.size(ofValue: address.sun_path))
        withUnsafeMutableBytes(of: &address.sun_path) { bytes in
            bytes.copyBytes(from: socket.path.utf8)
        }
        let descriptor = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        try #require(descriptor >= 0)
        do {
            let result = withUnsafePointer(to: &address) { pointer in
                pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.bind(descriptor, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
                }
            }
            try #require(result == 0)
            try FileManager.default.setAttributes([.posixPermissions: permissions], ofItemAtPath: socket.path)
            return descriptor
        } catch {
            Darwin.close(descriptor)
            throw error
        }
    }
}
