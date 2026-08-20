//===----------------------------------------------------------------------===//
// Copyright 2026 devcontainer project authors.
// Licensed under the Apache License, Version 2.0.
//===----------------------------------------------------------------------===//

@testable import DevContainerAppleRuntime
import DevContainerModel
import DevContainerRuntimeSPI
import Foundation
import Testing

@Suite(.serialized)
struct AppleContainerRuntimeLifecycleRaceTests {
    @Test
    func `restart waits for an in-flight start then executes`() async throws {
        let fixture = try FakeAppleCLI()
        try fixture.setState("created")
        let runtime = try fixture.runtime()
        let context = RuntimeRequestContext()

        async let start: Void = runtime.startContainer(
            id: "fixture",
            context: context
        )
        let deadline = ContinuousClock.now + .seconds(1)
        while !((try? fixture.log()) ?? "").contains("start fixture"),
              ContinuousClock.now < deadline
        {
            try await Task.sleep(for: .milliseconds(5))
        }
        #expect(try fixture.log().contains("start fixture"))
        try await runtime.restartContainer(
            id: "fixture",
            timeout: nil,
            context: context
        )
        try await start

        let lifecycleCommands = try fixture.log().split(separator: "\n")
            .filter { $0 == "start fixture" || $0 == "restart fixture" }
        #expect(lifecycleCommands == ["start fixture", "restart fixture"])
    }

    @Test
    func `automatic removal does not delete a restarted generation`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
        let context = RuntimeRequestContext()
        let container = try await runtime.createContainer(
            spec: ContainerSpec(
                name: "fixture",
                image: "fixture:latest",
                autoRemove: true
            ),
            context: context
        )
        try fixture.setState("stopped")
        await runtime.handleContainerExit(
            AppleContainerRuntime.ContainerExit(
                code: 23,
                finishedAt: Date()
            ),
            id: container.runtimeID.rawValue
        )

        try fixture.setState("running")
        try await Task.sleep(for: .milliseconds(1100))

        #expect(try !fixture.log().contains("delete --force fixture"))
    }

    @Test
    func `automatic removal waits through an active restart`() async throws {
        let fixture = try FakeAppleCLI()
        try fixture.setState("stopped")
        let runtime = try fixture.runtime()
        let operation = Task {
            try await Task.sleep(for: .seconds(5))
        }
        await runtime.registerTestStartOperation(
            id: "fixture",
            operation: operation
        )

        await runtime.scheduleAutomaticRemoval(id: "fixture")
        try await Task.sleep(for: .milliseconds(1100))

        #expect(!((try? fixture.log()) ?? "").contains("delete --force fixture"))
        operation.cancel()
        await runtime.clearTestStartOperation(id: "fixture")
    }

    @Test
    func `restart rejects an active automatic removal fence`() async throws {
        let fixture = try FakeAppleCLI()
        let runtime = try fixture.runtime()
        await runtime.registerTestAutomaticRemoval(id: "fixture")

        await #expect(throws: DevContainerError.self) {
            try await runtime.restartContainer(
                id: "fixture",
                timeout: nil,
                context: RuntimeRequestContext()
            )
        }
        #expect(!((try? fixture.log()) ?? "").contains("restart fixture"))
        await runtime.clearTestAutomaticRemoval(id: "fixture")
    }

    @Test
    func `wait ignores an exit from the replaced process generation`() async throws {
        let fixture = try FakeAppleCLI()
        try fixture.setState("running")
        let runtime = try fixture.runtime()
        let initialInventoryCount = ((try? fixture.log()) ?? "")
            .components(separatedBy: "list --all").count
        let oldTask = Task {
            try await Task.sleep(for: .milliseconds(500))
            return AppleContainerRuntime.ContainerExit(
                code: 11,
                finishedAt: Date()
            )
        }
        await runtime.registerTestExitTask(id: "fixture", task: oldTask)

        async let result = runtime.waitContainer(
            id: "fixture",
            context: RuntimeRequestContext()
        )
        let deadline = ContinuousClock.now + .seconds(1)
        while ((try? fixture.log()) ?? "")
            .components(separatedBy: "list --all").count <= initialInventoryCount,
            ContinuousClock.now < deadline
        {
            try await Task.sleep(for: .milliseconds(5))
        }
        try await Task.sleep(for: .milliseconds(20))
        let newTask = Task {
            try await Task.sleep(for: .milliseconds(700))
            return AppleContainerRuntime.ContainerExit(
                code: 22,
                finishedAt: Date()
            )
        }
        await runtime.registerTestExitTask(id: "fixture", task: newTask)

        let exitCode = try await result
        #expect(exitCode == 22)
    }
}

private extension AppleContainerRuntime {
    func registerTestStartOperation(
        id: String,
        operation: Task<Void, any Error>
    ) {
        containerStartOperations[id] = ContainerStartOperation(
            registration: UUID(),
            kind: .restart,
            task: operation
        )
    }

    func clearTestStartOperation(id: String) {
        containerStartOperations.removeValue(forKey: id)
    }

    func registerTestAutomaticRemoval(id: String) {
        automaticRemovalRegistrations[id] = UUID()
    }

    func clearTestAutomaticRemoval(id: String) {
        automaticRemovalRegistrations.removeValue(forKey: id)
    }

    func registerTestExitTask(
        id: String,
        task: Task<ContainerExit, any Error>
    ) {
        startedContainers.insert(id)
        containerExitTasks[id] = task
        containerExitRegistrations[id] = UUID()
    }
}
