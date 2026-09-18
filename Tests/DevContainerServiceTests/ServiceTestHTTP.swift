// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
import DevContainerProcess
import Foundation

/// Host-integration probes must not outlive the test or deadlock on full pipes.
enum ServiceTestHTTP {
    enum Failure: Error, Equatable {
        case helperExit(Int32)
        case truncated
        case malformedResponse
        case httpStatus(Int)
    }

    static func get(
        socket: String,
        path: String,
        executable: URL = URL(fileURLWithPath: "/usr/bin/curl")
    ) throws -> String {
        let response = try request(socket: socket, path: path, executable: executable)
        guard (200 ..< 300).contains(response.status) else {
            throw Failure.httpStatus(response.status)
        }
        return response.body
    }

    static func request(
        socket: String,
        path: String,
        method: String = "GET",
        executable: URL = URL(fileURLWithPath: "/usr/bin/curl"),
        deadline: Date = Date().addingTimeInterval(5)
    ) throws -> (status: Int, body: String) {
        // Honor a tighter enclosing request; never lengthen its deadline.
        var context = RuntimeRequestScope.context ?? RuntimeRequestContext()
        let maximumDeadline = min(Date().addingTimeInterval(5), deadline)
        context.deadline = min(context.deadline ?? maximumDeadline, maximumDeadline)
        let result = try RuntimeRequestScope.$context.withValue(context) {
            try ProcessRunner.capturedSync(
                executable: executable,
                arguments: [
                    "--disable", "--silent", "--show-error", "--noproxy", "*",
                    "--proto", "=http", "--max-time", "5", "--connect-timeout", "5",
                    "--unix-socket", socket, "--request", method,
                    "--write-out", "\n%{http_code}", "http://localhost\(path)"
                ],
                environment: ["PATH": "/usr/bin:/bin"],
                maximumOutputBytes: 65536
            )
        }
        guard result.exitCode == 0 else { throw Failure.helperExit(result.exitCode) }
        guard result.omittedStandardOutputBytes == 0, result.omittedStandardErrorBytes == 0 else {
            throw Failure.truncated
        }
        guard let text = String(data: result.standardOutput, encoding: .utf8),
              let newline = text.lastIndex(of: "\n"),
              let status = Int(text[text.index(after: newline)...]),
              (100 ... 599).contains(status)
        else {
            throw Failure.malformedResponse
        }
        return (status, String(text[..<newline]))
    }
}
