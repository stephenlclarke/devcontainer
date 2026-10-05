// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerizationError
import ContainerXPC
import Foundation
import Testing

@Suite("Pinned XPC fast-reply timeout teardown", .serialized)
struct AppleXPCFastReplyTimeoutTests {
    @Test
    func `quick replies repeatedly cancel long timeout children`() async throws {
        let server = FastReplyXPCServer()
        defer { server.close() }
        let client = server.makeClient()
        defer { client.close() }
        for sequence in 0 ..< 256 {
            let request = XPCMessage(route: "echo")
            request.set(key: "sequence", value: String(sequence))
            let response = try await client.send(request, responseTimeout: .seconds(60))
            #expect(response.string(key: "sequence") == String(sequence))
        }
    }

    @Test
    func `a closed connection reports an error with the long timeout configured`() async throws {
        let server = FastReplyXPCServer()
        defer { server.close() }
        let client = server.makeClient()
        client.close()
        await #expect(throws: ContainerizationError.self) {
            try await client.send(XPCMessage(route: "echo"), responseTimeout: .seconds(60))
        }
    }
}

private final class FastReplyXPCServer: @unchecked Sendable {
    private let listener: xpc_connection_t
    private let lock = NSLock()
    private var connections: [xpc_connection_t] = []

    init() {
        listener = xpc_connection_create(nil, nil)
        xpc_connection_set_event_handler(listener) { [weak self] object in
            guard xpc_get_type(object) == XPC_TYPE_CONNECTION else { return }
            self?.accept(object)
        }
        xpc_connection_activate(listener)
    }

    func makeClient() -> XPCClient {
        let endpoint = xpc_endpoint_create(listener)
        return XPCClient(
            connection: xpc_connection_create_from_endpoint(endpoint),
            label: "test.devcontainer.xpc.fast-reply"
        )
    }

    func close() {
        xpc_connection_cancel(listener)
        lock.withLock {
            for connection in connections {
                xpc_connection_cancel(connection)
            }
            connections.removeAll()
        }
    }

    private func accept(_ connection: xpc_connection_t) {
        lock.withLock { connections.append(connection) }
        nonisolated(unsafe) let peer = connection
        xpc_connection_set_event_handler(connection) { object in
            guard xpc_get_type(object) == XPC_TYPE_DICTIONARY,
                  let reply = xpc_dictionary_create_reply(object)
            else { return }
            let message = XPCMessage(object: object)
            let response = XPCMessage(object: reply)
            response.set(key: "sequence", value: message.string(key: "sequence") ?? "")
            xpc_connection_send_message(peer, reply)
        }
        xpc_connection_activate(connection)
    }
}
