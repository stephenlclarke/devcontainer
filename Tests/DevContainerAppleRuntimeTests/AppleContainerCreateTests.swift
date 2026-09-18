// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerAPIClient
import ContainerizationError
import ContainerizationOCI
import ContainerPersistence
import ContainerResource
@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

struct AppleContainerCreateTests {
    private var digest: String {
        "sha256:" + String(repeating: "a", count: 64)
    }

    private actor Creator: AppleContainerCreateClient, AppleContainerInventoryClient {
        var prepared: [ContainerConfiguration] = []
        var created: [ContainerConfiguration] = []
        let failCreate: Bool
        let failAfterCreate: Bool
        let replacement: Bool

        init(failCreate: Bool = false, failAfterCreate: Bool = false, replacement: Bool = false) {
            self.failCreate = failCreate
            self.failAfterCreate = failAfterCreate
            self.replacement = replacement
        }

        func prepare(
            spec: ContainerSpec, image: ResolvedAppleImage, context: RuntimeRequestContext
        ) throws -> ContainerConfiguration {
            try context.checkActive()
            let (description, platform) = try image.nativeIdentity()
            let configuration = try AppleContainerCreateProjection.configuration(
                spec: spec, identity: (description, platform), imageConfig: nil,
                system: .init(), builtinNetwork: "default"
            )
            prepared.append(configuration)
            return configuration
        }

        func create(configuration: ContainerConfiguration, mountOptions _: [String], context: RuntimeRequestContext) throws {
            try context.checkActive()
            guard !failCreate else {
                throw ContainerizationError(.notFound, message: "Captured image content missing")
            }
            created.append(configuration)
            if failAfterCreate {
                throw DevContainerError(.providerProtocolMismatch, message: "Injected post-create verification failure")
            }
        }

        func list() -> [ContainerResource.ContainerSnapshot] {
            created.map { .init(configuration: $0, status: .stopped, networks: []) }
        }

        func get(id _: String) throws -> ContainerResource.ContainerSnapshot {
            guard var configuration = created.last else {
                throw ContainerizationError(.notFound, message: "No fake native container")
            }
            if replacement {
                configuration.creationDate = configuration.creationDate.addingTimeInterval(1)
            }
            return .init(configuration: configuration, status: .stopped, networks: [])
        }
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

    private func configuration(_ spec: ContainerSpec) throws -> ContainerConfiguration {
        try AppleContainerCreateProjection.configuration(
            spec: spec, identity: (ImageDescription(reference: "fixture:latest", descriptor: descriptor()), .current),
            imageConfig: imageConfig(), system: .init(), builtinNetwork: "default"
        )
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
