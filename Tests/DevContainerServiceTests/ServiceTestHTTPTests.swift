// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Darwin
import DevContainerModel
import DevContainerTestStorage
import Foundation
import Testing

struct ServiceTestHTTPTests {
    @Test
    func `GET accepts success and rejects an HTTP failure`() throws {
        try withHelper("printf 'OK\\n200'") { executable, _ in
            let body = try ServiceTestHTTP.get(socket: "/unused", path: "/", executable: executable)
            #expect(body == "OK")
        }
        try withHelper("printf 'not found\\n404'") { executable, _ in
            #expect(throws: ServiceTestHTTP.Failure.httpStatus(404)) {
                try ServiceTestHTTP.get(socket: "/unused", path: "/", executable: executable)
            }
        }
    }

    @Test
    func `curl configuration is disabled and request arguments are literal`() throws {
        try withHelper("printf '%s\\n' \"$@\"; printf '200'") { executable, _ in
            let result = try ServiceTestHTTP.request(
                socket: "/tmp/socket with spaces", path: "/v1.53/test?x=1&y=2", method: "POST", executable: executable
            )
            #expect(result.body.components(separatedBy: "\n") == [
                "--disable", "--silent", "--show-error", "--noproxy", "*",
                "--proto", "=http", "--max-time", "5", "--connect-timeout", "5",
                "--unix-socket", "/tmp/socket with spaces", "--request", "POST",
                "--write-out", "", "%{http_code}", "http://localhost/v1.53/test?x=1&y=2"
            ])
        }
    }

    @Test
    func `request keeps body newlines and non success HTTP status`() throws {
        try withHelper("printf 'first\\nsecond\\n501'") { executable, _ in
            let result = try ServiceTestHTTP.request(socket: "/unused", path: "/", executable: executable)
            #expect(result.status == 501)
            #expect(result.body == "first\nsecond")
        }
    }

    @Test(arguments: ["printf missing", "printf 'body\\n000'", "printf 'body\\n600'", "printf '\\377\\n200'"])
    func `invalid helper responses cannot become a pass`(script: String) throws {
        try withHelper(script) { executable, _ in
            #expect(throws: ServiceTestHTTP.Failure.malformedResponse) {
                try ServiceTestHTTP.request(socket: "/unused", path: "/", executable: executable)
            }
        }
    }

    @Test(arguments: [false, true])
    func `large output is drained without pipe deadlock and rejected`(stderr: Bool) throws {
        let destination = stderr ? " >&2" : ""
        try withHelper("(/bin/dd if=/dev/zero bs=131072 count=1 2>/dev/null)\(destination); printf '\\n200'") { executable, _ in
            #expect(throws: ServiceTestHTTP.Failure.truncated) {
                try ServiceTestHTTP.request(socket: "/unused", path: "/", executable: executable)
            }
        }
    }

    @Test
    func `helper failure cannot be masked by a success status in its output`() throws {
        try withHelper("printf 'OK\\n200'; exit 28") { executable, _ in
            #expect(throws: ServiceTestHTTP.Failure.helperExit(28)) {
                try ServiceTestHTTP.request(socket: "/unused", path: "/", executable: executable)
            }
        }
    }

    @Test(arguments: [2.0, 30.0])
    func `deadline terminates and reaps a helper ignoring curl options`(requestedSeconds: Double) throws {
        try withHelper("printf '%s' $$ > \"$0.pid\"; exec /bin/sleep 10") { executable, root in
            let started = ContinuousClock.now
            do {
                _ = try ServiceTestHTTP.request(
                    socket: "/unused", path: "/", executable: executable,
                    deadline: Date().addingTimeInterval(requestedSeconds)
                )
                Issue.record("Expected bounded helper expiry")
            } catch let error as DevContainerError {
                #expect(error.code == .deadlineExceeded)
            }
            #expect(started.duration(to: .now) < .seconds(min(requestedSeconds, 5) + 2))
            let marker = root.appendingPathComponent("helper.pid")
            let pid = try #require(pid_t(String(contentsOf: marker, encoding: .utf8)))
            errno = 0
            #expect(kill(pid, 0) == -1 && errno == ESRCH)
        }
    }

    @Test
    func `expired enclosing deadline is not extended and prevents launch`() throws {
        try withHelper("printf launched > \"$0.pid\"; printf 'OK\\n200'") { executable, root in
            try RuntimeRequestScope.$context.withValue(RuntimeRequestContext(deadline: .distantPast)) {
                do {
                    _ = try ServiceTestHTTP.request(socket: "/unused", path: "/", executable: executable)
                    Issue.record("Expired enclosing request must not launch")
                } catch let error as DevContainerError {
                    #expect(error.code == .deadlineExceeded)
                }
            }
            #expect(!FileManager.default.fileExists(atPath: root.appendingPathComponent("helper.pid").path))
        }
    }

    private func withHelper(_ script: String, body: (URL, URL) throws -> Void) throws {
        let root = TestStorage.temporaryDirectory.appendingPathComponent("service-http-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let executable = root.appendingPathComponent("helper")
        try Data("#!/bin/sh\nset -eu\n\(script)\n".utf8).write(to: executable)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: executable.path)
        try body(executable, root)
    }
}
