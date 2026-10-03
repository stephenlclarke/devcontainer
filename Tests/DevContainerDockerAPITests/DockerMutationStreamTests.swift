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
import DevContainerTestStorage
import DevContainerTestSupport
import Foundation
import Testing

@Suite(.serialized)
struct DockerMutationStreamTests {
    @Test
    func `cancelled build does not emit A completed build result`() async throws {
        let runtime = InMemoryRuntime(buildImageStream: { _ in
            AsyncThrowingStream { continuation in
                continuation.yield(Data("progress".utf8))
            }
        })
        let fixture = try MutationStreamFixture(runtime: runtime)
        let response = await fixture.build()
        let consumer = Task { try await streamBytes(response) }
        try await Task.sleep(for: .milliseconds(50))
        consumer.cancel()
        await #expect(throws: CancellationError.self) {
            _ = try await consumer.value
        }
        try await fixture.awaitProjectState(.failed)
        #expect(try await fixture.store.unfinishedOperations().isEmpty)
    }

    @Test
    func `abandoned build does not commit its mutation`() async throws {
        let fixture = try MutationStreamFixture(runtime: InMemoryRuntime())
        var response: DockerHTTPResponse? = await fixture.build()
        #expect(try await fixture.project()?.reconciliationState == .applying)
        response = nil
        _ = response
        try await fixture.awaitProjectState(.failed)
        #expect(try await fixture.store.unfinishedOperations().isEmpty)
    }

    @Test(arguments: [false, true])
    func `build cancellation errors remain errors`(typed: Bool) async throws {
        let upstream = AsyncThrowingStream<Data, any Error> { continuation in
            if typed {
                continuation.finish(throwing: DevContainerError(.cancelled, message: "cancelled"))
            } else {
                continuation.finish(throwing: CancellationError())
            }
        }
        let stream = dockerBuildResultStream(upstream)
        await #expect(throws: (any Error).self) {
            for try await _ in stream {
                Issue.record("Cancellation must not become a build result")
            }
        }
    }

    @Test
    func `build stream without coordinator encodes unknown failure once`() async throws {
        let runtime = InMemoryRuntime(buildImageStream: { _ in
            AsyncThrowingStream { continuation in
                continuation.finish(throwing: NSError(domain: "fixture", code: 17))
            }
        })
        let response = await DockerRouter(runtime: runtime).respond(
            to: DockerHTTPRequest(method: .post, target: "/build")
        )
        #expect(response.status == 200)
        let bytes = try await streamBytes(response)
        #expect(bytes.split(separator: 0x0A).count == 1)
        let record = try #require(JSONSerialization.jsonObject(with: bytes) as? [String: Any])
        let message = try #require(record["error"] as? String)
        #expect(!message.isEmpty)
        #expect((record["errorDetail"] as? [String: String])?["message"] == message)
    }

    @Test(arguments: ["/build", "/v1.45/build?t=fixture:built"])
    func `image build failure is in band and its mutation remains failed`(target: String) async throws {
        let runtime = InMemoryRuntime(buildImageStream: { _ in
            AsyncThrowingStream { continuation in
                continuation.yield(Data("build progress\n".utf8))
                continuation.finish(throwing: DevContainerError(.build, message: "RUN failed with exit 17"))
            }
        })
        let fixture = try MutationStreamFixture(runtime: runtime)
        let response = await fixture.build(target: target)
        #expect(response.status == 200)
        #expect(try await fixture.project()?.reconciliationState == .applying)
        let bytes = try await streamBytes(response)
        let records = try bytes.split(separator: 0x0A).map {
            try #require(JSONSerialization.jsonObject(with: Data($0)) as? [String: Any])
        }
        #expect(records.count == 2)
        #expect(records.first?["stream"] as? String == "build progress\n")
        #expect(records.last?["error"] as? String == "RUN failed with exit 17")
        #expect((records.last?["errorDetail"] as? [String: String])?["message"] == "RUN failed with exit 17")
        #expect(try await fixture.project()?.reconciliationState == .failed)
        #expect(try await fixture.store.unfinishedOperations().isEmpty)
    }

    @Test
    func `image build success commits only after consumption`() async throws {
        let fixture = try MutationStreamFixture(runtime: InMemoryRuntime())
        let response = await fixture.build()
        #expect(response.status == 200)
        #expect(try await fixture.project()?.reconciliationState == .applying)
        #expect(try await !streamBytes(response).isEmpty)
        #expect(try await fixture.project() == nil)
        #expect(try await fixture.store.unfinishedOperations().isEmpty)
    }

    @Test
    func `image build preflight rejection is not A build stream`() async throws {
        let runtime = InMemoryRuntime(buildImageStream: { _ in
            throw DevContainerError(.invalidRequest, message: "invalid build context")
        })
        let fixture = try MutationStreamFixture(runtime: runtime)
        let response = await fixture.build()
        #expect(response.status == 400)
        #expect(try await fixture.project()?.reconciliationState == .failed)
        #expect(try await fixture.store.unfinishedOperations().isEmpty)
    }

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
        directory = TestStorage.temporaryDirectory
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

    func build(target: String = "/build") async -> DockerHTTPResponse {
        await router.respond(to: DockerHTTPRequest(method: .post, target: target))
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
