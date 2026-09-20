// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
import DevContainerProcess
import Foundation
import Testing

/// Reuse the runtime harness's isolated HOME; never edit the login keychain.
struct ServiceTestKeychain {
    let root: URL

    init(root: URL) throws {
        self.root = root
        try JSONEncoder().encode(["root": root.path]).write(
            to: root.appendingPathComponent("owner.json"), options: .withoutOverwriting
        )
        try operate("create")
    }

    func remove(after process: Process) throws {
        try #require(!process.isRunning, "Retain the private keychain while its service is running")
        try operate("delete")
        for name in ["login.keychain", "login.keychain-db"] {
            try #require(!FileManager.default
                .fileExists(atPath: root.appendingPathComponent("Library/Keychains/\(name)").path))
        }
    }

    func cleanup(after process: Process) {
        do {
            try remove(after: process)
            try FileManager.default.removeItem(at: root)
        } catch {
            Issue.record(error, "Private service-test state retained at \(root.path)")
        }
    }

    private func operate(_ action: String) throws {
        struct Receipt: Decodable { let status: String }
        let context = RuntimeRequestContext(deadline: Date().addingTimeInterval(10))
        let result = try RuntimeRequestScope.$context.withValue(context) {
            try ProcessRunner.capturedSync(
                executable: URL(fileURLWithPath: "/usr/bin/python3"),
                arguments: ["-I", helper().path, action, root.path],
                environment: ["PATH": "/usr/bin:/bin", "HOME": root.path, "TMPDIR": root.path],
                maximumOutputBytes: 4096
            )
        }
        try #require(result.exitCode == 0, "Private keychain helper failed; service-test state retained")
        try #require(result.omittedStandardOutputBytes == 0 && result.omittedStandardErrorBytes == 0)
        let receipt = try JSONDecoder().decode(Receipt.self, from: result.standardOutput)
        try #require(action == "create" ? receipt.status == "ready" : ["deleted", "absent"].contains(receipt.status))
    }

    private func helper() throws -> URL {
        let environment = ProcessInfo.processInfo.environment
        if environment["BAZEL_TEST"] == "1" {
            let source = try #require(environment["TEST_SRCDIR"])
            let workspace = try #require(environment["TEST_WORKSPACE"])
            let runfile = try #require(environment["DEVCONTAINER_KEYCHAIN_TEST_RUNFILE"])
            return URL(fileURLWithPath: source).appendingPathComponent(workspace).appendingPathComponent(runfile)
        }
        return URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Tools/testing/private_keychain.py")
    }
}
