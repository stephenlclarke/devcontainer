//===----------------------------------------------------------------------===//
//
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
//
//===----------------------------------------------------------------------===//

@testable import DevContainerAppleRuntime
import DevContainerModel
import Foundation
import Testing

struct ManagedVolumeStoreTests {
    @Test
    func `create inspect list and remove preserve metadata and data`() throws {
        try withStore { store in
            let spec = VolumeSpec(
                name: "cache.v1",
                labels: ["purpose": "test"]
            )
            let created = try store.create(spec: spec)
            #expect(created.spec == spec)
            #expect(created.mountpoint.hasSuffix("/cache.v1/_data"))
            try Data("shared".utf8).write(
                to: URL(fileURLWithPath: created.mountpoint)
                    .appendingPathComponent("value")
            )

            #expect(try store.create(spec: spec) == created)
            #expect(try store.inspect(name: spec.name) == created)
            #expect(try store.list() == [created])
            #expect(
                try String(
                    contentsOf: URL(fileURLWithPath: created.mountpoint)
                        .appendingPathComponent("value"),
                    encoding: .utf8
                ) == "shared"
            )

            try store.remove(name: spec.name)
            #expect(try store.list().isEmpty)
            #expect(throws: DevContainerError.self) {
                _ = try store.inspect(name: spec.name)
            }
        }
    }

    @Test(
        arguments: [
            "",
            ".hidden",
            "../escape",
            "slash/name",
            "white space",
            "unicode-λ"
        ]
    )
    func `unsafe names are rejected`(name: String) throws {
        try withStore { store in
            #expect(throws: DevContainerError.self) {
                _ = try store.create(spec: VolumeSpec(name: name))
            }
        }
    }

    @Test
    func `non-local driver is rejected`() throws {
        try withStore { store in
            #expect(throws: DevContainerError.self) {
                _ = try store.create(
                    spec: VolumeSpec(name: "cache", driver: "remote")
                )
            }
        }
    }

    private func withStore(
        _ body: (ManagedVolumeStore) throws -> Void
    ) throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(
                "devcontainer-volume-store-tests-\(UUID().uuidString)",
                isDirectory: true
            )
        defer { try? FileManager.default.removeItem(at: root) }
        try body(ManagedVolumeStore(root: root))
    }
}
