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
            guard let root = environment["DEVCONTAINER_TEST_SCRATCH_ROOT"] else {
                preconditionFailure("Bazel test runner must declare its enrolled scratch root")
            }
            precondition(path.hasPrefix(root), "Bazel test scratch must be on the enrolled SSD")
        }
        return URL(fileURLWithPath: path, isDirectory: true)
    }
}
