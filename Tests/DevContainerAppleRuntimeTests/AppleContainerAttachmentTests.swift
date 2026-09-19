// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerAPIClient
import ContainerizationOS
import Darwin
@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

struct AppleContainerAttachmentTests {
    @Test func `runtime attaches before start and preserves init output input and exit`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = AppleContainerCreateTests.Creator()
        let bootstrap = AttachmentBootstrap(creator: creator)
        let runtime = try fixture.runtime(useDirectProcessAPI: true, creator: creator, bootstrap: bootstrap)
        let snapshot = try await runtime.createContainer(spec: specification(), context: .init())
        let first = try await runtime.attachContainer(id: snapshot.dockerID.rawValue, terminal: false, context: .init())
        let second = try await runtime.attachContainer(id: "fixture", terminal: false, context: .init())
        #expect(await creator.starts == 0)
        #expect(await bootstrap.calls == 0)
        try await first.write(Data("input".utf8))
        try await runtime.startContainer(id: "fixture", context: .init())
        let process = try #require(await bootstrap.processes.first)
        #expect(try await process.readInput() == Data("input".utf8))
        await first.cancel()
        try await process.emit("after detach")
        try await process.finish(37)
        var output = Data()
        var error = Data()
        for try await frame in second.frames {
            if frame.channel == .standardOutput {
                output.append(frame.data)
            } else {
                error.append(frame.data)
            }
        }
        #expect(output == Data("first outputafter detach".utf8))
        #expect(error == Data("first error".utf8))
        #expect(try await second.wait() == 37)
        await #expect(throws: CancellationError.self) { try await first.wait() }
        #expect(await process.kills == 0)
        #expect(await creator.starts == 1)
        await runtime.shutdown()
    }

    @Test func `completed init gets fresh prestart descriptors without replaying old output`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = AppleContainerCreateTests.Creator()
        let bootstrap = AttachmentBootstrap(creator: creator)
        let runtime = try fixture.runtime(useDirectProcessAPI: true, creator: creator, bootstrap: bootstrap)
        _ = try await runtime.createContainer(spec: specification(), context: .init())
        let first = try await runtime.attachContainer(id: "fixture", terminal: false, context: .init())
        try await runtime.startContainer(id: "fixture", context: .init())
        try await #require(await bootstrap.processes.first).finish(12)
        #expect(try await first.wait() == 12)
        let second = try await runtime.attachContainer(id: "fixture", terminal: false, context: .init())
        try await runtime.startContainer(id: "fixture", context: .init())
        #expect(await bootstrap.calls == 2)
        try await #require(await bootstrap.processes.last).finish(23)
        var output = Data()
        for try await frame in second.frames where frame.channel == .standardOutput {
            output.append(frame.data)
        }
        #expect(output == Data("first output".utf8))
        #expect(try await first.wait() == 12)
        #expect(try await second.wait() == 23)
        await runtime.shutdown()
    }

    @Test func `ambiguous bootstrap fails attachments and cannot silently retry`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = AppleContainerCreateTests.Creator()
        let bootstrap = AttachmentBootstrap(creator: creator, failBootstrap: true)
        let runtime = try fixture.runtime(useDirectProcessAPI: true, creator: creator, bootstrap: bootstrap)
        _ = try await runtime.createContainer(spec: specification(), context: .init())
        let session = try await runtime.attachContainer(id: "fixture", terminal: false, context: .init())
        for _ in 0 ..< 2 {
            await #expect(throws: DevContainerError.self) {
                try await runtime.startContainer(id: "fixture", context: .init())
            }
        }
        await #expect(throws: DevContainerError.self) { try await session.wait() }
        #expect(await bootstrap.calls == 1)
        #expect(await creator.starts == 0)
        await runtime.shutdown()
    }

    @Test func `terminal mismatch and unmanaged running init are rejected`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = AppleContainerCreateTests.Creator()
        let runtime = try fixture.runtime(useDirectProcessAPI: true, creator: creator, bootstrap: creator)
        _ = try await runtime.createContainer(spec: specification(), context: .init())
        await #expect(throws: DevContainerError.self) {
            try await runtime.attachContainer(id: "fixture", terminal: true, context: .init())
        }
        let prepared = try await runtime.attachContainer(id: "fixture", terminal: false, context: .init())
        try await creator.start()
        await #expect(throws: DevContainerError.self) {
            try await runtime.attachContainer(id: "fixture", terminal: false, context: .init())
        }
        #expect(await creator.bootstraps == 0)
        await runtime.shutdown()
        await #expect(throws: CancellationError.self) { try await prepared.wait() }
    }

    @Test func `changed native start identity invalidates attach and terminal resize`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = AppleContainerCreateTests.Creator()
        let bootstrap = AttachmentBootstrap(creator: creator)
        let runtime = try fixture.runtime(useDirectProcessAPI: true, creator: creator, bootstrap: bootstrap)
        var spec = specification()
        spec.terminal = true
        _ = try await runtime.createContainer(spec: spec, context: .init())
        let session = try await runtime.attachContainer(id: "fixture", terminal: true, context: .init())
        try await runtime.startContainer(id: "fixture", context: .init())
        try await session.resize(width: 100, height: 40)
        await creator.recordPriorStart()
        await #expect(throws: DevContainerError.self) {
            try await runtime.attachContainer(id: "fixture", terminal: true, context: .init())
        }
        await #expect(throws: DevContainerError.self) { try await session.resize(width: 80, height: 24) }
        try await #require(await bootstrap.processes.first).finish(0)
        #expect(try await session.wait() == 0)
        await runtime.shutdown()
    }

    private func specification() -> ContainerSpec {
        ContainerSpec(
            name: "fixture",
            image: FakeAppleImageIdentityClient.digest,
            command: ["/bin/sh"],
            openStandardInput: true
        )
    }

    @Test(arguments: [false, true])
    func `replacement joins old controls without crossing resolved identity`(replaced: Bool) async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let creator = AppleContainerCreateTests.Creator()
        let bootstrap = AttachmentBootstrap(creator: creator)
        let runtime = try fixture.runtime(useDirectProcessAPI: true, creator: creator, bootstrap: bootstrap)
        var spec = specification()
        spec.terminal = true
        let snapshot = try await runtime.createContainer(spec: spec, context: .init())
        let original = try await runtime.attachContainer(id: "fixture", terminal: true, context: .init())
        let channel = try #require(await runtime.containerIO["fixture"])
        let held = HeldResizeProcess()
        try await channel.bind(held)
        try await channel.didStart(at: Date())
        let resizing = Task { try await original.resize(width: 100, height: 30) }
        await held.waitUntilEntered()
        _ = await runtime.discardContainerState(snapshot: snapshot)
        let closing = try #require(await runtime.containerIOClosures["fixture"])
        await #expect(throws: DevContainerError.self) { try await runtime.requireRecoveryQuiescence(context: .init()) }
        let attached = Task { try await runtime.attachContainer(id: "fixture", terminal: true, context: .init()) }
        let starting = Task { try await runtime.startContainer(id: "fixture", context: .init()) }
        while (await runtime.containerIOClosures)["fixture"]?.waiters != 2 {
            await Task.yield()
        }
        #expect(await runtime.containerIO["fixture"] == nil)
        #expect(await bootstrap.calls == 0)
        if replaced {
            await creator.replaceIncarnation()
        }
        await held.release()
        try await resizing.value
        await closing.task.value
        if replaced {
            await #expect(throws: DevContainerError.self) { try await attached.value }
            await #expect(throws: DevContainerError.self) { try await starting.value }
            #expect(await bootstrap.calls == 0)
        } else {
            let replacement = try await attached.value
            try await starting.value
            #expect(await bootstrap.calls == 1)
            try await #require(await bootstrap.processes.first).finish(19)
            #expect(try await replacement.wait() == 19)
        }
        #expect(await runtime.containerIOClosures["fixture"] == nil)
        await #expect(throws: CancellationError.self) { try await original.wait() }
        await runtime.shutdown()
    }
}

private actor AttachmentBootstrap: AppleContainerBootstrapClient {
    let creator: AppleContainerCreateTests.Creator
    let failBootstrap: Bool
    var calls = 0
    var processes: [AttachedInitProcess] = []

    init(creator: AppleContainerCreateTests.Creator, failBootstrap: Bool = false) {
        self.creator = creator
        self.failBootstrap = failBootstrap
    }

    func bootstrap(id _: String, stdio: [FileHandle?]) throws -> any ClientProcess {
        calls += 1
        if failBootstrap {
            throw DevContainerError(.runtimeUnavailable, message: "Injected lost bootstrap reply")
        }
        let handles = try stdio.map { handle -> FileHandle? in
            guard let handle else { return nil }
            let descriptor = dup(handle.fileDescriptor)
            guard descriptor >= 0 else { throw POSIXError(.EMFILE) }
            return FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        }
        let process = AttachedInitProcess(creator: creator, handles: handles)
        processes.append(process)
        return process
    }
}

private actor AttachedInitProcess: ClientProcess {
    nonisolated let id = "fixture"
    let creator: AppleContainerCreateTests.Creator
    let handles: [FileHandle?]
    var exit: Int32?
    var waiter: CheckedContinuation<Int32, Never>?
    var kills = 0

    init(creator: AppleContainerCreateTests.Creator, handles: [FileHandle?]) {
        self.creator = creator
        self.handles = handles
    }

    func start() async throws {
        try await creator.start()
        try handles[1]?.write(contentsOf: Data("first output".utf8))
        try handles[2]?.write(contentsOf: Data("first error".utf8))
    }

    func readInput() throws -> Data? {
        try handles[0]?.read(upToCount: 5)
    }

    func emit(_ text: String) throws {
        try handles[1]?.write(contentsOf: Data(text.utf8))
    }

    func finish(_ code: Int32) async throws {
        for handle in handles {
            try handle?.close()
        }
        await creator.recordExit()
        exit = code
        waiter?.resume(returning: code)
        waiter = nil
    }

    func wait() async -> Int32 {
        if let exit {
            return exit
        }
        return await withCheckedContinuation { waiter = $0 }
    }

    func resize(_: Terminal.Size) { /* Nonterminal runtime fixture. */ }
    func kill(_: Int32) {
        kills += 1
    }

    #if DEVCONTAINER_ENHANCED_RUNTIME
        nonisolated func disconnect() { /* No transport connection in this fixture. */ }
    #endif
}
