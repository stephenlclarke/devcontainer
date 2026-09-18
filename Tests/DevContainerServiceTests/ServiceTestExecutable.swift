// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
import Foundation

/// SwiftPM puts products beside the test bundle, not inside Contents/MacOS.
enum ServiceTestExecutable {
    static func resolve(beside bundle: URL) throws -> URL {
        guard bundle.pathExtension == "xctest" else {
            throw CocoaError(.fileReadInvalidFileName)
        }
        let executable = bundle.deletingLastPathComponent()
            .appendingPathComponent("devcontainer-engine", isDirectory: false)
            .resolvingSymlinksInPath()
        let values = try executable.resourceValues(forKeys: [.isRegularFileKey])
        guard values.isRegularFile == true,
              FileManager.default.isExecutableFile(atPath: executable.path)
        else {
            throw CocoaError(.fileReadNoPermission)
        }
        return executable
    }
}

final class ServiceIntegrationBundle: NSObject {}
