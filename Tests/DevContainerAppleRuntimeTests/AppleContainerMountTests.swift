// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerResource
@testable import DevContainerAppleRuntime
import DevContainerModel
import Testing

struct AppleContainerMountTests {
    private enum ProbeError: Error { case unavailable, unexpectedLookup }

    private actor Volumes {
        var names: [String] = []

        func inspect(_ name: String) -> VolumeConfiguration {
            names.append(name)
            return .init(name: name, format: "ext4", source: "/test-volumes/\(name).img")
        }
    }

    @Test func `empty mounts do not query the service`() async throws {
        let mounts = try await LiveAppleContainerCreateClient.mounts([]) { _ in
            throw ProbeError.unexpectedLookup
        }
        #expect(mounts.isEmpty)
    }

    @Test(arguments: [["--mount"], ["--tmpfs"], ["--unexpected", "/work"]])
    func `invalid option pairs fail before a volume query`(options: [String]) async {
        await #expect(throws: DevContainerError.self) {
            try await LiveAppleContainerCreateClient.mounts(options) { _ in
                throw ProbeError.unexpectedLookup
            }
        }
    }

    @Test func `bind tmpfs and volume mounts retain order paths and access mode`() async throws {
        let volumes = Volumes()
        let mounts = try await LiveAppleContainerCreateClient.mounts([
            "--mount", "type=bind,source=/tmp,destination=/work,readonly",
            "--tmpfs", "/scratch:rw,size=64m",
            "--mount", "type=volume,source=cache,destination=/cache,readonly"
        ], inspectVolume: { await volumes.inspect($0) })
        #expect(mounts.count == 3)
        #expect(mounts.map(\.destination) == ["/work", "/scratch", "/cache"])
        let bind = try #require(mounts.first)
        #expect(bind.isVirtiofs)
        #expect(bind.source == "/tmp")
        #expect(bind.options.readonly)
        #expect(mounts[1].isTmpfs)
        #expect(mounts[1].options.contains("size=64m"))
        let volume = try #require(mounts.last)
        #expect(volume.isVolume)
        #expect(volume.volumeName == "cache")
        #expect(volume.source == "/test-volumes/cache.img")
        #expect(volume.options.readonly)
        #expect(await volumes.names == ["cache"])
    }

    @Test func `unavailable named volumes propagate the original failure`() async {
        await #expect(throws: ProbeError.unavailable) {
            try await LiveAppleContainerCreateClient.mounts([
                "--mount", "type=volume,source=cache,destination=/cache"
            ]) { _ in throw ProbeError.unavailable }
        }
    }

    @Test func `malformed mount syntax is not silently ignored`() async {
        let volumes = Volumes()
        await #expect(throws: (any Error).self) {
            try await LiveAppleContainerCreateClient.mounts([
                "--mount", "type=not-a-mount,source=cache,destination=/cache"
            ]) { await volumes.inspect($0) }
        }
        #expect(await volumes.names.isEmpty)
    }

    #if DEVCONTAINER_ENHANCED_RUNTIME
        @Test func `image backed mounts are rejected before runtime lookup`() async {
            await #expect(throws: DevContainerError.self) {
                try await LiveAppleContainerCreateClient.mounts([
                    "--mount", "type=image,source=example/image:latest,destination=/image"
                ]) { _ in throw ProbeError.unexpectedLookup }
            }
        }
    #endif
}
