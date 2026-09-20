// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerState
import Foundation
import Testing

extension AppleContainerCreateTests {
    @Test func `engine configuration change cannot silently drop retained logger on start or restart`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator()
        let path = fixture.root.appendingPathComponent("state.sqlite")
        let store = try SQLiteStateStore(path: path)
        let first = try fixture.runtime(metadataStore: store, useDirectProcessAPI: true, creator: creator)
        let created = try await first.createContainer(
            spec: ContainerSpec(name: "fixture", image: FakeAppleImageIdentityClient.digest, command: ["/bin/true"]),
            context: .init()
        )
        #expect(created.spec.outputLogFormat == .jsonFileV1)
        let reopened = try SQLiteStateStore(path: path)
        let changed = try fixture.runtime(metadataStore: reopened, useDirectProcessAPI: false, creator: creator)
        await #expect(throws: DevContainerError.self) {
            try await changed.startContainer(id: "fixture", context: .init())
        }
        await #expect(throws: DevContainerError.self) {
            try await changed.restartContainer(id: "fixture", timeout: nil, context: .init())
        }
        #expect(await creator.starts == 0)
        #expect(await creator.bootstraps == 0)
        let log = try fixture.log()
        #expect(!log.contains("start fixture"))
        #expect(!log.contains("stop fixture"))
    }

    @Test(arguments: [false, true])
    func `durable logging requires direct process descriptors independently of native creation`(
        _ directProcess: Bool
    ) async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator()
        let store = try SQLiteStateStore(path: fixture.root.appendingPathComponent("state.sqlite"))
        let runtime = try fixture.runtime(
            metadataStore: store, useDirectProcessAPI: directProcess, creator: creator
        )
        let spec = ContainerSpec(
            name: "fixture", image: FakeAppleImageIdentityClient.digest,
            command: ["/bin/true"], outputLogFormat: .jsonFileV1
        )
        if directProcess {
            let created = try await runtime.createContainer(spec: spec, context: .init())
            #expect(created.spec.outputLogFormat == .jsonFileV1)
            #expect(await creator.created.count == 1)
        } else {
            await #expect(throws: DevContainerError.self) {
                try await runtime.createContainer(spec: spec, context: .init())
            }
            #expect(await creator.created.isEmpty)
            #expect(try await store.containerMetadata(id: "fixture") == nil)
        }
    }

    @Test(arguments: [false, true])
    func `explicit logger requires native capture and durable output authority`(_ direct: Bool) async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator()
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store, creator: direct ? creator : nil)
        let spec = ContainerSpec(name: "fixture", image: "fixture", outputLogFormat: .jsonFileV1)
        await #expect(throws: DevContainerError.self) {
            try await runtime.createContainer(spec: spec, context: .init())
        }
        #expect(await creator.created.isEmpty)
        #expect(await store.pendingContainerCreation(id: "fixture") == nil)
        #expect(await store.containerMetadata(id: "fixture") == nil)
    }
}
