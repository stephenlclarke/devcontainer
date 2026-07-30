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

import Foundation

private let versionPrefix = "DEVCONTAINER_VERSION ?= "

private func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data("version-generator: \(message)\n".utf8))
    exit(2)
}

private func matches(_ value: String, pattern: String) -> Bool {
    value.range(of: pattern, options: .regularExpression) != nil
}

private func readVersion(from path: String) -> String {
    let text: String
    do {
        text = try String(contentsOfFile: path, encoding: .utf8)
    } catch {
        fail("could not read \(path): \(error)")
    }
    let values = text.split(separator: "\n").compactMap { line -> String? in
        guard line.hasPrefix(versionPrefix) else {
            return nil
        }
        return String(line.dropFirst(versionPrefix.count))
    }
    guard values.count == 1 else {
        fail("Makefile must contain exactly one \(versionPrefix) assignment")
    }
    let version = values[0]
    guard matches(version, pattern: #"^[0-9]+\.[0-9]+\.[0-9]+$"#) else {
        fail("invalid semantic product version: \(version)")
    }
    return version
}

private func validate(commit: String, lane: String) {
    guard commit == "unspecified" || matches(commit, pattern: #"^[0-9a-f]{40}$"#) else {
        fail("commit must be unspecified or 40 lowercase hexadecimal characters")
    }
    guard matches(lane, pattern: #"^[a-z][a-z0-9-]*$"#) else {
        fail("build lane must be a lowercase identifier")
    }
}

private func swiftString(_ value: String) -> String {
    value
        .replacingOccurrences(of: "\\", with: "\\\\")
        .replacingOccurrences(of: "\"", with: "\\\"")
}

guard CommandLine.arguments.count == 5 else {
    fail("usage: version-generator MAKEFILE OUTPUT COMMIT LANE")
}

let makefile = CommandLine.arguments[1]
let output = CommandLine.arguments[2]
let commit = CommandLine.arguments[3]
let lane = CommandLine.arguments[4]
let version = readVersion(from: makefile)
validate(commit: commit, lane: lane)

let source = """
// Generated from Makefile by DevContainerVersionGenerator. Do not edit.
enum GeneratedBuildIdentity {
    static let version = "\(swiftString(version))"
    static let commit = "\(swiftString(commit))"
    static let lane = "\(swiftString(lane))"
}
"""

do {
    let destination = URL(fileURLWithPath: output)
    try FileManager.default.createDirectory(
        at: destination.deletingLastPathComponent(),
        withIntermediateDirectories: true
    )
    if (try? String(contentsOf: destination, encoding: .utf8)) != source {
        try Data((source + "\n").utf8).write(to: destination, options: .atomic)
    }
} catch {
    fail("could not write \(output): \(error)")
}
