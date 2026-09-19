// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineWire
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

struct DockerAttachmentOptions: Sendable {
    let logs: Bool
    let stream: Bool
    let standardInput: Bool
    let standardOutput: Bool
    let standardError: Bool
    let detachKeys: [UInt8]

    func needsLiveSession(spec: ContainerSpec) -> Bool {
        // Moby ignores requested stdin when the container has no stdin pipe.
        // Non-TTY StdinOnce still waits for exit even with no active copier.
        stream && (standardOutput || standardError || (standardInput && spec.openStandardInput)
            || (spec.standardInputOnce == true && !spec.terminal))
    }

    init(target: ParsedTarget) throws {
        logs = Self.flag("logs", target: target)
        stream = Self.flag("stream", target: target)
        standardInput = Self.flag("stdin", target: target)
        standardOutput = Self.flag("stdout", target: target)
        standardError = Self.flag("stderr", target: target)
        detachKeys = try Self.keys(target.first("detachKeys") ?? "")
    }

    private static func flag(_ name: String, target: ParsedTarget) -> Bool {
        // Match Moby's httputils.BoolValue, not strconv.ParseBool: historical
        // Engine attachment queries treat any other nonempty value as true.
        switch (target.first(name) ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "", "0", "no", "false", "none": false
        default: true
        }
    }

    private static func keys(_ value: String) throws -> [UInt8] {
        guard !value.isEmpty else { return [16, 17] }
        return try value.split(separator: ",", omittingEmptySubsequences: false).map { key in
            if key.utf8.count == 1, let byte = key.utf8.first {
                return byte
            }
            if key == "DEL" {
                return 127
            }
            if key.hasPrefix("ctrl-"), key.utf8.count == 6, let byte = key.utf8.last {
                if (97 ... 122).contains(byte) {
                    return byte - 96
                }
                if byte == 64 || (91 ... 95).contains(byte) {
                    return byte - 64
                }
            }
            throw DevContainerError(.invalidRequest, message: "Invalid detach keys")
        }
    }
}

/// HTTP attachment lifetime is separate from init lifetime. In particular an
/// output-only half-close must never close the container's shared stdin.
final class DockerContainerAttachment: DockerHijackSession, @unchecked Sendable {
    let frames: AsyncThrowingStream<DockerStreamFrame, any Error>
    private let session: (any RuntimeProcessSession)?
    private let pump: Task<Void, Never>
    private let input: DockerAttachmentInput

    init(
        session: (any RuntimeProcessSession)?, history: AsyncThrowingStream<RuntimeIOFrame, any Error>?,
        options: DockerAttachmentOptions, spec: ContainerSpec
    ) {
        self.session = session
        let input = DockerAttachmentInput(session: session, options: options, spec: spec)
        self.input = input
        // The native broker also has bounded buffers; the HTTP adapter must not
        // turn those into an unbounded intermediate queue for a slow client.
        let pair = AsyncThrowingStream<DockerStreamFrame, any Error>.makeStream(bufferingPolicy: .bufferingOldest(256))
        frames = pair.stream
        pump = Task {
            do {
                if let history {
                    try await Self.copy(history, options: options, to: pair.continuation)
                }
                if let session {
                    try await Self.copy(session.frames, options: options, to: pair.continuation)
                }
                pair.continuation.finish()
            } catch {
                pair.continuation.finish(throwing: error)
                await input.cancel()
            }
        }
        let pump = pump
        pair.continuation.onTermination = { termination in
            if case .cancelled = termination {
                pump.cancel()
            }
        }
    }

    deinit { pump.cancel() }

    func write(_ data: Data) async throws {
        try await input.write(data)
    }

    func closeStandardInput() async throws {
        try await input.close()
    }

    func wait() async throws -> Int32 {
        guard let session else { return 0 }
        do {
            return try await session.wait()
        } catch {
            if input.isDetached {
                return 0
            }
            throw error
        }
    }

    func cancel() async {
        await input.cancel()
        pump.cancel()
        await pump.value
    }

    private static func copy(
        _ source: AsyncThrowingStream<RuntimeIOFrame, any Error>, options: DockerAttachmentOptions,
        to continuation: AsyncThrowingStream<DockerStreamFrame, any Error>.Continuation
    ) async throws {
        for try await frame in source {
            try Task.checkCancellation()
            let channel: DockerStreamChannel
            switch frame.channel {
            case .standardOutput where options.standardOutput: channel = .standardOutput
            case .standardError where options.standardError: channel = .standardError
            default: continue
            }
            // Split large historical log records to keep the queue's byte bound.
            var offset = 0
            while offset < frame.data.count {
                let end = min(offset + 65536, frame.data.count)
                let data = frame.data.subdata(in: offset ..< end)
                switch continuation.yield(.init(channel: channel, data: data)) {
                case .enqueued: break
                case .dropped:
                    throw DevContainerError(
                        .runtimeUnavailable, message: "Attachment output exceeded its bounded buffer"
                    )
                case .terminated: throw CancellationError()
                @unknown default: throw CancellationError()
                }
                offset = end
            }
        }
        // Cancellation can end a quiet AsyncThrowingStream without throwing.
        // Propagate it so the pump always joins stdin/subscription cleanup.
        try Task.checkCancellation()
    }
}

/// Shared by transport teardown and the output pump so either failure applies
/// stdin EOF before detaching the native subscription that authorizes it.
private final class DockerAttachmentInput: @unchecked Sendable {
    private let session: (any RuntimeProcessSession)?
    private let inputEnabled: Bool
    private let closeContainerInput: Bool
    private let detachKeys: [UInt8]
    private let lock = NSLock()
    private var inputClosed = false
    private var pendingKeys: [UInt8] = []
    private var detached = false
    private var closure: Task<Void, any Error>?

    var isDetached: Bool {
        lock.withLock { detached }
    }

    init(session: (any RuntimeProcessSession)?, options: DockerAttachmentOptions, spec: ContainerSpec) {
        self.session = session
        inputEnabled = options.stream && options.standardInput && spec.openStandardInput
        closeContainerInput = spec.standardInputOnce == true && !spec.terminal
        detachKeys = spec.terminal ? options.detachKeys : []
    }

    func write(_ data: Data) async throws {
        guard inputEnabled, let session else { return }
        let (bytes, detach) = try lock.withLock { () throws -> (Data, Bool) in
            guard !inputClosed else { throw CancellationError() }
            guard !detachKeys.isEmpty else { return (data, false) }
            var output = Data()
            for byte in data {
                if byte == detachKeys[pendingKeys.count] {
                    pendingKeys.append(byte)
                } else {
                    output.append(contentsOf: pendingKeys)
                    output.append(byte)
                    pendingKeys.removeAll()
                }
                if pendingKeys == detachKeys {
                    pendingKeys.removeAll()
                    inputClosed = true
                    detached = true
                    return (output, true)
                }
            }
            return (output, false)
        }
        if !bytes.isEmpty {
            try await session.write(bytes)
        }
        if detach {
            await session.cancel()
        }
    }

    func close() async throws {
        guard inputEnabled, let session else { return }
        let closing = lock.withLock { () -> Task<Void, any Error>? in
            if let closure {
                return closure
            }
            guard !inputClosed else { return nil }
            inputClosed = true
            detached = !closeContainerInput
            let closesContainer = closeContainerInput
            let task = Task {
                if closesContainer {
                    try await session.closeStandardInput()
                } else {
                    await session.cancel()
                }
            }
            closure = task
            return task
        }
        // Concurrent pump/transport cleanup must join EOF before either can
        // revoke the subscription. Cancellation of the caller cannot skip it.
        try await closing?.value
    }

    func cancel() async {
        try? await close()
        await session?.cancel()
    }
}
