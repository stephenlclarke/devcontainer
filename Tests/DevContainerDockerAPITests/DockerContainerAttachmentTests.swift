// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineWire
@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerRuntimeSPI
import DevContainerTestSupport
import Foundation
import Testing

struct DockerContainerAttachmentTests {
    private func options(_ query: String) throws -> DockerAttachmentOptions {
        try DockerAttachmentOptions(target: ParsedTarget("/containers/test/attach?" + query))
    }

    @Test func `query defaults and booleans follow the pinned Engine parser`() throws {
        let defaults = try options("")
        let spec = ContainerSpec(name: "test", image: "test")
        #expect(!defaults.logs && !defaults.stream && !defaults.needsLiveSession(spec: spec))
        #expect(!defaults.standardInput && !defaults.standardOutput && !defaults.standardError)
        #expect(defaults.detachKeys == [16, 17])
        #expect(try options("stream=True&stdout=1").needsLiveSession(spec: spec))
        #expect(try options("detachKeys=ctrl-x,ctrl-y").detachKeys == [24, 25])
        #expect(try options("stream=maybe&stdout=yes").needsLiveSession(spec: spec))
        #expect(try !options("stream=none&stdout=1").needsLiveSession(spec: spec))
        #expect(try options("detachKeys=DEL").detachKeys == [127])
        for query in ["detachKeys=ctrl-invalid", "detachKeys=x,,y", "detachKeys=ctrl-A", "detachKeys=ctrl-?"] {
            #expect(throws: DevContainerError.self) { try options(query) }
        }
    }

    @Test(arguments: [false, true], [false, true])
    func `stdin only attachment uses effective input and StdinOnce exit policy`(tty: Bool, once: Bool) async throws {
        let probe = AttachmentProbe()
        let runtime = InMemoryRuntime(attachmentSession: probe)
        await runtime.seedImage(.init(id: "sha256:test", references: ["test"], createdAt: Date(), size: 1))
        let spec = ContainerSpec(name: "closed-input", image: "test", terminal: tty, standardInputOnce: once)
        let selected = try options("stream=1&stdin=1")
        #expect(selected.needsLiveSession(spec: spec) == (once && !tty))
        var openSpec = spec
        openSpec.openStandardInput = true
        #expect(selected.needsLiveSession(spec: openSpec))
        let snapshot = try await runtime.createContainer(spec: spec, context: .init())
        let response = await DockerRouter(runtime: runtime).respond(to: .init(
            method: .post, target: "/containers/\(snapshot.dockerID.rawValue)/attach?stream=1&stdin=1"
        ))
        guard case let .hijack(session, _) = response.body else {
            Issue.record("Expected attachment upgrade")
            return
        }
        if once, !tty {
            #expect(await runtime.attachmentCount == 1)
            // This probe never exits: StdinOnce must retain the live wait path.
            await session.cancel()
        } else {
            try #require(await runtime.attachmentCount == 0)
            for try await _ in session.frames {
                Issue.record("No output stream was selected")
            }
            #expect(try await session.wait() == 0)
            await session.cancel()
        }
    }

    @Test(arguments: [false, true])
    func `output only half close and disconnect never close shared stdin`(once: Bool) async throws {
        let probe = AttachmentProbe()
        let attachment = try DockerContainerAttachment(
            session: probe, history: nil, options: options("stream=1&stdout=1"),
            spec: .init(name: "test", image: "test", openStandardInput: true, standardInputOnce: once)
        )
        try await attachment.write(Data("ignored".utf8))
        try await attachment.closeStandardInput()
        #expect(await probe.input.isEmpty)
        #expect(await probe.inputCloses == 0)
        #expect(await probe.cancellations == 0)
        await attachment.cancel()
        #expect(await probe.inputCloses == 0)
        #expect(await probe.cancellations > 0)
    }

    @Test(arguments: [false, true], [false, true])
    func `interactive EOF follows nonterminal StdinOnce policy`(tty: Bool, once: Bool) async throws {
        let probe = AttachmentProbe()
        let attachment = try DockerContainerAttachment(
            session: probe, history: nil, options: options("stream=1&stdin=1&stdout=1"),
            spec: .init(name: "test", image: "test", terminal: tty, openStandardInput: true, standardInputOnce: once)
        )
        try await attachment.write(Data("hello".utf8))
        try await attachment.closeStandardInput()
        try await attachment.closeStandardInput()
        #expect(await probe.input == Data("hello".utf8))
        #expect(await probe.inputCloses == (once && !tty ? 1 : 0))
        #expect(await probe.cancellations == 0 || !(once && !tty))
        if !once || tty {
            #expect(await probe.cancellations > 0)
        }
        await #expect(throws: CancellationError.self) { try await attachment.write(Data([1])) }
        await attachment.cancel()
        #expect(await probe.inputCloses == (once && !tty ? 1 : 0))
    }

    @Test(arguments: [false, true])
    func `TTY detach sequence spans writes and leaves container input open`(custom: Bool) async throws {
        let probe = AttachmentProbe()
        let query = "stream=1&stdin=1&stdout=1" + (custom ? "&detachKeys=ctrl-x,ctrl-y" : "")
        let first: UInt8 = custom ? 24 : 16
        let second: UInt8 = custom ? 25 : 17
        let attachment = try DockerContainerAttachment(
            session: probe, history: nil, options: options(query),
            spec: .init(name: "test", image: "test", terminal: true, openStandardInput: true, standardInputOnce: true)
        )
        try await attachment.write(Data([65, first]))
        try await attachment.write(Data([66, first]))
        try await attachment.write(Data([second, 67]))
        #expect(await probe.input == Data([65, first, 66]))
        #expect(await probe.cancellations > 0)
        #expect(await probe.inputCloses == 0)
        #expect(try await attachment.wait() == 0)
        await attachment.cancel()
    }

    @Test func `history precedes selected live channels and retains exit status`() async throws {
        let probe = AttachmentProbe()
        await probe.emit(.standardOutput, "live out")
        await probe.emit(.standardError, "live error")
        await probe.finish()
        let history = AsyncThrowingStream<RuntimeIOFrame, any Error> { continuation in
            continuation.yield(.init(channel: .standardOutput, data: Data("old out".utf8)))
            continuation.yield(.init(channel: .standardError, data: Data("old error".utf8)))
            continuation.finish()
        }
        let attachment = try DockerContainerAttachment(
            session: probe, history: history, options: options("logs=1&stream=1&stderr=1"),
            spec: .init(name: "test", image: "test")
        )
        var output: [String] = []
        for try await frame in attachment.frames {
            #expect(frame.channel == .standardError)
            try output.append(#require(String(data: frame.data, encoding: .utf8)))
        }
        #expect(output == ["old error", "live error"])
        #expect(try await attachment.wait() == 23)
        await attachment.cancel()
    }

    @Test func `large log record is split without losing bytes`() async throws {
        let data = Data(repeating: 97, count: 131_073)
        let history = AsyncThrowingStream<RuntimeIOFrame, any Error> { continuation in
            continuation.yield(.init(channel: .standardOutput, data: data))
            continuation.finish()
        }
        let attachment = try DockerContainerAttachment(
            session: nil, history: history, options: options("logs=1&stdout=1"),
            spec: .init(name: "test", image: "test")
        )
        var result = Data()
        for try await frame in attachment.frames {
            #expect(frame.data.count <= 65536)
            result.append(frame.data)
        }
        #expect(result == data)
        #expect(try await attachment.wait() == 0)
        await attachment.cancel()
    }

    @Test(arguments: [false, true])
    func `cancelling a quiet output consumer cleans up the native attachment`(once: Bool) async throws {
        let probe = AttachmentProbe()
        let attachment = try DockerContainerAttachment(
            session: probe, history: nil, options: options("stream=1&stdin=1&stdout=1"),
            spec: .init(name: "test", image: "test", openStandardInput: true, standardInputOnce: once)
        )
        let received = AsyncStream<Void>.makeStream(bufferingPolicy: .bufferingOldest(1))
        let consumer = Task {
            for try await _ in attachment.frames {
                received.continuation.yield(())
            }
        }
        await probe.emit(.standardOutput, "ready")
        var iterator = received.stream.makeAsyncIterator()
        await iterator.next()
        // No more output follows. Cancelling a suspended AsyncThrowingStream
        // iterator may finish normally rather than throw CancellationError.
        consumer.cancel()
        _ = await consumer.result
        await expectCancellation(probe)
        #expect(await probe.inputCloses == (once ? 1 : 0))
        received.continuation.finish()
        await attachment.cancel()
    }

    @Test(arguments: [false, true])
    func `output failure closes StdinOnce before revoking the subscription`(overflow: Bool) async throws {
        let probe = AttachmentProbe()
        let history = AsyncThrowingStream<RuntimeIOFrame, any Error> { continuation in
            if overflow {
                for _ in 0 ..< 257 {
                    continuation.yield(.init(channel: .standardOutput, data: Data([65])))
                }
                continuation.finish()
            } else {
                continuation.finish(throwing: DevContainerError(.runtimeUnavailable, message: "log read failed"))
            }
        }
        let attachment = try DockerContainerAttachment(
            session: probe, history: history, options: options("logs=1&stream=1&stdin=1&stdout=1"),
            spec: .init(name: "test", image: "test", openStandardInput: true, standardInputOnce: true)
        )
        // Deliberately do not consume output: prove the bounded queue's failure
        // cleanup independently of the outer HTTP transport calling cancel.
        await expectCancellation(probe)
        #expect(await probe.inputCloses == 1)
        await #expect(throws: DevContainerError.self) {
            for try await _ in attachment.frames { /* Drain retained pre-failure frames. */ }
        }
        await attachment.cancel()
        #expect(await probe.inputCloses == 1)
    }

    @Test func `router persists StdinOnce and supports logs without live attachment`() async throws {
        let runtime = InMemoryRuntime()
        await runtime.seedImage(.init(id: "sha256:test", references: ["test"], createdAt: Date(), size: 1))
        let router = DockerRouter(runtime: runtime)
        let created = await router.respond(to: .init(
            method: .post, target: "/containers/create?name=attach-test",
            body: Data(#"{"Image":"test","OpenStdin":true,"StdinOnce":true}"#.utf8)
        ))
        #expect(created.status == 201)
        let inspect = await router.respond(to: .init(method: .get, target: "/containers/attach-test/json"))
        let object = try #require(JSONSerialization.jsonObject(with: bytes(inspect)) as? [String: Any])
        #expect((object["Config"] as? [String: Any])?["StdinOnce"] as? Bool == true)
        let response = await router.respond(to: .init(
            method: .post, target: "/containers/attach-test/attach?logs=1&stdout=0&stderr=1"
        ))
        guard case let .hijack(session, _) = response.body else {
            Issue.record("Expected attachment upgrade")
            return
        }
        var data = Data()
        for try await frame in session.frames {
            #expect(frame.channel == .standardError)
            data.append(frame.data)
        }
        #expect(data == Data("stderr\n".utf8))
        #expect(try await session.wait() == 0)
        await session.cancel()
        let invalid = await router.respond(to: .init(
            method: .post, target: "/containers/missing/attach?detachKeys=bad"
        ))
        #expect(invalid.status == 400)
    }

    @Test func `legacy metadata decodes without StdinOnce`() throws {
        let spec = ContainerSpec(name: "test", image: "test")
        let data = try JSONEncoder().encode(spec)
        #expect(try !#require(String(data: data, encoding: .utf8)).contains("standardInputOnce"))
        #expect(try JSONDecoder().decode(ContainerSpec.self, from: data).standardInputOnce == nil)
    }

    private func expectCancellation(_ probe: AttachmentProbe) async {
        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        while await probe.cancellations == 0, ContinuousClock.now < deadline {
            await Task.yield()
        }
        #expect(await probe.cancellations > 0)
    }
}

private actor AttachmentProbe: RuntimeProcessSession {
    nonisolated let frames: AsyncThrowingStream<RuntimeIOFrame, any Error>
    let continuation: AsyncThrowingStream<RuntimeIOFrame, any Error>.Continuation
    var input = Data()
    var inputCloses = 0
    var cancellations = 0

    init() {
        let pair = AsyncThrowingStream<RuntimeIOFrame, any Error>.makeStream()
        frames = pair.stream
        continuation = pair.continuation
    }

    func emit(_ channel: RuntimeIOChannel, _ value: String) {
        continuation.yield(.init(channel: channel, data: Data(value.utf8)))
    }

    func finish() {
        continuation.finish()
    }

    func write(_ data: Data) {
        input.append(data)
    }

    func closeStandardInput() throws {
        guard cancellations == 0 else { throw CancellationError() }
        inputCloses += 1
    }

    func resize(width _: UInt16, height _: UInt16) { /* No terminal in this boundary probe. */ }
    func wait() throws -> Int32 {
        if cancellations > 0 {
            throw CancellationError()
        }
        return 23
    }

    func cancel() {
        cancellations += 1
        continuation.finish(throwing: CancellationError())
    }
}
