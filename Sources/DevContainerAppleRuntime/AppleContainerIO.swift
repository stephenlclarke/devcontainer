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
    private let diagnostics: AppleContainerIODiagnostics
    private let input: AppleProcessInputChannel?
    private let output = Pipe()
    private let error: Pipe?
    private let inputWriter: ProcessInputWriter?
    private let outputMonitor: ProcessPipeMonitor
    private let errorMonitor: ProcessPipeMonitor?
    private let attachments: AppleContainerAttachmentState
    private let capturePreparation: Task<Void, any Error>?
    private let exits = AppleContainerExitState()
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

    init(
        createdAt: Date, terminal: Bool, openStandardInput: Bool,
        outputCapture: (@Sendable () async throws -> any RuntimeContainerOutputJournal)? = nil
    ) throws {
        self.createdAt = createdAt
        self.terminal = terminal
        let diagnostics = AppleContainerIODiagnostics()
        self.diagnostics = diagnostics
        input = try openStandardInput ? .socketPair() : nil
        error = terminal ? nil : Pipe()
        inputWriter = input.map {
            ProcessInputWriter(channel: $0, label: "io.github.stephenlclarke.devcontainer.container-input")
        }
        let state = AppleContainerAttachmentState(requiresJournal: outputCapture != nil)
        attachments = state
        capturePreparation = outputCapture.map { factory in
            Task {
                let journal = try await factory()
                try state.installJournal(journal)
            }
        }
        outputMonitor = ProcessPipeMonitor(
            handle: output.fileHandleForReading, channel: .standardOutput,
            callbacks: .init(
                onEOF: {
                    diagnostics.sourceEOF(.standardOutput)
                    state.endSource(.standardOutput)
                },
                onFrame: {
                    diagnostics.outputFrame($0)
                    state.publish($0)
                }, onError: { state.complete(.failure($0)) }
            )
        )
        errorMonitor = error.map {
            ProcessPipeMonitor(
                handle: $0.fileHandleForReading, channel: .standardError,
                callbacks: .init(
                    onEOF: {
                        diagnostics.sourceEOF(.standardError)
                        state.endSource(.standardError)
                    },
                    onFrame: {
                        diagnostics.outputFrame($0)
                        state.publish($0)
                    }, onError: { state.complete(.failure($0)) }
                )
            )
        }
    }

    deinit {
        diagnostics.cancellationSummary(pendingWrites: inputWriter?.pendingWriteCount ?? 0)
        capturePreparation?.cancel()
        exits.complete(.failure(CancellationError()))
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

    var hasPendingOwnedStart: Bool {
        lock.withLock {
            startAttempted && startedAt == nil && process != nil && !controlsClosed
                && !nativeExitObserved && !processExited
        }
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
        try attachments.requireCaptureReady()
        return try lock.withLock {
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

    func didStart(
        at startedAt: Date,
        validate: @escaping @Sendable () async throws -> Void = {
            // Callers without generation tracking have nothing to validate.
        }
    ) async throws {
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

    func prepareOutputCapture() async throws {
        do {
            try await capturePreparation?.value
        } catch {
            await fail(error)
            throw error
        }
        // A cancelled reader does not cancel the generation's shared capture.
        try Task.checkCancellation()
    }

    func prepareAttachment(
        history: Bool, live: Bool, context: RuntimeRequestContext
    ) throws -> RuntimeContainerAttachment {
        let (saved, subscription) = try attachments.prepare(history: history, live: live, context: context)
        return RuntimeContainerAttachment(
            history: saved, session: subscription.map { AppleContainerAttachment(owner: self, subscription: $0) }
        )
    }

    func prepareExitWait(snapshot: ContainerSnapshot) -> (any RuntimeContainerExitWait)? {
        exits.subscribe(snapshot: snapshot)
    }

    func finish(exitCode: Int32, drainTimeout: Duration = .seconds(30)) async {
        diagnostics.processExit(exitCode)
        lock.withLock { nativeExitObserved = true }
        exits.complete(.success(exitCode))
        let controls = sealControls()
        closeTransferredEnds()
        let timeout = Task {
            do {
                let clock = ContinuousClock()
                try await clock.sleep(until: clock.now.advanced(by: drainTimeout))
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
        if lock.withLock({ drainTimedOut }) {
            diagnostics.drainTimedOut()
            attachments.complete(.failure(DevContainerError(
                .runtimeUnavailable, message: "Container output did not reach EOF after process exit"
            )))
        } else {
            diagnostics.drainCompleted()
            attachments.complete(.success(exitCode))
        }
        // Replacement also waits for durable completion publication. Otherwise
        // a new generation could cancel this writer while it is still closing.
        lock.withLock { processExited = true }
    }

    func cancel() {
        diagnostics.cancellationSummary(pendingWrites: inputWriter?.pendingWriteCount ?? 0)
        exits.complete(.failure(CancellationError()))
        _ = sealControls()
        closeTransferredEnds()
        inputWriter?.cancel()
        outputMonitor.cancel()
        errorMonitor?.cancel()
        attachments.complete(.failure(CancellationError()))
    }

    func shutdown() async {
        cancel()
        _ = try? await capturePreparation?.value
        await outputMonitor.waitForCompletion()
        await errorMonitor?.waitForCompletion()
        await inputWriter?.waitForCompletion()
        _ = try? await lock.withLock { resizeTail }?.value
    }

    func fail(_ error: any Error) async {
        exits.complete(.failure(error))
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
        diagnostics.inputSubmitted(data.count, pendingWrites: inputWriter.pendingWriteCount)
        do {
            try await subscription.perform {
                try await inputWriter.write(data, cancelWriterOnCancellation: false) { !state.isActive(attachment) }
            }
            diagnostics.inputCompleted(data.count, pendingWrites: inputWriter.pendingWriteCount)
        } catch {
            diagnostics.inputFailed(data.count, pendingWrites: inputWriter.pendingWriteCount)
            throw error
        }
    }

    fileprivate func closeInput(attachment _: UUID) async throws {
        diagnostics.inputEOFRequested(pendingWrites: inputWriter?.pendingWriteCount ?? 0)
        // A failed output subscriber still owes StdinOnce EOF. Its capability
        // names this retained generation, never a replacement using the same ID.
        // Global stdin EOF must also interrupt another attachment's blocked
        // write. A queue-only close could sit behind that write indefinitely.
        inputWriter?.cancel()
        await inputWriter?.waitForCompletion()
        diagnostics.inputEOFCompleted(pendingWrites: inputWriter?.pendingWriteCount ?? 0)
    }

    fileprivate func resize(width: UInt16, height: UInt16, attachment: UUID) async throws {
        try attachments.requireActive(attachment)
        try await resize(width: width, height: height)
    }

    func resize(width: UInt16, height: UInt16) async throws {
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

/// Opt-in, payload-free counters for isolating native init attachment stalls.
final class AppleContainerIODiagnostics: @unchecked Sendable {
    typealias TraceWriter = @Sendable (Data) -> Void

    private let enabled: Bool
    private let traceWriter: TraceWriter
    private let generation = UUID().uuidString.lowercased()
    private let startedAt = DispatchTime.now().uptimeNanoseconds
    private let lock = NSLock()
    private let writeLock = NSLock()
    private let maximumProgressEvents = 256
    private let bytesPerMebibyte = 1024 * 1024
    private var progressEvents = 0
    private var inputSubmittedBytes = 0
    private var inputCompletedBytes = 0
    private var inputFailedBytes = 0
    private var lastReportedInputSubmittedMiB = 0
    private var lastReportedInputCompletedMiB = 0
    private var outputBytes: [RuntimeIOChannel: Int] = [:]
    private var lastReportedOutputMiB: [RuntimeIOChannel: Int] = [:]
    private var outputEOFChannels: Set<RuntimeIOChannel> = []
    private var nativeExitCode: Int32?
    private var terminalDrainRecorded = false
    private var cancellationSummaryRecorded = false

    init(
        enabled: Bool = ProcessInfo.processInfo.environment["DEVCONTAINER_TRACE_INIT_IO"] == "1",
        traceWriter: TraceWriter? = nil
    ) {
        self.enabled = enabled
        self.traceWriter = traceWriter ?? { data in
            try? FileHandle.standardError.write(contentsOf: data)
        }
    }

    func inputSubmitted(_ bytes: Int, pendingWrites: @autoclosure () -> Int) {
        guard enabled else { return }
        let total = lock.withLock { () -> Int? in
            inputSubmittedBytes += bytes
            let mebibytes = inputSubmittedBytes / bytesPerMebibyte
            guard mebibytes > lastReportedInputSubmittedMiB else { return nil }
            lastReportedInputSubmittedMiB = mebibytes
            return inputSubmittedBytes
        }
        if let total {
            recordProgress("input-write-submitted", "totalBytes=\(total) pending=\(pendingWrites())")
        }
    }

    func inputCompleted(_ bytes: Int, pendingWrites: @autoclosure () -> Int) {
        guard enabled else { return }
        let total = lock.withLock { () -> Int? in
            inputCompletedBytes += bytes
            let mebibytes = inputCompletedBytes / bytesPerMebibyte
            guard mebibytes > lastReportedInputCompletedMiB else { return nil }
            lastReportedInputCompletedMiB = mebibytes
            return inputCompletedBytes
        }
        if let total {
            recordProgress("input-write-completed", "totalBytes=\(total) pending=\(pendingWrites())")
        }
    }

    func inputFailed(_ bytes: Int, pendingWrites: @autoclosure () -> Int) {
        guard enabled else { return }
        let total = lock.withLock {
            inputFailedBytes += bytes
            return inputFailedBytes
        }
        recordProgress("input-write-failed", "bytes=\(bytes) total=\(total) pending=\(pendingWrites())")
    }

    func inputEOFRequested(pendingWrites: @autoclosure () -> Int) {
        guard enabled else { return }
        recordProgress("input-eof-requested", "pending=\(pendingWrites())")
    }

    func inputEOFCompleted(pendingWrites: @autoclosure () -> Int) {
        guard enabled else { return }
        let totals = lock.withLock { (inputSubmittedBytes, inputCompletedBytes, inputFailedBytes) }
        let pending = pendingWrites()
        recordFinal("input-eof-completed", "submitted=\(totals.0) completed=\(totals.1) failed=\(totals.2) pending=\(pending)")
    }

    func outputFrame(_ frame: RuntimeIOFrame) {
        guard enabled else { return }
        let channel = frame.channel
        let progress = lock.withLock { () -> (Int, Int)? in
            let total = (outputBytes[channel] ?? 0) + frame.data.count
            outputBytes[channel] = total
            let mebibytes = total / (1024 * 1024)
            guard mebibytes > (lastReportedOutputMiB[channel] ?? 0) else { return nil }
            lastReportedOutputMiB[channel] = mebibytes
            return (total, mebibytes)
        }
        if let (total, mebibytes) = progress {
            recordProgress("output-progress", "channel=\(channel) bytes=\(total) mebibytes=\(mebibytes)")
        }
    }

    func sourceEOF(_ channel: RuntimeIOChannel) {
        guard enabled else { return }
        let total = lock.withLock { () -> Int in
            outputEOFChannels.insert(channel)
            return outputBytes[channel] ?? 0
        }
        recordFinal("output-eof", "channel=\(channel) bytes=\(total)")
    }

    func processExit(_ exitCode: Int32) {
        guard enabled else { return }
        lock.withLock { nativeExitCode = exitCode }
        recordFinal("process-exit", "code=\(exitCode)")
    }

    func drainTimedOut() {
        recordDrain("output-drain-timeout")
    }

    func drainCompleted() {
        recordDrain("output-drain-completed")
    }

    func cancellationSummary(pendingWrites: @autoclosure () -> Int) {
        guard enabled else { return }
        let totals = lock.withLock { () -> (Int, Int, Int, Int, Int, Bool, Bool, Int32?)? in
            guard !terminalDrainRecorded, !cancellationSummaryRecorded else { return nil }
            cancellationSummaryRecorded = true
            return (
                inputSubmittedBytes,
                inputCompletedBytes,
                inputFailedBytes,
                outputBytes[.standardOutput] ?? 0,
                outputBytes[.standardError] ?? 0,
                outputEOFChannels.contains(.standardOutput),
                outputEOFChannels.contains(.standardError),
                nativeExitCode
            )
        }
        guard let totals else { return }
        let pending = pendingWrites()
        let exitCode = totals.7.map { String($0) } ?? "unknown"
        recordFinal(
            "io-cancelled-summary",
            "submitted=\(totals.0) completed=\(totals.1) failed=\(totals.2) stdout=\(totals.3) stderr=\(totals.4) stdoutEOF=\(totals.5) stderrEOF=\(totals.6) exit=\(exitCode) pending=\(pending)"
        )
    }

    private func recordDrain(_ event: String) {
        guard enabled else { return }
        let totals = lock.withLock {
            terminalDrainRecorded = true
            return (
                inputSubmittedBytes,
                inputCompletedBytes,
                inputFailedBytes,
                outputBytes[.standardOutput] ?? 0,
                outputBytes[.standardError] ?? 0,
                outputEOFChannels.contains(.standardOutput),
                outputEOFChannels.contains(.standardError),
                nativeExitCode
            )
        }
        let exitCode = totals.7.map { String($0) } ?? "unknown"
        recordFinal(event, "submitted=\(totals.0) completed=\(totals.1) failed=\(totals.2) stdout=\(totals.3) stderr=\(totals.4) stdoutEOF=\(totals.5) stderrEOF=\(totals.6) exit=\(exitCode)")
    }

    private func recordProgress(_ event: String, _ details: @autoclosure () -> String) {
        guard enabled else { return }
        let permitted = lock.withLock { () -> Bool in
            guard progressEvents < maximumProgressEvents else { return false }
            progressEvents += 1
            return true
        }
        if permitted {
            writeLine(event, details())
        }
    }

    private func recordFinal(_ event: String, _ details: String) {
        guard enabled else { return }
        writeLine(event, details)
    }

    private func writeLine(_ event: String, _ details: String) {
        let elapsed = DispatchTime.now().uptimeNanoseconds - startedAt
        let suffix = details.isEmpty ? "" : " \(details)"
        let line = Data("devcontainer-engine: init-io trace: generation=\(generation) elapsedNS=\(elapsed) event=\(event)\(suffix)\n".utf8)
        writeLock.withLock { traceWriter(line) }
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
