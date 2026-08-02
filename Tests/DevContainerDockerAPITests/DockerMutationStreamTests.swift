//===----------------------------------------------------------------------===//
// Copyright 2026 devcontainer project authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//===----------------------------------------------------------------------===//

import Darwin
import DevContainerCore
@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerState
import DevContainerTestSupport
import Foundation
import Testing

@Suite(.serialized)
struct DockerMutationStreamTests {
    @Test
    func `image pull commits only after its stream completes`() async throws {
        let fixture = try MutationStreamFixture(runtime: InMemoryRuntime())
        let response = await fixture.pull()

        #expect(response.status == 200)
        #expect(try await fixture.project()?.reconciliationState == .applying)
        #expect(try await fixture.store.unfinishedOperations().count == 1)

        #expect(try await !streamBytes(response).isEmpty)
        #expect(try await fixture.project() == nil)
        #expect(try await fixture.store.unfinishedOperations().isEmpty)
    }

    @Test
    func `image pull stream failure marks its mutation failed`() async throws {
        let runtime = InMemoryRuntime { _ in
            AsyncThrowingStream { continuation in
                continuation.yield(Data("progress".utf8))
                continuation.finish(
                    throwing: DevContainerError(
                        .runtimeUnavailable,
                        message: "injected pull failure"
                    )
                )
            }
        }
        let fixture = try MutationStreamFixture(runtime: runtime)
        let response = await fixture.pull()

        await #expect(throws: DevContainerError.self) {
            _ = try await streamBytes(response)
        }
        #expect(try await fixture.project()?.reconciliationState == .failed)
        #expect(try await fixture.store.unfinishedOperations().isEmpty)
    }

    @Test
    func `cancelled image pull stream marks its mutation failed`() async throws {
        let runtime = InMemoryRuntime { _ in
            AsyncThrowingStream { continuation in
                continuation.yield(Data("progress".utf8))
            }
        }
        let fixture = try MutationStreamFixture(runtime: runtime)
        let response = await fixture.pull()
        let consumer = Task {
            try await streamBytes(response)
        }

        try await Task.sleep(for: .milliseconds(50))
        consumer.cancel()
        _ = try? await consumer.value

        try await fixture.awaitProjectState(.failed)
        #expect(try await fixture.store.unfinishedOperations().isEmpty)
    }

    @Test
    func `abandoned image pull stream marks its mutation failed`() async throws {
        let runtime = InMemoryRuntime { _ in
            AsyncThrowingStream { _ in }
        }
        let fixture = try MutationStreamFixture(runtime: runtime)
        var response: DockerHTTPResponse? = await fixture.pull()

        #expect(try await fixture.project()?.reconciliationState == .applying)
        response = nil
        _ = response

        try await fixture.awaitProjectState(.failed)
        #expect(try await fixture.store.unfinishedOperations().isEmpty)
    }
}

private final class MutationStreamFixture: @unchecked Sendable {
    let store: SQLiteStateStore
    let router: DockerRouter
    private let directory: URL
    private let project = ProjectKey(rawValue: "\(getuid()):docker-api")

    init(runtime: InMemoryRuntime) throws {
        directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(
                "devcontainer-mutation-stream-tests-\(UUID().uuidString)",
                isDirectory: true
            )
        store = try SQLiteStateStore(
            path: directory.appendingPathComponent("state.sqlite")
        )
        router = DockerRouter(
            runtime: runtime,
            coordinator: ProjectCoordinator(store: store)
        )
    }

    deinit {
        try? FileManager.default.removeItem(at: directory)
    }

    func pull() async -> DockerHTTPResponse {
        await router.respond(
            to: DockerHTTPRequest(
                method: .post,
                target: "/images/create?fromImage=alpine&tag=latest"
            )
        )
    }

    func project() async throws -> ProjectRecord? {
        try await store.project(key: project)
    }

    func awaitProjectState(_ state: ReconciliationState) async throws {
        let deadline = ContinuousClock.now + .seconds(5)
        while try await project()?.reconciliationState != state,
              ContinuousClock.now < deadline
        {
            try await Task.sleep(for: .milliseconds(10))
        }
        #expect(try await project()?.reconciliationState == state)
    }
}
