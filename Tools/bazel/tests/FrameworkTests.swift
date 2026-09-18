import DevContainerTestStorage

// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
import Foundation
import Testing
import XCTest

/// This is a harness qualification probe, not a product-coverage substitute.
final class BazelXCTestDiscoveryTests: XCTestCase {
    func testXCTestDiscoveryAndSSDTemporaryStorage() throws {
        let root = try XCTUnwrap(ProcessInfo.processInfo.environment["TEST_TMPDIR"])
        XCTAssertTrue(root.hasPrefix("/Volumes/SSD/cf/bazel/"))
        XCTAssertEqual(ProcessInfo.processInfo.environment["TMPDIR"], root)
        XCTAssertTrue(FileManager.default.isWritableFile(atPath: root))
    }
}

@Test
func `swift testing discovery and SSD temporary storage`() throws {
    let root = try #require(ProcessInfo.processInfo.environment["TEST_TMPDIR"])
    #expect(root.hasPrefix("/Volumes/SSD/cf/bazel/"))
    #expect(ProcessInfo.processInfo.environment["TMPDIR"] == root)
    #expect(TestStorage.temporaryDirectory.path == root)
    let probe = URL(fileURLWithPath: root).appending(path: "probe-\(UUID().uuidString)")
    defer { try? FileManager.default.removeItem(at: probe) }
    try Data("SSD storage probe".utf8).write(to: probe)
    #expect(try String(contentsOf: probe, encoding: .utf8) == "SSD storage probe")
}
