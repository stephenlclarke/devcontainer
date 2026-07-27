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
import DevContainerDockerAPI
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Logging
import NIOCore
import NIOHTTP1
import NIOPosix

final class EngineServer: @unchecked Sendable {
    private let group: MultiThreadedEventLoopGroup
    private let router: DockerRouter
    private let socketPath: String
    private let logger: Logger
    private var channel: Channel?
    private var lockFileDescriptor: Int32 = -1
    private var ownsSocket = false

    init(router: DockerRouter, socketPath: String, logger: Logger) {
        group = MultiThreadedEventLoopGroup(numberOfThreads: System.coreCount)
        self.router = router
        self.socketPath = socketPath
        self.logger = logger
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
        let router = router
        let logger = logger
        let bootstrap = ServerBootstrap(group: group)
            .serverChannelOption(ChannelOptions.backlog, value: 256)
            .serverChannelOption(ChannelOptions.socketOption(.so_reuseaddr), value: 1)
            .childChannelInitializer { channel in
                let responseEncoder = HTTPResponseEncoder()
                let requestDecoder = ByteToMessageHandler(
                    HTTPRequestDecoder(leftOverBytesStrategy: .forwardBytes)
                )
                let handler = DockerHTTPHandler(
                    router: router,
                    logger: logger,
                    responseEncoder: responseEncoder,
                    requestDecoder: requestDecoder
                )
                do {
                    try channel.pipeline.syncOperations.addHandler(responseEncoder)
                    try channel.pipeline.syncOperations.addHandler(requestDecoder)
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
        logger.info("Engine API listening", metadata: ["socket": .string(socketPath)])
    }

    func wait() async throws {
        guard let channel else {
            throw EngineServerError.notStarted
        }
        try await channel.closeFuture.get()
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

    private static let maximumRequestBody = 1_073_741_824

    private let router: DockerRouter
    private let logger: Logger
    private let responseEncoder: HTTPResponseEncoder
    private let requestDecoder: ByteToMessageHandler<HTTPRequestDecoder>
    private var requestHead: HTTPRequestHead?
    private var requestBody = ByteBuffer()

    init(
        router: DockerRouter,
        logger: Logger,
        responseEncoder: HTTPResponseEncoder,
        requestDecoder: ByteToMessageHandler<HTTPRequestDecoder>
    ) {
        self.router = router
        self.logger = logger
        self.responseEncoder = responseEncoder
        self.requestDecoder = requestDecoder
    }

    func channelRead(context: ChannelHandlerContext, data: NIOAny) {
        switch unwrapInboundIn(data) {
        case let .head(head):
            requestHead = head
            requestBody.clear()
        case var .body(buffer):
            guard requestBody.readableBytes + buffer.readableBytes <= Self.maximumRequestBody else {
                writeError(
                    context: context,
                    status: .payloadTooLarge,
                    message: "request body exceeds the 1 GiB limit"
                )
                context.close(promise: nil)
                return
            }
            requestBody.writeBuffer(&buffer)
        case .end:
            handleRequest(context: context)
        }
    }

    func errorCaught(context: ChannelHandlerContext, error: any Error) {
        logger.error("Engine connection failed", metadata: ["error": .string(String(describing: error))])
        context.close(promise: nil)
    }

    private func handleRequest(context: ChannelHandlerContext) {
        guard let head = requestHead, let method = DockerHTTPMethod(rawValue: head.method.rawValue) else {
            writeError(context: context, status: .methodNotAllowed, message: "unsupported HTTP method")
            return
        }
        let body = Data(
            requestBody.getBytes(
                at: requestBody.readerIndex,
                length: requestBody.readableBytes
            ) ?? []
        )
        let headers = Dictionary(
            head.headers.map { ($0.name, $0.value) },
            uniquingKeysWith: { _, latest in latest }
        )
        let request = DockerHTTPRequest(
            method: method,
            target: head.uri,
            headers: headers,
            body: body
        )
        let promise = context.eventLoop.makePromise(of: DockerHTTPResponse.self)
        let sendableContext = SendableChannelHandlerContext(context)
        promise.completeWithTask {
            await self.router.respond(to: request)
        }
        promise.futureResult.whenComplete { result in
            let context = sendableContext.value
            switch result {
            case let .success(response):
                self.write(response, context: context)
            case let .failure(error):
                self.writeError(
                    context: context,
                    status: .internalServerError,
                    message: "Engine request failed: \(error)"
                )
            }
        }
    }

    private func write(_ response: DockerHTTPResponse, context: ChannelHandlerContext) {
        var headers = HTTPHeaders(response.headers.map { ($0.key, $0.value) })
        let status = HTTPResponseStatus(statusCode: response.status)
        switch response.body {
        case let .bytes(data):
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
            context.writeAndFlush(wrapOutboundOut(.end(nil)), promise: nil)
        case let .stream(stream):
            headers.replaceOrAdd(name: "Transfer-Encoding", value: "chunked")
            context.writeAndFlush(
                wrapOutboundOut(
                    .head(HTTPResponseHead(version: .http1_1, status: status, headers: headers))
                ),
                promise: nil
            )
            streamBody(stream, context: context)
        case let .hijack(session, terminal):
            let requestedUpgrade = requestHead?.headers.contains(name: "Upgrade") ?? false
            logger.debug(
                "Engine connection takeover requested",
                metadata: [
                    "request-target": .string(requestHead?.uri ?? "unknown"),
                    "upgrade": .stringConvertible(requestedUpgrade)
                ]
            )
            if !requestedUpgrade {
                headers.replaceOrAdd(name: "Connection", value: "close")
                headers.remove(name: "Upgrade")
                // HTTP/1.0 below makes the connection close delimit the raw
                // Docker multiplex bytes. An HTTP/1.1 encoder would otherwise
                // select chunked framing before it is removed from the
                // pipeline for connection takeover.
                headers.remove(name: "Transfer-Encoding")
                headers.remove(name: "Content-Length")
            }
            let hijackStatus: HTTPResponseStatus = requestedUpgrade ? .switchingProtocols : .ok
            let headPromise = context.eventLoop.makePromise(of: Void.self)
            context.writeAndFlush(
                wrapOutboundOut(
                    .head(
                        HTTPResponseHead(
                            version: requestedUpgrade ? .http1_1 : .http1_0,
                            status: hijackStatus,
                            headers: headers
                        )
                    )
                ),
                promise: headPromise
            )
            let rawHandler = DockerRawStreamHandler(
                session: session,
                terminal: terminal,
                logger: logger
            )
            let sendableContext = SendableChannelHandlerContext(context)
            headPromise.futureResult.whenComplete { result in
                let context = sendableContext.value
                switch result {
                case .success:
                    do {
                        try context.pipeline.syncOperations.addHandler(rawHandler)
                        context.pipeline.syncOperations.removeHandler(self.requestDecoder, promise: nil)
                        context.pipeline.syncOperations.removeHandler(self.responseEncoder, promise: nil)
                        context.pipeline.syncOperations.removeHandler(self, promise: nil)
                        rawHandler.start(channel: context.channel)
                    } catch {
                        self.logger.error(
                            "Engine connection takeover failed",
                            metadata: ["error": .string(String(describing: error))]
                        )
                        context.close(promise: nil)
                    }
                case let .failure(error):
                    self.logger.error(
                        "Engine connection takeover failed",
                        metadata: ["error": .string(String(describing: error))]
                    )
                    context.close(promise: nil)
                }
            }
        }
    }

    private func streamBody(
        _ stream: AsyncThrowingStream<Data, any Error>,
        context: ChannelHandlerContext
    ) {
        let sendableContext = SendableChannelHandlerContext(context)
        Task {
            do {
                for try await data in stream {
                    let bytes = data
                    sendableContext.value.eventLoop.execute {
                        let context = sendableContext.value
                        var buffer = context.channel.allocator.buffer(capacity: bytes.count)
                        buffer.writeBytes(bytes)
                        context.writeAndFlush(
                            self.wrapOutboundOut(.body(.byteBuffer(buffer))),
                            promise: nil
                        )
                    }
                }
                sendableContext.value.eventLoop.execute {
                    let context = sendableContext.value
                    context.writeAndFlush(self.wrapOutboundOut(.end(nil)), promise: nil)
                }
            } catch {
                self.logger.error(
                    "Engine stream failed",
                    metadata: ["error": .string(String(describing: error))]
                )
                sendableContext.value.eventLoop.execute {
                    let context = sendableContext.value
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
        let data = Data("{\"message\":\"\(message)\"}".utf8)
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
        context.writeAndFlush(wrapOutboundOut(.end(nil)), promise: nil)
    }
}

private final class DockerRawStreamHandler: ChannelInboundHandler, @unchecked Sendable {
    typealias InboundIn = ByteBuffer

    private let session: any RuntimeProcessSession
    private let terminal: Bool
    private let logger: Logger
    private let inputPump: OrderedRuntimeInputPump
    private var outputTask: Task<Void, Never>?
    private let stateLock = NSLock()
    private var finishedNormally = false

    init(session: any RuntimeProcessSession, terminal: Bool, logger: Logger) {
        self.session = session
        self.terminal = terminal
        self.logger = logger
        inputPump = OrderedRuntimeInputPump(session: session)
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
                logger.error(
                    "Engine raw stream failed",
                    metadata: ["error": .string(String(describing: error))]
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

    func userInboundEventTriggered(context: ChannelHandlerContext, event: Any) {
        if let channelEvent = event as? ChannelEvent, channelEvent == .inputClosed {
            inputPump.close()
        }
        context.fireUserInboundEventTriggered(event)
    }

    func channelInactive(context: ChannelHandlerContext) {
        let shouldCancel = stateLock.withLock {
            !finishedNormally
        }
        if shouldCancel {
            outputTask?.cancel()
            inputPump.cancel()
        } else {
            inputPump.finish()
        }
        context.fireChannelInactive()
    }
}

/// Serializes bytes and EOF from a hijacked Docker connection before forwarding
/// them to the runtime process. NIO invokes the synchronous enqueue methods in
/// channel order; the single consumer task preserves that order across the
/// asynchronous runtime boundary.
final class OrderedRuntimeInputPump: @unchecked Sendable {
    private enum Event: Sendable {
        case data(Data)
        case close
    }

    private let continuation: AsyncStream<Event>.Continuation
    private let worker: Task<Void, Never>
    private let stateLock = NSLock()
    private var finished = false

    init(session: any RuntimeProcessSession) {
        let (stream, continuation) = AsyncStream<Event>.makeStream()
        self.continuation = continuation
        worker = Task {
            do {
                for await event in stream {
                    try Task.checkCancellation()
                    switch event {
                    case let .data(data):
                        try await session.write(data)
                    case .close:
                        try await session.closeStandardInput()
                    }
                }
            } catch {
                await session.cancel()
            }
        }
    }

    func write(_ data: Data) {
        guard !data.isEmpty else {
            return
        }
        stateLock.withLock {
            guard !finished else {
                return
            }
            continuation.yield(.data(data))
        }
    }

    func close() {
        stateLock.withLock {
            guard !finished else {
                return
            }
            finished = true
            continuation.yield(.close)
            continuation.finish()
        }
    }

    func finish() {
        stateLock.withLock {
            guard !finished else {
                return
            }
            finished = true
            continuation.finish()
        }
    }

    func cancel() {
        stateLock.withLock {
            guard !finished else {
                return
            }
            finished = true
            continuation.finish()
            worker.cancel()
        }
    }

    func wait() async {
        await worker.value
    }
}

private struct SendableChannelHandlerContext: @unchecked Sendable {
    let value: ChannelHandlerContext

    init(_ value: ChannelHandlerContext) {
        self.value = value
    }
}
