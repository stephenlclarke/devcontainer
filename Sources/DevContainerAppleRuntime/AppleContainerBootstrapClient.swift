// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerAPIClient
import Containerization
import ContainerizationOS
import ContainerResource
import ContainerXPC
import DevContainerModel
import Foundation

protocol AppleContainerBootstrapClient: Sendable {
    func bootstrap(id: String, stdio: [FileHandle?]) async throws -> any ClientProcess
}

struct LiveAppleContainerBootstrapClient: AppleContainerBootstrapClient {
    let send: @Sendable (XPCMessage) async throws -> XPCMessage
    let disconnect: @Sendable () -> Void

    init() {
        #if DEVCONTAINER_ENHANCED_RUNTIME
            let service = ContainerServiceNamespace.current.apiServerIdentifier
        #else
            let service = "com.apple.container.apiserver"
        #endif
        let client = XPCClient(service: service)
        send = { try await client.send($0) }
        disconnect = { client.close() }
    }

    init(
        send: @escaping @Sendable (XPCMessage) async throws -> XPCMessage,
        disconnect: @escaping @Sendable () -> Void
    ) {
        self.send = send
        self.disconnect = disconnect
    }

    func bootstrap(id: String, stdio: [FileHandle?]) async throws -> any ClientProcess {
        guard stdio.count <= 3 else {
            throw DevContainerError(.invalidRequest, message: "Bootstrap accepts only three standard streams")
        }
        let request = XPCMessage(route: .containerBootstrap)
        request.set(key: .id, value: id)
        // Send this optional field with either pinned client SDK. Stock Apple
        // already propagates descriptor EOF; enhanced runtimes otherwise treat
        // it as client detach. A later close RPC could overtake buffered input.
        request.set(key: "closeStdinOnEOF", value: stdio.first.map { $0 != nil } ?? false)
        request.set(key: .dynamicEnv, value: Data("{}".utf8))
        let keys: [XPCKeys] = [.stdin, .stdout, .stderr]
        for (key, handle) in try zip(keys, AppleXPCFileHandleTransfer.copies(of: stdio)) {
            if let handle {
                request.set(key: key, value: handle)
            }
        }
        _ = try await send(request)
        return AppleBootstrapProcess(id: id, send: send, disconnect: disconnect)
    }
}

/// Keeps the bootstrap's connection for the same public process operations on
/// both SDK profiles, without bootstrapping the container a second time.
private struct AppleBootstrapProcess: ClientProcess {
    let id: String
    let send: @Sendable (XPCMessage) async throws -> XPCMessage
    let disconnectBody: @Sendable () -> Void

    init(
        id: String, send: @escaping @Sendable (XPCMessage) async throws -> XPCMessage,
        disconnect: @escaping @Sendable () -> Void
    ) {
        self.id = id
        self.send = send
        disconnectBody = disconnect
    }

    func start() async throws {
        _ = try await send(identified(XPCMessage(route: .containerStartProcess)))
    }

    func resize(_ size: Terminal.Size) async throws {
        let request = identified(XPCMessage(route: .containerResize))
        request.set(key: .width, value: UInt64(size.width))
        request.set(key: .height, value: UInt64(size.height))
        _ = try await send(request)
    }

    func kill(_ signal: Int32) async throws {
        let request = identified(XPCMessage(route: .containerKill))
        request.set(key: .signal, value: Signal.platform.first { $0.value == signal }?.key ?? String(signal))
        _ = try await send(request)
    }

    func wait() async throws -> Int32 {
        let response = try await send(identified(XPCMessage(route: .containerWait)))
        guard let code = Int32(exactly: response.int64(key: .exitCode)) else {
            throw DevContainerError(.runtimeUnavailable, message: "Native process returned an invalid exit code")
        }
        return code
    }

    func disconnect() {
        disconnectBody()
    }

    private func identified(_ request: XPCMessage) -> XPCMessage {
        request.set(key: .id, value: id)
        request.set(key: .processIdentifier, value: id)
        return request
    }
}
