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
import DevContainerProcess
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

    private static func processExists(_ identifier: pid_t) -> Bool {
        errno = 0
        return Darwin.kill(identifier, 0) == 0 || errno != ESRCH
    }
}
