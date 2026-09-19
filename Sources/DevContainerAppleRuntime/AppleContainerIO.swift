// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerAPIClient
import ContainerizationOS
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

/// One init-process generation owns its descriptors independently of attachments.
/// Bootstrap receives these descriptors before the user's entrypoint may start.
final class AppleContainerIO: @unchecked Sendable {
    let createdAt: Date
    let terminal: Bool
    private let input: AppleProcessInputChannel?
    private let output = Pipe()
    private let error: Pipe?
    private let inputWriter: ProcessInputWriter?
    private let outputMonitor: ProcessPipeMonitor
    private let errorMonitor: ProcessPipeMonitor?
    private let attachments = AppleContainerAttachmentState()
    private let lock = NSLock()
    private var process: (any ClientProcess)?
    private var pendingSize: Terminal.Size?
    private var drainTimedOut = false
    private var bootstrapStarted = false
    private var startAttempted = false
    private var processExited = false
    private var nativeExitObserved = false
    private var resizeTail: Task<Void, any Error>?
    private var pendingResizes = 0
    private var controlsClosed = false
    private var startedAt: Date?
    private var validateGeneration: (@Sendable () async throws -> Void)?

    init(createdAt: Date, terminal: Bool, openStandardInput: Bool) throws {
        self.createdAt = createdAt
        self.terminal = terminal
        input = try openStandardInput ? .socketPair() : nil
        error = terminal ? nil : Pipe()
        inputWriter = input.map {
            ProcessInputWriter(channel: $0, label: "io.github.stephenlclarke.devcontainer.container-input")
        }
        let state = attachments
        outputMonitor = ProcessPipeMonitor(
            handle: output.fileHandleForReading, channel: .standardOutput,
            onFrame: { state.publish($0) }, onError: { state.complete(.failure($0)) }
        )
        errorMonitor = error.map {
            ProcessPipeMonitor(
                handle: $0.fileHandleForReading, channel: .standardError,
                onFrame: { state.publish($0) }, onError: { state.complete(.failure($0)) }
            )
        }
    }

    deinit {
        inputWriter?.cancel()
        outputMonitor.cancel()
        errorMonitor?.cancel()
    }

    var isFinished: Bool {
        attachments.isFinished
    }

    var pendingInputWrites: Int {
        inputWriter?.pendingWriteCount ?? 0
    }

    var pendingResizeCount: Int {
        lock.withLock { pendingResizes }
    }

    var hasExited: Bool {
        lock.withLock { processExited }
    }

    var boundProcess: (any ClientProcess)? {
        lock.withLock { process }
    }

    func reserveStart() throws {
        try lock.withLock {
            guard process != nil, !startAttempted, !attachments.isFinished else {
                throw DevContainerError(
                    .conflict, message: "Container process start cannot be retried safely; remove and recreate it"
                )
            }
            startAttempted = true
        }
    }

    /// A failed/ambiguous bootstrap must not send replacement descriptors.
    func bootstrapHandles() throws -> [FileHandle?] {
        try lock.withLock {
            guard !bootstrapStarted, !attachments.isFinished else {
                throw DevContainerError(.conflict, message: "Container I/O bootstrap was already attempted")
            }
            bootstrapStarted = true
            return [input?.processEnd, output.fileHandleForWriting, error?.fileHandleForWriting]
        }
    }

    func bind(_ process: any ClientProcess) async throws {
        lock.withLock { self.process = process }
        closeTransferredEnds()
    }

    func didStart(at startedAt: Date, validate: @escaping @Sendable () async throws -> Void = {}) async throws {
        let resize: Task<Void, any Error>? = try lock.withLock {
            if nativeExitObserved {
                return nil
            }
            guard !controlsClosed, let process else { throw CancellationError() }
            self.startedAt = startedAt
            validateGeneration = validate
            return pendingSize.map { enqueueResize($0, process: process) }
        }
        try await resize?.value
    }

    func ownsProcess(startedAt: Date?) -> Bool {
        lock.withLock { self.startedAt != nil && self.startedAt == startedAt && !controlsClosed }
    }

    func attach() -> any RuntimeProcessSession {
        AppleContainerAttachment(owner: self, subscription: attachments.subscribe())
    }

    func finish(exitCode: Int32, drainTimeout: Duration = .seconds(30)) async {
        lock.withLock { nativeExitObserved = true }
        let controls = sealControls()
        closeTransferredEnds()
        let timeout = Task {
            do {
                try await Task.sleep(for: drainTimeout)
                lock.withLock { drainTimedOut = true }
                outputMonitor.cancel()
                errorMonitor?.cancel()
            } catch { /* Successful EOF cancelled the deadline. */ }
        }
        await outputMonitor.waitForCompletion()
        await errorMonitor?.waitForCompletion()
        timeout.cancel()
        await timeout.value
        inputWriter?.cancel()
        await inputWriter?.waitForCompletion()
        _ = try? await controls?.value
        // Replacement is safe only after both the native wait and reader joins.
        // Output failure or attachment cancellation alone never proves exit.
        lock.withLock { processExited = true }
        if lock.withLock({ drainTimedOut }) {
            attachments.complete(.failure(DevContainerError(
                .runtimeUnavailable, message: "Container output did not reach EOF after process exit"
            )))
        } else {
            attachments.complete(.success(exitCode))
        }
    }

    func cancel() {
        _ = sealControls()
        closeTransferredEnds()
        inputWriter?.cancel()
        outputMonitor.cancel()
        errorMonitor?.cancel()
        attachments.complete(.failure(CancellationError()))
    }

    func shutdown() async {
        cancel()
        await outputMonitor.waitForCompletion()
        await errorMonitor?.waitForCompletion()
        await inputWriter?.waitForCompletion()
        _ = try? await lock.withLock { resizeTail }?.value
    }

    func fail(_ error: any Error) async {
        attachments.complete(.failure(error))
        await shutdown()
    }

    fileprivate func write(_ data: Data, subscription: AppleContainerSubscription) async throws {
        let attachment = subscription.id
        try attachments.requireActive(attachment)
        guard let inputWriter else {
            throw DevContainerError(.conflict, message: "Container standard input is not open")
        }
        let state = attachments
        try await subscription.perform {
            try await inputWriter.write(data, cancelWriterOnCancellation: false) { !state.isActive(attachment) }
        }
    }

    fileprivate func closeInput(attachment: UUID) async throws {
        try attachments.requireActive(attachment)
        try await inputWriter?.close()
    }

    fileprivate func resize(width: UInt16, height: UInt16, attachment: UUID) async throws {
        try attachments.requireActive(attachment)
        guard terminal else {
            throw DevContainerError(.conflict, message: "Container does not have a terminal")
        }
        guard width > 0, height > 0 else { return }
        let size = Terminal.Size(width: width, height: height)
        let resize = try lock.withLock {
            guard !controlsClosed else { throw CancellationError() }
            pendingSize = size
            return startedAt == nil ? nil : process.map { enqueueResize(size, process: $0) }
        }
        try await resize?.value
    }

    /// Called with the lock held; serializes pending bootstrap size with later requests.
    private func enqueueResize(_ size: Terminal.Size, process: any ClientProcess) -> Task<Void, any Error> {
        let previous = resizeTail
        pendingResizes += 1
        let task = Task { [weak self] in
            guard let self else { throw CancellationError() }
            defer { lock.withLock { pendingResizes -= 1 } }
            _ = try? await previous?.value
            let validate = try lock.withLock {
                guard !controlsClosed else { throw CancellationError() }
                return validateGeneration
            }
            try await validate?()
            try lock.withLock {
                if controlsClosed {
                    throw CancellationError()
                }
            }
            try await process.resize(size)
        }
        resizeTail = task
        return task
    }

    private func sealControls() -> Task<Void, any Error>? {
        lock.withLock {
            controlsClosed = true
            return resizeTail
        }
    }

    fileprivate func detach(_ attachment: UUID) {
        attachments.detach(attachment)
    }

    private func closeTransferredEnds() {
        lock.withLock {
            try? input?.processEnd.close()
            try? output.fileHandleForWriting.close()
            try? error?.fileHandleForWriting.close()
        }
    }
}

private final class AppleContainerAttachment: RuntimeProcessSession, @unchecked Sendable {
    var frames: AsyncThrowingStream<RuntimeIOFrame, any Error> {
        subscription.frames
    }

    private let owner: AppleContainerIO
    private let subscription: AppleContainerSubscription
    private var id: UUID {
        subscription.id
    }

    init(owner: AppleContainerIO, subscription: AppleContainerSubscription) {
        self.owner = owner
        self.subscription = subscription
    }

    deinit { owner.detach(id) }
    func write(_ data: Data) async throws {
        try await owner.write(data, subscription: subscription)
    }

    func closeStandardInput() async throws {
        try await owner.closeInput(attachment: id)
    }

    func resize(width: UInt16, height: UInt16) async throws {
        try await owner.resize(width: width, height: height, attachment: id)
    }

    func wait() async throws -> Int32 {
        try await withTaskCancellationHandler {
            try await subscription.wait()
        } onCancel: { owner.detach(id) }
    }

    func cancel() {
        owner.detach(id)
    }
}
