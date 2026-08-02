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

import DevContainerDockerAPI
import Foundation
import NIOCore
import NIOHTTP1

/// Holds the connection state needed to preserve an input half-close that
/// arrives while an HTTP upgrade response is being prepared.
final class DockerUpgradeState: @unchecked Sendable {
    var upgradeCandidate = false
    var inputClosed = false

    func beginRequest(_ head: HTTPRequestHead) {
        upgradeCandidate = head.headers.contains(name: "Upgrade")
    }
}

/// Prevents the HTTP decoder from consuming upgraded-protocol bytes as a
/// truncated HTTP message when the client sends its input and EOF eagerly.
final class DockerInputCloseBarrier:
    ChannelInboundHandler,
    RemovableChannelHandler,
    @unchecked Sendable
{
    typealias InboundIn = ByteBuffer

    private let state: DockerUpgradeState

    init(state: DockerUpgradeState) {
        self.state = state
    }

    func channelRead(context: ChannelHandlerContext, data: NIOAny) {
        context.fireChannelRead(data)
    }

    func userInboundEventTriggered(
        context: ChannelHandlerContext,
        event: Any
    ) {
        if let channelEvent = event as? ChannelEvent,
           channelEvent == .inputClosed,
           state.upgradeCandidate
        {
            state.inputClosed = true
            return
        }
        context.fireUserInboundEventTriggered(event)
    }

    func removeHandler(
        context: ChannelHandlerContext,
        removalToken: ChannelHandlerContext.RemovalToken
    ) {
        context.leavePipeline(removalToken: removalToken)
    }
}

struct DockerHTTPPendingRequest {
    let head: HTTPRequestHead
    let request: DockerHTTPRequest?
    let bodyBytes: Int
}

struct DockerHTTPPipeline {
    let responseEncoder: HTTPResponseEncoder
    let requestDecoder: ByteToMessageHandler<HTTPRequestDecoder>
    let upgradeState: DockerUpgradeState
    let inputCloseBarrier: DockerInputCloseBarrier
}

final class EngineConnectionTracker: @unchecked Sendable {
    private let lock = NSLock()
    private var active = 0

    var count: Int {
        lock.withLock { active }
    }

    func opened() {
        lock.withLock {
            active += 1
        }
    }

    func closed() {
        lock.withLock {
            active = max(0, active - 1)
        }
    }
}

struct SendableChannelHandlerContext: @unchecked Sendable {
    let value: ChannelHandlerContext

    init(_ value: ChannelHandlerContext) {
        self.value = value
    }
}
