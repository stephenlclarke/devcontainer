//===----------------------------------------------------------------------===//
// Copyright 2026 devcontainer project authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//===----------------------------------------------------------------------===//

@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import DevContainerState
import Foundation
import Testing

@Suite(.serialized)
struct AppleContainerRuntimeRecoveryTests {
    @Test
    func `engine restart restores host managed fixed port listeners`() async throws {
        let fixture = try FakeAppleCLI()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let store = try SQLiteStateStore(
            path: fixture.root.appendingPathComponent("state.sqlite")
        )
        let context = RuntimeRequestContext()
        let initial = try fixture.runtime(metadataStore: store)
        let snapshot = try #require(
            try await initial.listContainers(
                all: true,
                labels: [:],
                context: context
            ).first
        )
        let fixedPort = try await reserveHostPort()

        var spec = snapshot.spec
        spec.ports = [
            PortBinding(
                containerPort: 8080,
                hostPort: fixedPort,
                hostAddress: "127.0.0.1",
                published: true,
                hostForwarded: true
            )
        ]
        try await store.recordContainerMetadata(
            RuntimeContainerMetadata(
                runtimeID: snapshot.runtimeID,
                dockerID: snapshot.dockerID,
                imageID: snapshot.imageID,
                spec: spec,
                createdAt: snapshot.createdAt,
                startedAt: snapshot.startedAt
            )
        )

        let restarted = try fixture.runtime(metadataStore: store)
        try await restarted.restorePortForwarding(context: context)
        #expect(
            await restarted.portForwarding.hasListeners(
                containerID: snapshot.runtimeID.rawValue
            )
        )
        let log = try fixture.log()
        #expect(!log.contains("stop --time 0 fixture"))
        try await restarted.killContainer(id: snapshot.runtimeID.rawValue, signal: "SIGUSR1", context: context)
        #expect(await restarted.portForwarding.hasListeners(containerID: snapshot.runtimeID.rawValue))
        try fixture.setState("stopped")
        await expectListenerClosed(restarted, id: snapshot.runtimeID.rawValue)
        await restarted.shutdown()
    }

    private func expectListenerClosed(_ runtime: AppleContainerRuntime, id: String) async {
        let deadline = ContinuousClock.now.advanced(by: .seconds(3))
        while await runtime.portForwarding.hasListeners(containerID: id), ContinuousClock.now < deadline {
            try? await Task.sleep(for: .milliseconds(10))
        }
        #expect(await !runtime.portForwarding.hasListeners(containerID: id))
    }

    private func reserveHostPort() async throws -> UInt16 {
        let reservation = PortForwarding()
        let resolved = try await reservation.start(
            containerID: "reservation",
            bindings: [
                PortBinding(
                    containerPort: 65000,
                    hostAddress: "127.0.0.1"
                )
            ],
            networkAddresses: ["bridge": "127.0.0.1/8"]
        )
        let port = try #require(resolved.first?.hostPort)
        await reservation.stopAll()
        return port
    }
}
