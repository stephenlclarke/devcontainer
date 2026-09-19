// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerCore
@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerTestSupport
import Foundation
import Testing

struct DockerNetworkProjectionTests {
    private let context = RuntimeRequestContext()
    private let wireID = String(repeating: "a", count: 64)

    @Test(arguments: [false, true])
    func `container creation resolves returned network IDs before native allocation`(endpoint: Bool) async throws {
        let runtime = InMemoryRuntime()
        await runtime.seedImage(ImageSnapshot(id: "sha256:image", references: ["alpine"], createdAt: Date(), size: 1))
        let router = DockerRouter(runtime: runtime)
        let networkBody = try JSONSerialization.data(withJSONObject: ["Name": "project_default"])
        let created = await router.respond(to: .init(method: .post, target: "/networks/create", body: networkBody))
        #expect(created.status == 201)
        let object = try #require(JSONSerialization.jsonObject(with: bytes(created)) as? [String: String])
        let id = try #require(object["Id"])
        var request: [String: Any] = ["Image": "alpine"]
        if endpoint {
            request["NetworkingConfig"] = ["EndpointsConfig": [id: ["Aliases": ["service"]]]]
        } else {
            request["HostConfig"] = ["NetworkMode": id]
        }
        let body = try JSONSerialization.data(withJSONObject: request)
        let response = await router.respond(to: .init(method: .post, target: "/containers/create?name=app", body: body))
        #expect(response.status == 201)
        let container = try await runtime.inspectContainer(id: "app", context: context)
        #expect(container.spec.networks == [NetworkAttachment(
            name: "project_default",
            aliases: endpoint ? ["service"] : []
        )])

        // Removing and recreating the name cannot make the old wire ID valid.
        #expect(await router.respond(to: .init(method: .delete, target: "/networks/\(id)")).status == 204)
        #expect(await router.respond(to: .init(method: .post, target: "/networks/create", body: networkBody))
            .status == 201)
        let stale = await router.respond(to: .init(method: .post, target: "/containers/create?name=stale", body: body))
        #expect(stale.status == 404)
        #expect(await runtime.listContainers(all: true, labels: [:], context: context).count == 1)
    }

    @Test
    func `network wire identity persists across routers and name reuse`() async throws {
        let runtime = InMemoryRuntime()
        let router = DockerRouter(runtime: runtime)
        let body = try JSONSerialization.data(withJSONObject: ["Name": "project_default"])
        let request = DockerHTTPRequest(method: .post, target: "/networks/create", body: body)
        let created = await router.respond(to: request)
        #expect(created.status == 201)
        let object = try #require(JSONSerialization.jsonObject(with: bytes(created)) as? [String: String])
        let first = try #require(object["Id"])
        #expect(first.count == 64)
        let native = try await runtime.inspectNetwork(id: "project_default", context: context)
        #expect(native.spec.labels[RuntimeLabels.dockerID] == first)
        #expect(native.id != first)
        let restarted = DockerRouter(runtime: runtime)
        #expect(await restarted.respond(to: .init(method: .get, target: "/networks/\(first)")).status == 200)
        #expect(await restarted.respond(to: .init(method: .delete, target: "/networks/\(first)")).status == 204)
        let replacement = await restarted.respond(to: request)
        let next = try #require(JSONSerialization.jsonObject(with: bytes(replacement)) as? [String: String])
        #expect(next["Id"] != first)
        #expect(await restarted.respond(to: .init(method: .get, target: "/networks/\(first)")).status == 404)
        #expect(await restarted.respond(to: .init(method: .delete, target: "/networks/\(first)")).status == 404)
        #expect(await runtime.listNetworks(context: context).count == 1)
    }

    @Test
    func `network list filters use projected labels and wire identity`() async throws {
        let runtime = InMemoryRuntime()
        _ = try await runtime.createNetwork(spec: spec(), context: context)
        let router = DockerRouter(runtime: runtime)
        let filters = try JSONSerialization.data(withJSONObject: [
            "label": ["com.docker.compose.project=project", "com.docker.compose.network=default"],
            "id": [String(wireID.prefix(12))]
        ])
        let query = try #require(String(data: filters, encoding: .utf8)?
            .addingPercentEncoding(withAllowedCharacters: .alphanumerics))
        let response = await router.respond(to: .init(method: .get, target: "/networks?filters=\(query)"))
        #expect(response.status == 200)
        let networks = try #require(JSONSerialization.jsonObject(with: bytes(response)) as? [[String: Any]])
        #expect(networks.count == 1)
        #expect(networks.first?["Id"] as? String == wireID)
        let labels = try #require(networks.first?["Labels"] as? [String: String])
        #expect(labels["com.docker.compose.network"] == "default")
    }

    @Test
    func `reserved network identity cannot be supplied by API clients`() async throws {
        let runtime = InMemoryRuntime()
        let body = try JSONSerialization.data(withJSONObject: [
            "Name": "reserved", "Labels": [RuntimeLabels.dockerID: wireID]
        ])
        let response = await DockerRouter(runtime: runtime).respond(to: .init(
            method: .post, target: "/networks/create", body: body
        ))
        #expect(response.status == 400)
        #expect(await runtime.listNetworks(context: context).isEmpty)
    }

    @Test(arguments: ["", "short", String(repeating: "A", count: 64), String(repeating: "g", count: 64)])
    func `malformed native network identity is rejected`(value: String) throws {
        var network = NetworkSnapshot(id: "native", spec: spec(), createdAt: Date())
        network.spec.labels[RuntimeLabels.dockerID] = value
        #expect(throws: DevContainerError.self) { try RuntimeLabels.networkDockerID(network) }
        network.spec.labels.removeValue(forKey: RuntimeLabels.dockerID)
        #expect(try RuntimeLabels.networkDockerID(network) == "native")
    }

    @Test
    func `ambiguous native wire identities cannot select a deletion target`() async throws {
        let runtime = InMemoryRuntime()
        _ = try await runtime.createNetwork(spec: spec(), context: context)
        var other = spec()
        other.name = "other"
        _ = try await runtime.createNetwork(spec: other, context: context)
        let response = await DockerRouter(runtime: runtime).respond(to: .init(
            method: .delete,
            target: "/networks/\(wireID)"
        ))
        #expect(response.status == 409)
        #expect(await runtime.listNetworks(context: context).count == 2)
    }

    @Test(arguments: ["192.0.2.2/24", "2001:db8::2/64"])
    func `observed native attachments expose Docker identities and names`(address: String) throws {
        let router = DockerRouter(runtime: InMemoryRuntime())
        let network = NetworkSnapshot(id: "native", spec: spec(), createdAt: Date())
        var container = snapshot()
        container.networkAddresses = [network.spec.name: address]
        let value = try router.networkInspect(network, containers: [container])
        let endpoint = try #require(value.containers[container.dockerID.rawValue])
        #expect(value.id == wireID)
        #expect(endpoint.name == "app")
        #expect(endpoint.ipv4Address == (address.contains(":") ? "" : address))
        #expect(endpoint.ipv6Address == (address.contains(":") ? address : ""))
        container.networkAddresses = [:]
        #expect(try router.networkInspect(network, containers: [container]).containers.isEmpty)
    }

    @Test
    func `provider reported membership also uses observed container identity`() throws {
        let router = DockerRouter(runtime: InMemoryRuntime())
        let container = snapshot()
        let network = NetworkSnapshot(
            id: "native",
            spec: spec(),
            createdAt: Date(),
            containers: [container.runtimeID: "192.0.2.3"]
        )
        #expect(try router.networkInspect(network, containers: [container]).containers[container.dockerID.rawValue]?
            .name == "app")
        #expect(throws: DevContainerError.self) { try router.networkInspect(network, containers: []) }
        #expect(throws: DevContainerError.self) {
            try router.networkInspect(network, containers: [container, container])
        }
    }

    @Test
    func `conflicting native and Docker network labels are not overwritten`() throws {
        var network = NetworkSnapshot(id: "native", spec: spec(), createdAt: Date())
        network.spec.labels["com.docker.compose.network"] = "foreign"
        let router = DockerRouter(runtime: InMemoryRuntime())
        #expect(throws: DevContainerError.self) { try router.networkInspect(network, containers: []) }
    }

    private func spec() -> NetworkSpec {
        NetworkSpec(name: "project_default", labels: [
            RuntimeLabels.dockerID: wireID,
            "com.apple.container.compose.project": "project",
            "com.apple.container.compose.network": "default"
        ])
    }

    private func snapshot() -> ContainerSnapshot {
        ContainerSnapshot(
            runtimeID: RuntimeID(rawValue: "native-app"), dockerID: DockerID(rawValue: String(
                repeating: "b",
                count: 64
            )),
            spec: ContainerSpec(name: "app", image: "alpine", networks: [NetworkAttachment(name: "project_default")]),
            state: .running, createdAt: Date()
        )
    }
}
