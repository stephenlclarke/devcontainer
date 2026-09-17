// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
import Foundation

/// Explicit scratch selection for test fixtures on both SwiftPM and Bazel.
public enum TestStorage {
    public static var temporaryDirectory: URL {
        guard let directory = resolve(
            environment: ProcessInfo.processInfo.environment,
            fallback: FileManager.default.temporaryDirectory.path
        ) else {
            preconditionFailure("Test runner must declare valid absolute scratch on its enrolled storage")
        }
        return directory
    }

    /// Explicit inputs let both build systems check canonical scratch roots
    /// without mutating process-wide environment during parallel tests.
    public static func resolve(environment: [String: String], fallback: String) -> URL? {
        let path = environment["TEST_TMPDIR"] ?? environment["TMPDIR"]
            ?? fallback
        guard path.hasPrefix("/") else { return nil }
        let directory = URL(fileURLWithPath: path, isDirectory: true)
            .standardizedFileURL.resolvingSymlinksInPath()
        if environment["BAZEL_TEST"] == "1" {
            guard let root = environment["DEVCONTAINER_TEST_SCRATCH_ROOT"], root.hasPrefix("/"), root != "/"
            else { return nil }
            let rootURL = URL(fileURLWithPath: root, isDirectory: true).standardizedFileURL
            let rootPath = rootURL.resolvingSymlinksInPath().path
            guard rootPath == rootURL.path, rootPath != "/",
                  directory.path.hasPrefix(rootPath + "/") else { return nil }
        }
        return directory
    }
}
