// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerAPIClient
import Containerization
import ContainerizationError
import ContainerizationOCI
import ContainerizationOS
import ContainerPersistence
import ContainerResource
@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import DevContainerState
import Foundation
import Testing

struct AppleContainerCreateTests {
    private enum PreflightFailure: Error {
        case missingKernel
        case stoppedBeforeRPC
    }

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

    @Test func `post-create verification failure retains intent and blocks launches`() async throws {
        let fixture = try FakeAppleCLI(distribution: "enhanced")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator(failAfterCreate: true)
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store, creator: creator)
        let spec = ContainerSpec(name: "fixture", image: FakeAppleImageIdentityClient.digest, command: ["/bin/true"])
        await #expect(throws: DevContainerError.self) {
            try await runtime.createContainer(spec: spec, context: RuntimeRequestContext())
        }
        let created = try #require(await creator.created.first)
        let intent = try #require(await store.pendingContainerCreation(id: "fixture"))
        #expect(intent.nativeCreatedAt == created.creationDate)
        let recorded = try JSONDecoder().decode(ContainerConfiguration.self, from: intent.nativeConfiguration)
        #expect(recorded.image.digest == created.image.digest)
        #expect(await store.containerMetadata(id: "fixture") == nil)
        let restartedBridge = try fixture.runtime(
            metadataStore: store, creator: Creator(),
            inventory: Inventory(snapshot: .init(configuration: created, status: .stopped, networks: []))
        )
        await #expect(throws: DevContainerError.self) {
            try await restartedBridge.startContainer(id: "fixture", context: RuntimeRequestContext())
        }
        await #expect(throws: DevContainerError.self) {
            try await restartedBridge.restartContainer(id: "fixture", timeout: nil, context: RuntimeRequestContext())
        }
        let log = try fixture.log()
        #expect(!log.contains("delete"))
        #expect(!log.contains("start fixture"))
        #expect(!log.contains("restart fixture"))
        #expect(await store.pendingContainerCreation(id: "fixture") == intent)
    }

    @Test func `corrupt mount metadata does not poison a container creation retry`() async throws {
        let fixture = try FakeAppleCLI(distribution: "enhanced")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator()
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store, creator: creator)
        _ = try await runtime.createVolume(spec: VolumeSpec(name: "data"), context: RuntimeRequestContext())
        let metadata = fixture.root.appendingPathComponent("volumes/data/metadata.json")
        let original = try Data(contentsOf: metadata)
        try Data("invalid volume metadata".utf8).write(to: metadata)
        let spec = ContainerSpec(
            name: "fixture", image: FakeAppleImageIdentityClient.digest, command: ["/bin/true"],
            mounts: [.init(type: .volume, source: "data", destination: "/data")]
        )
        await #expect(throws: DevContainerError.self) {
            try await runtime.createContainer(spec: spec, context: RuntimeRequestContext())
        }
        #expect(await creator.created.isEmpty)
        #expect(await store.pendingContainerCreation(id: "fixture") == nil)
        #expect(await store.containerMetadata(id: "fixture") == nil)
        try original.write(to: metadata)
        let snapshot = try await runtime.createContainer(spec: spec, context: RuntimeRequestContext())
        #expect(snapshot.runtimeID.rawValue == "fixture")
        #expect(await creator.created.count == 1)
        #expect(await store.pendingContainerCreation(id: "fixture") == nil)
        #expect(await store.containerMetadata(id: "fixture") != nil)
    }

    @Test func `unverified native volume leaves no container creation intent`() async throws {
        let fixture = try FakeAppleCLI(distribution: "enhanced")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator()
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store, creator: creator)
        let spec = ContainerSpec(
            name: "fixture", image: FakeAppleImageIdentityClient.digest, command: ["/bin/true"],
            mounts: [.init(type: .volume, source: "buildx_buildkit_missing_state", destination: "/data")]
        )
        await #expect(throws: DevContainerError.self) {
            try await runtime.createContainer(spec: spec, context: RuntimeRequestContext())
        }
        #expect(await creator.created.isEmpty)
        #expect(await store.pendingContainerCreation(id: "fixture") == nil)
        #expect(try fixture.log().contains("volume create buildx_buildkit_missing_state"))
        let repaired = ContainerSpec(name: "fixture", image: spec.image, command: ["/bin/true"])
        _ = try await runtime.createContainer(spec: repaired, context: RuntimeRequestContext())
        #expect(await creator.created.count == 1)
    }

    @Test func `live kernel lookup fails before recording possible submission`() async throws {
        let spec = ContainerSpec(name: "fixture", image: digest)
        let native = try configuration(spec)
        let store = TestMetadataStore()
        let client = LiveAppleContainerCreateClient(client: ContainerClient(), loadKernel: {
            throw PreflightFailure.missingKernel
        })
        await #expect(throws: PreflightFailure.missingKernel) {
            try await client.create(
                configuration: native, mountOptions: [], context: RuntimeRequestContext()
            ) { prepared in
                let intent = try RuntimeContainerCreation(
                    runtimeID: prepared.id, nativeCreatedAt: prepared.creationDate, imageID: spec.image,
                    spec: spec, nativeConfiguration: JSONEncoder().encode(prepared)
                )
                try await store.beginContainerCreation(intent)
                // Never reach the real native create RPC, even on regression.
                throw PreflightFailure.stoppedBeforeRPC
            }
        }
        #expect(await store.pendingContainerCreation(id: "fixture") == nil)
    }

    @Test func `live submission journals final mounts before any native create RPC`() async throws {
        let spec = ContainerSpec(name: "fixture", image: digest)
        let native = try configuration(spec)
        let store = TestMetadataStore()
        let client = LiveAppleContainerCreateClient(client: ContainerClient(), loadKernel: {
            Kernel(path: URL(fileURLWithPath: "/unused-test-kernel"), platform: .linuxArm)
        })
        await #expect(throws: PreflightFailure.stoppedBeforeRPC) {
            try await client.create(
                configuration: native, mountOptions: ["--tmpfs", "/work"], context: RuntimeRequestContext()
            ) { prepared in
                let intent = try RuntimeContainerCreation(
                    runtimeID: prepared.id, nativeCreatedAt: prepared.creationDate, imageID: spec.image,
                    spec: spec, nativeConfiguration: JSONEncoder().encode(prepared)
                )
                try await store.beginContainerCreation(intent)
                // Stop at the real journal boundary, without contacting the service.
                throw PreflightFailure.stoppedBeforeRPC
            }
        }
        let intent = try #require(await store.pendingContainerCreation(id: "fixture"))
        let recorded = try JSONDecoder().decode(ContainerConfiguration.self, from: intent.nativeConfiguration)
        #expect(recorded.mounts.count == 1)
        #expect(recorded.mounts.first?.destination == "/work")
        #expect(native.mounts.isEmpty)
    }

    @Test func `live submission preserves typed file mounts in its durable intent`() async throws {
        let spec = ContainerSpec(name: "fixture", image: digest)
        var native = try configuration(spec)
        native.mounts = [.virtiofs(
            source: "/private/runtime-owned/share/hosts", destination: "/etc/hosts", options: ["ro"]
        )]
        let store = TestMetadataStore()
        let client = LiveAppleContainerCreateClient(client: ContainerClient(), loadKernel: {
            Kernel(path: URL(fileURLWithPath: "/unused-test-kernel"), platform: .linuxArm)
        })
        await #expect(throws: PreflightFailure.stoppedBeforeRPC) {
            try await client.create(
                configuration: native, mountOptions: ["--tmpfs", "/work"], context: RuntimeRequestContext()
            ) { prepared in
                let intent = try RuntimeContainerCreation(
                    runtimeID: prepared.id, nativeCreatedAt: prepared.creationDate, imageID: spec.image,
                    spec: spec, nativeConfiguration: JSONEncoder().encode(prepared)
                )
                try await store.beginContainerCreation(intent)
                throw PreflightFailure.stoppedBeforeRPC
            }
        }
        let intent = try #require(await store.pendingContainerCreation(id: "fixture"))
        let recorded = try JSONDecoder().decode(ContainerConfiguration.self, from: intent.nativeConfiguration)
        #expect(recorded.mounts.map(\.destination) == ["/etc/hosts", "/work"])
        #expect(recorded.mounts[0].source == "/private/runtime-owned/share/hosts")
        #expect(recorded.mounts[0].isVirtiofs)
        #expect(recorded.mounts[0].options.readonly)
    }

    @Test(arguments: ["/etc/hosts", "/etc", "/", "/etc/hosts/child", "/etc/../etc/hosts/"])
    func `overlapping runtime mounts fail before kernel lookup and journalling`(destination: String) async throws {
        let spec = ContainerSpec(name: "fixture", image: digest)
        var native = try configuration(spec)
        native.mounts = [.virtiofs(
            source: "/private/runtime-owned/share/hosts", destination: "/etc/hosts", options: ["ro"]
        )]
        let client = LiveAppleContainerCreateClient(client: ContainerClient(), loadKernel: {
            throw PreflightFailure.missingKernel
        })
        await #expect(throws: DevContainerError.self) {
            try await client.create(
                configuration: native, mountOptions: ["--tmpfs", destination], context: RuntimeRequestContext()
            ) { _ in throw PreflightFailure.stoppedBeforeRPC }
        }
    }

    @Test func `metadata completion failure preserves pending create without deletion`() async throws {
        let fixture = try FakeAppleCLI(distribution: "enhanced")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator()
        let store = TestMetadataStore(failCreationCompletion: true)
        let runtime = try fixture.runtime(metadataStore: store, creator: creator)
        await #expect(throws: MetadataTestError.self) {
            try await runtime.createContainer(
                spec: ContainerSpec(
                    name: "fixture", image: FakeAppleImageIdentityClient.digest, command: ["/bin/true"]
                ),
                context: RuntimeRequestContext()
            )
        }
        #expect(await creator.created.count == 1)
        #expect(await store.pendingContainerCreation(id: "fixture") != nil)
        #expect(await store.containerMetadata(id: "fixture") == nil)
        #expect(try !fixture.log().contains("delete"))
    }

    @Test func `replacement after create reply cannot complete the original operation`() async throws {
        let fixture = try FakeAppleCLI(distribution: "enhanced")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator(replacement: true)
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store, creator: creator)
        await #expect(throws: DevContainerError.self) {
            try await runtime.createContainer(
                spec: ContainerSpec(
                    name: "fixture", image: FakeAppleImageIdentityClient.digest, command: ["/bin/true"]
                ),
                context: RuntimeRequestContext()
            )
        }
        #expect(await creator.created.count == 1)
        #expect(await store.pendingContainerCreation(id: "fixture") != nil)
        #expect(await store.containerMetadata(id: "fixture") == nil)
        #expect(try !fixture.log().contains("delete"))
    }

    @Test(arguments: ["created", "running"])
    func `archive transfers and rename cannot bypass pending creation`(state: String) async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        try fixture.setState(state)
        let store = TestMetadataStore()
        let spec = ContainerSpec(name: "fixture", image: digest)
        let native = try configuration(spec)
        let intent = try RuntimeContainerCreation(
            runtimeID: "fixture", nativeCreatedAt: native.creationDate, imageID: digest,
            spec: spec, nativeConfiguration: JSONEncoder().encode(native)
        )
        try await store.beginContainerCreation(intent)
        let runtime = try fixture.runtime(
            metadataStore: store,
            inventory: Inventory(snapshot: .init(configuration: native, status: .stopped, networks: []))
        )
        await #expect(throws: DevContainerError.self) {
            try await runtime.copyArchiveFromContainer(id: "fixture", path: "/work", context: RuntimeRequestContext())
        }
        await #expect(throws: DevContainerError.self) {
            try await runtime.copyArchiveToContainer(
                id: "fixture", path: "/work", archive: Data(repeating: 0, count: 1024), context: RuntimeRequestContext()
            )
        }
        await #expect(throws: DevContainerError.self) {
            try await runtime.renameContainer(id: "fixture", name: "renamed", context: RuntimeRequestContext())
        }
        let log = try fixture.log()
        #expect(!log.contains("start fixture"))
        #expect(!log.contains("exec fixture"))
        #expect(!log.contains("cp "))
        #expect(await store.containerMetadata(id: "fixture") == nil)
        #expect(await store.pendingContainerCreation(id: "fixture") == intent)
    }

    @Test func `explicit removal retains unresolved creation evidence`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let store = TestMetadataStore()
        let spec = ContainerSpec(name: "fixture", image: digest)
        let native = try configuration(spec)
        let intent = try RuntimeContainerCreation(
            runtimeID: "fixture", nativeCreatedAt: native.creationDate, imageID: digest,
            spec: spec, nativeConfiguration: JSONEncoder().encode(native)
        )
        try await store.beginContainerCreation(intent)
        let runtime = try fixture.runtime(metadataStore: store)
        try await runtime.removeContainer(id: "fixture", force: true, context: RuntimeRequestContext())
        #expect(try fixture.log().contains("delete --force fixture"))
        #expect(await store.pendingContainerCreation(id: "fixture") == intent)
    }

    @Test func `native creation requires a creation journal before side effects`() async throws {
        let fixture = try FakeAppleCLI(distribution: "enhanced")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator()
        let runtime = try fixture.runtime(metadataStore: FailingMetadataStore(), creator: creator)
        await #expect(throws: DevContainerError.self) {
            try await runtime.createContainer(
                spec: ContainerSpec(name: "fixture", image: FakeAppleImageIdentityClient.digest),
                context: RuntimeRequestContext()
            )
        }
        #expect(await creator.prepared.isEmpty)
        #expect(await creator.created.isEmpty)
    }

    @Test(arguments: ["absent", "replacement", "running", "unavailable", "wrong-id"])
    func `reconciliation never deletes by mutable name`(mode: String) async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let store = TestMetadataStore()
        let spec = ContainerSpec(name: "fixture", image: digest)
        var native = try configuration(spec)
        let intent = try RuntimeContainerCreation(
            runtimeID: "fixture", nativeCreatedAt: native.creationDate, imageID: digest,
            spec: spec, nativeConfiguration: JSONEncoder().encode(native)
        )
        try await store.beginContainerCreation(intent)
        if mode == "replacement" {
            native.creationDate = native.creationDate.addingTimeInterval(1)
        }
        if mode == "wrong-id" {
            native.id = "foreign"
        }
        let inventory = Inventory(
            snapshot: mode == "absent" ? nil : .init(configuration: native, status: .running, networks: []),
            unavailable: mode == "unavailable"
        )
        let runtime = try fixture.runtime(metadataStore: store, inventory: inventory)
        if mode == "replacement" {
            try await runtime.requireCompletedCreation(id: "fixture")
        } else {
            await #expect(throws: DevContainerError.self) {
                try await runtime.requireCompletedCreation(id: "fixture")
            }
        }
        #expect(await store.pendingContainerCreation(id: "fixture") == intent)
        await #expect(throws: DevContainerError.self) {
            try await runtime.requireCompletedCreation(id: "fixture", forCreate: true)
        }
        #expect(!FileManager.default.fileExists(atPath: fixture.logURL.path))
    }

    @Test func `image id creation never forwards a mutable image tag to CLI`() async throws {
        let fixture = try FakeAppleCLI(distribution: "enhanced")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator()
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store, creator: creator)
        let spec = ContainerSpec(name: "fixture", image: FakeAppleImageIdentityClient.digest, command: ["/bin/true"])
        let snapshot = try await runtime.createContainer(spec: spec, context: RuntimeRequestContext())
        #expect(snapshot.imageID == FakeAppleImageIdentityClient.digest)
        let created = try #require(await creator.created.first)
        #expect(created.image.digest == digest)
        #expect(created.image.reference == "fixture:latest")
        #expect(created.initProcess.executable == "/bin/true")
        #expect(try !(fixture.log()).contains("create --name"))
        #expect(await store.pendingContainerCreation(id: "fixture") == nil)
        #expect(await store.containerMetadata(id: "fixture")?.imageID == FakeAppleImageIdentityClient.digest)
        #expect(snapshot.dockerID.rawValue.count == 64)
        let hexadecimalID = snapshot.dockerID.rawValue.allSatisfy(\.isHexDigit)
        #expect(hexadecimalID)
        let restarted = try fixture.runtime(metadataStore: store, creator: creator)
        let listed = try await restarted.listContainersDirect(all: true, labels: [:], context: RuntimeRequestContext())
        #expect(listed.first?.dockerID == snapshot.dockerID)
        let inspected = try await restarted.inspectContainerDirect(
            id: snapshot.dockerID.rawValue, context: RuntimeRequestContext()
        )
        #expect(inspected?.dockerID == snapshot.dockerID)
    }

    @Test func `native completion preserves supplied Docker identity`() async throws {
        let fixture = try FakeAppleCLI(distribution: "enhanced")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator()
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store, creator: creator)
        let dockerID = String(repeating: "d", count: 64)
        let spec = ContainerSpec(
            name: "fixture", image: FakeAppleImageIdentityClient.digest, command: ["/bin/true"],
            labels: [AppleContainerRuntime.dockerIDLabel: dockerID]
        )
        let snapshot = try await runtime.createContainer(spec: spec, context: RuntimeRequestContext())
        #expect(snapshot.dockerID.rawValue == dockerID)
        #expect(await store.containerMetadata(id: "fixture")?.dockerID == snapshot.dockerID)
    }

    @Test func `invalid digest creation does not allocate volumes`() async throws {
        let fixture = try FakeAppleCLI(distribution: "enhanced")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator()
        let runtime = try fixture.runtime(creator: creator)
        let spec = ContainerSpec(
            name: "fixture", image: FakeAppleImageIdentityClient.digest, command: ["/bin/true"],
            mounts: [.init(type: .volume, source: "new-volume", destination: "/data")],
            networks: [.init(name: "none"), .init(name: "other")]
        )
        await #expect(throws: DevContainerError.self) {
            try await runtime.createContainer(spec: spec, context: RuntimeRequestContext())
        }
        #expect(await creator.created.isEmpty)
        let volumes = try FileManager.default.contentsOfDirectory(
            atPath: fixture.root.appendingPathComponent("volumes").path
        )
        #expect(volumes.isEmpty)
        #expect(try !(fixture.log()).contains("volume create"))
    }

    @Test func `missing captured image content fails without CLI fallback or metadata`() async throws {
        let fixture = try FakeAppleCLI(distribution: "enhanced")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator(failCreate: true)
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store, creator: creator)
        await #expect(throws: DevContainerError.self) {
            try await runtime.createContainer(
                spec: ContainerSpec(
                    name: "fixture", image: FakeAppleImageIdentityClient.digest, command: ["/bin/true"]
                ),
                context: RuntimeRequestContext()
            )
        }
        #expect(await creator.created.isEmpty)
        #expect(await store.containerMetadata(id: "fixture") == nil)
        // Once create was submitted, even an error must retain uncertain intent.
        #expect(await store.pendingContainerCreation(id: "fixture") != nil)
        #expect(try !(fixture.log()).contains("create --name"))
        #expect(try !(fixture.log()).contains("image pull"))
    }

    private func descriptor() throws -> Descriptor {
        try JSONDecoder().decode(Descriptor.self, from: Data("""
        {"mediaType":"application/vnd.oci.image.index.v1+json","digest":"\(digest)","size":123}
        """.utf8))
    }

    private func imageConfig() throws -> ImageConfig {
        try JSONDecoder().decode(ImageConfig.self, from: Data("""
        {"Entrypoint":["/bin/sh","-c"],"Cmd":["echo image"],"User":"1000:1000",
        "WorkingDir":"/image","Env":["A=original","B=retained"],"StopSignal":"SIGTERM"}
        """.utf8))
    }
}

extension AppleContainerCreateTests {
    private func nativeComposeSpec() -> ContainerSpec {
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

    @Test func `native Compose allocation shares the durable creation operation identity`() async throws {
        let fixture = try FakeAppleCLI(distribution: "enhanced")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator()
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store, creator: creator)
        let snapshot = try await runtime.createContainer(spec: nativeComposeSpec(), context: RuntimeRequestContext())
        let native = try #require(await creator.created.first)
        let identity = try #require(await runtime.managedNetworkHostsIdentity(configuration: native))
        let hosts = try #require(native.mounts.first)
        #expect(hosts.options.readonly)
        #expect(try String(contentsOfFile: hosts.source, encoding: .utf8) == AppleContainerRuntime.initialNetworkHosts)
        #expect(await store.pendingContainerCreation(id: "fixture") == nil)
        let metadata = try #require(await store.containerMetadata(id: "fixture"))
        #expect(metadata.spec.labels[AppleContainerRuntime.managedNetworkHostsLabel] == identity.operationID.uuidString)
        #expect(snapshot.spec.labels[AppleContainerRuntime.managedNetworkHostsLabel] == identity.operationID.uuidString)
        let restarted = try fixture.runtime(metadataStore: store, creator: creator)
        #expect(try await restarted.managedNetworkHostsIdentity(configuration: native) == identity)
    }

    @Test(arguments: [false, true])
    func `mounted hosts reject a stopped or restarted generation before writing`(restarted: Bool) async throws {
        let fixture = try FakeAppleCLI(distribution: "enhanced")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator()
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store, creator: creator)
        var target = try await runtime.createContainer(spec: nativeComposeSpec(), context: RuntimeRequestContext())
        let native = try #require(await creator.created.first)
        let hosts = try #require(native.mounts.first)
        let original = try String(contentsOfFile: hosts.source, encoding: .utf8)
        let started = Date(timeIntervalSince1970: 100)
        target.state = .running
        target.startedAt = started
        let changed = ContainerResource.ContainerSnapshot(
            configuration: native, status: restarted ? .running : .stopped, networks: [],
            startedDate: restarted ? started.addingTimeInterval(1) : started
        )
        let bridge = try fixture.runtime(
            metadataStore: store,
            creator: creator,
            inventory: Inventory(snapshot: changed)
        )
        await #expect(throws: DevContainerError.self) {
            _ = try await bridge.updateMountedNetworkHosts(
                target: target, hosts: "192.0.2.1 stale-peer\n", context: RuntimeRequestContext()
            )
        }
        #expect(try String(contentsOfFile: hosts.source, encoding: .utf8) == original)
    }

    @Test(arguments: ["active", "retired", "removed"])
    func `native absence cleanup resumes from durable ownership`(phase: String) async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator()
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store, creator: creator)
        let target = try await runtime.createContainer(spec: nativeComposeSpec(), context: RuntimeRequestContext())
        let native = try #require(await creator.created.first)
        let identity = try #require(await runtime.managedNetworkHostsIdentity(configuration: native))
        let backing = try ManagedNetworkHostsStore(root: fixture.root.appendingPathComponent("network-hosts"))
        let slot = backing.fileURL(for: identity).deletingLastPathComponent().deletingLastPathComponent()
        if phase == "retired" {
            try FileManager.default.moveItem(at: slot, to: slot.appendingPathExtension("retired"))
        } else if phase == "removed" {
            try backing.remove(identity: identity)
        }
        let bridge = try fixture.runtime(metadataStore: store, creator: creator, inventory: Inventory(snapshot: nil))
        // Inventory must not discard the only durable identity before recovery.
        _ = try await bridge.listContainers(all: true, labels: [:], context: RuntimeRequestContext())
        #expect(await store.containerMetadata(id: "fixture") != nil)
        try await bridge.removeContainer(id: target.dockerID.rawValue, force: false, context: RuntimeRequestContext())
        #expect(await store.containerMetadata(id: "fixture") == nil)
        #expect(!FileManager.default.fileExists(atPath: slot.path))
        #expect(!FileManager.default.fileExists(atPath: slot.appendingPathExtension("retired").path))
    }

    @Test func `absent container retains cleanup identity when backing provenance is unsafe`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator()
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store, creator: creator)
        _ = try await runtime.createContainer(spec: nativeComposeSpec(), context: RuntimeRequestContext())
        let native = try #require(await creator.created.first)
        let hosts = try URL(fileURLWithPath: #require(native.mounts.first).source)
        let extra = hosts.deletingLastPathComponent().appendingPathComponent("unexpected")
        try Data("retain".utf8).write(to: extra)
        let bridge = try fixture.runtime(metadataStore: store, creator: creator, inventory: Inventory(snapshot: nil))
        await #expect(throws: (any Error).self) {
            try await bridge.removeContainer(id: "fixture", force: true, context: RuntimeRequestContext())
        }
        #expect(await store.containerMetadata(id: "fixture") != nil)
        #expect(try String(contentsOf: extra, encoding: .utf8) == "retain")
        #expect(FileManager.default.fileExists(atPath: hosts.path))
    }

    @Test func `SQLite journal completes owned creation and retains recovery authority`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator()
        let path = fixture.root.appendingPathComponent("state.sqlite")
        let store = try SQLiteStateStore(path: path)
        let runtime = try fixture.runtime(metadataStore: store, creator: creator)
        let created = try await runtime.createContainer(spec: nativeComposeSpec(), context: RuntimeRequestContext())
        #expect(try await store.pendingContainerCreation(id: "fixture") == nil)
        let reopened = try SQLiteStateStore(path: path)
        let metadata = try #require(try await reopened.containerMetadata(id: "fixture"))
        #expect(metadata.spec.labels[AppleContainerRuntime.managedNetworkHostsLabel] != nil)
        let replacement = Creator()
        let bridge = try fixture.runtime(
            metadataStore: reopened,
            creator: replacement,
            inventory: Inventory(snapshot: nil)
        )
        await #expect(throws: DevContainerError.self) {
            _ = try await bridge.createContainer(spec: nativeComposeSpec(), context: RuntimeRequestContext())
        }
        #expect(await replacement.created.isEmpty)
        #expect(try await reopened.containerMetadata(id: "fixture") == metadata)
        try await bridge.removeContainer(id: created.dockerID.rawValue, force: false, context: RuntimeRequestContext())
        #expect(try await reopened.containerMetadata(id: "fixture") == nil)
    }

    @Test(arguments: [false, true])
    func `managed restart rejects CLI fallback without changing lifecycle`(running: Bool) async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator()
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store, creator: creator)
        _ = try await runtime.createContainer(spec: nativeComposeSpec(), context: RuntimeRequestContext())
        if running {
            try await creator.start()
        }
        let metadata = await store.containerMetadata(id: "fixture")
        await #expect(throws: DevContainerError.self) {
            try await runtime.restartContainer(id: "fixture", timeout: nil, context: RuntimeRequestContext())
        }
        #expect(await store.containerMetadata(id: "fixture") == metadata)
        #expect(try !fixture.log().split(separator: "\n").contains {
            $0.hasPrefix("stop ") || $0.hasPrefix("restart ")
        })
    }

    @Test(arguments: [false, true])
    func `failed hosts preparation survives stop and restart without stopping the booted VM`(
        previouslyStarted: Bool
    ) async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator()
        let attachment = try ContainerResource.Attachment(
            network: "shared", hostname: "fixture", ipv4Address: .init("192.0.2.8/24"),
            ipv4Gateway: .init("192.0.2.1"), ipv6Address: nil, macAddress: nil
        )
        let runtime = try fixture.runtime(
            useDirectProcessAPI: true, creator: creator, bootstrap: creator,
            networks: FakeNetworkClient(snapshot: .init(
                id: "shared", spec: NetworkSpec(name: "shared"), createdAt: Date()
            )), allocations: FakeManagedNetworkAllocations(attachment: attachment, failOnce: true)
        )
        var spec = nativeComposeSpec()
        spec.networks = [.init(name: "shared")]
        _ = try await runtime.createContainer(spec: spec, context: RuntimeRequestContext())
        if previouslyStarted {
            await creator.recordPriorStart()
        }
        await #expect(throws: DevContainerError.self) {
            try await runtime.startContainer(id: "fixture", context: RuntimeRequestContext())
        }
        #expect(await creator.bootstraps == 1)
        #expect(await creator.starts == 0)
        _ = try await runtime.portForwarding.start(
            containerID: "fixture", bindings: [PortBinding(
                containerPort: 80, hostPort: 0, hostAddress: "127.0.0.1"
            )], networkAddresses: ["shared": "192.0.2.8"]
        )
        #expect(await runtime.portForwarding.hasListeners(containerID: "fixture"))
        try await runtime.stopContainer(id: "fixture", timeout: nil, context: RuntimeRequestContext())
        #expect(await !runtime.portForwarding.hasListeners(containerID: "fixture"))
        try await runtime.restartContainer(id: "fixture", timeout: nil, context: RuntimeRequestContext())
        #expect(await creator.bootstraps == 2)
        #expect(await creator.starts == 1)
        #expect(await creator.startedAt != nil)
        #expect(try !fixture.log().split(separator: "\n").contains { $0.hasPrefix("stop ") })
        await runtime.shutdown()
    }

    @Test func `managed hosts allocation is installed before the original process starts`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator()
        let attachment = try ContainerResource.Attachment(
            network: "shared", hostname: "fixture", ipv4Address: .init("192.0.2.8/24"),
            ipv4Gateway: .init("192.0.2.1"), ipv6Address: nil, macAddress: nil
        )
        let runtime = try fixture.runtime(
            useDirectProcessAPI: true, creator: creator, bootstrap: creator,
            networks: FakeNetworkClient(snapshot: .init(
                id: "shared", spec: NetworkSpec(name: "shared"), createdAt: Date()
            )), allocations: FakeManagedNetworkAllocations(attachment: attachment), files: FakeContainerFileClient()
        )
        var spec = nativeComposeSpec()
        spec.networks = [.init(name: "shared")]
        _ = try await runtime.createContainer(spec: spec, context: RuntimeRequestContext())
        try await addStartupPeers(creator)
        try await runtime.startContainer(id: "fixture", context: RuntimeRequestContext())
        let beforeBootstrap = try #require(await creator.hostsAtBootstrap)
        let atProcessStart = try #require(await creator.hostsAtStart)
        #expect(!beforeBootstrap.contains("192.0.2.8"))
        #expect(atProcessStart.contains("192.0.2.8"))
        for contents in [beforeBootstrap, atProcessStart] {
            #expect(contents.split(separator: "\n").contains { line in
                let fields = line.split(whereSeparator: \.isWhitespace)
                return fields.first == "192.0.2.9" && fields.dropFirst().contains("database")
                    && fields.dropFirst().contains("test-project-database-1")
            })
            #expect(!contents.contains("198.51.100.9"))
            #expect(!contents.contains("isolated"))
        }
        #expect(atProcessStart.contains("app"))
        #expect(atProcessStart.contains("localhost"))
        #expect(await creator.bootstraps == 1)
        #expect(await creator.starts == 1)
        await runtime.shutdown()
    }

    private func addStartupPeers(_ creator: Creator) async throws {
        var peer = try #require(await creator.created.first)
        peer.id = "test-project-database-1"
        peer.labels["com.apple.container.compose.service"] = "database"
        for key in peer.labels.keys where key.hasPrefix("com.docker.compose.") {
            peer.labels.removeValue(forKey: key)
        }
        peer.labels.removeValue(forKey: AppleContainerRuntime.managedNetworkHostsLabel)
        peer.mounts = []
        peer.networks = [.init(network: "shared", options: .init(hostname: peer.id, mtu: 1280))]
        try await creator.addRunningPeer(configuration: peer, attachment: .init(
            network: "shared", hostname: peer.id, ipv4Address: .init("192.0.2.9/24"),
            ipv4Gateway: .init("192.0.2.1"), ipv6Address: nil, macAddress: nil
        ))
        peer.id = "test-project-isolated-1"
        peer.labels["com.apple.container.compose.service"] = "isolated"
        peer.networks = [.init(network: "private", options: .init(hostname: peer.id, mtu: 1280))]
        try await creator.addRunningPeer(configuration: peer, attachment: .init(
            network: "private", hostname: peer.id, ipv4Address: .init("198.51.100.9/24"),
            ipv4Gateway: .init("198.51.100.1"), ipv6Address: nil, macAddress: nil
        ))
    }

    @Test(arguments: ["missing", "wrong-network", "wrong-host", "allocated"])
    func `pre-entrypoint hosts require the exact bootstrapped network allocation`(mode: String) async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator()
        let attachment = try ContainerResource.Attachment(
            network: mode == "wrong-network" ? "isolated" : "shared",
            hostname: mode == "wrong-host" ? "another-container" : "fixture",
            ipv4Address: .init("192.0.2.8/24"), ipv4Gateway: .init("192.0.2.1"),
            ipv6Address: nil, macAddress: nil
        )
        let networks = FakeNetworkClient(snapshot: .init(
            id: "shared", spec: NetworkSpec(name: "shared"), createdAt: Date()
        ))
        let runtime = try fixture.runtime(
            creator: creator, networks: networks,
            allocations: FakeManagedNetworkAllocations(attachment: mode == "missing" ? nil : attachment)
        )
        var spec = nativeComposeSpec()
        spec.networks = [.init(name: "shared")]
        _ = try await runtime.createContainer(spec: spec, context: RuntimeRequestContext())
        let native = try #require(await creator.created.first)
        let hosts = try #require(native.mounts.first)
        if mode == "allocated" {
            try await runtime.populateManagedHostsBeforeProcess(
                configuration: native, includeAllocatedSelf: true, context: RuntimeRequestContext()
            )
            let contents = try String(contentsOfFile: hosts.source, encoding: .utf8)
            #expect(contents.contains("192.0.2.8"))
            #expect(contents.contains("app"))
            #expect(contents.contains("localhost"))
        } else {
            await #expect(throws: DevContainerError.self) {
                try await runtime.populateManagedHostsBeforeProcess(
                    configuration: native, includeAllocatedSelf: true, context: RuntimeRequestContext()
                )
            }
            #expect(try String(contentsOfFile: hosts.source, encoding: .utf8) == AppleContainerRuntime
                .initialNetworkHosts)
        }
    }

    @Test func `failed creation intent never allocates a shared hosts file`() async throws {
        let fixture = try FakeAppleCLI(distribution: "enhanced")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator()
        let runtime = try fixture.runtime(metadataStore: TestMetadataStore(failCreationIntent: true), creator: creator)
        await #expect(throws: DevContainerError.self) {
            try await runtime.createContainer(spec: nativeComposeSpec(), context: RuntimeRequestContext())
        }
        #expect(await creator.created.isEmpty)
        let files = try FileManager.default
            .contentsOfDirectory(atPath: fixture.root.appendingPathComponent("network-hosts").path)
        #expect(files.isEmpty)
    }

    @Test func `uncertain native creation retains the journalled backing file`() async throws {
        let fixture = try FakeAppleCLI(distribution: "enhanced")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store, creator: Creator(failAfterCreate: true))
        await #expect(throws: DevContainerError.self) {
            try await runtime.createContainer(spec: nativeComposeSpec(), context: RuntimeRequestContext())
        }
        let intent = try #require(await store.pendingContainerCreation(id: "fixture"))
        let native = try JSONDecoder().decode(ContainerConfiguration.self, from: intent.nativeConfiguration)
        let identity = try #require(await runtime.managedNetworkHostsIdentity(configuration: native))
        #expect(identity.operationID == intent.operationID)
        #expect(try FileManager.default.fileExists(atPath: #require(native.mounts.first).source))
        #expect(await store.containerMetadata(id: "fixture") == nil)
    }

    private func configuration(_ spec: ContainerSpec) throws -> ContainerConfiguration {
        try AppleContainerCreateProjection.configuration(
            spec: spec, identity: (ImageDescription(reference: "fixture:latest", descriptor: descriptor()), .current),
            imageConfig: imageConfig(), system: .init(), builtinNetwork: "default"
        )
    }

    @Test func `native DNS projection preserves resolver policy and explicit BuildKit precedence`() async throws {
        var spec = ContainerSpec(name: "buildx_buildkit_fixture", image: "buildkit:test")
        #expect(AppleContainerRuntime.requiresHostDNS(spec))
        spec.dns = .init(searchDomains: ["example.test"], options: ["ndots:2"])
        #expect(AppleContainerRuntime.requiresHostDNS(spec))
        spec.dns?.nameservers = ["192.0.2.53", "2001:db8::53"]
        #expect(!AppleContainerRuntime.requiresHostDNS(spec))
        let native = try configuration(spec)
        #expect(native.dns?.nameservers == spec.dns?.nameservers)
        #expect(native.dns?.searchDomains == ["example.test"])
        #expect(native.dns?.options == ["ndots:2"])
        let object = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(native)) as? [String: Any])
        let adopted = AppleContainerRuntime.observedContainerSpec(id: spec.name, configuration: object, labels: [:])
        #expect(adopted.dns == spec.dns)
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let runtime = try fixture.runtime()
        let typed = try await runtime.containerRecord(.init(configuration: native, status: .stopped, networks: []))
        #expect(typed.spec.dns == spec.dns)
        var legacy = spec
        legacy.dns = nil
        #expect(AppleContainerRuntime.effectiveContainerSpec(requested: legacy, observed: typed.spec).dns == spec.dns)
        var explicitDefaults = spec
        explicitDefaults.dns = .init()
        let effective = AppleContainerRuntime.effectiveContainerSpec(requested: explicitDefaults, observed: typed.spec)
        #expect(effective.dns == .init())
        spec.dns?.nameservers = ["invalid"]
        #expect(throws: DevContainerError.self) { try configuration(spec) }
    }

    @Test func `native projection retains image defaults and descriptor`() throws {
        let config = try configuration(ContainerSpec(name: "fixture", image: digest))
        #expect(config.image.descriptor.digest == digest)
        #expect(config.initProcess.executable == "/bin/sh")
        #expect(config.initProcess.arguments == ["-c", "echo image"])
        #expect(config.initProcess.workingDirectory == "/image")
        #expect(config.initProcess.user == .raw(userString: "1000:1000"))
        #expect(config.initProcess.environment.contains("B=retained"))
        #expect(config.stopSignal == "SIGTERM")
        #expect(config.networks.first?.network == "default")
        #expect(config.networks.first?.options.hostname == "fixture")
        #expect(config.resources.cpus == ContainerSystemConfig().container.cpus)
    }

    @Test func `native projection overrides process fields without losing environment`() throws {
        let spec = ContainerSpec(
            name: "fixture", image: digest, command: ["one", "two"], entrypoint: ["/custom", "--flag"],
            environment: ["A": "override", "EMPTY": "", "EQUAL": "a=b"], labels: ["key": "a=b"],
            workingDirectory: "/work", user: "2000", terminal: true, openStandardInput: true,
            initProcess: true, capabilitiesToAdd: ["NET_ADMIN"], capabilitiesToDrop: ["MKNOD"]
        )
        let config = try configuration(spec)
        #expect(config.initProcess.executable == "/custom")
        #expect(config.initProcess.arguments == ["--flag", "one", "two"])
        #expect(config.initProcess.workingDirectory == "/work")
        #expect(config.initProcess.user == .raw(userString: "2000"))
        #expect(config.initProcess.terminal)
        #expect(config.initProcess.environment.contains("A=override"))
        #expect(!config.initProcess.environment.contains("A=original"))
        #expect(config.initProcess.environment.contains("B=retained"))
        #expect(config.initProcess.environment.contains("EMPTY="))
        #expect(config.initProcess.environment.contains("EQUAL=a=b"))
        #expect(config.labels == ["key": "a=b"])
        #expect(config.useInit)
        #expect(config.capAdd == ["CAP_NET_ADMIN"])
        #expect(config.capDrop == ["CAP_MKNOD"])
    }

    @Test func `entrypoint override clears image command and missing command fails`() throws {
        let process = try AppleContainerCreateProjection.process(
            ContainerSpec(name: "test", image: digest, entrypoint: ["/replacement"]), image: imageConfig()
        )
        #expect(process.arguments.isEmpty)
        #expect(throws: DevContainerError.self) {
            try AppleContainerCreateProjection.process(ContainerSpec(name: "test", image: digest), image: nil)
        }
        let bare = try AppleContainerCreateProjection.process(
            ContainerSpec(name: "test", image: digest, command: ["/bin/true"]), image: nil
        )
        #expect(bare.user == .id(uid: 0, gid: 0))
        #expect(bare.workingDirectory == "/")
    }

    @Test func `empty user and directory inherit non root image defaults`() throws {
        let spec = ContainerSpec(name: "fixture", image: digest, workingDirectory: "", user: "")
        let process = try AppleContainerCreateProjection.process(spec, image: imageConfig())
        #expect(process.user == .raw(userString: "1000:1000"))
        #expect(process.workingDirectory == "/image")
    }

    @Test func `network projection preserves none default and multiple attachments`() throws {
        var spec = ContainerSpec(name: "fixture", image: digest)
        #expect(throws: DevContainerError.self) {
            try AppleContainerCreateProjection.networks(spec, builtin: nil, domain: nil)
        }
        spec.networks = [.init(name: "none")]
        #expect(try AppleContainerCreateProjection.networks(spec, builtin: nil, domain: nil).isEmpty)
        spec.networks.append(.init(name: "second"))
        #expect(throws: DevContainerError.self) {
            try AppleContainerCreateProjection.networks(spec, builtin: nil, domain: nil)
        }
        spec.networks = [.init(name: "one"), .init(name: "two")]
        let networks = try AppleContainerCreateProjection.networks(spec, builtin: nil, domain: "test")
        #expect(networks.map(\.network) == ["one", "two"])
        #expect(networks.map(\.options.hostname) == ["fixture.test.", "fixture"])
        spec.name = "fixture.test"
        let qualified = try AppleContainerCreateProjection.networks(spec, builtin: nil, domain: nil)
        #expect(qualified.first?.options.hostname == "fixture.test.")
    }

    @Test func `verification rejects a replacement image or container identity`() throws {
        let expected = try configuration(ContainerSpec(name: "fixture", image: digest))
        try AppleContainerCreateProjection.verify(expected, expected: expected)
        var actual = expected
        actual.id = "replacement"
        #expect(throws: DevContainerError.self) {
            try AppleContainerCreateProjection.verify(actual, expected: expected)
        }
        actual = expected
        let descriptor = Descriptor(
            mediaType: "application/vnd.oci.image.index.v1+json",
            digest: "sha256:" + String(repeating: "c", count: 64), size: 123
        )
        actual.image = ImageDescription(reference: "fixture:latest", descriptor: descriptor)
        #expect(throws: DevContainerError.self) {
            try AppleContainerCreateProjection.verify(actual, expected: expected)
        }
    }

    @Test func `captured native identity rejects incomplete or inconsistent metadata`() throws {
        var image = ResolvedAppleImage(
            snapshot: ImageSnapshot(id: digest, references: ["fixture:latest"], createdAt: .distantPast, size: 0),
            nativeReference: "fixture:latest", nativeDigest: digest
        )
        #expect(throws: DevContainerError.self) { try image.nativeIdentity() }
        image.descriptor = try JSONEncoder().encode(descriptor())
        image.platform = try JSONEncoder().encode(Platform.current)
        #expect(try image.nativeIdentity().0.digest == digest)
        image.descriptor = try JSONEncoder().encode(Descriptor(
            mediaType: "application/vnd.oci.image.index.v1+json",
            digest: "sha256:" + String(repeating: "b", count: 64), size: 123
        ))
        #expect(throws: DevContainerError.self) { try image.nativeIdentity() }
        image.descriptor = try JSONEncoder().encode(descriptor())
        image.platform = Data("{}".utf8)
        #expect(throws: ContainerizationError.self) { try image.nativeIdentity() }
        image.platform = try JSONEncoder().encode(Platform.current)
        image.descriptor = Data("{}".utf8)
        #expect(throws: DecodingError.self) { try image.nativeIdentity() }
    }

    @Test func `stock rejects unsupported security before create`() throws {
        #if !DEVCONTAINER_ENHANCED_RUNTIME
            for spec in [
                ContainerSpec(name: "fixture", image: digest, hostname: "custom"),
                ContainerSpec(name: "fixture", image: digest, privileged: true),
                ContainerSpec(name: "fixture", image: digest, securityOptions: ["no-new-privileges=true"])
            ] {
                #expect(throws: DevContainerError.self) { try configuration(spec) }
            }
        #else
            let config = try configuration(ContainerSpec(
                name: "fixture", image: digest, hostname: "custom", privileged: true,
                securityOptions: ["no-new-privileges=true", "systempaths=unconfined"]
            ))
            #expect(config.hostname == "custom")
            #expect(config.initProcess.privileged)
            #expect(config.initProcess.noNewPrivileges)
            #expect(config.unconfinedSystemPaths)
        #endif
    }
}
