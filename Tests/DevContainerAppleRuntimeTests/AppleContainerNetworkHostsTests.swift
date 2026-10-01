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

import ContainerAPIClient
import ContainerizationError
import ContainerResource
import Darwin
@testable import DevContainerAppleRuntime
import DevContainerModel
import Foundation
import Testing

struct AppleContainerNetworkHostsTests {
    #if !DEVCONTAINER_ENHANCED_RUNTIME
        @Test(arguments: ["SIGUSR1", "SIGUSR2", "SIGTERM"])
        func `signal delivery preserves forwarding until actual process exit`(_ signal: String) async throws {
            let fixture = try FakeAppleCLI()
            let snapshot = try nativeNetworkSnapshot(id: "app", service: nil, address: "192.0.2.2")
            let inventory = FakeContainerInventory(snapshots: [snapshot])
            let runtime = try directRuntime(fixture: fixture, inventory: inventory)
            var observed = try await runtime.inspectContainer(id: "app", context: RuntimeRequestContext())
            observed.spec.ports = [PortBinding(
                containerPort: 80, hostPort: 0, hostAddress: "127.0.0.1", published: true, hostForwarded: true
            )]
            try await runtime.startPortForwarding(snapshot: observed, startedAt: #require(observed.startedAt))
            try await runtime.killContainer(id: "app", signal: signal, context: RuntimeRequestContext())
            #expect(await runtime.portForwarding.hasListeners(containerID: "app"))
            #expect(try fixture.log().contains("kill --signal \(signal) app"))
            await inventory.replaceSnapshots([ContainerResource.ContainerSnapshot(
                configuration: snapshot.configuration, status: .stopped, networks: [], startedDate: snapshot.startedDate
            )])
            let deadline = ContinuousClock.now.advanced(by: .seconds(3))
            while await runtime.portForwarding.hasListeners(containerID: "app"), ContinuousClock.now < deadline {
                try await Task.sleep(for: .milliseconds(10))
            }
            #expect(await !runtime.portForwarding.hasListeners(containerID: "app"))
            await runtime.shutdown()
        }

        @Test
        func `process exit during hosts transfer cannot open a late forwarding listener`() async throws {
            let fixture = try FakeAppleCLI()
            let snapshot = try nativeNetworkSnapshot(id: "app", service: "app", address: "192.0.2.2")
            let inventory = FakeContainerInventory(snapshots: [snapshot])
            let files = FakeContainerFileClient()
            let runtime = try directRuntime(fixture: fixture, inventory: inventory, files: files)
            await files.holdNextDownload()
            let operation = Task {
                _ = try await runtime
                    .synchronizeNetworkHostsAndInventory(context: RuntimeRequestContext()) { snapshots in
                        let target = try #require(snapshots.first)
                        _ = try await runtime.portForwarding.start(
                            containerID: "app", bindings: [PortBinding(
                                containerPort: 80,
                                hostPort: 0,
                                hostAddress: "127.0.0.1"
                            )],
                            networkAddresses: target.networkAddresses
                        )
                    }
            }
            await files.waitForHeldDownload()
            #expect(await runtime.portForwarding.hasListeners(containerID: "app"))
            await inventory.replaceSnapshots([ContainerResource.ContainerSnapshot(
                configuration: snapshot.configuration, status: .stopped, networks: [], startedDate: snapshot.startedDate
            )])
            let exit = Task {
                await runtime.handleContainerExit(.init(code: 0, finishedAt: Date()), id: "app")
            }
            let deadline = ContinuousClock.now.advanced(by: .seconds(5))
            while await runtime.portForwarding.hasListeners(containerID: "app"), ContinuousClock.now < deadline {
                await Task.yield()
            }
            let closedBeforeTransfer = await !runtime.portForwarding.hasListeners(containerID: "app")
            await files.releaseDownload()
            try await operation.value
            await exit.value
            #expect(closedBeforeTransfer)
            #expect(await !runtime.portForwarding.hasListeners(containerID: "app"))
        }

        @Test(arguments: [false, true])
        func `native exec reconciles complete inventory without touching unrelated guests`(
            nativeOnly: Bool
        ) async throws {
            let fixture = try FakeAppleCLI()
            let inventory = try FakeContainerInventory(snapshots: [
                nativeNetworkSnapshot(id: "app", service: "app", address: "192.0.2.2", nativeOnly: nativeOnly),
                nativeNetworkSnapshot(
                    id: "database-1",
                    service: "database",
                    address: "192.0.2.3",
                    nativeOnly: nativeOnly
                ),
                nativeNetworkSnapshot(id: "unrelated", service: nil, address: "192.0.2.4"),
                nativeNetworkSnapshot(
                    id: "other-project",
                    service: "database",
                    address: "198.51.100.2",
                    network: "isolated"
                )
            ])
            let files = FakeContainerFileClient()
            let runtime = try directRuntime(fixture: fixture, inventory: inventory, files: files)
            _ = try await runtime.listContainers(
                all: true,
                labels: ["com.docker.compose.service": "app"],
                context: RuntimeRequestContext()
            )
            #expect(await files.copyInCallCount() == 0)
            _ = try await runtime.createExec(
                containerID: "app",
                spec: ExecSpec(command: ["true"]),
                context: RuntimeRequestContext()
            )
            #expect(await files.copyInCallCount() == 1)
            #expect(await files.copyOutCallCount() == 1)
            #expect(await files.hosts().contains("192.0.2.3 database database-1"))
            #expect(await !files.hosts().contains("other-project"))
        }

        @Test
        func `cancelled hosts transfer never uploads and a later request can recover`() async throws {
            let fixture = try FakeAppleCLI()
            let inventory = try FakeContainerInventory(snapshots: [nativeNetworkSnapshot(
                id: "app",
                service: "app",
                address: "192.0.2.2"
            )])
            let files = FakeContainerFileClient()
            let runtime = try directRuntime(fixture: fixture, inventory: inventory, files: files)
            await files.holdNextDownload()
            let operation = Task { try await runtime.synchronizeNetworkHosts(context: RuntimeRequestContext()) }
            await files.waitForHeldDownload()
            operation.cancel()
            await files.releaseDownload()
            await #expect(throws: DevContainerError.self) { try await operation.value }
            #expect(await files.copyInCallCount() == 0)
            try await runtime.synchronizeNetworkHosts(context: RuntimeRequestContext())
            #expect(await files.copyInCallCount() == 1)
        }

        @Test
        func `cancelled queued reconciliation does not cancel its predecessor`() async throws {
            let fixture = try FakeAppleCLI()
            let inventory = try FakeContainerInventory(snapshots: [nativeNetworkSnapshot(
                id: "app",
                service: "app",
                address: "192.0.2.2"
            )])
            let files = FakeContainerFileClient()
            let runtime = try directRuntime(fixture: fixture, inventory: inventory, files: files)
            await files.holdNextDownload()
            let first = Task { try await runtime.synchronizeNetworkHosts(context: RuntimeRequestContext()) }
            await files.waitForHeldDownload()
            let registration = try #require(await runtime.networkHostsOperation?.id)
            let second = Task { try await runtime.synchronizeNetworkHosts(context: RuntimeRequestContext()) }
            let queued = await waitForHostsQueueChange(runtime: runtime, previous: registration)
            second.cancel()
            await files.releaseDownload()
            try await first.value
            #expect(queued)
            await #expect(throws: DevContainerError.self) { try await second.value }
            #expect(await files.copyOutCallCount() == 1)
            #expect(await files.copyInCallCount() == 1)
        }

        @Test
        func `queued hosts reconciliation reloads inventory after earlier transfers`() async throws {
            let fixture = try FakeAppleCLI()
            let inventory = try FakeContainerInventory(snapshots: [
                nativeNetworkSnapshot(id: "app", service: "app", address: "192.0.2.2"),
                nativeNetworkSnapshot(id: "database-1", service: "database", address: "192.0.2.3")
            ])
            let files = FakeContainerFileClient()
            let runtime = try directRuntime(fixture: fixture, inventory: inventory, files: files)
            let context = RuntimeRequestContext()
            await files.holdNextDownload()
            let first = Task { try await runtime.synchronizeNetworkHosts(
                context: context,
                targetID: RuntimeID(rawValue: "app")
            ) }
            await files.waitForHeldDownload()
            let registration = try #require(await runtime.networkHostsOperation?.id)
            try await inventory.replaceSnapshots([
                nativeNetworkSnapshot(id: "app", service: "app", address: "192.0.2.2"),
                nativeNetworkSnapshot(id: "database-2", service: "database", address: "192.0.2.5")
            ])
            let second = Task {
                try await runtime.synchronizeNetworkHosts(context: context, targetID: RuntimeID(rawValue: "app"))
            }
            let queued = await waitForHostsQueueChange(runtime: runtime, previous: registration)
            await files.releaseDownload()
            try await first.value
            try await second.value
            #expect(queued)
            #expect(await files.hosts().contains("192.0.2.5 database database-2"))
            #expect(await !files.hosts().contains("database-1"))
        }
    #endif
}
