// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
import Foundation
import Testing

struct ContainerEnvironmentOverridesTests {
    @Test func distinguishesRemovalAndEmptyValues() throws {
        let value = try ContainerEnvironmentOverrides(["REMOVE=old", "REMOVE", "EMPTY=", "SET", "SET=a=b", "Z"])
        #expect(value.values == ["EMPTY": "", "SET": "a=b"])
        #expect(value.removedKeys == ["REMOVE", "Z"])
        #expect(try ContainerEnvironmentOverrides([]).values.isEmpty)
    }

    @Test(arguments: ["", "=value", "BAD\0=value", "KEY=v\0"])
    func rejectsInvalidEntries(_ entry: String) {
        #expect(throws: DevContainerError.self) { try ContainerEnvironmentOverrides([entry]) }
    }

    @Test func optionalMetadataIsBackwardCompatible() throws {
        let original = ContainerSpec(name: "test", image: "test")
        let encoder = JSONEncoder()
        let decoder = JSONDecoder()
        #expect(try decoder.decode(ContainerSpec.self, from: encoder.encode(original)).removedEnvironmentKeys == nil)
        var modified = original
        modified.removedEnvironmentKeys = ["KEY"]
        #expect(try decoder.decode(ContainerSpec.self, from: encoder.encode(modified)) == modified)
    }
}
