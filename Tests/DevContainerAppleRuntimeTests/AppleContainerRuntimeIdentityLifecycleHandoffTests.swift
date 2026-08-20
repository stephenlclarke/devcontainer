//===----------------------------------------------------------------------===//
// Copyright 2026 devcontainer project authors.
// Licensed under the Apache License, Version 2.0.
//===----------------------------------------------------------------------===//

import ContainerEngineRuntimeSPI
@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

@Suite("Apple identity/lifecycle handoff")
struct AppleRuntimeHandoffTests {
    @Test
    func `quiesced records preserve stable identity without invented events`() throws {
        let identifier = String(repeating: "a", count: 64)
        let snapshot = DevContainerModel.ContainerSnapshot(
            runtimeID: RuntimeID(rawValue: "runtime-api"),
            dockerID: DockerID(rawValue: identifier),
            spec: ContainerSpec(
                name: "api",
                image: "fixture:latest",
                autoRemove: true
            ),
            state: .stopped,
            createdAt: Date(timeIntervalSince1970: 1),
            startedAt: Date(timeIntervalSince1970: 2),
            finishedAt: Date(timeIntervalSince1970: 3),
            exitCode: 17
        )

        let records = try AppleContainerRuntime
            .collectPortableIdentityLifecycleHandoffContainers(
                resourceIDs: [identifier],
                providerFingerprint: "sha256:provider",
                inventory: [snapshot]
            )
        let record = try #require(records.first)

        #expect(record.lifecycle.containerID == identifier)
        #expect(record.lifecycle.canonicalName == "api")
        #expect(record.lifecycle.snapshot.state == .exited)
        #expect(record.lifecycle.snapshot.exitCode == 17)
        #expect(record.lifecycle.intent.autoRemove)
        #expect(record.events.isEmpty)
    }

    @Test
    func `running records cannot cross the writer switch`() throws {
        let snapshot = DevContainerModel.ContainerSnapshot(
            runtimeID: RuntimeID(rawValue: "runtime-api"),
            dockerID: DockerID(rawValue: String(repeating: "a", count: 64)),
            spec: ContainerSpec(name: "api", image: "fixture:latest"),
            state: .running,
            createdAt: Date(timeIntervalSince1970: 1)
        )

        #expect(throws: DevContainerError.self) {
            try AppleContainerRuntime
                .collectPortableIdentityLifecycleHandoffContainers(
                    resourceIDs: [],
                    providerFingerprint: "sha256:provider",
                    inventory: [snapshot]
                )
        }
    }

    @Test
    func `exited records require exact exit details`() throws {
        let snapshot = DevContainerModel.ContainerSnapshot(
            runtimeID: RuntimeID(rawValue: "runtime-api"),
            dockerID: DockerID(rawValue: String(repeating: "a", count: 64)),
            spec: ContainerSpec(name: "api", image: "fixture:latest"),
            state: .stopped,
            createdAt: Date(timeIntervalSince1970: 1),
            startedAt: Date(timeIntervalSince1970: 2)
        )

        #expect(throws: DevContainerError.self) {
            try AppleContainerRuntime
                .collectPortableIdentityLifecycleHandoffContainers(
                    resourceIDs: [],
                    providerFingerprint: "sha256:provider",
                    inventory: [snapshot]
                )
        }
    }

    @Test
    func `created records reject evidence of a prior start`() throws {
        let snapshot = DevContainerModel.ContainerSnapshot(
            runtimeID: RuntimeID(rawValue: "runtime-api"),
            dockerID: DockerID(rawValue: String(repeating: "a", count: 64)),
            spec: ContainerSpec(name: "api", image: "fixture:latest"),
            state: .created,
            createdAt: Date(timeIntervalSince1970: 1),
            startedAt: Date(timeIntervalSince1970: 2)
        )

        #expect(throws: DevContainerError.self) {
            try AppleContainerRuntime
                .collectPortableIdentityLifecycleHandoffContainers(
                    resourceIDs: [],
                    providerFingerprint: "sha256:provider",
                    inventory: [snapshot]
                )
        }
    }

    @Test
    func `concurrent first inventory shares one durable identity`() async throws {
        let fixture = try FakeAppleCLI()
        let store = TestMetadataStore(recordDelay: .milliseconds(100))
        let runtime = try fixture.runtime(metadataStore: store)
        let observed = DevContainerModel.ContainerSnapshot(
            runtimeID: RuntimeID(rawValue: "runtime-api"),
            dockerID: DockerID(rawValue: "runtime-api"),
            spec: ContainerSpec(name: "api", image: "fixture:latest"),
            state: .stopped,
            createdAt: Date(timeIntervalSince1970: 1)
        )

        async let first = runtime.containerSnapshotWithMetadata(
            observed,
            metadata: nil,
            imageID: nil
        )
        async let second = runtime.containerSnapshotWithMetadata(
            observed,
            metadata: nil,
            imageID: nil
        )
        let (firstSnapshot, secondSnapshot) = try await (first, second)

        #expect(firstSnapshot.dockerID == secondSnapshot.dockerID)
        #expect(await store.recordCount() == 1)
    }

    @Test
    func `replacement inventory retries first identity adoption`() async throws {
        let fixture = try FakeAppleCLI()
        let store = TestMetadataStore(recordDelay: .milliseconds(100))
        let runtime = try fixture.runtime(metadataStore: store)
        let original = DevContainerModel.ContainerSnapshot(
            runtimeID: RuntimeID(rawValue: "runtime-api"),
            dockerID: DockerID(rawValue: "runtime-api"),
            spec: ContainerSpec(name: "api", image: "fixture:old"),
            state: .stopped,
            createdAt: Date(timeIntervalSince1970: 1)
        )
        let replacement = DevContainerModel.ContainerSnapshot(
            runtimeID: RuntimeID(rawValue: "runtime-api"),
            dockerID: DockerID(rawValue: "runtime-api"),
            spec: ContainerSpec(name: "api", image: "fixture:new"),
            state: .stopped,
            createdAt: Date(timeIntervalSince1970: 2)
        )

        async let originalSnapshot = runtime.containerSnapshotWithMetadata(
            original,
            metadata: nil,
            imageID: nil
        )
        try await Task.sleep(for: .milliseconds(10))
        let replacementSnapshot = try await runtime.containerSnapshotWithMetadata(
            replacement,
            metadata: nil,
            imageID: nil
        )
        let completedOriginalSnapshot = try await originalSnapshot
        let persisted = try #require(
            await store.containerMetadata(id: replacement.runtimeID.rawValue)
        )

        #expect(replacementSnapshot.dockerID != completedOriginalSnapshot.dockerID)
        #expect(replacementSnapshot.spec.image == "fixture:new")
        #expect(persisted.dockerID == replacementSnapshot.dockerID)
        #expect(persisted.createdAt == replacement.createdAt)
        #expect(await store.recordCount() == 2)
    }

    @Test
    func `a start in flight prevents identity lifecycle handoff`() throws {
        let snapshot = DevContainerModel.ContainerSnapshot(
            runtimeID: RuntimeID(rawValue: "runtime-api"),
            dockerID: DockerID(
                rawValue: String(repeating: "a", count: 64)
            ),
            spec: ContainerSpec(name: "api", image: "fixture:latest"),
            state: .stopped,
            createdAt: Date(timeIntervalSince1970: 1)
        )

        #expect(throws: DevContainerError.self) {
            try AppleContainerRuntime
                .requireIdentityLifecycleHandoffQuiescence(
                    resourceIDs: ["api"],
                    inventory: [snapshot],
                    startingRuntimeIDs: ["runtime-api"],
                    runningExecRuntimeIDs: []
                )
        }
    }

    @Test
    func `a concurrent lifecycle mutation prevents identity lifecycle handoff`() throws {
        let snapshot = DevContainerModel.ContainerSnapshot(
            runtimeID: RuntimeID(rawValue: "runtime-api"),
            dockerID: DockerID(
                rawValue: String(repeating: "a", count: 64)
            ),
            spec: ContainerSpec(name: "api", image: "fixture:latest"),
            state: .stopped,
            createdAt: Date(timeIntervalSince1970: 1)
        )

        #expect(throws: DevContainerError.self) {
            try AppleContainerRuntime
                .requireIdentityLifecycleHandoffQuiescence(
                    resourceIDs: ["api"],
                    inventory: [snapshot],
                    startingRuntimeIDs: [],
                    runningExecRuntimeIDs: [],
                    mutatingContainerIdentifiers: [snapshot.dockerID.rawValue]
                )
        }
        #expect(throws: DevContainerError.self) {
            try AppleContainerRuntime
                .requireIdentityLifecycleHandoffQuiescence(
                    resourceIDs: ["api"],
                    inventory: [snapshot],
                    startingRuntimeIDs: [],
                    runningExecRuntimeIDs: [],
                    mutationRevisionUnchanged: false
                )
        }
        #expect(throws: DevContainerError.self) {
            try AppleContainerRuntime
                .requireIdentityLifecycleHandoffQuiescence(
                    resourceIDs: ["api"],
                    inventory: [snapshot],
                    startingRuntimeIDs: [],
                    runningExecRuntimeIDs: [],
                    mutatingContainerIdentifiers: ["aaaa"]
                )
        }
    }
}
