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
import DequeModule
import DevContainerDockerAPI
import DevContainerModel
import DevContainerRuntimeSPI
import Dispatch
import Foundation
import Logging
import NIOCore
import NIOFoundationCompat
import NIOHTTP1
import NIOPosix

final class EngineServer: @unchecked Sendable {
    private let group: MultiThreadedEventLoopGroup
    private let router: DockerRouter
    private let socketPath: String
    private let logger: Logger
    private let limits: EngineServerLimits
    private let connections = EngineConnectionTracker()
    private let resourceBudget: EngineResourceBudget
    private var channel: Channel?
    private var lockFileDescriptor: Int32 = -1
    private var ownsSocket = false

    init(
        router: DockerRouter,
        socketPath: String,
        logger: Logger,
        limits: EngineServerLimits = .production
    ) {
        group = MultiThreadedEventLoopGroup(numberOfThreads: System.coreCount)
        self.router = router
        self.socketPath = socketPath
        self.logger = logger
        self.limits = limits
        resourceBudget = EngineResourceBudget(limits: limits)
    }

    func start() async throws {
        try prepareSocketDirectory()
        try acquireInstanceLock()
        do {
            try prepareSocketPath()
        } catch {
            releaseInstanceLock()
            throw error
        }
        let bootstrap = makeBootstrap()

        do {
            channel = try await bootstrap.bind(unixDomainSocketPath: socketPath).get()
            ownsSocket = true
        } catch {
            releaseInstanceLock()
            throw error
        }
        guard chmod(socketPath, S_IRUSR | S_IWUSR) == 0 else {
            try? await channel?.close()
            try? removeOwnedSocket()
            ownsSocket = false
            releaseInstanceLock()
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        logger.info(
            "Engine API listening",
            metadata: ["socket": .string(DiagnosticsRedactor.redact(socketPath))]
        )
    }

    private func makeBootstrap() -> ServerBootstrap {
        let router = router
        let logger = logger
        let connections = connections
        let resourceBudget = resourceBudget
        let limits = limits
        return ServerBootstrap(group: group)
            .serverChannelOption(ChannelOptions.backlog, value: 256)
            .serverChannelOption(ChannelOptions.socketOption(.so_reuseaddr), value: 1)
            .childChannelInitializer { channel in
                let upgradeState = DockerUpgradeState()
                let pipeline = DockerHTTPPipeline(
                    responseEncoder: HTTPResponseEncoder(),
                    requestDecoder: ByteToMessageHandler(
                        HTTPRequestDecoder(leftOverBytesStrategy: .forwardBytes)
                    ),
                    upgradeState: upgradeState,
                    inputCloseBarrier: DockerInputCloseBarrier(state: upgradeState)
                )
                let handler = DockerHTTPHandler(
                    router: router,
                    logger: logger,
                    connections: connections,
                    resourceBudget: resourceBudget,
                    pipeline: pipeline,
                    limits: limits
                )
                do {
                    try channel.pipeline.syncOperations.addHandler(pipeline.responseEncoder)
                    try channel.pipeline.syncOperations.addHandler(pipeline.inputCloseBarrier)
                    try channel.pipeline.syncOperations.addHandler(pipeline.requestDecoder)
                    try channel.pipeline.syncOperations.addHandler(handler)
                    return channel.eventLoop.makeSucceededVoidFuture()
                } catch {
                    return channel.eventLoop.makeFailedFuture(error)
                }
            }
            .childChannelOption(ChannelOptions.socketOption(.so_reuseaddr), value: 1)
            .childChannelOption(ChannelOptions.allowRemoteHalfClosure, value: true)
            .childChannelOption(ChannelOptions.maxMessagesPerRead, value: 16)
            .childChannelOption(ChannelOptions.recvAllocator, value: AdaptiveRecvByteBufferAllocator())
            .childChannelOption(
                ChannelOptions.writeBufferWaterMark,
                value: ChannelOptions.Types.WriteBufferWaterMark(
                    low: 256 * 1024,
                    high: 1024 * 1024
                )
            )
    }

    func wait() async throws {
        guard let channel else {
            throw EngineServerError.notStarted
        }
        try await channel.closeFuture.get()
    }

    var activeConnectionCount: Int {
        connections.count
    }

    func shutdown() async throws {
        defer { releaseInstanceLock() }
        if let channel {
            try await channel.close()
        }
        try await group.shutdownGracefully()
        if ownsSocket {
            try removeOwnedSocket()
            ownsSocket = false
        }
    }

    private func prepareSocketDirectory() throws {
        let url = URL(fileURLWithPath: socketPath)
        let parent = url.deletingLastPathComponent()
        try FileManager.default.createDirectory(
            at: parent,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        var parentStatus = stat()
        guard
            lstat(parent.path, &parentStatus) == 0,
            parentStatus.st_uid == getuid(),
            parentStatus.st_mode & S_IFMT == S_IFDIR,
            parentStatus.st_mode & (S_IWGRP | S_IWOTH) == 0
        else {
            throw EngineServerError.unsafeSocketDirectory(parent.path)
        }
        guard chmod(parent.path, S_IRWXU) == 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
    }

    private func prepareSocketPath() throws {
        var status = stat()
        if lstat(socketPath, &status) == 0 {
            guard
                status.st_uid == getuid(),
                status.st_mode & S_IFMT == S_IFSOCK
            else {
                throw EngineServerError.unsafeExistingSocket(socketPath)
            }
            try FileManager.default.removeItem(atPath: socketPath)
        } else if errno != ENOENT {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
    }

    private func acquireInstanceLock() throws {
        let lockPath = socketPath + ".lock"
        let descriptor = open(
            lockPath,
            O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW,
            S_IRUSR | S_IWUSR
        )
        guard descriptor >= 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        var status = stat()
        guard
            fstat(descriptor, &status) == 0,
            status.st_uid == getuid(),
            status.st_mode & S_IFMT == S_IFREG
        else {
            close(descriptor)
            throw EngineServerError.unsafeInstanceLock(lockPath)
        }
        guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
            let code = errno
            close(descriptor)
            if code == EWOULDBLOCK {
                throw EngineServerError.alreadyRunning
            }
            throw POSIXError(POSIXErrorCode(rawValue: code) ?? .EIO)
        }
        lockFileDescriptor = descriptor
    }

    private func releaseInstanceLock() {
        guard lockFileDescriptor >= 0 else {
            return
        }
        _ = flock(lockFileDescriptor, LOCK_UN)
        close(lockFileDescriptor)
        lockFileDescriptor = -1
    }

    private func removeOwnedSocket() throws {
        var status = stat()
        guard lstat(socketPath, &status) == 0 else {
            if errno == ENOENT {
                return
            }
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        guard status.st_uid == getuid(), status.st_mode & S_IFMT == S_IFSOCK else {
            throw EngineServerError.unsafeExistingSocket(socketPath)
        }
        try FileManager.default.removeItem(atPath: socketPath)
    }
}

enum EngineServerError: Error, CustomStringConvertible {
    case notStarted
    case alreadyRunning
    case unsafeExistingSocket(String)
    case unsafeInstanceLock(String)
    case unsafeSocketDirectory(String)

    var description: String {
        switch self {
        case .notStarted:
            "Engine server is not started"
        case .alreadyRunning:
            "another devcontainer engine owns this socket"
        case let .unsafeExistingSocket(path):
            "refusing to replace non-owned or non-socket path at \(path)"
        case let .unsafeInstanceLock(path):
            "instance lock is not a current-user regular file: \(path)"
        case let .unsafeSocketDirectory(path):
            "socket directory must be owned by the current user and not group/world writable: \(path)"
        }
    }
}

private final class DockerHTTPHandler:
    ChannelInboundHandler,
    RemovableChannelHandler,
    @unchecked Sendable
{
    typealias InboundIn = HTTPServerRequestPart
    typealias OutboundOut = HTTPServerResponsePart

    private let router: DockerRouter
    private let logger: Logger
    private let connections: EngineConnectionTracker
    private let resourceBudget: EngineResourceBudget
    private let responseEncoder: HTTPResponseEncoder
    private let requestDecoder: ByteToMessageHandler<HTTPRequestDecoder>
    private let upgradeState: DockerUpgradeState
    private let inputCloseBarrier: DockerInputCloseBarrier
    private let limits: EngineServerLimits
    private var requestHead: HTTPRequestHead?
    private var requestBody = ByteBuffer()
    private var activeRequestHead: HTTPRequestHead?
    private var activeRequestBodyBytes = 0
    private var activeRequestMeasuredBodyBytes = 0
    private var activeRequestStartedNanoseconds: UInt64 = 0
    private var activeResponseStatus = 0
    private var activeResponseBytes = 0
    private var activeResponseKind = "none"
    private var activeCorrelationID = "unassigned"
    private var activeObservationLogged = false
    private var pendingRequests = Deque<DockerHTTPPendingRequest>()
    private var retainedRequestBodyBytes = 0
    private var responseInFlight = false
    private var closeAfterResponse = false
    private var activeRouterTask: Task<DockerHTTPResponse, Never>?
    private var responseStreamTask: Task<Void, Never>?
    private var registeredConnection = false
    private var processReservedBodyBytes = 0

    init(
        router: DockerRouter,
        logger: Logger,
        connections: EngineConnectionTracker,
        resourceBudget: EngineResourceBudget,
        pipeline: DockerHTTPPipeline,
        limits: EngineServerLimits
    ) {
        self.router = router
        self.logger = logger
        self.connections = connections
        self.resourceBudget = resourceBudget
        responseEncoder = pipeline.responseEncoder
        requestDecoder = pipeline.requestDecoder
        upgradeState = pipeline.upgradeState
        inputCloseBarrier = pipeline.inputCloseBarrier
        self.limits = limits
    }

    func channelActive(context: ChannelHandlerContext) {
        guard resourceBudget.openConnection() else {
            context.close(promise: nil)
            return
        }
        registeredConnection = true
        connections.opened()
        context.fireChannelActive()
    }

    func channelInactive(context: ChannelHandlerContext) {
        activeRouterTask?.cancel()
        activeRouterTask = nil
        responseStreamTask?.cancel()
        responseStreamTask = nil
        if processReservedBodyBytes > 0 {
            resourceBudget.releaseBodyBytes(processReservedBodyBytes)
            processReservedBodyBytes = 0
        }
        recordActiveRequest(termination: "disconnect")
        activeRequestBodyBytes = 0
        activeRequestMeasuredBodyBytes = 0
        retainedRequestBodyBytes = 0
        requestBody.clear()
        pendingRequests.removeAll()
        if registeredConnection {
            releaseRegisteredConnection()
        }
        context.fireChannelInactive()
    }

    private func releaseRegisteredConnection() {
        guard registeredConnection else { return }
        registeredConnection = false
        resourceBudget.closeConnection()
        connections.closed()
    }

    func userInboundEventTriggered(
        context: ChannelHandlerContext,
        event: Any
    ) {
        if let channelEvent = event as? ChannelEvent,
           channelEvent == .inputClosed
        {
            if !upgradeState.upgradeCandidate {
                if responseInFlight || !pendingRequests.isEmpty
                    || requestHead != nil
                {
                    closeAfterResponse = true
                } else {
                    context.close(promise: nil)
                }
                return
            }
            if responseInFlight || !pendingRequests.isEmpty {
                closeAfterResponse = true
            } else {
                context.close(promise: nil)
            }
            return
        }
        context.fireUserInboundEventTriggered(event)
    }

    func channelRead(context: ChannelHandlerContext, data: NIOAny) {
        switch unwrapInboundIn(data) {
        case let .head(head):
            guard requestHead == nil else {
                context.close(promise: nil)
                return
            }
            if responseInFlight,
               activeRequestHead?.headers.contains(name: "Upgrade") == true
            {
                // The active response is about to replace the HTTP pipeline
                // with a raw Docker stream, so no later HTTP request can be
                // represented safely on this connection.
                context.close(promise: nil)
                return
            }
            if head.headers.contains(name: "Upgrade"),
               responseInFlight || !pendingRequests.isEmpty
            {
                // An HTTP upgrade consumes the connection. It cannot be queued
                // behind or ahead of another HTTP response safely.
                context.close(promise: nil)
                return
            }
            upgradeState.beginRequest(head)
            requestHead = head
            requestBody.clear()
        case var .body(buffer):
            let additionalBytes = buffer.readableBytes
            guard canBufferRequestBody(additionalBytes),
                  resourceBudget.reserveBodyBytes(additionalBytes)
            else {
                guard !responseInFlight, pendingRequests.isEmpty else {
                    // A standalone error response would overtake an earlier
                    // pipelined response, so reject this connection instead.
                    context.close(promise: nil)
                    return
                }
                writeError(
                    context: context,
                    status: .payloadTooLarge,
                    message: "request body exceeds the configured buffering limit"
                )
                context.close(promise: nil)
                return
            }
            processReservedBodyBytes += additionalBytes
            requestBody.writeBuffer(&buffer)
        case .end:
            enqueueRequest(context: context)
        }
    }

    func errorCaught(context: ChannelHandlerContext, error: any Error) {
        logger.error(
            "Engine connection failed",
            metadata: [
                "error": .string(
                    DiagnosticsRedactor.redact(String(describing: error))
                )
            ]
        )
        context.close(promise: nil)
    }

    private func enqueueRequest(context: ChannelHandlerContext) {
        guard let head = requestHead else {
            context.close(promise: nil)
            return
        }
        guard pendingRequests.count < limits.maximumPendingRequests else {
            context.close(promise: nil)
            return
        }
        let bodyBytes = requestBody.readableBytes
        let body = requestBody.readData(
            length: bodyBytes,
            byteTransferStrategy: .noCopy
        ) ?? Data()
        requestBody = ByteBuffer()
        retainedRequestBodyBytes += body.count
        let headers = Dictionary(
            head.headers.map { ($0.name, $0.value) },
            uniquingKeysWith: { _, latest in latest }
        )
        let request = DockerHTTPMethod(rawValue: head.method.rawValue).map {
            DockerHTTPRequest(
                method: $0,
                target: head.uri,
                headers: headers,
                body: body
            )
        }
        requestHead = nil
        pendingRequests.append(
            DockerHTTPPendingRequest(
                head: head,
                request: request,
                bodyBytes: body.count
            )
        )
        processNextRequest(context: context)
    }

    private func canBufferRequestBody(_ additionalBytes: Int) -> Bool {
        let (requestBytes, requestOverflow) = requestBody.readableBytes
            .addingReportingOverflow(additionalBytes)
        guard
            !requestOverflow,
            requestBytes <= limits.maximumRequestBodyBytes
        else {
            return false
        }
        let (totalBytes, totalOverflow) = retainedRequestBodyBytes
            .addingReportingOverflow(requestBytes)
        return !totalOverflow
            && totalBytes <= limits.maximumBufferedRequestBodyBytes
    }

    // swiftlint:disable:next function_body_length
    private func processNextRequest(context: ChannelHandlerContext) {
        guard !responseInFlight, let pending = pendingRequests.popFirst() else {
            return
        }
        responseInFlight = true
        activeRequestHead = pending.head
        activeRequestBodyBytes = pending.bodyBytes
        activeRequestMeasuredBodyBytes = pending.bodyBytes
        activeRequestStartedNanoseconds = DispatchTime.now().uptimeNanoseconds
        activeResponseStatus = 0
        activeResponseBytes = 0
        activeResponseKind = "none"
        activeCorrelationID = "unassigned"
        activeObservationLogged = false
        upgradeState.beginRequest(pending.head)
        guard let request = pending.request else {
            releaseActiveRequestBody()
            writeError(
                context: context,
                status: .methodNotAllowed,
                message: "unsupported HTTP method"
            )
            return
        }
        let promise = context.eventLoop.makePromise(of: DockerHTTPResponse.self)
        let sendableContext = SendableChannelHandlerContext(context)
        let task = Task {
            await self.router.respond(to: request)
        }
        activeRouterTask = task
        promise.completeWithTask {
            await task.value
        }
        promise.futureResult.whenComplete { result in
            let context = sendableContext.value
            self.activeRouterTask = nil
            self.releaseActiveRequestBody()
            switch result {
            case let .success(response):
                self.activeResponseStatus = response.status
                self.activeCorrelationID =
                    response.headers["X-Request-ID"] ?? "unassigned"
                self.write(response, context: context)
            case let .failure(error):
                self.activeResponseStatus = 500
                self.writeError(
                    context: context,
                    status: .internalServerError,
                    message: "Engine request failed: \(error)"
                )
            }
        }
    }

    private func releaseActiveRequestBody() {
        retainedRequestBodyBytes -= activeRequestBodyBytes
        resourceBudget.releaseBodyBytes(activeRequestBodyBytes)
        processReservedBodyBytes -= activeRequestBodyBytes
        activeRequestBodyBytes = 0
    }

    private func write(_ response: DockerHTTPResponse, context: ChannelHandlerContext) {
        closeAfterResponse = closeAfterResponse || upgradeState.inputClosed
        var headers = HTTPHeaders(response.headers.map { ($0.key, $0.value) })
        let status = HTTPResponseStatus(statusCode: response.status)
        switch response.body {
        case let .bytes(data):
            activeResponseKind = "bytes"
            writeBytes(data, headers: &headers, status: status, context: context)
        case let .stream(stream):
            activeResponseKind = "stream"
            writeStream(stream, headers: &headers, status: status, context: context)
        case let .hijack(session, terminal):
            activeResponseKind = "hijack"
            writeHijack(
                session: session,
                terminal: terminal,
                headers: &headers,
                context: context
            )
        }
    }

    private func writeBytes(
        _ data: Data,
        headers: inout HTTPHeaders,
        status: HTTPResponseStatus,
        context: ChannelHandlerContext
    ) {
        activeResponseBytes = data.count
        headers.replaceOrAdd(name: "Content-Length", value: String(data.count))
        context.write(
            wrapOutboundOut(
                .head(HTTPResponseHead(version: .http1_1, status: status, headers: headers))
            ),
            promise: nil
        )
        if !data.isEmpty {
            var buffer = context.channel.allocator.buffer(capacity: data.count)
            buffer.writeBytes(data)
            context.write(wrapOutboundOut(.body(.byteBuffer(buffer))), promise: nil)
        }
        let promise = context.eventLoop.makePromise(of: Void.self)
        context.writeAndFlush(
            wrapOutboundOut(.end(nil)),
            promise: promise
        )
        finishResponse(promise.futureResult, context: context)
    }

    private func writeStream(
        _ stream: AsyncThrowingStream<Data, any Error>,
        headers: inout HTTPHeaders,
        status: HTTPResponseStatus,
        context: ChannelHandlerContext
    ) {
        headers.replaceOrAdd(name: "Transfer-Encoding", value: "chunked")
        context.writeAndFlush(
            wrapOutboundOut(
                .head(HTTPResponseHead(version: .http1_1, status: status, headers: headers))
            ),
            promise: nil
        )
        streamBody(stream, context: context)
    }

    private func writeHijack(
        session: any RuntimeProcessSession,
        terminal: Bool,
        headers: inout HTTPHeaders,
        context: ChannelHandlerContext
    ) {
        let requestedUpgrade = activeRequestHead?.headers.contains(name: "Upgrade") ?? false
        logger.debug(
            "Engine connection takeover requested",
            metadata: [
                "request-target": .string(
                    DiagnosticsRedactor.redact(activeRequestHead?.uri ?? "unknown")
                ),
                "upgrade": .stringConvertible(requestedUpgrade)
            ]
        )
        if !requestedUpgrade {
            headers.replaceOrAdd(name: "Connection", value: "close")
            headers.remove(name: "Upgrade")
            // HTTP/1.0 makes connection close delimit the raw Docker bytes.
            headers.remove(name: "Transfer-Encoding")
            headers.remove(name: "Content-Length")
        }
        let headPromise = context.eventLoop.makePromise(of: Void.self)
        context.writeAndFlush(
            wrapOutboundOut(
                .head(
                    HTTPResponseHead(
                        version: requestedUpgrade ? .http1_1 : .http1_0,
                        status: requestedUpgrade ? .switchingProtocols : .ok,
                        headers: headers
                    )
                )
            ),
            promise: headPromise
        )
        let rawHandler = DockerRawStreamHandler(
            session: session,
            terminal: terminal,
            logger: logger,
            onClose: releaseRegisteredConnection
        )
        recordActiveRequest(termination: "hijack")
        let sendableContext = SendableChannelHandlerContext(context)
        headPromise.futureResult.whenComplete { result in
            self.completeHijack(
                result,
                handler: rawHandler,
                context: sendableContext.value
            )
        }
    }

    private func completeHijack(
        _ result: Result<Void, any Error>,
        handler: DockerRawStreamHandler,
        context: ChannelHandlerContext
    ) {
        do {
            try result.get()
            try context.pipeline.syncOperations.addHandler(handler)
            let pipeline = context.pipeline
            let sendableContext = SendableChannelHandlerContext(context)
            pipeline.syncOperations.removeHandler(self)
                .flatMap {
                    pipeline.syncOperations.removeHandler(self.requestDecoder)
                }
                .flatMap {
                    pipeline.syncOperations.removeHandler(self.responseEncoder)
                }
                .flatMap {
                    pipeline.syncOperations.removeHandler(self.inputCloseBarrier)
                }
                .whenComplete { removal in
                    let context = sendableContext.value
                    switch removal {
                    case .success:
                        handler.start(channel: context.channel)
                        if self.closeAfterResponse || self.upgradeState.inputClosed {
                            handler.closeInput()
                        }
                    case let .failure(error):
                        self.logger.error(
                            "Engine connection takeover failed",
                            metadata: [
                                "error": .string(
                                    DiagnosticsRedactor.redact(
                                        String(describing: error)
                                    )
                                )
                            ]
                        )
                        context.close(promise: nil)
                    }
                }
        } catch {
            logger.error(
                "Engine connection takeover failed",
                metadata: [
                    "error": .string(
                        DiagnosticsRedactor.redact(String(describing: error))
                    )
                ]
            )
            context.close(promise: nil)
        }
    }

    // Stream ownership includes awaited writes, completion, cancellation, and
    // terminal error closure.
    // swiftlint:disable:next function_body_length
    private func streamBody(
        _ stream: AsyncThrowingStream<Data, any Error>,
        context: ChannelHandlerContext
    ) {
        let sendableContext = SendableChannelHandlerContext(context)
        responseStreamTask = Task {
            do {
                for try await data in stream {
                    try Task.checkCancellation()
                    try await withCheckedThrowingContinuation { continuation in
                        let bytes = data
                        sendableContext.value.eventLoop.execute {
                            let context = sendableContext.value
                            self.activeResponseBytes += bytes.count
                            var buffer = context.channel.allocator.buffer(capacity: bytes.count)
                            buffer.writeBytes(bytes)
                            let promise = context.eventLoop.makePromise(of: Void.self)
                            context.writeAndFlush(
                                self.wrapOutboundOut(.body(.byteBuffer(buffer))),
                                promise: promise
                            )
                            promise.futureResult.whenComplete {
                                continuation.resume(with: $0)
                            }
                        }
                    }
                }
                try Task.checkCancellation()
                try await withCheckedThrowingContinuation { continuation in
                    sendableContext.value.eventLoop.execute {
                        let context = sendableContext.value
                        let promise = context.eventLoop.makePromise(of: Void.self)
                        context.writeAndFlush(
                            self.wrapOutboundOut(.end(nil)),
                            promise: promise
                        )
                        promise.futureResult.whenComplete {
                            continuation.resume(with: $0)
                        }
                    }
                }
                sendableContext.value.eventLoop.execute {
                    let context = sendableContext.value
                    self.responseStreamTask = nil
                    self.finishResponse(
                        context.eventLoop.makeSucceededVoidFuture(),
                        context: context
                    )
                }
            } catch is CancellationError {
                // Channel teardown owns cancellation and closure.
            } catch {
                self.logger.error(
                    "Engine stream failed",
                    metadata: [
                        "error": .string(
                            DiagnosticsRedactor.redact(String(describing: error))
                        )
                    ]
                )
                sendableContext.value.eventLoop.execute {
                    let context = sendableContext.value
                    self.responseStreamTask = nil
                    context.close(promise: nil)
                }
            }
        }
    }

    private func writeError(
        context: ChannelHandlerContext,
        status: HTTPResponseStatus,
        message: String
    ) {
        let data = EngineResponseEncoding.dockerError(message)
        activeResponseStatus = Int(status.code)
        activeResponseKind = "error"
        activeResponseBytes = data.count
        var headers = HTTPHeaders()
        headers.add(name: "Content-Type", value: "application/json")
        headers.add(name: "Content-Length", value: String(data.count))
        context.write(
            wrapOutboundOut(
                .head(HTTPResponseHead(version: .http1_1, status: status, headers: headers))
            ),
            promise: nil
        )
        var buffer = context.channel.allocator.buffer(capacity: data.count)
        buffer.writeBytes(data)
        context.write(wrapOutboundOut(.body(.byteBuffer(buffer))), promise: nil)
        let promise = context.eventLoop.makePromise(of: Void.self)
        context.writeAndFlush(
            wrapOutboundOut(.end(nil)),
            promise: promise
        )
        finishResponse(promise.futureResult, context: context)
    }

    private func finishResponse(
        _ future: EventLoopFuture<Void>,
        context: ChannelHandlerContext
    ) {
        let sendableContext = SendableChannelHandlerContext(context)
        future.whenComplete { _ in
            self.recordActiveRequest(termination: "completed")
            self.responseInFlight = false
            self.activeRequestHead = nil
            if self.closeAfterResponse, self.pendingRequests.isEmpty {
                sendableContext.value.close(promise: nil)
            } else {
                self.processNextRequest(context: sendableContext.value)
            }
        }
    }

    private func recordActiveRequest(termination: String) {
        guard responseInFlight, !activeObservationLogged else {
            return
        }
        activeObservationLogged = true
        let elapsedNanoseconds =
            DispatchTime.now().uptimeNanoseconds
                .subtractingReportingOverflow(activeRequestStartedNanoseconds)
                .partialValue
        let elapsedMilliseconds = Double(elapsedNanoseconds) / 1_000_000
        logger.info(
            "Engine request completed",
            metadata: [
                "bytes-in": .stringConvertible(activeRequestMeasuredBodyBytes),
                "bytes-out": .stringConvertible(activeResponseBytes),
                "correlation": .string(
                    DiagnosticsRedactor.redact(activeCorrelationID)
                ),
                "duration-ms": .string(String(format: "%.3f", elapsedMilliseconds)),
                "endpoint": .string(
                    DiagnosticsRedactor.redact(activeRequestHead?.uri ?? "unknown")
                ),
                "response-kind": .string(activeResponseKind),
                "status": .stringConvertible(activeResponseStatus),
                "termination": .string(termination)
            ]
        )
    }
}

enum EngineResponseEncoding {
    static func dockerError(_ message: String) -> Data {
        (try? DockerJSON.encoder.encode(DockerErrorEnvelope(message: message)))
            ?? Data(#"{"message":"internal server error"}"#.utf8)
    }
}

private final class DockerRawStreamHandler: ChannelInboundHandler, @unchecked Sendable {
    typealias InboundIn = ByteBuffer

    private let session: any RuntimeProcessSession
    private let terminal: Bool
    private let logger: Logger
    private let onClose: @Sendable () -> Void
    private let inputPump: OrderedRuntimeInputPump
    private let cancellation: RuntimeSessionCancellation
    private var outputTask: Task<Void, Never>?
    private let stateLock = NSLock()
    private var finishedNormally = false

    init(
        session: any RuntimeProcessSession,
        terminal: Bool,
        logger: Logger,
        onClose: @escaping @Sendable () -> Void
    ) {
        self.session = session
        self.terminal = terminal
        self.logger = logger
        self.onClose = onClose
        let cancellation = RuntimeSessionCancellation(session: session)
        self.cancellation = cancellation
        inputPump = OrderedRuntimeInputPump(
            session: session,
            onFailure: cancellation.cancel
        )
    }

    func start(channel: any Channel) {
        outputTask = Task {
            do {
                for try await frame in session.frames {
                    let data = DockerStreamFraming.encode(frame, terminal: terminal)
                    var buffer = channel.allocator.buffer(capacity: data.count)
                    buffer.writeBytes(data)
                    try await channel.writeAndFlush(buffer).get()
                }
                _ = try await session.wait()
                stateLock.withLock {
                    finishedNormally = true
                }
                try await channel.close().get()
            } catch {
                cancellation.cancel()
                logger.error(
                    "Engine raw stream failed",
                    metadata: [
                        "error": .string(
                            DiagnosticsRedactor.redact(String(describing: error))
                        )
                    ]
                )
                channel.eventLoop.execute {
                    channel.close(promise: nil)
                }
            }
        }
    }

    func channelRead(context _: ChannelHandlerContext, data: NIOAny) {
        var buffer = unwrapInboundIn(data)
        let bytes = buffer.readBytes(length: buffer.readableBytes) ?? []
        guard !bytes.isEmpty else {
            return
        }
        inputPump.write(Data(bytes))
    }

    func closeInput() {
        inputPump.close()
    }

    func userInboundEventTriggered(context: ChannelHandlerContext, event: Any) {
        if let channelEvent = event as? ChannelEvent, channelEvent == .inputClosed {
            closeInput()
        }
        context.fireUserInboundEventTriggered(event)
    }

    func channelInactive(context: ChannelHandlerContext) {
        onClose()
        let shouldCancel = stateLock.withLock {
            !finishedNormally
        }
        if shouldCancel {
            outputTask?.cancel()
            inputPump.cancel()
            cancellation.cancel()
        } else {
            inputPump.finish()
        }
        context.fireChannelInactive()
    }
}
