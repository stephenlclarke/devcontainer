// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Containerization
import ContainerizationError
import ContainerizationOCI
import ContainerResource
@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

struct AppleContainerCreationRecoveryTests {
    private var digest: String {
        "sha256:" + String(repeating: "a", count: 64)
    }

    private struct Inventory: AppleContainerInventoryClient {
        let snapshot: ContainerResource.ContainerSnapshot?
        var unavailable = false

        func list() -> [ContainerResource.ContainerSnapshot] {
            snapshot.map { [$0] } ?? []
        }

        func get(id _: String) throws -> ContainerResource.ContainerSnapshot {
            if unavailable {
                throw DevContainerError(.runtimeUnavailable, message: "Injected transport failure")
            }
            guard let snapshot else { throw ContainerizationError(.notFound, message: "Absent fixture") }
            return snapshot
        }
    }

    private func composeSpec() -> ContainerSpec {
        var labels = ["com.apple.container.compose.version": "1"]
        for prefix in ["com.apple.container.compose.", "com.docker.compose."] {
            labels[prefix + "project"] = "test-project"
            labels[prefix + "service"] = "app"
            labels[prefix + "oneoff"] = "false"
        }
        return ContainerSpec(
            name: "fixture", image: FakeAppleImageIdentityClient.digest, command: ["/bin/true"], labels: labels
        )
    }

    @Test(arguments: [
        "absent", "replacement", "unavailable", "wrong-id", "labels", "process", "mount", "security"
    ])
    func `restarted recovery retains ambiguous creation evidence`(mode: String) async throws {
        let fixture = try FakeAppleCLI(distribution: "enhanced")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = AppleContainerCreateTests.Creator(failAfterCreate: true)
        let store = TestMetadataStore()
        let original = try fixture.runtime(metadataStore: store, creator: creator)
        await #expect(throws: DevContainerError.self) {
            try await original.createContainer(
                spec: ContainerSpec(name: "fixture", image: FakeAppleImageIdentityClient.digest),
                context: RuntimeRequestContext()
            )
        }
        let intent = try #require(await store.pendingContainerCreation(id: "fixture"))
        var native = try #require(await creator.created.first)
        if mode == "replacement" {
            native.creationDate = native.creationDate.addingTimeInterval(1)
        }
        if mode == "wrong-id" {
            native.id = "foreign"
        }
        switch mode {
        case "labels": native.labels["foreign"] = "replacement"
        case "process": native.initProcess.arguments.append("replacement")
        case "mount":
            native.mounts.append(.virtiofs(source: "/tmp/foreign", destination: "/etc/hosts", options: []))
        case "security": native.readOnly.toggle()
        default: break
        }
        let inventory = Inventory(
            snapshot: mode == "absent" ? nil : .init(configuration: native, status: .stopped, networks: []),
            unavailable: mode == "unavailable"
        )
        let restarted = try fixture.runtime(
            metadataStore: store, creator: AppleContainerCreateTests.Creator(), inventory: inventory
        )
        await #expect(throws: DevContainerError.self) {
            try await restarted.requireRecoveryQuiescence(context: RuntimeRequestContext())
        }
        #expect(await store.pendingContainerCreation(id: "fixture") == intent)
        #expect(await store.containerMetadata(id: "fixture") == nil)
        #expect(try !(fixture.log()).contains("delete"))
    }

    @Test func `restarted recovery commits exact native incarnation atomically`() async throws {
        let fixture = try FakeAppleCLI(distribution: "enhanced")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = AppleContainerCreateTests.Creator(failAfterCreate: true)
        let store = TestMetadataStore()
        let original = try fixture.runtime(metadataStore: store, creator: creator)
        await #expect(throws: DevContainerError.self) {
            try await original.createContainer(
                spec: ContainerSpec(name: "fixture", image: FakeAppleImageIdentityClient.digest),
                context: RuntimeRequestContext()
            )
        }
        let intent = try #require(await store.pendingContainerCreation(id: "fixture"))
        let native = try #require(await creator.created.first)
        let restarted = try fixture.runtime(
            metadataStore: store,
            creator: AppleContainerCreateTests.Creator(),
            inventory: Inventory(snapshot: .init(configuration: native, status: .stopped, networks: []))
        )
        try await restarted.requireRecoveryQuiescence(context: RuntimeRequestContext())
        #expect(await store.pendingContainerCreation(id: "fixture") == nil)
        let metadata = try #require(await store.containerMetadata(id: "fixture"))
        #expect(metadata.runtimeID.rawValue == intent.runtimeID)
        #expect(metadata.imageID == intent.imageID)
        #expect(metadata.spec == intent.spec)
        #expect(metadata.createdAt == intent.nativeCreatedAt)
    }

    @Test func `cancelled restarted recovery retains creation intent`() async throws {
        let fixture = try FakeAppleCLI(distribution: "enhanced")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = AppleContainerCreateTests.Creator(failAfterCreate: true)
        let store = TestMetadataStore()
        let original = try fixture.runtime(metadataStore: store, creator: creator)
        await #expect(throws: DevContainerError.self) {
            try await original.createContainer(
                spec: ContainerSpec(name: "fixture", image: FakeAppleImageIdentityClient.digest),
                context: RuntimeRequestContext()
            )
        }
        let intent = try #require(await store.pendingContainerCreation(id: "fixture"))
        let native = try #require(await creator.created.first)
        let restarted = try fixture.runtime(
            metadataStore: store, creator: AppleContainerCreateTests.Creator(),
            inventory: Inventory(snapshot: .init(configuration: native, status: .stopped, networks: []))
        )
        let recovery = Task {
            withUnsafeCurrentTask { $0?.cancel() }
            try await restarted.requireRecoveryQuiescence(context: RuntimeRequestContext())
        }
        do {
            try await recovery.value
            Issue.record("cancelled recovery unexpectedly completed")
        } catch let error as DevContainerError {
            #expect(error.code == .cancelled)
        }
        #expect(await store.pendingContainerCreation(id: "fixture") == intent)
        #expect(await store.containerMetadata(id: "fixture") == nil)
    }

    @Test(arguments: ["pending", "replacement"])
    func `direct archive upload requires the exact pending incarnation`(mode: String) async throws {
        let fixture = try FakeAppleCLI(distribution: "enhanced")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let archive = try await makeArchive(in: fixture.root)

        let spec = ContainerSpec(name: "fixture", image: digest)
        let image = ImageDescription(
            reference: spec.image,
            descriptor: Descriptor(mediaType: "application/vnd.oci.image.index.v1+json", digest: digest, size: 123)
        )
        var native = try AppleContainerCreateProjection.configuration(
            spec: spec, identity: (image, .current), imageConfig: nil, system: .init(), builtinNetwork: "default"
        )
        let intent = try RuntimeContainerCreation(
            runtimeID: "fixture", nativeCreatedAt: native.creationDate, imageID: digest,
            spec: spec, nativeConfiguration: JSONEncoder().encode(native)
        )
        if mode == "replacement" {
            native.creationDate = native.creationDate.addingTimeInterval(1)
        }
        let store = TestMetadataStore()
        try await store.beginContainerCreation(intent)
        let files = FakeContainerFileClient()
        let runtime = try fixture.runtime(
            metadataStore: store,
            useDirectProcessAPI: true,
            creator: AppleContainerCreateTests.Creator(),
            files: files,
            inventory: Inventory(snapshot: .init(configuration: native, status: .running, networks: []))
        )

        if mode == "replacement" {
            try await runtime.copyArchiveToContainer(
                id: "fixture", path: "/work", archive: archive,
                context: RuntimeRequestContext()
            )
            #expect(await files.copyInCallCount() == 1)
        } else {
            await #expect(throws: DevContainerError.self) {
                try await runtime.copyArchiveToContainer(
                    id: "fixture", path: "/work", archive: archive,
                    context: RuntimeRequestContext()
                )
            }
            #expect(await files.copyInCallCount() == 0)
        }
        #expect(await store.pendingContainerCreation(id: "fixture") == intent)
    }

    private func makeArchive(in root: URL) async throws -> Data {
        let input = root.appendingPathComponent("archive-input", isDirectory: true)
        try FileManager.default.createDirectory(at: input, withIntermediateDirectories: false)
        try Data("archive payload".utf8).write(to: input.appendingPathComponent("payload.txt"))
        let result = try await AppleCommandRunner.run(
            executable: URL(fileURLWithPath: "/usr/bin/tar"),
            arguments: ["--format=ustar", "-cf", "-", "-C", input.path, "."],
            environment: ["COPYFILE_DISABLE": "1"]
        )
        #expect(result.exitCode == 0)
        return result.standardOutput
    }

    @Test func `image labels survive create and bridge restart`() async throws {
        let fixture = try FakeAppleCLI(distribution: "enhanced")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = AppleContainerCreateTests.Creator(
            imageLabels: ["image.label": "default", "shared.label": "image"]
        )
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store, creator: creator)
        let spec = ContainerSpec(
            name: "fixture", image: FakeAppleImageIdentityClient.digest,
            labels: ["request.label": "value", "shared.label": "request"]
        )
        let created = try await runtime.createContainer(spec: spec, context: RuntimeRequestContext())
        #expect(created.spec.labels == [
            "image.label": "default", "request.label": "value", "shared.label": "request"
        ])
        let restarted = try fixture.runtime(metadataStore: store, creator: creator)
        let filtered = try await restarted.listContainersDirect(
            all: true, labels: ["image.label": "default"], context: RuntimeRequestContext()
        )
        #expect(filtered.map(\.runtimeID.rawValue) == ["fixture"])
        #expect(filtered.first?.spec.labels["shared.label"] == "request")
        let inspected = try await restarted.inspectContainerDirect(id: "fixture", context: RuntimeRequestContext())
        #expect(inspected?.spec.labels == created.spec.labels)
    }

    @Test func `image label projection covers defaults and request precedence`() throws {
        try expectProjectedLabels(image: ["image": "default"], requested: [:], expected: ["image": "default"])
        try expectProjectedLabels(image: [:], requested: ["request": "value"], expected: ["request": "value"])
        try expectProjectedLabels(
            image: ["shared": "image"], requested: ["shared": "request"], expected: ["shared": "request"]
        )
        try expectProjectedLabels(image: [:], requested: [:], expected: [:])
    }

    private func expectProjectedLabels(
        image: [String: String], requested: [String: String], expected: [String: String]
    ) throws {
        let spec = ContainerSpec(name: "fixture", image: FakeAppleImageIdentityClient.digest, labels: requested)
        let imageConfig = ImageConfig(labels: image)
        let descriptor = Descriptor(
            mediaType: "application/vnd.oci.image.index.v1+json", digest: digest, size: 123
        )
        let projected = try AppleContainerCreateProjection.configuration(
            spec: spec,
            identity: (ImageDescription(reference: "fixture:latest", descriptor: descriptor), .current),
            imageConfig: imageConfig, system: .init(), builtinNetwork: "default"
        )
        #expect(projected.labels == expected)
    }

    @Test func `mounted hosts update refuses an unresolved matching creation`() async throws {
        let fixture = try FakeAppleCLI(distribution: "enhanced")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = AppleContainerCreateTests.Creator()
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store, creator: creator)
        var target = try await runtime.createContainer(spec: composeSpec(), context: RuntimeRequestContext())
        let native = try #require(await creator.created.first)
        let mount = try #require(native.mounts.first)
        let original = try String(contentsOfFile: mount.source, encoding: .utf8)
        try await creator.start()
        target.state = .running
        target.startedAt = await creator.startedAt
        let intent = try RuntimeContainerCreation(
            runtimeID: target.runtimeID.rawValue, nativeCreatedAt: native.creationDate,
            imageID: #require(target.imageID), spec: target.spec,
            nativeConfiguration: JSONEncoder().encode(native)
        )
        try await store.beginContainerCreation(intent)
        await #expect(throws: DevContainerError.self) {
            _ = try await runtime.updateMountedNetworkHosts(
                target: target, hosts: "192.0.2.1 stale-peer\\n", context: RuntimeRequestContext()
            )
        }
        #expect(try String(contentsOfFile: mount.source, encoding: .utf8) == original)
        #expect(await store.pendingContainerCreation(id: target.runtimeID.rawValue) == intent)
    }
}
