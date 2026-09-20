// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerResource
@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import DevContainerState
import Foundation
import Testing

#if !DEVCONTAINER_ENHANCED_RUNTIME
    struct ApplePortForwardingObservationTests {
        @Test
        func shutdownJoinsReplacedObserver() async throws {
            try await withFixture { runtime, inventory, snapshot, _ in
                await inventory.holdOneGet()
                try await runtime.startPortForwarding(snapshot: snapshot, startedAt: Date())
                let held = await eventually { await inventory.isGetHeld() }
                #expect(held)
                try await runtime.startPortForwarding(snapshot: snapshot, startedAt: Date())
                let completed = CompletionFlag()
                let shutdown = Task {
                    await runtime.shutdown()
                    await completed.mark()
                }
                // The held read ignores cancellation deliberately. Shutdown
                // must join that retired observer, not merely cancel it.
                try await Task.sleep(for: .milliseconds(100))
                #expect(await !completed.value)
                await inventory.releaseGet()
                await shutdown.value
                #expect(await completed.value)
                #expect(await !runtime.portForwarding.hasListeners(containerID: "app"))
            }
        }

        @Test
        func dynamicRestorePreservesInferredStartTime() async throws {
            try await withFixture(unknownStart: true) { runtime, inventory, snapshot, store in
                #expect(snapshot.startedAt == snapshot.createdAt)
                try await runtime.restorePortForwarding(context: RuntimeRequestContext())
                let reads = await inventory.getCallCount()
                let observed = await eventually { await inventory.getCallCount() >= reads + 2 }
                #expect(observed)
                let metadata = try #require(try await store.containerMetadata(id: "app"))
                #expect(metadata.startedAt == snapshot.startedAt)
                #expect((metadata.spec.ports.first?.hostPort ?? 0) > 0)
                #expect(await runtime.portForwarding.hasListeners(containerID: "app"))
            }
        }

        @Test
        func transportFailurePreservesListenersAndConfirmedAbsenceClosesThem() async throws {
            try await withFixture { runtime, inventory, snapshot, _ in
                await inventory.setGetFailure(.failed)
                try await runtime.startPortForwarding(snapshot: snapshot, startedAt: Date())
                let reads = await inventory.getCallCount()
                let retried = await eventually { await inventory.getCallCount() >= reads + 2 }
                #expect(retried)
                #expect(await runtime.portForwarding.hasListeners(containerID: "app"))
                await inventory.setGetFailure(nil)
                await inventory.replaceSnapshots([])
                let closed = await eventually { await !runtime.portForwarding.hasListeners(containerID: "app") }
                #expect(closed)
            }
        }

        @Test
        func nativeRestartClosesOnlyTheOldListenerGeneration() async throws {
            try await withFixture { runtime, inventory, snapshot, _ in
                try await runtime.startPortForwarding(snapshot: snapshot, startedAt: Date())
                let native = try await inventory.get(id: "app")
                await inventory.replaceSnapshots([ContainerResource.ContainerSnapshot(
                    configuration: native.configuration, status: .running, networks: native.networks,
                    startedDate: Date(timeIntervalSince1970: 99)
                )])
                let closed = await eventually { await !runtime.portForwarding.hasListeners(containerID: "app") }
                #expect(closed)
                let current = try await runtime.inspectContainer(id: "app", context: RuntimeRequestContext())
                try await runtime.startPortForwarding(snapshot: current, startedAt: Date())
                let reads = await inventory.getCallCount()
                let observed = await eventually { await inventory.getCallCount() >= reads + 2 }
                #expect(observed)
                #expect(await runtime.portForwarding.hasListeners(containerID: "app"))
            }
        }

        private func withFixture(
            unknownStart: Bool = false,
            operation: (AppleContainerRuntime, FakeContainerInventory, DevContainerModel.ContainerSnapshot,
                        SQLiteStateStore) async throws -> Void
        ) async throws {
            let fixture = try FakeAppleCLI()
            defer { try? FileManager.default.removeItem(at: fixture.root) }
            let base = try nativeNetworkSnapshot(id: "app", service: nil, address: "192.0.2.2")
            let native = ContainerResource.ContainerSnapshot(
                configuration: base.configuration, status: .running, networks: base.networks,
                startedDate: unknownStart ? nil : base.startedDate
            )
            let inventory = FakeContainerInventory(snapshots: [native])
            let store = try SQLiteStateStore(path: fixture.root.appendingPathComponent("state.sqlite"))
            let runtime = try directRuntime(fixture: fixture, inventory: inventory, metadataStore: store)
            do {
                var snapshot = try await runtime.inspectContainer(id: "app", context: RuntimeRequestContext())
                snapshot.spec.ports = [PortBinding(
                    containerPort: 80, hostPort: 0, hostAddress: "127.0.0.1", published: true, hostForwarded: true
                )]
                try await store.recordContainerMetadata(RuntimeContainerMetadata(
                    runtimeID: snapshot.runtimeID, dockerID: snapshot.dockerID, imageID: snapshot.imageID,
                    spec: snapshot.spec, createdAt: snapshot.createdAt, startedAt: snapshot.startedAt
                ))
                try await operation(runtime, inventory, snapshot, store)
            } catch {
                await inventory.releaseGet()
                await runtime.shutdown()
                throw error
            }
            await inventory.releaseGet()
            await runtime.shutdown()
        }

        private func eventually(_ predicate: () async -> Bool) async -> Bool {
            let deadline = ContinuousClock.now.advanced(by: .seconds(3))
            while ContinuousClock.now < deadline {
                if await predicate() { return true }
                try? await Task.sleep(for: .milliseconds(10))
            }
            return await predicate()
        }
    }

    private actor CompletionFlag {
        private(set) var value = false
        func mark() { value = true }
    }
#endif
