// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineWire
@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerRuntimeSPI
import DevContainerTestSupport
import Foundation
import Testing

@Suite(.timeLimit(.minutes(1)))
struct DockerContainerExitWaitTests {
    @Test(arguments: [true, false])
    func `version advertises provider registration support only`(supported: Bool) async throws {
        let runtime = supported
            ? InMemoryRuntime(containerExitWait: { RecordedExit(snapshot: $0) }) : InMemoryRuntime()
        let response = await DockerRouter(runtime: runtime).respond(to: .init(method: .get, target: "/version"))
        guard case let .bytes(body) = response.body else {
            Issue.record("Expected version JSON"); return
        }
        let object = try #require(JSONSerialization.jsonObject(with: body) as? [String: Any])
        let components = try #require(object["Components"] as? [[String: Any]])
        let details = try #require(components.first?["Details"] as? [String: String])
        #expect(details["ContainerExitWaitRegistration"] == (supported ? "1" : "0"))
    }

    @Test func `registration precedes response and survives automatic removal`() async throws {
        let barrier = ExitRegistrationBarrier()
        let runtime = InMemoryRuntime(containerExitWait: { snapshot in
            await barrier.register(snapshot)
        })
        let snapshot = try await create(runtime, autoRemove: true)
        let router = DockerRouter(runtime: runtime)
        let completion = ExitResponseCompletion()
        let response = Task {
            let result = await router.respond(to: .init(
                method: .post,
                target: "/containers/\(snapshot.dockerID)/wait?condition=next-exit"
            ))
            completion.mark()
            return result
        }
        let deadline = ContinuousClock.now.advanced(by: .seconds(3))
        while await !(barrier.entered), ContinuousClock.now < deadline {
            await Task.yield()
        }
        try #require(await barrier.entered)
        // Registration is deliberately suspended while the route is active.
        #expect(await !(barrier.acknowledged))
        #expect(!completion.finished)
        await barrier.release()
        let registered = await response.value
        #expect(registered.status == 200)
        #expect(await barrier.acknowledged)
        try await runtime.removeContainer(id: snapshot.dockerID.rawValue, force: true, context: .init())
        guard case let .stream(stream) = registered.body else {
            Issue.record("Expected registered wait stream"); return
        }
        var body = Data()
        for try await chunk in stream {
            body.append(chunk)
        }
        let object = try #require(JSONSerialization.jsonObject(with: body) as? [String: Any])
        #expect(object["StatusCode"] as? Int == 37)
    }

    @Test func `unsupported registration fails before successful headers`() async throws {
        let runtime = InMemoryRuntime()
        let snapshot = try await create(runtime, autoRemove: false)
        let response = await DockerRouter(runtime: runtime).respond(to: .init(
            method: .post, target: "/containers/\(snapshot.dockerID)/wait?condition=next-exit"
        ))
        #expect(response.status != 200)
        guard case .bytes = response.body else {
            Issue.record("Registration failure must precede streaming headers"); return
        }
    }

    private func create(_ runtime: InMemoryRuntime, autoRemove: Bool) async throws -> ContainerSnapshot {
        await runtime.seedImage(.init(id: "sha256:test", references: ["test"], createdAt: Date(), size: 1))
        return try await runtime.createContainer(
            spec: .init(name: "test", image: "test", autoRemove: autoRemove), context: .init()
        )
    }
}

private final class ExitResponseCompletion: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false
    var finished: Bool {
        lock.withLock { value }
    }

    func mark() {
        lock.withLock { value = true }
    }
}

private actor ExitRegistrationBarrier {
    private var continuation: CheckedContinuation<Void, Never>?
    private(set) var entered = false
    private(set) var acknowledged = false
    func register(_ snapshot: ContainerSnapshot) async -> any RuntimeContainerExitWait {
        entered = true
        await withCheckedContinuation { continuation = $0 }
        acknowledged = true
        return RecordedExit(snapshot: snapshot)
    }

    func release() {
        continuation?.resume(); continuation = nil
    }
}

private struct RecordedExit: RuntimeContainerExitWait {
    let snapshot: ContainerSnapshot
    func wait() -> Int32 {
        37
    }

    func cancel() { /* The completed test receipt owns no live resources. */ }
}
