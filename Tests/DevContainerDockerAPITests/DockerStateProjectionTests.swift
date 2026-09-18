// Copyright 2026 devcontainer project authors.
// SPDX-License-Identifier: Apache-2.0

@testable import DevContainerDockerAPI
import DevContainerModel
import DevContainerTestSupport
import Foundation
import Testing

@Test
func `native stopped state is projected as Docker exited without changing runtime state`() throws {
    let router = DockerRouter(runtime: InMemoryRuntime())
    let states: [(RuntimeContainerState, String)] = [
        (.created, "created"), (.running, "running"), (.stopped, "exited"),
        (.removing, "removing"), (.unknown, "unknown")
    ]
    for (state, expected) in states {
        let snapshot = ContainerSnapshot(
            runtimeID: RuntimeID(rawValue: "native-state"),
            dockerID: DockerID(rawValue: String(repeating: "a", count: 64)),
            spec: ContainerSpec(name: "state", image: "fixture:latest"),
            state: state,
            createdAt: Date(timeIntervalSince1970: 1),
            exitCode: 42
        )
        let summary = try router.containerSummary(snapshot)
        let inspected = try router.containerInspect(snapshot, health: nil)
        #expect(summary.state == expected)
        #expect(summary.status == expected)
        #expect(inspected.state.status == expected)
        #expect(inspected.state.running == (state == .running))
        #expect(inspected.state.exitCode == 42)
        #expect(snapshot.state == state)
    }
}
