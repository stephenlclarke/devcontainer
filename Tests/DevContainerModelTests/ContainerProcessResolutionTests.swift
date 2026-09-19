// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
import Foundation
import Testing

struct ContainerProcessResolutionTests {
    @Test(arguments: [nil, [], [""], ["/override"]] as [[String]?], [[], ["/command"]])
    func `entrypoint and command matrix`(_ entrypoint: [String]?, _ command: [String]) throws {
        let spec = ContainerSpec(
            name: "test", image: "image", command: command, entrypoint: entrypoint ?? [],
            inheritImageEntrypoint: entrypoint == nil
        )
        if entrypoint == [""], command.isEmpty {
            #expect(throws: DevContainerError.self) {
                try spec.resolvingImageProcess(imageEntrypoint: ["/image-entry"], imageCommand: ["/image-command"])
            }
            return
        }
        let resolved = try spec.resolvingImageProcess(
            imageEntrypoint: ["/image-entry"], imageCommand: ["/image-command"]
        )
        let expectedEntrypoint = entrypoint == [""] ? [] : entrypoint ?? ["/image-entry"]
        let expectedCommand = command.isEmpty && (entrypoint?.isEmpty ?? true) ? ["/image-command"] : command
        #expect(resolved.entrypoint == expectedEntrypoint)
        #expect(resolved.command == expectedCommand)
        #expect(resolved.inheritImageEntrypoint == false)
        #expect(try resolved.resolvingImageProcess(
            imageEntrypoint: ["/different"], imageCommand: ["/different"]
        ) == resolved)
    }

    @Test func `legacy metadata and explicit empty round trip`() throws {
        let legacy = ContainerSpec(name: "legacy", image: "image")
        let decoded = try JSONDecoder().decode(ContainerSpec.self, from: JSONEncoder().encode(legacy))
        #expect(decoded.inheritImageEntrypoint == nil)
        let resolved = try decoded.resolvingImageProcess(imageEntrypoint: ["/entry"], imageCommand: ["arg"])
        #expect(resolved.entrypoint == ["/entry"])
        var explicit = legacy
        explicit.inheritImageEntrypoint = false
        #expect(try JSONDecoder().decode(ContainerSpec.self, from: JSONEncoder().encode(explicit)) == explicit)
        #expect(throws: DevContainerError.self) {
            try explicit.resolvingImageProcess(imageEntrypoint: ["/entry"], imageCommand: [])
        }
    }
}
