// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerizationOCI
@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

struct AppleContainerProcessResolutionTests {
    @Test(arguments: [true, false])
    func `native creation persists descriptor bound defaults`(_ inherit: Bool) async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let store = TestMetadataStore()
        let creator = AppleContainerCreateTests.Creator()
        let identity = FakeAppleImageIdentityClient(entrypoint: ["/image-entry"], command: ["/image-command", "arg"])
        let runtime = try fixture.runtime(metadataStore: store, images: identity, creator: creator)
        let requested = ContainerSpec(name: "fixture", image: "fixture:latest", inheritImageEntrypoint: inherit)
        let created = try await runtime.createContainer(spec: requested, context: RuntimeRequestContext())
        let native = try #require(await creator.created.first)
        #expect(native.initProcess.executable == (inherit ? "/image-entry" : "/image-command"))
        #expect(native.initProcess.arguments == (inherit ? ["/image-command", "arg"] : ["arg"]))
        #expect(created.spec.entrypoint == (inherit ? ["/image-entry"] : []))
        #expect(created.spec.command == ["/image-command", "arg"])
        let metadata = try #require(await store.containerMetadata(id: "fixture"))
        #expect(metadata.spec.entrypoint == created.spec.entrypoint)
        #expect(metadata.spec.command == created.spec.command)
        #expect(metadata.spec.inheritImageEntrypoint == false)
        let restarted = try fixture.runtime(metadataStore: store, images: identity, creator: creator)
        let observed = try await restarted.inspectContainer(id: "fixture", context: RuntimeRequestContext())
        #expect(observed.spec.entrypoint == created.spec.entrypoint)
        #expect(observed.spec.command == created.spec.command)
    }

    @Test(arguments: [nil, [], [""], ["/override"]] as [[String]?])
    func `native process uses resolved entrypoint`(_ entrypoint: [String]?) throws {
        let image = try JSONDecoder().decode(
            ImageConfig.self, from: Data(#"{"Entrypoint":["/image-entry"],"Cmd":["image-arg"]}"#.utf8)
        )
        let spec = ContainerSpec(
            name: "fixture", image: "fixture:latest", command: ["/command", "arg"], entrypoint: entrypoint ?? [],
            inheritImageEntrypoint: entrypoint == nil
        )
        let process = try AppleContainerCreateProjection.process(spec, image: image)
        let expected = (entrypoint == [""] ? [] : entrypoint ?? ["/image-entry"]) + ["/command", "arg"]
        #expect(process.executable == expected[0])
        #expect(process.arguments == Array(expected.dropFirst()))
    }

    @Test func `cli clear and metadata recovery retain the resolved command`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store)
        let spec = ContainerSpec(
            name: "fixture", image: "fixture:latest", command: ["/bin/printf", "hello"],
            inheritImageEntrypoint: false
        )
        let created = try await runtime.createContainer(spec: spec, context: RuntimeRequestContext())
        #expect(try fixture.log().contains("--entrypoint /bin/printf fixture:latest hello"))
        #expect(created.spec.entrypoint.isEmpty)
        #expect(created.spec.command == spec.command)
        let restarted = try fixture.runtime(metadataStore: store)
        let inspected = try await restarted.inspectContainer(
            id: created.runtimeID.rawValue, context: RuntimeRequestContext()
        )
        #expect(inspected.spec.entrypoint.isEmpty)
        #expect(inspected.spec.command == spec.command)
        #expect(inspected.spec.inheritImageEntrypoint == false)
    }
}
