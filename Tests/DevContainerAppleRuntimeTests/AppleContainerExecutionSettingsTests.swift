// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Containerization
import ContainerizationOCI
import ContainerResource
@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

struct AppleContainerExecutionSettingsTests {
    private var settings: ContainerExecutionSettings {
        .init(
            memoryLimitInBytes: 268_435_456, sharedMemorySizeInBytes: 33_554_432,
            readOnlyRootFilesystem: true, sysctls: ["net.ipv4.ip_forward": "1"], stopSignal: "SIGUSR1"
        )
    }

    private func configuration() throws -> ContainerConfiguration {
        let digest = FakeAppleImageIdentityClient.digest
        let descriptor = try JSONDecoder().decode(Descriptor.self, from: Data("""
        {"mediaType":"application/vnd.oci.image.index.v1+json","digest":"\(digest)","size":123}
        """.utf8))
        return ContainerConfiguration(
            id: "fixture", image: .init(reference: "fixture:latest", descriptor: descriptor),
            process: .init(executable: "/bin/true", arguments: [], environment: [])
        )
    }

    @Test func `native creation journals and adopts execution settings`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = AppleContainerCreateTests.Creator()
        let store = TestMetadataStore()
        let runtime = try fixture.runtime(metadataStore: store, creator: creator)
        let spec = ContainerSpec(
            name: "fixture", image: "fixture:latest", command: ["/bin/true"], executionSettings: settings,
            stopTimeoutSeconds: 7
        )
        let created = try await runtime.createContainer(spec: spec, context: RuntimeRequestContext())
        let native = try #require(await creator.created.first)
        #expect(native.resources.memoryInBytes == 268_435_456)
        #expect(native.shmSize == 33_554_432)
        #expect(native.readOnly)
        #expect(native.sysctls == settings.sysctls)
        #expect(native.stopSignal == "SIGUSR1")
        #expect(created.spec.executionSettings == settings)
        #expect(await store.containerMetadata(id: "fixture")?.spec.executionSettings == settings)
        #expect(await store.containerMetadata(id: "fixture")?.spec.stopTimeoutSeconds == 7)
        let recovered = try fixture.runtime(metadataStore: store, creator: creator)
        let recoveredRecord = try await recovered.containerRecord(
            .init(configuration: native, status: .stopped, networks: [])
        )
        let recoveredSnapshot = try await recovered.containerSnapshotWithMetadata(
            recovered.containerSnapshot(recoveredRecord),
            metadata: store.containerMetadata(id: "fixture"), imageID: created.imageID
        )
        #expect(recoveredSnapshot.spec.stopTimeoutSeconds == 7)
        let typed = try await runtime.containerRecord(.init(configuration: native, status: .stopped, networks: []))
        let object = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(native)) as? [String: Any])
        let json = AppleContainerRuntime.observedContainerSpec(id: "fixture", configuration: object, labels: [:])
        #expect(typed.spec.executionSettings == settings)
        #expect(json.executionSettings == settings)
        let legacy = ContainerSpec(name: "fixture", image: "fixture:latest")
        let adopted = AppleContainerRuntime.effectiveContainerSpec(requested: legacy, observed: typed.spec)
        #expect(adopted.executionSettings == settings)
    }

    @Test func `defaults do not overwrite the image stop signal or report VM memory as requested`() throws {
        var native = try configuration()
        native.stopSignal = "SIGINT"
        let originalMemory = native.resources.memoryInBytes
        try AppleContainerExecutionSettings.apply(nil, to: &native)
        try AppleContainerExecutionSettings.apply(.init(), to: &native)
        #expect(native.stopSignal == "SIGINT")
        #expect(native.resources.memoryInBytes == originalMemory)
        #expect(!native.readOnly)
        let requested = ContainerSpec(name: "test", image: "image", executionSettings: .init())
        let observed = ContainerSpec(name: "test", image: "image", executionSettings: .init(
            memoryLimitInBytes: originalMemory, stopSignal: "SIGINT"
        ))
        let effective = AppleContainerRuntime.effectiveContainerSpec(requested: requested, observed: observed)
        #expect(effective.executionSettings?.memoryLimitInBytes == nil)
        #expect(effective.executionSettings?.stopSignal == "SIGINT")
        #expect(AppleContainerExecutionSettings.observed([:]) == .init())
    }

    @Test(arguments: ["vm.overcommit_memory", "vm.max_map_count"])
    func `rejects forced sysctl conflicts`(_ key: String) throws {
        var native = try configuration()
        #expect(throws: DevContainerError.self) {
            try AppleContainerExecutionSettings.apply(.init(sysctls: [key: "0"]), to: &native)
        }
        let forced = key == "vm.overcommit_memory" ? "1" : "262144"
        try AppleContainerExecutionSettings.apply(.init(sysctls: [key: forced]), to: &native)
        #expect(native.sysctls[key] == forced)
    }

    @Test(arguments: ["SIGUSR1", "10", "TERM", "SIGRTMAX", "invalid"])
    func `validates linux stop signals`(_ signal: String) throws {
        var native = try configuration()
        if signal == "invalid" {
            #expect(throws: DevContainerError.self) {
                try AppleContainerExecutionSettings.apply(.init(stopSignal: signal), to: &native)
            }
        } else {
            try AppleContainerExecutionSettings.apply(.init(stopSignal: signal), to: &native)
            #expect(native.stopSignal == signal)
        }
    }

    @Test func `legacy CLI cannot silently drop settings`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let runtime = try fixture.runtime()
        await #expect(throws: DevContainerError.self) {
            try await runtime.createContainer(spec: .init(
                name: "fixture", image: "fixture:latest", command: ["/bin/true"], executionSettings: settings
            ), context: RuntimeRequestContext())
        }
        #expect(try !fixture.log().contains("create --name"))
    }
}
