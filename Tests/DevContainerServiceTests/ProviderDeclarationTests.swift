// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineRuntimeSPI
import DevContainerModel
@testable import DevContainerService
import DevContainerState
import Foundation
import Testing

struct ProviderDeclarationTests {
    @Test(arguments: BackendProvider.allCases)
    func `declaration preserves provenance and separates owner from compiled profile`(owner: BackendProvider) throws {
        let declaration = try DevContainerServiceCommand.providerDeclaration(
            runtime: descriptor(), resourceOwner: owner
        )
        #expect(declaration.kind == .devcontainerStock)
        #expect(declaration.implementationVersion == BuildInfo.current.version)
        #expect(declaration.stateSchemaVersion == UInt64(SQLiteStateStore.schemaVersion))
        #expect(declaration.runtimeRevisions == [
            "apple-container": "1.4.1", "apple-container-commit": "runtime-source",
            "devcontainer": BuildInfo.current.commit, "resource-owner": owner.rawValue
        ])
        let handoffs = Set(declaration.capabilities.filter { $0.identifier.hasPrefix("engine.handoff.") })
        var expected = ["engine.handoff.part.identity-lifecycle-events.v1", "engine.handoff.provider-key-enrollment.v1"]
        #if DEVCONTAINER_ENHANCED_RUNTIME
            #expect(declaration.profile == .enhanced)
            expected.append("engine.handoff.part.logging.v1")
        #else
            #expect(declaration.profile == .stock)
        #endif
        #expect(Set(handoffs.map(\.identifier)) == Set(expected))
        #expect(handoffs.allSatisfy { $0.status == .native })
    }

    @Test
    func `declaration projects unsupported runtime capabilities without inventing routes`() throws {
        let declaration = try DevContainerServiceCommand.providerDeclaration(
            runtime: descriptor(), resourceOwner: .stock
        )
        let capabilities = Dictionary(uniqueKeysWithValues: declaration.capabilities.map { ($0.identifier, $0.status) })
        #expect(capabilities["engine.containers"] == .native)
        #expect(capabilities["engine.attach"] == .emulated)
        #expect(capabilities["engine.registryAuthentication"] == .unavailable)
        #expect(capabilities["engine.volumes"] == nil)
        #expect(capabilities["engine.route.ContainerAttachWebsocket"] == .emulated)
        #expect(capabilities["engine.route.ContainerResize"] == .native)
        let routes = Set(capabilities.keys.filter { $0.hasPrefix("engine.route.") })
        let expectedRoutes = """
        SystemPing SystemPingHead SystemVersion SystemInfo ContainerList ContainerCreate ContainerInspect ContainerStart
        ContainerStop ContainerRestart ContainerKill ContainerRename ContainerWait ContainerExec ContainerLogs
        ContainerAttach ContainerResize
        ContainerAttachWebsocket ContainerArchive ContainerArchiveInfo PutContainerArchive ContainerDelete ExecInspect
        ExecStart ExecResize ImageList ImageInspect ImageCreate ImageLoad ImageBuild ImageTag ImageDelete NetworkList
        NetworkCreate NetworkInspect NetworkConnect NetworkDisconnect NetworkDelete VolumeList VolumeCreate
        VolumeInspect
        VolumeDelete SystemEvents
        """.split(whereSeparator: \.isWhitespace).map { "engine.route.\($0)" }
        #expect(routes == Set(expectedRoutes))
        let nativeRoutes = routes.filter { $0 != "engine.route.ContainerAttachWebsocket" }
        #expect(nativeRoutes.allSatisfy { capabilities[$0] == .native })
    }

    @Test
    func `fingerprint is deterministic but changes with runtime identity owner or effective capability`() throws {
        let root = UUID()
        let original = descriptor()
        func fingerprint(_ value: ProtocolDescriptor, owner: BackendProvider = .stock) throws -> String {
            try ContainerEngineProviderFingerprint(
                declaration: DevContainerServiceCommand.providerDeclaration(runtime: value, resourceOwner: owner),
                stateRootUUID: root
            ).digest
        }
        let expected = try fingerprint(original)
        var reordered = original
        reordered.capabilities = Dictionary(uniqueKeysWithValues: original.capabilities.reversed())
        #expect(try fingerprint(reordered) == expected)
        #expect(try fingerprint(original, owner: .containerCompose) != expected)
        var changed = original
        changed.providerCommit = "another-runtime-source"
        #expect(try fingerprint(changed) != expected)
        changed = original
        changed.capabilities[.attach] = .native
        #expect(try fingerprint(changed) != expected)
    }

    @Test(arguments: [true, false])
    func `empty runtime provenance fails before identity enrollment`(version: Bool) throws {
        var invalid = descriptor()
        if version {
            invalid.providerVersion = ""
        } else {
            invalid.providerCommit = ""
        }
        #expect(throws: ContainerEngineProviderIdentityError.invalidDeclaration) {
            try DevContainerServiceCommand.providerDeclaration(runtime: invalid, resourceOwner: .stock)
        }
    }

    private func descriptor() -> ProtocolDescriptor {
        ProtocolDescriptor(
            provider: .stock, providerVersion: "1.4.1", providerCommit: "runtime-source", distribution: "fixture",
            capabilities: [.containers: .native, .attach: .emulated, .registryAuthentication: .unsupported]
        )
    }
}
