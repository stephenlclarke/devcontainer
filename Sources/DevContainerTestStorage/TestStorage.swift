// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
import Foundation

/// Explicit scratch selection for test fixtures on both SwiftPM and Bazel.
public enum TestStorage {
    public static var temporaryDirectory: URL {
        let environment = ProcessInfo.processInfo.environment
        let path = environment["TEST_TMPDIR"] ?? environment["TMPDIR"]
            ?? FileManager.default.temporaryDirectory.path
        precondition(path.hasPrefix("/"), "Test scratch must be absolute")
        if environment["BAZEL_TEST"] == "1" {
            precondition(path.hasPrefix("/Volumes/SSD/cf/bazel/"), "Bazel test scratch must be on the enrolled SSD")
        }
        return URL(fileURLWithPath: path, isDirectory: true)
    }
}
