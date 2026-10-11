//===----------------------------------------------------------------------===//
// Copyright 2026 devcontainer project authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// https://www.apache.org/licenses/LICENSE-2.0
//===----------------------------------------------------------------------===//

import Darwin
import DevContainerCore
@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerState
import DevContainerTestStorage
import DevContainerTestSupport
import Foundation
import Testing

private enum CreateFailureStage {
    case beforeCreate
    case afterCreate
}

private struct FailedCreateFixture {
    let directory: URL
    let store: SQLiteStateStore
    let key: ProjectKey
    let runtime: InMemoryRuntime
    let router: DockerRouter
    let body: Data

    func cleanUp() {
        try? FileManager.default.removeItem(at: directory)
    }
}

private func makeFailedCreateFixture(stage: CreateFailureStage) async throws -> FailedCreateFixture {
    let directory = TestStorage.temporaryDirectory
        .appendingPathComponent("failed-native-create-\(UUID().uuidString)", isDirectory: true)
    let store = try SQLiteStateStore(path: directory.appendingPathComponent("state.sqlite"))
    let runtime = makeFailingRuntime(stage: stage)
    await runtime.seedImage(ImageSnapshot(
        id: "sha256:image", references: ["alpine:3.22"], createdAt: Date(), size: 1
    ))
    return try FailedCreateFixture(
        directory: directory,
        store: store,
        key: ProjectKey(rawValue: "\(getuid()):docker-api"),
        runtime: runtime,
        router: DockerRouter(
            runtime: runtime,
            coordinator: ProjectCoordinator(store: store),
            provider: .containerCompose
        ),
        body: JSONSerialization.data(withJSONObject: ["Image": "alpine:3.22"])
    )
}

private func makeFailingRuntime(stage: CreateFailureStage) -> InMemoryRuntime {
    switch stage {
    case .beforeCreate:
        InMemoryRuntime(provider: .containerCompose, containerCreateWillBegin: { _ in
            throw DevContainerError(.runtimeUnavailable, message: "injected pre-create failure")
        })
    case .afterCreate:
        InMemoryRuntime(provider: .containerCompose, containerCreateDidComplete: { _ in
            throw DevContainerError(.runtimeUnavailable, message: "simulated lost create response")
        })
    }
}

private func createRequest(_ fixture: FailedCreateFixture, target: String = "/containers/create")
    async -> DockerHTTPResponse
{
    await fixture.router.respond(to: DockerHTTPRequest(
        method: .post, target: target, body: fixture.body
    ))
}

private func makeRecoveryClaim(_ fixture: FailedCreateFixture) async throws -> OperationRecord {
    _ = try await fixture.store.claimProject(
        key: fixture.key, provider: .containerCompose, composeProject: nil,
        projectDirectory: nil, configurationHash: "recovery"
    )
    try await fixture.store.setProjectState(
        key: fixture.key, desiredState: .running, reconciliationState: .applying, generation: 1
    )
    let now = Date()
    let recovery = OperationRecord(
        id: OperationID(rawValue: "prior-native-create"), project: fixture.key,
        requestKind: "POST /containers/create", requestHash: "prior-request",
        createdAt: now, updatedAt: now
    )
    try await fixture.store.beginOperation(recovery)
    return recovery
}

@Test
func `failed native create releases only its new empty project claim`() async throws {
    let fixture = try await makeFailedCreateFixture(stage: .beforeCreate)
    defer { fixture.cleanUp() }

    #expect(await (createRequest(fixture)).status == 500)
    #expect(try await fixture.store.project(key: fixture.key) == nil)
    #expect(try await fixture.store.resources(project: fixture.key).isEmpty)
    #expect(try await fixture.store.unfinishedOperations().isEmpty)
    #expect(await fixture.runtime.listContainers(all: true, labels: [:], context: RuntimeRequestContext()).isEmpty)
}

@Test
func `failed native create retains untracked container claim`() async throws {
    let fixture = try await makeFailedCreateFixture(stage: .afterCreate)
    defer { fixture.cleanUp() }

    #expect(await (createRequest(fixture, target: "/containers/create?name=partial")).status == 500)
    #expect(try await fixture.store.project(key: fixture.key)?.reconciliationState == .failed)
    #expect(try await fixture.store.resources(project: fixture.key).isEmpty)
    let containers = await fixture.runtime.listContainers(
        all: true, labels: [:], context: RuntimeRequestContext()
    )
    #expect(containers.count == 1)
    #expect(containers.first?.spec.labels[RuntimeLabels.operation] != nil)
}

@Test
func `failed native create preserves an existing recovery claim`() async throws {
    let fixture = try await makeFailedCreateFixture(stage: .beforeCreate)
    defer { fixture.cleanUp() }
    let recovery = try await makeRecoveryClaim(fixture)

    #expect(await (createRequest(fixture)).status == 500)
    #expect(try await fixture.store.project(key: fixture.key)?.reconciliationState == .failed)
    #expect(try await fixture.store.resources(project: fixture.key).isEmpty)
    #expect(try await fixture.store.unfinishedOperations() == [recovery])
}
