// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerEngineGateway
import ContainerEngineProviderSession
import ContainerEngineRuntimeSPI
import ContainerEngineWire
import DevContainerDockerAPI
@testable import DevContainerService
import DevContainerTestStorage
import DevContainerTestSupport
import Foundation
import Testing

struct RecoveryGatewayTests {
    @Test func `selected provider recovery reaches the runtime barrier`() async throws {
        let root = TestStorage.temporaryDirectory.appendingPathComponent("rg-\(UUID().uuidString.prefix(8))")
        try FileManager.default.createDirectory(
            at: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700]
        )
        defer { try? FileManager.default.removeItem(at: root) }
        let socket = root.appendingPathComponent("p.sock").path
        let runtime = InMemoryRuntime()
        let declaration = try await DevContainerServiceCommand.providerDeclaration(
            runtime: runtime.descriptor(context: .init()), resourceOwner: .stock
        )
        let fingerprint = try ContainerEngineProviderFingerprint(declaration: declaration, stateRootUUID: UUID())
        let provider = try ContainerEngineProviderSessionServer(
            responder: DockerRouter(runtime: runtime), socketPath: socket,
            declaration: declaration, stateRootUUID: fingerprint.stateRootUUID
        )
        try provider.start()
        do {
            let gateway = try ContainerEngineGatewayResponder(providerSocketPath: socket, fingerprint: fingerprint)
            try await exerciseBarrier(gateway)
        } catch {
            await provider.shutdown()
            throw error
        }
        await provider.shutdown()
        #expect(!FileManager.default.fileExists(atPath: socket))
    }

    private func exerciseBarrier(_ gateway: ContainerEngineGatewayResponder) async throws {
        let target = "/v1.53/_container-family/recovery"
        let response = await gateway.respond(to: .init(method: .get, target: target))
        #expect(response.status == 200)
        guard case let .bytes(body) = response.body else {
            Issue.record("Recovery discovery must have a complete byte response")
            return
        }
        let value = try JSONDecoder().decode([String: String].self, from: body)
        #expect(value["protocol"] == "1")
        let epoch = try #require(value["epoch"])
        let owner = String(repeating: "a", count: 64)
        let wrongEpoch = try DockerHTTPRequest(
            method: .post,
            target: target,
            body: JSONEncoder().encode(["epoch": "stale", "owner": owner])
        )
        #expect(await gateway.respond(to: wrongEpoch).status == 409)
        let freeze = try DockerHTTPRequest(
            method: .post,
            target: target,
            body: JSONEncoder().encode(["epoch": epoch, "owner": owner])
        )
        #expect(await gateway.respond(to: freeze).status == 200)
        #expect(await gateway.respond(to: .init(method: .post, target: "/containers/create", body: Data("{}".utf8)))
            .status == 409)
        #expect(await gateway.respond(to: .init(method: .get, target: "/containers/json")).status == 200)
        #expect(await gateway.respond(to: freeze).status == 200)
        // Deletes remain admitted, but a failed mutation conservatively revokes
        // quiescence; forwarding must not turn uncertainty into cleanup authority.
        #expect(await gateway.respond(to: .init(method: .delete, target: "/containers/absent")).status == 404)
        let uncertain = await gateway.respond(to: freeze)
        #expect(uncertain.status == 409)
    }
}
