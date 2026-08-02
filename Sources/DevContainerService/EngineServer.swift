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

import ContainerEngineWire
import ContainerUnixHTTPServer
import DevContainerDockerAPI
import Foundation
import Logging

/// Stock-provider adapter around the one shared Engine listener.
///
/// Transport ownership, socket safety, request buffering and hijack lifecycle
/// live only in `container-engine-api`; devcontainer retains Docker endpoint
/// policy until the neutral runtime-operation router is complete.
final class EngineServer: @unchecked Sendable {
    private let server: ContainerUnixHTTPServer

    init(
        router: DockerRouter,
        socketPath: String,
        logger: Logger,
        limits: EngineServerLimits = .production
    ) {
        server = ContainerUnixHTTPServer(
            responder: router,
            socketPath: socketPath,
            logger: logger,
            limits: limits.shared
        )
    }

    func start() async throws {
        try await server.start()
    }

    func wait() async throws {
        try await server.wait()
    }

    var activeConnectionCount: Int {
        server.activeConnectionCount
    }

    func shutdown() async throws {
        try await server.shutdown()
    }
}

typealias EngineServerError = ContainerUnixHTTPServerError

enum EngineResponseEncoding {
    static func dockerError(_ message: String) -> Data {
        (try? DockerJSON.encoder.encode(
            ContainerEngineWire.DockerErrorEnvelope(message: message)
        ))
            ?? Data("{\"message\":\"server error\"}".utf8)
    }
}
