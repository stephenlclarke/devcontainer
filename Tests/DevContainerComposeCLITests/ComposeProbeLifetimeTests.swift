// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Darwin
@testable import DevContainerComposeCLI
import DevContainerModel
import DevContainerState
import DevContainerTestStorage
import Foundation
import Testing

@Suite(.serialized)
struct ComposeProbeLifetimeTests {
    @Test
    func `expired discovery does not launch the frontend or create state`() async throws {
        let fixture = try ProbeFixture(body: "printf '%s' $$ > \"$PROBE_PID\"")
        let context = RuntimeRequestContext(correlationID: "compose-expired", deadline: .distantPast)
        try await RuntimeRequestScope.$context.withValue(context) {
            do {
                _ = try await DevContainerComposeCommand.run(arguments: ["up"], environment: fixture.environment)
                Issue.record("Expired discovery must fail before launch")
            } catch let error as DevContainerError {
                #expect(error.code == .deadlineExceeded)
                #expect(error.correlationID == "compose-expired")
            }
        }
        #expect(!FileManager.default.fileExists(atPath: fixture.pid.path))
        #expect(!FileManager.default.fileExists(atPath: fixture.state.path))
    }

    @Test
    func `project discovery deadline reaps child before claiming state`() async throws {
        let fixture = try ProbeFixture(
            body: "printf '%s' $$ > \"$PROBE_PID\"; /bin/sleep 2; printf '{\"name\":\"probe\"}'"
        )
        let context = RuntimeRequestContext(
            correlationID: "compose-discovery", deadline: Date().addingTimeInterval(0.4)
        )
        let started = ContinuousClock.now
        await #expect(throws: (any Error).self) {
            try await RuntimeRequestScope.$context.withValue(context) {
                _ = try await DevContainerComposeCommand.run(arguments: ["up"], environment: fixture.environment)
            }
        }
        #expect(started.duration(to: .now) < .milliseconds(1500))
        try fixture.expectReaped()
        #expect(!FileManager.default.fileExists(atPath: fixture.state.path))
    }

    @Test
    func `cancelled volume discovery propagates cancellation and preserves ownership`() async throws {
        let fixture = try ProbeFixture(body: "printf '%s' $$ > \"$PROBE_PID\"; exec /bin/sleep 2")
        let task = Task {
            try await DevContainerComposeCommand.run(
                arguments: ["--project-name", "probe", "down"], environment: fixture.environment
            )
        }
        defer { task.cancel() }
        let limit = ContinuousClock.now.advanced(by: .seconds(5))
        while !FileManager.default.fileExists(atPath: fixture.pid.path), ContinuousClock.now < limit {
            try await Task.sleep(for: .milliseconds(10))
        }
        #expect(FileManager.default.fileExists(atPath: fixture.pid.path))
        task.cancel()
        await #expect(throws: CancellationError.self) { _ = try await task.value }
        try fixture.expectReaped()
        let store = try SQLiteStateStore(path: fixture.state)
        #expect(try await store.project(key: fixture.key) != nil)
    }

    @Test
    func `volume discovery timeout preserves ownership and reaps child`() async throws {
        let fixture = try ProbeFixture(body: "printf '%s' $$ > \"$PROBE_PID\"; exec /bin/sleep 2")
        let context = RuntimeRequestContext(deadline: Date().addingTimeInterval(0.5))
        let started = ContinuousClock.now
        #expect(try await RuntimeRequestScope.$context.withValue(context) {
            try await DevContainerComposeCommand.run(
                arguments: ["--project-name", "probe", "down"], environment: fixture.environment
            )
        } == 0)
        #expect(started.duration(to: .now) < .milliseconds(1500))
        try fixture.expectReaped()
        let store = try SQLiteStateStore(path: fixture.state)
        #expect(try await store.project(key: fixture.key) != nil)
    }

    @Test(arguments: ["config", "volumes"], [false, true])
    func `truncated probes never authorize state changes`(probe: String, standardError: Bool) async throws {
        let prefix = probe == "config" ? "printf '{\"name\":\"probe\"}'; " : ""
        let redirect = standardError ? " >&2" : ""
        let fixture = try ProbeFixture(
            body: prefix + "/usr/bin/head -c 1048577 /dev/zero | /usr/bin/tr '\\000' '\\n'" + redirect
        )
        if probe == "config" {
            await #expect(throws: DevContainerError.self) {
                _ = try await DevContainerComposeCommand.run(arguments: ["up"], environment: fixture.environment)
            }
            #expect(!FileManager.default.fileExists(atPath: fixture.state.path))
        } else {
            #expect(try await DevContainerComposeCommand.run(
                arguments: ["--project-name", "probe", "down"], environment: fixture.environment
            ) == 0)
            let store = try SQLiteStateStore(path: fixture.state)
            #expect(try await store.project(key: fixture.key) != nil)
        }
    }
}

private final class ProbeFixture: Sendable {
    let root: URL
    let executable: URL
    var pid: URL {
        root.appendingPathComponent("pid")
    }

    var state: URL {
        root.appendingPathComponent("state.sqlite")
    }

    var key: ProjectKey {
        ProjectKey(rawValue: "\(getuid()):probe")
    }

    init(body: String) throws {
        root = TestStorage.temporaryDirectory.appendingPathComponent("compose-probe-\(UUID().uuidString)")
        executable = root.appendingPathComponent("frontend")
        try FileManager.default.createDirectory(
            at: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700]
        )
        let script = """
        #!/bin/sh
        case " $* " in
          *" config --format json "*|*" volumes --quiet "*) \(body);;
          *) exit 0;;
        esac
        """
        try Data(script.utf8).write(to: executable)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: executable.path)
    }

    deinit { try? FileManager.default.removeItem(at: root) }

    var environment: [String: String] {
        [
            "DEVCONTAINER_COMPOSE_PROVIDER": "container-compose",
            "DEVCONTAINER_BACKEND": "stock",
            "DEVCONTAINER_CONFIG": root.appendingPathComponent("config.toml").path,
            "DEVCONTAINER_STATE": state.path,
            "DEVCONTAINER_SOCKET": root.appendingPathComponent("engine.sock").path,
            "DEVCONTAINER_COMPOSE_BIN": executable.path,
            "PROBE_PID": pid.path,
            "PATH": "/usr/bin:/bin"
        ]
    }

    func expectReaped() throws {
        let value = try #require(pid_t(String(contentsOf: pid, encoding: .utf8)))
        errno = 0
        #expect(Darwin.kill(value, 0) == -1 && errno == ESRCH)
    }
}
