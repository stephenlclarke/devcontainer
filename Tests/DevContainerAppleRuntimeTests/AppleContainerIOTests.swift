// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerAPIClient
import ContainerizationOS
import Darwin
@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

private final class TraceCapture: @unchecked Sendable {
    private let lock = NSLock()
    private var storedLines: [String] = []

    func append(_ data: Data) {
        guard let line = String(data: data, encoding: .utf8) else {
            preconditionFailure("init I/O diagnostic emitted invalid UTF-8")
        }
        lock.withLock { storedLines.append(line) }
    }

    var lines: [String] {
        lock.withLock { storedLines }
    }
}

struct AppleContainerIOTests {
    @Test func `init IO trace uses MiB watermarks and retains final cancellation totals`() {
        let capture = TraceCapture()
        let diagnostics = AppleContainerIODiagnostics(enabled: true, traceWriter: capture.append)
        for _ in 0 ..< 8 {
            diagnostics.inputSubmitted(512 * 1024, pendingWrites: 0)
            diagnostics.inputCompleted(512 * 1024, pendingWrites: 0)
        }
        diagnostics.outputFrame(RuntimeIOFrame(
            channel: .standardOutput,
            data: Data(repeating: 0, count: 1024 * 1024)
        ))
        diagnostics.cancellationSummary(pendingWrites: 2)
        diagnostics.cancellationSummary(pendingWrites: 3)

        let lines = capture.lines
        #expect(lines.filter { $0.contains("event=input-write-submitted") }.count == 4)
        #expect(lines.filter { $0.contains("event=input-write-completed") }.count == 4)
        #expect(lines.contains { $0.contains("event=output-progress") && $0.contains("bytes=1048576") })
        #expect(lines.filter { $0.contains("event=io-cancelled-summary") }.count == 1)
        #expect(lines.contains {
            $0.contains("submitted=4194304 completed=4194304 failed=0 stdout=1048576 stderr=0")
                && $0.contains("stdoutEOF=false stderrEOF=false exit=unknown pending=2")
        })
    }

    @Test func `disabled init IO trace avoids pending writer reads`() {
        let capture = TraceCapture()
        let diagnostics = AppleContainerIODiagnostics(enabled: false, traceWriter: capture.append)
        var pendingRead = false
        func pendingWrites() -> Int {
            pendingRead = true
            return 1
        }

        diagnostics.inputSubmitted(1024, pendingWrites: pendingWrites())
        diagnostics.inputCompleted(1024, pendingWrites: pendingWrites())
        diagnostics.inputFailed(1024, pendingWrites: pendingWrites())
        diagnostics.inputEOFRequested(pendingWrites: pendingWrites())
        diagnostics.inputEOFCompleted(pendingWrites: pendingWrites())
        diagnostics.cancellationSummary(pendingWrites: pendingWrites())

        #expect(!pendingRead)
        #expect(capture.lines.isEmpty)
    }

    @Test func `init IO trace records failed writes EOF and process drain state`() {
        let capture = TraceCapture()
        let diagnostics = AppleContainerIODiagnostics(enabled: true, traceWriter: capture.append)

        diagnostics.inputFailed(7, pendingWrites: 3)
        diagnostics.inputEOFRequested(pendingWrites: 2)
        diagnostics.inputEOFCompleted(pendingWrites: 0)
        diagnostics.outputFrame(RuntimeIOFrame(channel: .standardOutput, data: Data(repeating: 0, count: 11)))
        diagnostics.outputFrame(RuntimeIOFrame(channel: .standardError, data: Data(repeating: 0, count: 13)))
        diagnostics.sourceEOF(.standardOutput)
        diagnostics.sourceEOF(.standardError)
        diagnostics.processExit(17)
        diagnostics.drainTimedOut()
        diagnostics.drainCompleted()

        let lines = capture.lines
        #expect(lines.contains {
            $0.contains("event=input-write-failed bytes=7 total=7 pending=3")
        })
        #expect(lines.contains { $0.contains("event=input-eof-requested pending=2") })
        #expect(lines.contains {
            $0.contains("event=input-eof-completed submitted=0 completed=0 failed=7 pending=0")
        })
        #expect(lines.contains { $0.contains("event=output-eof channel=standardOutput bytes=11") })
        #expect(lines.contains { $0.contains("event=output-eof channel=standardError bytes=13") })
        #expect(lines.contains { $0.contains("event=process-exit code=17") })
        #expect(lines.contains {
            $0.contains("event=output-drain-timeout submitted=0 completed=0 failed=7 stdout=11 stderr=13")
                && $0.contains("stdoutEOF=true stderrEOF=true exit=17")
        })
        #expect(lines.contains {
            $0.contains("event=output-drain-completed submitted=0 completed=0 failed=7 stdout=11 stderr=13")
                && $0.contains("stdoutEOF=true stderrEOF=true exit=17")
        })
    }

    @Test func `prestart attachments receive separate complete streams and real exit`() async throws {
        let channel = try AppleContainerIO(createdAt: Date(), terminal: false, openStandardInput: false)
        let first = channel.attach()
        let second = channel.attach()
        let handles = try duplicate(channel.bootstrapHandles())
        let process = AttachProcess()
        try await channel.bind(process)
        try handles[1]?.write(contentsOf: Data("out".utf8))
        try handles[2]?.write(contentsOf: Data("err".utf8))
        try handles[1]?.close()
        try handles[2]?.close()
        await channel.finish(exitCode: 42)
        for session in [first, second] {
            let channels = try await collected(session)
            #expect(channels[.standardOutput] == Data("out".utf8))
            #expect(channels[.standardError] == Data("err".utf8))
            #expect(try await session.wait() == 42)
        }
        #expect(await process.starts == 0)
        #expect(await process.kills == 0)
        await channel.shutdown()
    }

    @Test func `detaching one client preserves other output and process ownership`() async throws {
        let channel = try AppleContainerIO(createdAt: Date(), terminal: false, openStandardInput: false)
        let first = channel.attach()
        let second = channel.attach()
        let handles = try duplicate(channel.bootstrapHandles())
        let process = AttachProcess()
        try await channel.bind(process)
        await first.cancel()
        try handles[1]?.write(contentsOf: Data("still running".utf8))
        try handles[1]?.close()
        try handles[2]?.close()
        await channel.finish(exitCode: 7)
        await #expect(throws: CancellationError.self) { try await first.wait() }
        #expect(try await collected(second)[.standardOutput] == Data("still running".utf8))
        #expect(try await second.wait() == 7)
        #expect(await process.kills == 0)
        let later = channel.attach()
        #expect(try await collected(later).isEmpty)
        #expect(try await later.wait() == 7)
    }

    @Test func `stdin reaches the captured init descriptor and half close signals EOF`() async throws {
        let channel = try AppleContainerIO(createdAt: Date(), terminal: false, openStandardInput: true)
        let session = channel.attach()
        let handles = try duplicate(channel.bootstrapHandles())
        let input = try #require(handles[0])
        try await channel.bind(AttachProcess())
        try await session.write(Data("hello".utf8))
        #expect(try input.read(upToCount: 5) == Data("hello".utf8))
        try await session.closeStandardInput()
        #expect(try input.read(upToCount: 1)?.isEmpty != false)
        try handles[1]?.close()
        try handles[2]?.close()
        await channel.finish(exitCode: 0)
        await #expect(throws: DevContainerError.self) { try await session.write(Data([1])) }
    }

    @Test func `terminal resize can be prepared before bootstrap without starting init`() async throws {
        let channel = try AppleContainerIO(createdAt: Date(), terminal: true, openStandardInput: false)
        let session = channel.attach()
        try await session.resize(width: 132, height: 43)
        let handles = try duplicate(channel.bootstrapHandles())
        #expect(handles[2] == nil)
        let process = AttachProcess()
        try await channel.bind(process)
        #expect(await process.dimensions.isEmpty)
        try await process.start()
        try await channel.didStart(at: Date())
        #expect(await process.dimensions == ["132x43"])
        try await session.resize(width: 80, height: 24)
        try await session.resize(width: 0, height: 0)
        #expect(await process.dimensions == ["132x43", "80x24"])
        await #expect(throws: DevContainerError.self) { try await session.write(Data([1])) }
        #expect(throws: DevContainerError.self) { try channel.bootstrapHandles() }
        await channel.shutdown()
        #expect(await process.starts == 1)
        #expect(await process.kills == 0)
    }

    @Test(arguments: [false, true])
    func `cancelled backpressured input leaves shared stdin open`(detach: Bool) async throws {
        let channel = try AppleContainerIO(createdAt: Date(), terminal: false, openStandardInput: true)
        let first = channel.attach()
        let second = channel.attach()
        let handles = try duplicate(channel.bootstrapHandles())
        let input = try #require(handles[0])
        try await channel.bind(AttachProcess())
        let write = Task { try await first.write(Data(repeating: 65, count: 4 * 1024 * 1024)) }
        let ready = await waitForInput(input.fileDescriptor)
        if ready <= 0 {
            write.cancel()
            await channel.shutdown()
            for handle in handles {
                try? handle?.close()
            }
        }
        try #require(ready > 0)
        if detach {
            await first.cancel()
        } else {
            write.cancel()
        }
        await #expect(throws: CancellationError.self) { try await write.value }
        // Drain the partial cancelled write, then prove another client can write.
        let flags = fcntl(input.fileDescriptor, F_GETFL)
        #expect(fcntl(input.fileDescriptor, F_SETFL, flags | O_NONBLOCK) == 0)
        var bytes = [UInt8](repeating: 0, count: 16384)
        while Darwin.read(input.fileDescriptor, &bytes, bytes.count) > 0 {}
        #expect(errno == EAGAIN)
        try await second.write(Data("alive".utf8))
        #expect(try input.read(upToCount: 5) == Data("alive".utf8))
        await channel.shutdown()
        for handle in handles {
            try handle?.close()
        }
        #expect(!channel.hasExited)
    }

    @Test func `stdin EOF interrupts another attachment blocked in a write`() async throws {
        let channel = try AppleContainerIO(createdAt: Date(), terminal: false, openStandardInput: true)
        let first = channel.attach()
        let second = channel.attach()
        let handles = try duplicate(channel.bootstrapHandles())
        let input = try #require(handles[0])
        try await channel.bind(AttachProcess())
        let blocked = Task { try await first.write(Data(repeating: 65, count: 4 * 1024 * 1024)) }
        let ready = await waitForInput(input.fileDescriptor)
        if ready <= 0 {
            blocked.cancel()
            await channel.shutdown()
            for handle in handles {
                try? handle?.close()
            }
        }
        try #require(ready > 0)
        // Output failure can revoke a subscription before transport cleanup.
        // Its EOF still belongs to this init, never a replacement generation.
        await second.cancel()
        try await second.closeStandardInput()
        await #expect(throws: CancellationError.self) { try await blocked.value }
        #expect(!channel.isFinished)
        let flags = fcntl(input.fileDescriptor, F_GETFL)
        #expect(fcntl(input.fileDescriptor, F_SETFL, flags | O_NONBLOCK) == 0)
        var bytes = [UInt8](repeating: 0, count: 16384)
        var count = Darwin.read(input.fileDescriptor, &bytes, bytes.count)
        while count > 0 {
            count = Darwin.read(input.fileDescriptor, &bytes, bytes.count)
        }
        #expect(count == 0)
        await channel.shutdown()
        for handle in handles {
            try handle?.close()
        }
    }

    @Test func `shutdown seals queued resizes and joins accepted control operations`() async throws {
        let channel = try AppleContainerIO(createdAt: Date(), terminal: true, openStandardInput: false)
        let session = channel.attach()
        let process = HeldResizeProcess()
        try await channel.bind(process)
        try await channel.didStart(at: Date())
        let first = Task { try await session.resize(width: 100, height: 30) }
        await process.waitUntilEntered()
        let second = Task { try await session.resize(width: 101, height: 31) }
        while channel.pendingResizeCount != 2 {
            await Task.yield()
        }
        channel.cancel()
        let shutdown = Task { await channel.shutdown() }
        await process.release()
        try await first.value
        await #expect(throws: (any Error).self) { try await second.value }
        await shutdown.value
        #expect(await process.calls == 1)
        await #expect(throws: DevContainerError.self) { try await session.resize(width: 80, height: 24) }
        #expect(!channel.hasExited)
    }

    @Test(arguments: [false, true])
    func `queued input cancellation does not wait for another clients blocked write`(detach: Bool) async throws {
        let channel = try AppleContainerIO(createdAt: Date(), terminal: false, openStandardInput: true)
        let first = channel.attach()
        let second = channel.attach()
        let handles = try duplicate(channel.bootstrapHandles())
        let input = try #require(handles[0])
        try await channel.bind(AttachProcess())
        let blocked = Task { try await first.write(Data(repeating: 65, count: 4 * 1024 * 1024)) }
        let ready = await waitForInput(input.fileDescriptor)
        if ready <= 0 {
            blocked.cancel()
            await channel.shutdown()
            for handle in handles {
                try? handle?.close()
            }
        }
        try #require(ready > 0)
        let queued = Task { try await second.write(Data("must not write".utf8)) }
        let queueDeadline = ContinuousClock.now.advanced(by: .seconds(5))
        while channel.pendingInputWrites != 2, ContinuousClock.now < queueDeadline {
            await Task.yield()
        }
        if channel.pendingInputWrites != 2 {
            queued.cancel()
            blocked.cancel()
            await channel.shutdown()
            for handle in handles {
                try? handle?.close()
            }
        }
        try #require(channel.pendingInputWrites == 2)
        if detach {
            await second.cancel()
        } else {
            queued.cancel()
        }
        // This must complete while the first client's full socket remains blocked.
        await #expect(throws: (any Error).self) { try await queued.value }
        #expect(!channel.isFinished)
        blocked.cancel()
        await #expect(throws: CancellationError.self) { try await blocked.value }
        await channel.shutdown()
        for handle in handles {
            try handle?.close()
        }
    }

    @Test func `missing output EOF fails instead of reporting complete delivery`() async throws {
        let channel = try AppleContainerIO(createdAt: Date(), terminal: false, openStandardInput: false)
        let session = channel.attach()
        let handles = try duplicate(channel.bootstrapHandles())
        try await channel.bind(AttachProcess())
        await channel.finish(exitCode: 0, drainTimeout: .milliseconds(20))
        await #expect(throws: DevContainerError.self) { try await session.wait() }
        await #expect(throws: DevContainerError.self) { try await collected(session) }
        for handle in handles {
            try handle?.close()
        }
    }

    @Test func `slow subscriber fails explicitly without stopping other subscribers`() async throws {
        let state = AppleContainerAttachmentState()
        let slow = state.subscribe()
        let fast = state.subscribe()
        var reader = fast.frames.makeAsyncIterator()
        for index in 0 ..< 257 {
            let frame = RuntimeIOFrame(channel: .standardOutput, data: Data([UInt8(index % 256)]))
            state.publish(frame)
            #expect(try await reader.next()?.data == frame.data)
        }
        await #expect(throws: DevContainerError.self) { try await slow.wait() }
        state.complete(.success(9))
        #expect(try await fast.wait() == 9)
        #expect(try await reader.next() == nil)
    }

    @Test func `cancelling a waiting attachment leaves the generation alive`() async throws {
        let channel = try AppleContainerIO(createdAt: Date(), terminal: false, openStandardInput: false)
        let session = channel.attach()
        let waiter = Task { try await session.wait() }
        waiter.cancel()
        await #expect(throws: CancellationError.self) { try await waiter.value }
        #expect(!channel.isFinished)
        await channel.shutdown()
        #expect(channel.isFinished)
    }

    private func duplicate(_ handles: [FileHandle?]) throws -> [FileHandle?] {
        try handles.map { handle in
            guard let handle else { return nil }
            let descriptor = dup(handle.fileDescriptor)
            guard descriptor >= 0 else { throw POSIXError(.EMFILE) }
            return FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        }
    }

    private func collected(_ session: any RuntimeProcessSession) async throws -> [RuntimeIOChannel: Data] {
        var result: [RuntimeIOChannel: Data] = [:]
        for try await frame in session.frames {
            result[frame.channel, default: Data()].append(frame.data)
        }
        return result
    }
}

private actor AttachProcess: ClientProcess {
    nonisolated let id = "attachment-fixture"
    var starts = 0
    var kills = 0
    var dimensions: [String] = []
    func start() throws {
        starts += 1
    }

    func kill(_: Int32) {
        kills += 1
    }

    func wait() -> Int32 {
        0
    }

    func resize(_ size: Terminal.Size) throws {
        guard starts > 0 else { throw DevContainerError(.conflict, message: "Stock resize requires running init") }
        dimensions.append("\(size.width)x\(size.height)")
    }

    #if DEVCONTAINER_ENHANCED_RUNTIME
        nonisolated func disconnect() { /* No XPC connection in this descriptor fixture. */ }
    #endif
}

actor HeldResizeProcess: ClientProcess {
    nonisolated let id = "held-resize"
    var calls = 0
    private var entered: CheckedContinuation<Void, Never>?
    private var held: CheckedContinuation<Void, Never>?
    func start() { /* This fixture models an already started process. */ }
    func kill(_: Int32) { /* Attachment control must never invoke kill. */ }
    func wait() -> Int32 {
        0
    }

    func resize(_: Terminal.Size) async {
        calls += 1
        entered?.resume()
        entered = nil
        await withCheckedContinuation { held = $0 }
    }

    func waitUntilEntered() async {
        if calls == 0 {
            await withCheckedContinuation { entered = $0 }
        }
    }

    func release() {
        held?.resume()
        held = nil
    }

    #if DEVCONTAINER_ENHANCED_RUNTIME
        nonisolated func disconnect() { /* No XPC transport in this fixture. */ }
    #endif
}

private func waitForInput(_ descriptor: Int32) async -> Int32 {
    await withCheckedContinuation { continuation in
        // The existing five-second poll runs on an OS queue so it cannot
        // occupy a Swift cooperative executor thread needed by the writer.
        DispatchQueue.global(qos: .utility).async {
            var event = pollfd(fd: descriptor, events: Int16(POLLIN), revents: 0)
            continuation.resume(returning: Darwin.poll(&event, 1, 5000))
        }
    }
}
