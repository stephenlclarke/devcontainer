// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
import Foundation

actor DockerRecoveryBarrier {
    static let route = "/_container-family/recovery"
    static let preflightHeader = "X-Container-Create-Preflight"
    let epoch = UUID().uuidString
    private var owner: String?
    private var active = Set<UUID>()
    private var uncertain = false

    var healthProbesAllowed: Bool { owner == nil }

    func begin(_ method: DockerHTTPMethod, path: String = "") throws -> UUID {
        let segments = path.split(separator: "/")
        let attach = segments.count >= 3 && segments[0] == "containers" && segments[2] == "attach"
        let readOnly = (method == .get || method == .head) && !attach
        guard owner == nil || method == .delete || readOnly else {
            throw DevContainerError(.conflict, message: "Gateway is frozen for resource recovery")
        }
        let token = UUID()
        active.insert(token)
        return token
    }

    func finish(_ token: UUID, method: DockerHTTPMethod = .post, path: String = "", response: DockerHTTPResponse) {
        active.remove(token)
        // Streaming work can outlive the handler; absent a completion receipt
        // it cannot grant recovery authority. Runtime failures may also leave
        // native RPCs pending after the client receives an error.
        switch response.body {
        case .bytes: break
        case .stream, .managedStream, .hijack, .webSocket: uncertain = true
        }
        let segments = path.split(separator: "/")
        if method == .post, segments.count >= 3, segments[0] == "exec", segments[2] == "start" {
            uncertain = true // Detached execution has no response-body lifetime.
        }
        if method != .get, method != .head, response.status >= 400,
           response.headers[Self.preflightHeader] != "rejected" { uncertain = true }
    }

    func recordUncertainWork() {
        uncertain = true
    }

    func freeze(epoch: String, owner: String) throws {
        guard epoch == self.epoch, owner.count == 64,
              owner.allSatisfy({ "0123456789abcdef".contains($0) }), self.owner == nil || self.owner == owner
        else {
            throw DevContainerError(.conflict, message: "Recovery epoch or owner differs")
        }
        self.owner = owner
        try requireIdle()
    }

    func requireIdle() throws {
        guard active.isEmpty, !uncertain else {
            throw DevContainerError(.conflict, message: "Gateway has unfinished or uncertain mutations")
        }
    }
}
