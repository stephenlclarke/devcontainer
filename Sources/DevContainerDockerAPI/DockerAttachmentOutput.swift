// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineWire
import DevContainerModel
import Foundation

/// Exactly one cancellable read is admitted at a time. The cursor has no eager
/// pump or intermediate byte queue: slow history consumers only retain a frame.
final class DockerAttachmentOutput: @unchecked Sendable {
    private let lock = NSLock()
    private let cursor: DockerAttachmentOutputCursor
    private let onFailure: @Sendable () async -> Void
    private var read: Task<DockerStreamFrame?, any Error>?
    private var cancelled = false

    init(
        history: AsyncThrowingStream<RuntimeIOFrame, any Error>?,
        live: AsyncThrowingStream<RuntimeIOFrame, any Error>?, options: DockerAttachmentOptions,
        onFailure: @escaping @Sendable () async -> Void
    ) {
        cursor = DockerAttachmentOutputCursor(history: history, live: live, options: options)
        self.onFailure = onFailure
    }

    deinit { read?.cancel() }

    func next() async throws -> DockerStreamFrame? {
        let task = try lock.withLock {
            guard !cancelled else { throw CancellationError() }
            guard read == nil else {
                throw DevContainerError(.conflict, message: "Attachment output has more than one reader")
            }
            let cursor = cursor
            let task = Task { try await cursor.next() }
            read = task
            return task
        }
        defer { lock.withLock { read = nil } }
        do {
            return try await withTaskCancellationHandler {
                try Task.checkCancellation()
                let frame = try await task.value
                try Task.checkCancellation()
                return frame
            } onCancel: { task.cancel() }
        } catch {
            task.cancel()
            _ = await task.result
            await onFailure()
            throw error
        }
    }

    func cancel() async {
        let pending = lock.withLock {
            cancelled = true
            return read
        }
        pending?.cancel()
        _ = await pending?.result
    }
}

/// Non-Sendable iterators are touched by the single admitted read task only;
/// admission and joined cancellation are owned by DockerAttachmentOutput.
private final class DockerAttachmentOutputCursor: @unchecked Sendable {
    private var history: AsyncThrowingStream<RuntimeIOFrame, any Error>.Iterator?
    private var live: AsyncThrowingStream<RuntimeIOFrame, any Error>.Iterator?
    private let options: DockerAttachmentOptions
    private var pending: DockerStreamFrame?
    private var offset = 0

    init(
        history: AsyncThrowingStream<RuntimeIOFrame, any Error>?,
        live: AsyncThrowingStream<RuntimeIOFrame, any Error>?, options: DockerAttachmentOptions
    ) {
        self.history = history?.makeAsyncIterator()
        self.live = live?.makeAsyncIterator()
        self.options = options
    }

    func next() async throws -> DockerStreamFrame? {
        try Task.checkCancellation()
        while pending == nil {
            let frame = try await nextSourceFrame()
            try Task.checkCancellation()
            guard let frame else { return nil }
            guard !frame.data.isEmpty, let channel = selectedChannel(frame.channel) else { continue }
            pending = DockerStreamFrame(channel: channel, data: frame.data)
            offset = 0
        }
        guard let frame = pending else { return nil }
        let end = offset + min(65536, frame.data.count - offset)
        let result = DockerStreamFrame(channel: frame.channel, data: frame.data.subdata(in: offset ..< end))
        offset = end
        if end == frame.data.count {
            pending = nil
        }
        return result
    }

    private func nextSourceFrame() async throws -> RuntimeIOFrame? {
        if var iterator = history {
            let frame = try await iterator.next()
            history = frame == nil ? nil : iterator
            if let frame {
                return frame
            }
        }
        guard var iterator = live else { return nil }
        let frame = try await iterator.next()
        live = frame == nil ? nil : iterator
        return frame
    }

    private func selectedChannel(_ channel: RuntimeIOChannel) -> DockerStreamChannel? {
        switch channel {
        case .standardOutput where options.standardOutput: .standardOutput
        case .standardError where options.standardError: .standardError
        default: nil
        }
    }
}
