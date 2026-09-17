// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

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

    private actor Creator: AppleContainerCreateClient {
        var prepared: [ContainerConfiguration] = []
        var created: [ContainerConfiguration] = []
        let failCreate: Bool

        init(failCreate: Bool = false) {
            self.failCreate = failCreate
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
        }
    }

    @Test func `image id creation never forwards a mutable image tag to CLI`() async throws {
        let fixture = try FakeAppleCLI(distribution: "enhanced")
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = Creator()
        let runtime = try fixture.runtime(creator: creator)
        let spec = ContainerSpec(name: "fixture", image: FakeAppleImageIdentityClient.digest, command: ["/bin/true"])
        let snapshot = try await runtime.createContainer(spec: spec, context: RuntimeRequestContext())
        #expect(snapshot.imageID == FakeAppleImageIdentityClient.digest)
        let created = try #require(await creator.created.first)
        #expect(created.image.digest == digest)
        #expect(created.image.reference == "fixture:latest")
        #expect(created.initProcess.executable == "/bin/true")
        #expect(try !(fixture.log()).contains("create --name"))
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
