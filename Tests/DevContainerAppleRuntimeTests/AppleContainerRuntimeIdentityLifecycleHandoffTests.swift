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
}
